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
        repos[repo] = snap
        atomic_write_json(reputation_path, {"schema_version": 2, "repos": repos})
        return snap
    old = repos.get(repo)
    if isinstance(old, dict):
        stale = dict(old)
        stale["stale"] = True
        stale["error"] = snap.get("error", "refresh-failed")
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

    成功返回 {ok, commit_sha, files, tree_hash};任何失败返回 {ok: False, error},
    绝不把半成品目录当作候选。
    """
    dest = Path(dest)
    try:
        code, out = gh_runner(["repos/{}/git/trees/{}?recursive=1".format(repo, commit_sha)])
        if code != 0:
            return {"ok": False, "error": "tree-fetch-failed", "commit_sha": commit_sha}
        data = json.loads(out)
        tree = data.get("tree") or []
    except (json.JSONDecodeError, TypeError) as e:
        return {"ok": False, "error": "tree-parse-failed: " + type(e).__name__, "commit_sha": commit_sha}

    prefix = str(source_dir).strip("/") + "/"
    members = [t for t in tree if t.get("type") == "blob" and str(t.get("path", "")).startswith(prefix)]
    if not members:
        return {"ok": False, "error": "source-dir-empty-or-missing", "commit_sha": commit_sha}

    fetched = []
    for m in members:
        rel = str(m["path"])[len(prefix):]
        if not _safe_rel(rel):
            return {"ok": False, "error": "unsafe-path", "path": rel, "commit_sha": commit_sha}
        fetched.append((rel, m.get("sha"), str(m.get("mode") or "")))

    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for rel, blob_sha, git_mode in fetched:
        try:
            bc, bo = gh_runner(["repos/{}/git/blobs/{}".format(repo, blob_sha)])
            if bc != 0:
                return {"ok": False, "error": "blob-fetch-failed", "path": rel, "commit_sha": commit_sha}
            blob = json.loads(bo)
            if blob.get("encoding") != "base64":
                return {"ok": False, "error": "unsupported-blob-encoding", "path": rel, "commit_sha": commit_sha}
            content = base64.b64decode(blob.get("content") or "")
        except (json.JSONDecodeError, TypeError, OSError) as e:
            return {"ok": False, "error": "blob-error: " + type(e).__name__, "path": rel, "commit_sha": commit_sha}
        local = dest / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        # Git mode 100755 的文件落地后必须保留可执行位;完整树指纹含权限,丢位会产生幽灵更新
        if git_mode.endswith("755"):
            os.chmod(local, 0o755)
        count += 1
    return {"ok": True, "commit_sha": commit_sha, "files": count, "tree_hash": tree_hash(dest)}
