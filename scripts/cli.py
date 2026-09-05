#!/usr/bin/env python3
"""skill-keeper 统一 CLI(v4):所有平台与 Agent 用同一组命令。

  skill-keeper scan [--json]      多客户端适配器发现 + 完整树指纹 → inventory(只读)
  skill-keeper report [--json]    价值审查报告(data/report.md + report.html)
  skill-keeper manage ...         plan/apply/status/recover/rescan(与网页共用 service 层)
  skill-keeper doctor [--json]    版本、Python、运行目录、锁后端、已登记位置

旧入口 `python3 scripts/*.py` 继续有效,与新 CLI 调用同一 service/引擎层;
运行态解析优先级:显式参数 > 环境变量 > 可识别旧仓库运行态 > 新默认 ~/.skill-keeper。
"""
import json
import platform
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from scripts import __version__                    # noqa: E402
from scripts.core.io import load_json_checked      # noqa: E402
from scripts.core.runtime import RuntimePaths      # noqa: E402


def _lock_backend():
    try:
        import fcntl  # noqa: F401
        return "fcntl"
    except ImportError:
        pass
    try:
        import msvcrt  # noqa: F401
        return "msvcrt"
    except ImportError:
        return "none"


def _registered_locations(data_dir):
    """data/client-locations.json 的登记概览(只读 id 白名单字段,不读路径内容)。"""
    value, _ = load_json_checked(Path(data_dir) / "client-locations.json", {})
    rows = value.get("locations") if isinstance(value, dict) else None
    ids = [str(x.get("location_id")) for x in rows
           if isinstance(x, dict) and x.get("location_id")] if isinstance(rows, list) else []
    return {"count": len(ids), "ids": ids}


def cmd_doctor(as_json=False):
    paths = RuntimePaths()
    payload = {
        "ok": True,
        "version": __version__,
        "python": platform.python_version(),
        "layout": paths.layout,
        "paths": paths.to_dict(),
        "lock_backend": _lock_backend(),
        "registered_locations": _registered_locations(paths.data_dir),
        "repo_root": str(BASE),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print("skill-keeper {} · Python {} · 运行态: {}".format(
        __version__, payload["python"], payload["layout"]))
    for key in ("home", "data_dir", "staging_dir", "backup_dir"):
        print("  {}: {}".format(key, payload["paths"][key]))
    print("  lock_backend: {}".format(payload["lock_backend"]))
    reg = payload["registered_locations"]
    print("  已登记位置: {} 个{}".format(
        reg["count"], ("(" + ", ".join(reg["ids"]) + ")") if reg["ids"] else ""))
    return 0


COMMANDS = ("scan", "report", "manage", "doctor")
_USAGE = """skill-keeper <command> [args...]

命令(参数与对应 scripts/*.py 入口完全一致,原样透传):
  scan     扫描盘点(只读)
  report   价值审查报告
  manage   plan/apply/status/recover/rescan
  doctor   运行环境自检(--json 输出版本/Python/运行目录/锁后端/已登记位置)
"""


def main(argv=None):
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    # 手动分发:子命令后的参数原样透传,不经 argparse 二次解析(REMAINDER 有吞参缺陷)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip() or _USAGE)
        print(_USAGE)
        return 0 if argv else 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "scan":
        from scripts import scan
        return scan.main(rest)
    if cmd == "report":
        from scripts import report
        return report.main(rest)
    if cmd == "manage":
        from scripts import manage
        return manage.main(rest)
    if cmd == "doctor":
        return cmd_doctor(as_json=("--json" in rest))
    print("未知命令: {}\n{}".format(cmd, _USAGE), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
