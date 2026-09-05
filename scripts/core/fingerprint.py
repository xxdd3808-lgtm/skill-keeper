"""skill-keeper v2 完整目录指纹:整个 skill 目录树的确定性 manifest 与 SHA-256 摘要。

规则(设计 §5):
- 覆盖相对路径、文件/目录/符号链接类型、权限、符号链接目标、全部文件内容摘要;
- 符号链接只记录 target 字符串,绝不读取目标内容(防外部内容渗入指纹,也防泄密);
- 运行时垃圾文件排除清单是模块常量,参与摘要计算,不得临时改动;
- 摘要使用完整 SHA-256,不截断。
"""
import hashlib
import json
import os
import stat
from typing import List

# 稳定排除清单:任何调整都等于改变指纹算法,必须同步升 MANIFEST_VERSION
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_DIR_NAMES = {"__pycache__"}
EXCLUDED_SUFFIXES = (".pyc",)

MANIFEST_VERSION = 2


class FingerprintError(OSError):
    """指纹计算遇到不可读/消失的对象;部分树绝不能冒充完整树。"""

    def __init__(self, issues):
        self.issues = issues
        first = issues[0] if issues else {}
        super().__init__("指纹目标不完整: {}({})".format(
            first.get("path"), first.get("code")))


def _is_excluded(rel_parts):
    for part in rel_parts:
        if part in EXCLUDED_NAMES or part in EXCLUDED_DIR_NAMES:
            return True
        if part.endswith(EXCLUDED_SUFFIXES):
            return True
    return False


def _walk_error(collect_errors, root):
    """os.walk onerror:目录不可读时结构化记录;未提供收集容器则抛错,绝不静默跳过。"""
    def handler(err):
        path = err.filename
        issue = {"code": "unreadable", "path": os.path.relpath(path, root)
                 if path.startswith(root) else path}
        if collect_errors is None:
            raise FingerprintError([issue])
        collect_errors.append(issue)
    return handler


def tree_manifest(root, collect_errors=None) -> List[dict]:
    """返回按相对路径 UTF-8 字节序排序的条目列表;root 不是目录时抛 NotADirectoryError。

    不可读的目录/文件:collect_errors 给 None 时抛 FingerprintError(备份/变更路径),
    给列表时收集结构化错误并继续(观察路径,由调用方决定 partial 的用途)。
    """
    root = os.fspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"指纹目标不是目录: {os.path.basename(root)}")
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False,
                                                onerror=_walk_error(collect_errors, root)):
        rel_dir = os.path.relpath(dirpath, root)
        # 排除目录就地剪枝:不进入 __pycache__ 等运行时垃圾目录
        dirnames[:] = sorted(d for d in dirnames
                             if not _is_excluded((*_rel_parts(rel_dir), d)))
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            rel_parts = (*_rel_parts(rel_dir), name)
            if os.path.islink(full):
                entries.append(_entry(rel_parts, "symlink", os.lstat(full), target=os.readlink(full)))
            elif os.path.isdir(full):
                entries.append(_entry(rel_parts, "dir", os.lstat(full)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel_parts = (*_rel_parts(rel_dir), name)
            if _is_excluded(rel_parts):
                continue
            try:
                st = os.lstat(full)
            except OSError as e:
                issue = {"code": "unreadable", "path": "/".join(rel_parts),
                         "reason": type(e).__name__}
                if collect_errors is None:
                    raise FingerprintError([issue])
                collect_errors.append(issue)
                continue
            if os.path.islink(full):
                entries.append(_entry(rel_parts, "symlink", st, target=os.readlink(full)))
            elif os.path.isfile(full):
                try:
                    digest = _file_sha256(full)
                except OSError as e:
                    issue = {"code": "unreadable", "path": "/".join(rel_parts),
                             "reason": type(e).__name__}
                    if collect_errors is None:
                        raise FingerprintError([issue])
                    collect_errors.append(issue)
                    continue
                entries.append(_entry(rel_parts, "file", st, sha256=digest))
            else:
                # 设备/套接字等非常规条目记录类型但不读内容
                entries.append(_entry(rel_parts, "special", st))
    entries.sort(key=lambda e: e["path"].encode("utf-8"))
    return entries


def _rel_parts(rel_dir):
    return () if rel_dir == "." else tuple(rel_dir.split(os.sep))


def _entry(rel_parts, entry_type, st, sha256=None, target=None):
    row = {
        "path": "/".join(rel_parts),
        "type": entry_type,
        "mode": oct(stat.S_IMODE(st.st_mode)),
    }
    if sha256 is not None:
        row["sha256"] = sha256
    if target is not None:
        row["target"] = target
    return row


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_manifest_document(manifest):
    """参与摘要的规范化文档:算法版本 + 排除清单 + 条目,排除规则变化必然改变摘要。"""
    return {
        "manifest_version": MANIFEST_VERSION,
        "excluded_names": sorted(EXCLUDED_NAMES),
        "excluded_dir_names": sorted(EXCLUDED_DIR_NAMES),
        "excluded_suffixes": sorted(EXCLUDED_SUFFIXES),
        "entries": manifest,
    }


def tree_hash(root) -> str:
    """完整目录树 SHA-256(不截断)。内容、权限、路径、链接目标任一变化都会改变结果。"""
    doc = canonical_manifest_document(tree_manifest(root))
    canonical = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def instance_id(location_id: str, directory_name: str, real_path: str) -> str:
    """稳定实例 ID = SHA-256(三个规范化输入),返回前 20 位;完整输入保存在实例记录里。"""
    parts = [_normalize(x) for x in (location_id, directory_name, real_path)]
    canonical = "\n".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def instance_id_evidence(location_id: str, directory_name: str, real_path: str) -> str:
    """instance_id 的规范化输入原文(作为证据保存,可离线复核 ID)。"""
    return "\n".join(_normalize(x) for x in (location_id, directory_name, real_path))


def _normalize(value) -> str:
    text = str(value)
    if text in (".", ".."):
        return text
    text = text.rstrip("/")
    return text or "/"
