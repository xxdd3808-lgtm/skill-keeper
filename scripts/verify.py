#!/usr/bin/env python3
"""skill-keeper 验收入口(Task 9,F11):基于 unittest 实际结果的真实验收器。

- 默认发现 tests/ 全部用例:0 失败、0 错误、0 跳过、数量不少于基线才返回 0;
- --test-dir 供反向验证(临时测试集必须能被本器发现真实失败),不计入基线门槛;
- 不包装成永远成功的 shell 脚本:一切以 unittest 的 TestResult 为准。
"""
import argparse
import json
import sys
import unittest
from unittest import TextTestRunner
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
BASELINE_MIN_TESTS = 126  # 2026-09-05 审视基线;只允许随真实用例增长,不允许调低


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
    }


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
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, indent=1))
    else:
        print("✅ 验收通过:{test_count} 项 / 0 失败 / 0 跳过".format(**verdict)
              if passed else "⛔ 验收未通过:{}".format(verdict))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
