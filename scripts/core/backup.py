"""skill-keeper v2 备份引擎:manifest-first 归档、逐项校验、原子恢复(设计 §9.2)。

归档布局:
  manifest.json                    ← 结构、来源位置、每个实例的类型/链接/权限/完整树摘要
  payload/<instance_id>/<rel-path> ← 只含普通文件成员(目录/权限/符号链接只在 manifest 记录)

铁律:
- 恢复绝不使用 extractall;逐成员校验后手工重建;
- 拒绝绝对路径、..、重复成员、设备文件、tar 内符号/硬链接与 manifest 外成员;
- 重建先在目标父目录的临时目录完成并验证 tree_hash,再原子移动;冲突默认失败不覆盖;
- 旧格式备份只能 inspect,不能自动恢复。
"""
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import tempfile
import time
from pathlib import Path

from .fingerprint import canonical_manifest_document, tree_hash, tree_manifest
from .paths import PathScopeError, confined_destination, validate_relative_path

SCHEMA = 2
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "payload/"

# 资源上限(本轮保守默认;作为命名常量与错误信息提供,不得自动提高)
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

MANIFEST_ENTRY_TYPES = ("dir", "file", "symlink")
TREE_ENTRY_TYPES = ("file", "dir", "symlink", "special")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODE_RE = re.compile(r"^0o[0-7]{1,4}$")


class BackupError(Exception):
    """备份/校验/恢复失败;消息只描述问题,不夹带敏感内容。"""


def manifest_hash(entries) -> str:
    """manifest 条目列表的 SHA-256,算法与 tree_hash 完全一致(规范化文档 + 完整摘要)。"""
    doc = canonical_manifest_document(list(entries or []))
    canonical = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise BackupError(message)


def _valid_mode(value) -> bool:
    return isinstance(value, str) and bool(_MODE_RE.match(value))


def _valid_sha256(value) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _paths_overlap(a, b) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _validate_tree_entries(tree_entries, iid):
    """tree_manifest 条目的类型/权限/路径/摘要/父子关系;special 一律拒绝(不可恢复)。"""
    seen_paths = set()
    dir_paths = set()
    for row in tree_entries:
        _require(isinstance(row, dict), "实例 {} 的 tree 条目不是对象".format(iid))
        path = row.get("path")
        try:
            validate_relative_path(path)
        except PathScopeError as e:
            raise BackupError("实例 {} 的 tree 路径非法: {}".format(iid, e))
        _require(path not in seen_paths, "实例 {} 的 tree 路径重复: {}".format(iid, path))
        seen_paths.add(path)
        etype = row.get("type")
        _require(etype in TREE_ENTRY_TYPES,
                 "实例 {} 的 tree 条目类型非法: {}".format(iid, path))
        _require(etype != "special",
                 "实例 {} 包含不支持恢复的特殊文件(设备/套接字),拒绝: {}".format(iid, path))
        _require(_valid_mode(row.get("mode")),
                 "实例 {} 的条目权限非法: {}".format(iid, path))
        if etype == "file":
            _require(_valid_sha256(row.get("sha256")),
                     "实例 {} 的文件摘要格式非法: {}".format(iid, path))
        if etype == "symlink":
            _require(isinstance(row.get("target"), str) and row["target"],
                     "实例 {} 的链接条目缺少 target: {}".format(iid, path))
    for row in tree_entries:
        parts = str(row["path"]).split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            _require(parent in dir_paths,
                     "实例 {} 的条目父目录缺失或不是目录: {}".format(iid, row["path"]))
        if row.get("type") == "dir":
            dir_paths.add(row["path"])


def validate_backup_manifest(document) -> dict:
    """严格校验 manifest:结构、路径边界、类型、权限范围、摘要格式、唯一性与整树摘要。

    任何一项不满足都抛 BackupError;返回值就是传入的规范化对象(验证通过才可用)。
    """
    _require(isinstance(document, dict), "manifest 不是 JSON 对象")
    _require(document.get("schema") == SCHEMA, "manifest schema 不受支持")
    for field in ("backup_id", "plan_id", "action", "created_at"):
        _require(isinstance(document.get(field), str) and document[field],
                 "manifest 缺少必要字段: " + field)
    entries = document.get("entries")
    _require(isinstance(entries, list) and bool(entries),
             "manifest 缺少 entries 或 entries 为空")
    _require(len(entries) <= MAX_ENTRIES, "manifest 条目数超过上限({})".format(MAX_ENTRIES))
    seen_iids = set()
    seen_targets = []
    for entry in entries:
        _require(isinstance(entry, dict), "manifest 条目不是对象")
        iid = entry.get("instance_id")
        _require(isinstance(iid, str) and bool(iid) and iid not in seen_iids,
                 "manifest 条目 instance_id 缺失或重复")
        seen_iids.add(iid)
        for field in ("location_id", "directory_name"):
            _require(isinstance(entry.get(field), str) and entry[field],
                     "实例 {} 缺少字段: {}".format(iid, field))
        rel = entry.get("original_relative_path")
        try:
            validate_relative_path(rel)
        except PathScopeError as e:
            raise BackupError("实例 {} 的 original_relative_path 非法: {}".format(iid, e))
        etype = entry.get("type")
        _require(etype in MANIFEST_ENTRY_TYPES,
                 "实例 {} 的实体类型非法: {!r}".format(iid, etype))
        _require(_valid_mode(entry.get("mode")), "实例 {} 的权限值非法".format(iid))
        _require(_valid_sha256(entry.get("tree_hash")), "实例 {} 的整树摘要格式非法".format(iid))
        target_key = (str(entry.get("location_id")), str(rel))
        for other_loc, other_rel in seen_targets:
            _require(not (other_loc == target_key[0]
                          and _paths_overlap(other_rel, target_key[1])),
                     "manifest 目标重复或重叠: {} 与 {}".format(
                         (other_loc, other_rel), target_key))
        seen_targets.append(target_key)
        tree_entries = entry.get("tree_manifest")
        _require(isinstance(tree_entries, list), "实例 {} 缺少 tree_manifest".format(iid))
        if etype == "file":
            _require(len(tree_entries) == 1
                     and isinstance(tree_entries[0], dict)
                     and tree_entries[0].get("type") == "file"
                     and tree_entries[0].get("path") == rel,
                     "单文件实例 {} 的 manifest 不合规范".format(iid))
            _require(tree_entries[0].get("sha256") == entry.get("tree_hash"),
                     "单文件实例 {} 的内容摘要与 tree_hash 不符".format(iid))
        else:
            _require(manifest_hash(tree_entries) == entry.get("tree_hash"),
                     "实例 {} 的整树摘要与 manifest 内容不符".format(iid))
        if etype == "symlink":
            _require(isinstance(entry.get("link_target"), str) and entry["link_target"],
                     "symlink 实例 {} 缺少 link_target".format(iid))
        _validate_tree_entries(tree_entries, iid)
    return document


def _chunked_sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(os.fspath(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _entry_of(inst, locations):
    loc = locations.get(inst.get("location_id"))
    if not loc:
        raise BackupError("实例位置不在 inventory 中: " + str(inst.get("location_id")))
    root = loc["path"]
    p = Path(inst["path"])
    rel = os.path.relpath(str(p), root)
    if rel.startswith(".."):
        raise BackupError("实例路径不在位置根目录内: " + str(inst.get("directory_name")))
    if p.is_symlink():
        etype, link_target = "symlink", os.readlink(p)
        content_root = os.path.realpath(p)
        tm = tree_manifest(content_root)
        th = manifest_hash(tm)
    elif p.is_dir():
        etype, link_target = "dir", None
        tm = tree_manifest(str(p))
        th = manifest_hash(tm)
    elif p.is_file():
        # 单文件 skill:合成单条目 manifest,tree_hash 取文件内容摘要
        etype, link_target = "file", None
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        th = digest
        tm = [{"path": rel.replace(os.sep, "/"), "type": "file",
               "mode": oct(stat.S_IMODE(os.lstat(str(p)).st_mode)), "sha256": digest}]
    else:
        raise BackupError("不支持的实体类型: " + str(inst.get("directory_name")))
    return {
        "instance_id": inst.get("instance_id"),
        "location_id": inst.get("location_id"),
        "directory_name": inst.get("directory_name"),
        "original_relative_path": rel.replace(os.sep, "/"),
        "type": etype,
        "link_target": link_target,
        "mode": oct(stat.S_IMODE(os.lstat(str(p)).st_mode)),
        "tree_hash": th,
        "tree_manifest": tm,
    }


def _payload_files(entry):
    """返回 entry 的 payload 文件列表 [(archive_name, absolute_path)];symlink 展开目标内容。"""
    iid = entry["instance_id"]
    src_root = Path(entry["_content_root"])
    rows = []
    for e in entry["tree_manifest"]:
        if e["type"] != "file":
            continue
        rows.append((PAYLOAD_PREFIX + iid + "/" + e["path"], src_root / e["path"]))
    return rows


def create_backup(plan, inventory, backup_dir):
    """按 ChangePlan 目标创建带 manifest 的唯一归档;返回 {backup_id, path, entries, files, archive_sha256}。

    归档写入同目录临时文件,fsync 后原子发布;写入失败不留"可选中"的成功备份。
    """
    locations = {l["location_id"]: l for l in inventory.get("locations", [])}
    insts = {i["instance_id"]: i for i in inventory.get("instances", [])}
    entries = []
    for iid in plan.target_ids:
        inst = insts.get(iid)
        if not inst:
            raise BackupError("计划目标不在当前 inventory: " + str(iid))
        entry = _entry_of(inst, locations)
        entry["_content_root"] = (os.path.realpath(inst["path"]) if inst.get("is_symlink")
                                  else inst["path"])
        entries.append(entry)

    backup_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
    manifest = {
        "schema": SCHEMA,
        "backup_id": backup_id,
        "plan_id": plan.plan_id,
        "action": plan.action,
        "reason": plan.summary,
        "created_at": _now(),
        "entries": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries],
    }
    validate_backup_manifest(manifest)  # 自检:自己生成的 manifest 必须能过严格校验
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    final_path = backup_dir / ("backup-" + backup_id + ".tar.gz")

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    n_files = 0
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-backup-", suffix=".tar.gz",
                                    dir=str(backup_dir))
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w:gz") as t:
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(manifest_bytes)
            info.mtime = int(time.time())
            t.addfile(info, io.BytesIO(manifest_bytes))
            for entry, entry_row in zip(entries, manifest["entries"]):
                for arcname, src in _payload_files(entry):
                    if not src.is_file():
                        raise BackupError("payload 文件消失: " + entry["directory_name"])
                    data = src.read_bytes()
                    info = tarfile.TarInfo(arcname)
                    info.size = len(data)
                    info.mtime = int(time.time())
                    t.addfile(info, io.BytesIO(data))
                    n_files += 1
        with open(tmp_path, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
    except (tarfile.TarError, OSError) as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if isinstance(e, BackupError):
            raise
        raise BackupError("归档写入失败({}),未发布任何备份".format(type(e).__name__))
    return {"backup_id": backup_id, "path": str(final_path), "plan_id": plan.plan_id,
            "entries": len(entries), "files": n_files, "created_at": manifest["created_at"],
            "archive_sha256": _chunked_sha256_file(final_path)}


def _safe_member_name(name: str) -> bool:
    if name.startswith("/") or name.endswith("/"):
        return False
    if "\\" in name:
        return False
    parts = name.split("/")
    return ".." not in parts and all(p != "" for p in parts)


def verify_backup(archive_path):
    """逐成员校验归档并严格校验 manifest;任何异常立即抛 BackupError,绝不解包到磁盘。

    返回增加 archive_sha256;同时强制资源上限,超限明确拒绝,不跳过文件后宣称完整。
    """
    import zlib
    p = Path(archive_path)
    if not p.is_file():
        raise BackupError("备份不存在: " + p.name)
    archive_sha256 = _chunked_sha256_file(p)
    try:
        t = tarfile.open(str(p), "r:gz")
    except (tarfile.TarError, OSError, zlib.error, EOFError) as e:
        raise BackupError("无法打开备份: " + type(e).__name__)
    seen = set()
    payload = {}
    manifest = None
    total_bytes = 0
    try:
        with t:
            for m in t:
                name = m.name
                if name in seen:
                    raise BackupError("tar 成员重复: " + name)
                seen.add(name)
                if not _safe_member_name(name):
                    raise BackupError("不安全成员路径: " + name)
                if m.isdir():
                    continue  # 容忍目录成员仅作占位,但不记录
                if m.issym() or m.islnk():
                    raise BackupError("tar 内不允许链接成员: " + name)
                if m.ischr() or m.isblk() or m.isfifo():
                    raise BackupError("tar 内不允许设备/管道成员: " + name)
                if not m.isfile():
                    raise BackupError("未知成员类型: " + name)
                f = t.extractfile(m)
                if f is None:
                    raise BackupError("成员不可读: " + name)
                if name == MANIFEST_NAME:
                    if m.size > MAX_MANIFEST_BYTES:
                        raise BackupError("manifest 超过大小上限({})".format(MAX_MANIFEST_BYTES))
                    content = f.read()
                    total_bytes += len(content)
                    try:
                        manifest = json.loads(content.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise BackupError("manifest.json 不是合法 JSON")
                    continue
                if not name.startswith(PAYLOAD_PREFIX):
                    raise BackupError("manifest 外成员: " + name)
                if m.size > MAX_FILE_BYTES:
                    raise BackupError("成员超过单文件上限({}): {}".format(MAX_FILE_BYTES, name))
                total_bytes += m.size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise BackupError("归档解包总量超过上限({})".format(MAX_TOTAL_BYTES))
                h = hashlib.sha256()
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                payload[name] = h.hexdigest()
    except BackupError:
        raise
    except (tarfile.TarError, OSError, zlib.error, EOFError) as e:
        raise BackupError("归档读取失败(" + type(e).__name__ + ")")

    manifest = validate_backup_manifest(manifest)
    for entry in manifest["entries"]:
        iid = str(entry.get("instance_id"))
        prefix = PAYLOAD_PREFIX + iid + "/"
        expected = {prefix + e["path"]: e["sha256"] for e in entry.get("tree_manifest", [])
                    if e.get("type") == "file"}
        for arc, sha in expected.items():
            if arc not in payload:
                raise BackupError("payload 缺文件: " + arc)
            if payload[arc] != sha:
                raise BackupError("payload 摘要不符: " + arc)
            del payload[arc]
        if any(k.startswith(prefix) for k in payload):
            raise BackupError("payload 存在 manifest 外文件: " + str(iid))
    if payload:
        raise BackupError("payload 存在未知 instance 的文件")
    return {"ok": True, "backup_id": manifest.get("backup_id"), "manifest": manifest,
            "entries": len(manifest.get("entries", [])), "archive_sha256": archive_sha256}


def _mode_of(value):
    try:
        return int(str(value), 8)
    except (TypeError, ValueError):
        raise BackupError("权限值非法: " + str(value)[:20])


def _entity_hash(entry, path):
    """按实体类型计算恢复后摘要:file=内容摘要;symlink=目标树摘要;dir=整树摘要。"""
    path = os.fspath(path)
    if entry["type"] == "file":
        return _chunked_sha256_file(path)
    if entry["type"] == "symlink":
        return tree_hash(os.path.realpath(path))
    return tree_hash(path)


def _copy_member(tar, member, dest_path):
    src = tar.extractfile(member)
    if src is None:
        raise BackupError("成员不可读: " + member.name)
    with open(dest_path, "wb") as out:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())


def _try_chmod(path, mode):
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        pass


def _try_chmod_link(link, mode_value):
    """符号链接权限尽量还原;平台不支持时跳过(由整树摘要校验兜底发现差异)。"""
    try:
        os.chmod(link, _mode_of(mode_value), follow_symlinks=False)
    except (NotImplementedError, OSError, TypeError):
        pass


def _symlink_target_abs(dest, link_target):
    if os.path.isabs(link_target):
        return os.path.normpath(link_target)
    return os.path.normpath(os.path.join(os.path.dirname(os.fspath(dest)), link_target))


def _check_symlink_target(entry, dest, published):
    """顶层链接只恢复链接本身:目标必须已存在且匹配备份,或是本事务先恢复的实体。"""
    link_target = str(entry.get("link_target") or "")
    if not link_target:
        raise BackupError("symlink 实例缺少链接目标: " + str(entry.get("directory_name")))
    abs_target = _symlink_target_abs(dest, link_target)
    if not os.path.lexists(abs_target):
        raise BackupError("顶层链接目标缺失,拒绝凭链接 payload 写出外部目标: "
                          + str(entry.get("directory_name")))
    for _entry, pdest in published:
        if os.path.realpath(abs_target) == os.path.realpath(pdest):
            return  # 同一事务中先恢复的实体
    actual = _entity_hash(entry, abs_target if not os.path.islink(abs_target)
                          else abs_target)
    if actual != entry["tree_hash"]:
        raise BackupError("顶层链接目标与备份内容不一致: " + str(entry.get("directory_name")))


def _materialize_dir(entry, build, tar, members, prefix):
    """目录实体:先目录,再文件,内部 symlink 最后;目录权限在子内容完成后还原。

    返回最终权限下的整树摘要。发布前把 build 根临时调回可写:macOS 跨父目录
    rename 目录要更新 ".." 项,只读目录会 EACCES;最终权限在发布后还原。
    """
    rows = entry.get("tree_manifest", [])
    build.mkdir()
    for row in rows:
        if row["type"] == "dir":
            (build / row["path"]).mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row["type"] != "file":
            continue
        m = members.get(prefix + row["path"])
        if m is None:
            raise BackupError("payload 缺文件: " + prefix + row["path"])
        target_file = build / row["path"]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        _copy_member(tar, m, target_file)
        os.chmod(target_file, _mode_of(row["mode"]))
    for row in rows:
        if row["type"] != "symlink":
            continue
        link = build / row["path"]
        os.symlink(row["target"], link)
        _try_chmod_link(link, row["mode"])
    for row in sorted((r for r in rows if r["type"] == "dir"),
                      key=lambda r: -str(r["path"]).count("/")):
        os.chmod(build / row["path"], _mode_of(row["mode"]))
    os.chmod(build, _mode_of(entry["mode"]))
    built_hash = tree_hash(build)
    os.chmod(build, 0o700)  # 发布前临时可写;最终权限由发布方还原
    return built_hash


def restore_backup(archive_path, locations, conflict="fail"):
    """验证后重建到原位置:先预检全部目标,逐实体物化-验证-发布;失败只撤销本次实际落地的对象。

    冲突默认失败;发布前再次核验冲突;顶层 symlink 排在最后(目标可能是同事务先恢复的实体)。
    """
    info = verify_backup(archive_path)
    manifest = info["manifest"]
    loc_map = {}
    for loc in locations or []:
        if isinstance(loc, dict):
            loc_map[str(loc.get("location_id"))] = loc.get("path")
        else:
            loc_map[str(loc.location_id)] = loc.path

    plans = []
    for entry in manifest["entries"]:
        root = loc_map.get(str(entry.get("location_id")))
        if not root:
            raise BackupError("备份位置未登记,无法恢复: " + str(entry.get("location_id")))
        try:
            dest = confined_destination(root, str(entry["original_relative_path"]))
        except PathScopeError as e:
            raise BackupError("恢复目标越界,拒绝落地: " + str(e))
        if os.path.lexists(dest):
            if conflict != "overwrite":
                raise BackupError("目标已存在,冲突失败(未改动任何文件): "
                                  + str(entry["directory_name"]))
        plans.append((entry, Path(dest)))

    ordered = ([p for p in plans if p[0]["type"] != "symlink"]
               + [p for p in plans if p[0]["type"] == "symlink"])
    published = []  # 实际成功落地清单;清理只按它撤销,不按计划逆序猜测
    tmp_dirs = []
    try:
        with tarfile.open(str(archive_path), "r:gz") as t:
            members = {m.name: m for m in t}
            for entry, dest in ordered:
                if os.path.lexists(dest):
                    raise BackupError("发布前冲突核验失败,目标已出现,拒绝覆盖: "
                                      + str(entry["directory_name"]))
                tmp_dir = Path(tempfile.mkdtemp(prefix=".restore-", dir=str(dest.parent)))
                tmp_dirs.append(tmp_dir)
                _publish_entity(entry, dest, tmp_dir, t, members, published)
    except BackupError as e:
        cleanup_failures = _revoke_published(published)
        _remove_tmp_dirs(tmp_dirs)
        if cleanup_failures:
            raise BackupError("{};另外清理半成品失败,需人工检查: {}".format(e, cleanup_failures))
        raise
    except (OSError, tarfile.TarError, RuntimeError) as e:
        cleanup_failures = _revoke_published(published)
        _remove_tmp_dirs(tmp_dirs)
        message = "恢复失败({}),已撤销本次已落地对象".format(type(e).__name__)
        if cleanup_failures:
            message += ";清理半成品失败,需人工检查: " + cleanup_failures
        raise BackupError(message)
    return {"ok": True, "backup_id": info["backup_id"],
            "restored": [str(d) for _, d in published],
            "restored_hashes": {str(e["instance_id"]): _entity_hash(e, d)
                                for e, d in published}}


def _publish_entity(entry, dest, tmp_dir, tar, members, published):
    """物化 → 摘要验证 → 原子发布 → 发布后验;任何失败向上抛,由调用方统一撤销。"""
    iid = str(entry["instance_id"])
    prefix = PAYLOAD_PREFIX + iid + "/"
    entity = tmp_dir / "entity"
    if entry["type"] == "symlink":
        _check_symlink_target(entry, dest, published)
        os.symlink(entry["link_target"], entity)
        # 链接本身无内容可预验:相对目标的解析基准是最终位置而非临时目录,
        # 目标匹配已在 _check_symlink_target 完成,落地后再由发布后验兜底
        built_hash = entry["tree_hash"]
    elif entry["type"] == "file":
        m = members.get(prefix + entry["original_relative_path"])
        if m is None:
            raise BackupError("payload 缺文件: " + prefix + entry["original_relative_path"])
        _copy_member(tar, m, entity)
        os.chmod(entity, _mode_of(entry["mode"]))
        built_hash = _entity_hash(entry, entity)
    else:
        built_hash = _materialize_dir(entry, entity, tar, members, prefix)
    if built_hash != entry["tree_hash"]:
        raise BackupError("重建摘要与 manifest 不符: " + str(entry["directory_name"]))
    if os.path.lexists(dest):
        raise BackupError("发布前冲突核验失败,目标已出现,拒绝覆盖: "
                          + str(entry["directory_name"]))
    os.replace(str(entity), str(dest))
    if entry["type"] == "dir":
        os.chmod(str(dest), _mode_of(entry["mode"]))  # 发布后还原最终权限
    # 先登记进实际落地清单,再做发布后验:后验失败时撤销逻辑才能覆盖它
    published.append((entry, dest))
    if _entity_hash(entry, dest) != entry["tree_hash"]:
        raise BackupError("恢复后摘要不一致: " + str(entry["directory_name"]))


def _revoke_published(published):
    """按实际落地清单逆序撤销;只删本次发布的精确目标,绝不碰陌生文件。返回失败描述或 None。"""
    failures = []
    for entry, dest in reversed(published):
        try:
            if os.path.islink(dest) or (os.path.lexists(dest) and not os.path.isdir(dest)):
                os.remove(dest)
            elif os.path.isdir(dest):
                shutil.rmtree(dest)
        except OSError as e:
            failures.append("{}({})".format(dest, type(e).__name__))
    return ", ".join(failures) if failures else None


def _remove_tmp_dirs(tmp_dirs):
    for tmp in reversed(tmp_dirs):
        shutil.rmtree(tmp, ignore_errors=True)


def inspect_legacy_backup(archive_path):
    """只读检视旧格式备份(无 manifest);永不自动恢复。"""
    p = Path(archive_path)
    if not p.is_file():
        raise BackupError("备份不存在: " + p.name)
    try:
        with tarfile.open(str(p), "r:*") as t:
            names = [m.name for m in t]
    except (tarfile.TarError, OSError) as e:
        raise BackupError("无法打开备份: " + type(e).__name__)
    has_manifest = MANIFEST_NAME in names
    return {
        "legacy": not has_manifest,
        "restored": False,
        "members": names[:50],
        "member_count": len(names),
        "has_manifest": has_manifest,
        "limitations": [] if has_manifest else [
            "旧格式缺少位置 manifest,无法确认原位置、符号链接拓扑和逐文件摘要",
            "旧 tar 成员可能重复/互相覆盖;恢复前必须人工确认目标位置",
            "skill-keeper v2 不会自动恢复旧格式备份",
        ],
    }
