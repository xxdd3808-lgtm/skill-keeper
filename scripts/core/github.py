"""GitHub 证据:repo 热度快照、固定 commit 完整树抓取与本地缓存。

热度口径(设计 §6):stars/forks/活跃度只属于"仓库",永远不能冒充单个 Skill 的
真实使用人数;网络失败/限流时保留旧缓存并标记 stale,绝不把缺数据解释为低质量。
所有网络调用都通过注入的 gh_runner(args_list) -> (returncode, stdout),便于测试替换。
"""
import base64
import json
import os
import subprocess
import time
from pathlib import Path

from .fingerprint import tree_hash
from .io import atomic_write_json, load_json_checked


def gh_cli_runner(timeout=30):
    """返回经 gh CLI 的 gh_runner;gh 未安装/超时返回非零码,由调用方降级。"""

    def run(args):
        try:
            r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout
        except (OSError, subprocess.TimeoutExpired):
            return 124, ""

    return run


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def repo_snapshot(repo, gh_runner):
    """采集仓库级证据;失败返回结构化 {ok: False, stale: True, error},不抛异常。"""
    try:
        code, out = gh_runner(["repos/" + repo])
        if code != 0:
            return {"ok": False, "stale": True, "repo": repo, "error": "repo-fetch-failed",
                    "fetched_at": _iso_now()}
        data = json.loads(out)
        snap = {
            "ok": True,
            "repo": repo,
            "stars": int(data.get("stargazers_count") or 0),
            "forks": int(data.get("forks_count") or 0),
            "archived": bool(data.get("archived")),
            "pushed_at": data.get("pushed_at"),
            "created_at": data.get("created_at"),
            "open_issues": int(data.get("open_issues_count") or 0),
            "license": (data.get("license") or {}).get("spdx_id") if isinstance(data.get("license"), dict) else None,
            "default_branch": data.get("default_branch") or "main",
            "popularity_scope": "repository",
            "popularity_note": "仓库热度,不等于该 Skill 的真实使用人数",
            "fetched_at": _iso_now(),
        }
        branch = snap["default_branch"]
        try:
            c, o = gh_runner(["repos/{}/commits/{}".format(repo, branch)])
            snap["commit_sha"] = json.loads(o).get("sha") if c == 0 else None
        except (ValueError, TypeError):
            snap["commit_sha"] = None
        try:
            c, o = gh_runner(["repos/{}/releases/latest".format(repo)])
            snap["latest_release"] = json.loads(o).get("tag_name") if c == 0 else None
        except (ValueError, TypeError):
            snap["latest_release"] = None
        try:
            c, o = gh_runner(["repos/{}/contributors?per_page=100&anon=true".format(repo)])
            snap["contributors"] = len(json.loads(o)) if c == 0 else None
        except (ValueError, TypeError):
            snap["contributors"] = None
        return snap
    except (json.JSONDecodeError, TypeError, OSError) as e:
        return {"ok": False, "stale": True, "repo": repo, "error": type(e).__name__,
                "fetched_at": _iso_now()}


def flatten_repos(node, out=None):
    """把任意形态/嵌套的 reputation 文件展平成 {repo: snapshot}。

    修复历史 bug:旧版把整个文件当成缓存表再嵌套写回,导致一层层套娃。
    规则:键含 "/" 且值是 dict 的视为仓库快照(同仓库取 fetched_at 最新),
    其余 dict 值继续向下找;非 dict 一律忽略。
    """
    if out is None:
        out = {}
    if not isinstance(node, dict):
        return out
    for key, value in node.items():
        if not isinstance(value, dict):
            continue
        name = str(key)
        if "/" in name:
            prev = out.get(name)
            if prev is None or str(value.get("fetched_at") or "") >= str(prev.get("fetched_at") or ""):
                out[name] = value
        else:
            flatten_repos(value, out)
    return out


def cached_repo_snapshot(repo, reputation_path, gh_runner):
    """带本地缓存的快照:网络失败时保留旧缓存并标记 stale,不把旧数据清空。

    读取时先展平历史嵌套损坏;成功时以规范形态 {"schema_version": 2, "repos": {...}}
    整体重写,旧文件顺带自愈。
    """
    reputation_path = Path(reputation_path)
    cache, _ = load_json_checked(reputation_path, {})
    repos = flatten_repos(cache if isinstance(cache, dict) else {})
    snap = repo_snapshot(repo, gh_runner)
    if snap.get("ok"):
        snap["refresh_status"] = "fresh"
        repos[repo] = snap
        atomic_write_json(reputation_path, {"schema_version": 2, "repos": repos})
        return snap
    old = repos.get(repo)
    if isinstance(old, dict):
        stale = dict(old)
        stale["stale"] = True
        stale["error"] = snap.get("error", "refresh-failed")
        stale["last_attempt_at"] = _iso_now()
        stale["refresh_status"] = "error"
        return stale
    return snap


def _safe_rel(rel: str) -> bool:
    if not rel or rel in (".", ".."):
        return False
    if rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    return ".." not in parts and all(p not in ("", ".") for p in parts)


def fetch_skill_tree(repo, source_dir, commit_sha, dest, gh_runner):
    """按固定 commit 抓取 repo 内 source_dir 的完整文件树到 dest(二进制安全)。

    协议不完整即失败:truncated 树、submodule、重复路径、链接父级冲突、缺根
    SKILL.md、无效 frontmatter 都明确拒绝,绝不降格成"完整候选"。
    支持 100644/100755/120000;symlink 只落地链接目标字符串,物化不跟随。
    成功返回 {ok, commit_sha, files, tree_hash, source_dir, tree_complete,
    source_tree_sha, materialization_version};失败没有 candidate_hash 可用。
    """
    from .fingerprint import FingerprintError
    from scripts.scan import parse_frontmatter
    dest = Path(dest)
    try:
        code, out = gh_runner(["repos/{}/git/trees/{}?recursive=1".format(repo, commit_sha)])
        if code != 0:
            return {"ok": False, "error": "tree-fetch-failed", "commit_sha": commit_sha}
        data = json.loads(out)
        tree = data.get("tree") or []
    except (json.JSONDecodeError, TypeError) as e:
        return {"ok": False, "error": "tree-parse-failed: " + type(e).__name__, "commit_sha": commit_sha}
    if data.get("truncated"):
        # 官方协议:truncated 表示响应缺子树;一期明确失败,不补齐不猜测
        return {"ok": False, "error": "tree-truncated", "commit_sha": commit_sha}

    sd = str(source_dir or "").strip("/")
    prefix = sd + "/" if sd else ""
    members = [t for t in tree if str(t.get("path", "")).startswith(prefix)]
    if not members:
        return {"ok": False, "error": "source-dir-empty-or-missing", "commit_sha": commit_sha}

    rows = []
    seen_paths = set()
    symlink_dirs = set()
    for m in members:
        full_path = str(m.get("path", ""))
        rel = full_path[len(prefix):]
        mtype = str(m.get("type") or "")
        if mtype == "commit":
            return {"ok": False, "error": "unsupported-submodule", "path": rel,
                    "commit_sha": commit_sha}
        if mtype != "blob":
            return {"ok": False, "error": "unsupported-tree-entry", "path": rel,
                    "commit_sha": commit_sha}
        if not _safe_rel(rel):
            return {"ok": False, "error": "unsafe-path", "path": rel, "commit_sha": commit_sha}
        if rel in seen_paths:
            return {"ok": False, "error": "duplicate-path", "path": rel, "commit_sha": commit_sha}
        seen_paths.add(rel)
        git_mode = str(m.get("mode") or "")
        parts = rel.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in symlink_dirs:
                return {"ok": False, "error": "link-parent-conflict", "path": rel,
                        "commit_sha": commit_sha}
        if git_mode == "120000":
            symlink_dirs.add(rel)
        rows.append((rel, m.get("sha"), git_mode))
    if "SKILL.md" not in seen_paths:
        return {"ok": False, "error": "source-dir-empty-or-missing", "commit_sha": commit_sha}

    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for rel, blob_sha, git_mode in rows:
        try:
            bc, bo = gh_runner(["repos/{}/git/blobs/{}".format(repo, blob_sha)])
            if bc != 0:
                return {"ok": False, "error": "blob-fetch-failed", "path": rel, "commit_sha": commit_sha}
            blob = json.loads(bo)
            if blob.get("encoding") != "base64":
                return {"ok": False, "error": "unsupported-blob-encoding", "path": rel, "commit_sha": commit_sha}
            content = base64.b64decode(blob.get("content") or "", validate=True)
            if blob.get("size") is not None and int(blob["size"]) != len(content):
                return {"ok": False, "error": "blob-size-mismatch", "path": rel,
                        "commit_sha": commit_sha}
        except (json.JSONDecodeError, TypeError, OSError, ValueError) as e:
            return {"ok": False, "error": "blob-error: " + type(e).__name__, "path": rel,
                    "commit_sha": commit_sha}
        local = dest / rel
        if git_mode == "120000":
            try:
                target = content.decode("utf-8")
            except UnicodeDecodeError:
                return {"ok": False, "error": "unsafe-link-target", "path": rel,
                        "commit_sha": commit_sha}
            if target.startswith("/") or target in ("", ".", ".."):
                # 相对目标(可含 ..)是合法 Git 形态:只落地链接字符串,物化/指纹都不跟随;
                # 绝对路径目标一律拒绝
                return {"ok": False, "error": "unsafe-link-target", "path": rel,
                        "commit_sha": commit_sha}
            if local.exists() or local.is_symlink():
                local.unlink()
            os.symlink(target, local)
        else:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            # 权限固定,不依赖进程 umask;100755 保留可执行位(指纹含权限,丢位产生幽灵更新)
            os.chmod(local, 0o755 if git_mode.endswith("755") else 0o644)
        count += 1
    try:
        result_hash = tree_hash(dest)
    except FingerprintError as e:
        return {"ok": False, "error": "materialize-incomplete", "detail": str(e)[:120],
                "commit_sha": commit_sha}
    with open(dest / "SKILL.md", encoding="utf-8", errors="ignore") as f:
        fm, ok = parse_frontmatter(f.read(8000))
    if not ok or not str(fm.get("name") or "").strip():
        return {"ok": False, "error": "invalid-frontmatter", "commit_sha": commit_sha}
    return {"ok": True, "commit_sha": commit_sha, "files": count, "tree_hash": result_hash,
            "source_dir": sd, "tree_complete": True, "source_tree_sha": str(data.get("sha") or ""),
            "materialization_version": 1}
