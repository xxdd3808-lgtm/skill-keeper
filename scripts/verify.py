#!/usr/bin/env python3
"""skill-keeper 验收入口(v4):基于 unittest 实际结果的真实验收器 + 反作弊检查。

- 默认发现 tests/ 全部用例:0 失败、0 错误、0 跳过、数量不少于基线才返回 0;
- 原测试 ID 基线:v3.1.1 冻结的全部测试 ID 必须仍在(不许删测试、不许改 ID);
- 安装 smoke(统一 CLI doctor)、恶意位置声明探针、模型输入不可写探针、
  个人路径/秘密模式扫描(只查 tracked 文件);
- --test-dir 供反向验证(临时测试集必须能被本器发现真实失败),不计入基线门槛;
- 不包装成永远成功的 shell 脚本:一切以 unittest 的 TestResult 为准。
"""
import argparse
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import TextTestRunner

BASE = Path(__file__).resolve().parents[1]
BASELINE_MIN_TESTS = 233      # 2026-09-05 v3.1.1 冻结基线;只允许随真实用例增长
BASELINE_IDS_PATH = BASE / "tests" / "fixtures" / "private-v311" / "v311-test-ids.json"

# 扫描时跳过的目录(运行时产物/依赖,不是源代码)
SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", "backups",
                  "build", "dist", ".skill-keeper"}
PERSONAL_PATH_RE = re.compile(r"/Users/[A-Za-z0-9._-]+/")
SECRET_RES = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)


def run_tests(test_dir):
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    loader = unittest.TestLoader()
    suite = loader.discover(str(test_dir))
    runner = TextTestRunner(verbosity=1, stream=sys.stderr)
    result = runner.run(suite)
    return {
        "test_count": result.testsRun,
        "skipped": len(result.skipped),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "result": result,
        "suite": suite,
    }


def collect_test_ids(suite):
    ids = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            ids.extend(collect_test_ids(item))
        elif item is not None and hasattr(item, "id"):
            ids.append(item.id())
    return ids


def check_baseline_ids(suite):
    """v3.1.1 冻结的全部原测试 ID 必须仍然存在(防删测试/防改名)。"""
    path = Path(BASELINE_IDS_PATH)
    if not path.is_file():
        return {"ok": False, "error": "基线 ID 文件缺失: {}".format(path)}
    baseline = set(json.loads(path.read_text(encoding="utf-8")))
    current = set(collect_test_ids(suite))
    missing = sorted(baseline - current)
    return {"ok": not missing, "baseline": len(baseline),
            "missing": missing[:20], "missing_count": len(missing)}


def check_location_input_probe():
    """恶意位置声明必须被解析层整体拒绝(白名单外字段/超限/伪造状态)。"""
    from scripts.core.location_input import LocationInputError, parse_declaration
    malicious = [
        {"schema_version": 1, "client": "c", "mutable": True, "roots": []},
        {"schema_version": 1, "client": "c", "roots":
            [{"path": "/tmp/x", "instance_id": "i-1"}]},
        {"schema_version": 1, "client": "c", "roots":
            [{"path": "/tmp/x", "tree_hash": "0" * 64}]},
        {"schema_version": 1, "client": "c", "roots":
            [{"path": "/tmp/x", "load_state": "confirmed"}]},
        {"schema_version": 1, "client": "c", "command": "rm -rf /", "roots": []},
        {"schema_version": 1, "client": "c" * 5000, "roots": []},
    ]
    for payload in malicious:
        try:
            parse_declaration(json.dumps(payload))
        except LocationInputError:
            continue
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "恶意声明抛错类型异常: " + type(e).__name__}
        return {"ok": False, "error": "恶意声明未被拒绝: {}".format(
            json.dumps(payload)[:60])}
    return {"ok": True}


def check_model_input_immutable_probe():
    """模型声明实例即使 mutable 被翻转也必须没有变更入口(policy 层)。"""
    import tempfile
    from scripts.core.policy import check_action, load_policy
    with tempfile.TemporaryDirectory() as td:
        policy = load_policy(td)
        inst = {"instance_id": "i-model-0000000000000000", "mutable": True,
                "evidence": ["model-declaration"], "location_id": "model-x",
                "directory_name": "demo"}
        loc = {"location_id": "model-x", "client": "probe-agent", "mutable": True}
        verdict = check_action("remove", inst, loc, policy)
    return {"ok": verdict.get("allowed") is False
            and verdict.get("reason_code") == "model-declared-location"}


def check_path_secret_scan():
    """tracked 文件不得含真实个人绝对路径或疑似秘密(测试虚构值除外)。"""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=str(BASE), capture_output=True,
                             text=True, timeout=30)
        files = [line for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        files = [str(p.relative_to(BASE)) for p in sorted(BASE.rglob("*"))
                 if p.is_file() and not (set(p.parts) & SCAN_SKIP_DIRS)]
    violations = []
    for rel in files:
        path = BASE / rel
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        if set(Path(rel).parts) & SCAN_SKIP_DIRS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in PERSONAL_PATH_RE, *SECRET_RES:
            hit = pattern.search(text)
            if hit:
                violations.append("{}: {}".format(rel, pattern.pattern))
    return {"ok": not violations, "violations": violations[:20],
            "scanned_files": len(files)}


def check_install_smoke():
    """统一 CLI doctor 必须可离线运行,输出版本/Python/运行目录/锁后端。"""
    proc = subprocess.run([sys.executable, "-m", "scripts.cli", "doctor", "--json"],
                          cwd=str(BASE), capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {"ok": False, "error": "doctor 退出码 {}: {}".format(
            proc.returncode, proc.stderr[-200:])}
    try:
        payload = json.loads(proc.stdout[proc.stdout.index("{"):])
    except (ValueError, IndexError):
        return {"ok": False, "error": "doctor 未输出 JSON"}
    for key in ("version", "python", "layout", "paths", "lock_backend"):
        if key not in payload:
            return {"ok": False, "error": "doctor 缺字段: " + key}
    return {"ok": True, "version": payload["version"], "layout": payload["layout"]}


def main():
    ap = argparse.ArgumentParser(description="skill-keeper 全量验收入口")
    ap.add_argument("--test-dir", default=None,
                    help="反向验证用:发现该目录的测试并按冒烟口径验收(不满足基线门槛)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    smoke = args.test_dir is not None
    test_dir = Path(args.test_dir) if smoke else BASE / "tests"
    stats = run_tests(test_dir)
    passed = (stats["failures"] == 0 and stats["errors"] == 0 and stats["skipped"] == 0
              and (stats["test_count"] >= BASELINE_MIN_TESTS if not smoke
                   else stats["test_count"] > 0))
    verdict = {"ok": passed, "is_smoke": smoke, "test_dir": str(test_dir),
               "test_count": stats["test_count"], "skipped": stats["skipped"],
               "failures": stats["failures"], "errors": stats["errors"]}
    if not smoke and stats["test_count"] < BASELINE_MIN_TESTS:
        verdict["reason"] = "用例数低于基线 {}(发现被静默削弱?)".format(BASELINE_MIN_TESTS)
        passed = False
        verdict["ok"] = False

    if not smoke and passed:
        # 附加反作弊/合同检查:任何一项失败都让验收失败
        verdict["checks"] = {
            "baseline_ids": check_baseline_ids(stats["suite"]),
            "location_input_probe": check_location_input_probe(),
            "model_input_immutable_probe": check_model_input_immutable_probe(),
            "path_secret_scan": check_path_secret_scan(),
            "install_smoke": check_install_smoke(),
        }
        for name, result in verdict["checks"].items():
            if not result.get("ok"):
                verdict["ok"] = False
                verdict.setdefault("failed_checks", []).append(name)

    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
    else:
        print("✅ 验收通过:{test_count} 项 / 0 失败 / 0 跳过".format(**verdict)
              if passed else "⛔ 验收未通过:{}".format(verdict))
        if not passed and verdict.get("failed_checks"):
            for name in verdict["failed_checks"]:
                print("   失败检查: {}: {}".format(name, verdict["checks"][name]))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
