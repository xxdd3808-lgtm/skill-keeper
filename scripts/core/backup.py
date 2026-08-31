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
import secrets
import shutil
import stat
import tarfile
import tempfile
import time
from pathlib import Path

from .fingerprint import tree_hash, tree_manifest

SCHEMA = 2
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "payload/"


class BackupError(Exception):
    """备份/校验/恢复失败;消息只描述问题,不夹带敏感内容。"""


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
        th = inst.get("tree_hash") or tree_hash(content_root)
        tm = tree_manifest(content_root)
    elif p.is_dir():
        etype, link_target = "dir", None
        th = inst.get("tree_hash") or tree_hash(str(p))
        tm = tree_manifest(str(p))
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
    """按 ChangePlan 目标创建带 manifest 的唯一归档;返回 {backup_id, path, entries, files}。"""
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
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / ("backup-" + backup_id + ".tar.gz")

    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
    n_files = 0
    with tarfile.open(path, "w:gz") as t:
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
    return {"backup_id": backup_id, "path": str(path), "plan_id": plan.plan_id,
            "entries": len(entries), "files": n_files, "created_at": manifest["created_at"]}


def _safe_member_name(name: str) -> bool:
    if name.startswith("/") or name.endswith("/"):
        return False
    if "\\" in name:
        return False
    parts = name.split("/")
    return ".." not in parts and all(p != "" for p in parts)


def verify_backup(archive_path):
    """逐成员校验归档;任何异常立即抛 BackupError,绝不解包到磁盘。"""
    p = Path(archive_path)
    if not p.is_file():
        raise BackupError("备份不存在: " + p.name)
    try:
        t = tarfile.open(str(p), "r:gz")
    except (tarfile.TarError, OSError) as e:
        raise BackupError("无法打开备份: " + type(e).__name__)
    seen = set()
    payload = {}
    manifest = None
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
            content = f.read()
            if name == MANIFEST_NAME:
                try:
                    manifest = json.loads(content.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise BackupError("manifest.json 不是合法 JSON")
                continue
            if not name.startswith(PAYLOAD_PREFIX):
                raise BackupError("manifest 外成员: " + name)
            payload[name] = hashlib.sha256(content).hexdigest()

    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise BackupError("manifest 缺失或 schema 不受支持")
    known_iids = set()
    for entry in manifest.get("entries", []):
        iid = str(entry.get("instance_id"))
        if not iid or iid in known_iids:
            raise BackupError("manifest 条目重复或缺少 instance_id")
        known_iids.add(iid)
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
            "entries": len(manifest.get("entries", []))}


def restore_backup(archive_path, locations, conflict="fail"):
    """验证后重建到原位置;重建在临时目录完成并验证摘要,再原子就位。冲突默认失败。"""
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
        dest = Path(root) / str(entry["original_relative_path"])
        if os.path.lexists(dest):
            if conflict != "overwrite":
                raise BackupError("目标已存在,冲突失败(未改动任何文件): " + str(entry["directory_name"]))
        plans.append((entry, dest))

    restored_hashes = {}
    with tarfile.open(str(archive_path), "r:gz") as t:
        members = {m.name: m for m in t}
        for entry, dest in plans:
            iid = str(entry["instance_id"])
            prefix = PAYLOAD_PREFIX + iid + "/"
            parent = dest.parent
            parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = Path(tempfile.mkdtemp(prefix=".restore-", dir=parent))
            try:
                if entry["type"] == "symlink":
                    tmp_link = tmp_dir / "entity"
                    os.symlink(entry["link_target"], tmp_link)
                    os.replace(tmp_link, dest)
                elif entry["type"] == "file":
                    m = members.get(prefix + entry["original_relative_path"])
                    if m is None:
                        raise BackupError("payload 缺文件: " + iid)
                    tmp_file = tmp_dir / "entity"
                    tmp_file.write_bytes(t.extractfile(m).read())
                    os.chmod(tmp_file, int(entry["mode"], 8))
                    os.replace(tmp_file, dest)
                else:
                    build = tmp_dir / "entity"
                    build.mkdir()
                    for e in entry.get("tree_manifest", []):
                        if e["type"] == "dir":
                            (build / e["path"]).mkdir(parents=True, exist_ok=True)
                    for e in entry.get("tree_manifest", []):
                        if e["type"] != "file":
                            continue
                        m = members.get(prefix + e["path"])
                        if m is None:
                            raise BackupError("payload 缺文件: " + prefix + e["path"])
                        target_file = build / e["path"]
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        target_file.write_bytes(t.extractfile(m).read())
                        os.chmod(target_file, int(e["mode"], 8))
                    if tree_hash(build) != entry["tree_hash"]:
                        raise BackupError("重建摘要与 manifest 不符: " + str(entry["directory_name"]))
                    os.replace(build, dest)
            except BackupError:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                _cleanup_partial(dest, plans, restored_hashes)
                raise
            except (OSError, tarfile.TarError) as e:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                _cleanup_partial(dest, plans, restored_hashes)
                raise BackupError("恢复失败(" + type(e).__name__ + "): " + str(entry["directory_name"]))
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if entry["type"] == "symlink":
                restored_hashes[iid] = tree_hash(os.path.realpath(dest))
            else:
                restored_hashes[iid] = tree_hash(dest)
            if restored_hashes[iid] != entry["tree_hash"]:
                _cleanup_partial(dest, plans, restored_hashes)
                raise BackupError("恢复后摘要不一致: " + str(entry["directory_name"]))
    return {"ok": True, "backup_id": info["backup_id"], "restored": [str(d) for _, d in plans],
            "restored_hashes": restored_hashes}


def _cleanup_partial(current_dest, plans, restored_hashes):
    """恢复中途失败:把本次已就位的实体按逆序移除,不留半成品。"""
    if os.path.lexists(current_dest) and current_dest in [d for _, d in plans]:
        try:
            if os.path.islink(current_dest):
                os.remove(current_dest)
            elif Path(current_dest).is_dir():
                shutil.rmtree(current_dest)
            else:
                os.remove(current_dest)
        except OSError:
            pass
    for entry, dest in reversed(plans):
        if str(dest) == str(current_dest):
            break
        try:
            if os.path.islink(dest):
                os.remove(dest)
            elif Path(dest).is_dir():
                shutil.rmtree(dest)
            elif os.path.lexists(dest):
                os.remove(dest)
            restored_hashes.pop(str(entry["instance_id"]), None)
        except OSError:
            pass


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
