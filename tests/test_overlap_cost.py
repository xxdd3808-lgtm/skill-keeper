"""Task 8(F09):队列构建一次语料读取、一次评分——读取 O(N)、打分 O(N²),结果等价。

等价标准是改算法前冻结的 tests/fixtures/overlap-baseline.json(名字+分数+理由,
无机器路径/时间);壁钟时间只报告,不作门槛。
"""
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core.overlap import (alternative_candidates, candidate_pairs,
                                  exact_duplicate_groups)
from scripts.core.reviews import build_review_queue
from tests.helpers import write_skill

BODIES = {
    "alpha-tool": "Manage alpha workflows with steps and reports. Runs local scripts.",
    "alpha-helper": "Helper for alpha workflows: wraps scripts and builds reports.",
    "beta-notes": "Local notes and quick capture with markdown files and sync.",
    "beta-memo": "Memo pad for notes capture, markdown files, offline first storage.",
    "gamma-fmt": "Format tables and sheets: align columns, tidy rows, export CSV.",
    "clip-tool": "Manage clipboard history: capture, search, paste board items.",
    "delta-scan": "Scan folders for duplicates, list big files, cleanup hints.",
    "protected-core": "Core runtime component provided by the client application bundle.",
}
TAGS = {
    "alpha-tool": "alpha", "alpha-helper": "alpha", "beta-notes": "beta",
    "beta-memo": "beta", "gamma-fmt": "gamma", "clip-tool": "clip",
    "delta-scan": "delta", "protected-core": "core",
}


def _corpus_inventory(td):
    home = Path(td)
    (home / "data").mkdir(exist_ok=True)
    for name, body in BODIES.items():
        d = write_skill(home / ".agents/skills", name, body=body + " " + TAGS[name])
        (d / "extra.md").write_text("# {}\n\n{}\n".format(name, body * 2), encoding="utf-8")
    from scripts.scan import build_inventory
    return build_inventory(home, home / "data")


def _gold_view(inv):
    pairs = candidate_pairs(inv, min_similarity=0.32)
    view = {"pairs": {}, "alternatives": {}, "duplicate_groups": len(exact_duplicate_groups(inv))}
    for p in pairs:
        view["pairs"]["|".join(sorted([p["a_name"], p["b_name"]]))] = p["score"]
    for lg in inv["logical_skills"]:
        cands = alternative_candidates(inv, lg["logical_id"], min_similarity=0.32,
                                       max_candidates=8)
        view["alternatives"][lg["name"]] = [
            {"name": c["name"], "score": c["score"], "reasons": c["reasons"]}
            for c in cands]
    return view


class OverlapEquivalenceTests(unittest.TestCase):
    def test_results_match_frozen_baseline(self):
        gold = json.loads(
            (Path(__file__).resolve().parents[1] / "tests/fixtures/overlap-baseline.json")
            .read_text(encoding="utf-8"))
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            inv = _corpus_inventory(td)
            view = _gold_view(inv)
        self.assertEqual(view["pairs"], gold["pairs"], "相似对分数必须与冻结基线等价")
        self.assertEqual(view["alternatives"], gold["alternatives"], "替代候选必须等价")
        self.assertEqual(view["duplicate_groups"], gold["duplicate_groups"])


class OverlapCostTests(unittest.TestCase):
    def _inventory_n(self, td, n):
        home = Path(td)
        (home / "data").mkdir(exist_ok=True)
        for i in range(n):
            name = "skill-{:03d}".format(i)
            body = ("Skill {} handles workflow {} with unique-{} tokens and "
                    "shared vocabulary about reports scripts capture storage.").format(i, i, i)
            d = write_skill(home / ".agents/skills", name, body=body)
            (d / "run.py").write_text("# {}".format(name), encoding="utf-8")
        from scripts.scan import build_inventory
        return build_inventory(home, home / "data")

    def test_eighty_logical_skills_single_read_single_scoring(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            inv = self._inventory_n(td, 80)
            from scripts.core import overlap
            with patch.object(overlap, "read_head", wraps=overlap.read_head) as reads:
                with patch.object(overlap, "pair_breakdown",
                                  wraps=overlap.pair_breakdown) as pairs:
                    t0 = time.time()
                    queue = build_review_queue(inv)
                    elapsed = time.time() - t0
            self.assertLessEqual(reads.call_count, 80,
                                 "正文读取必须 O(N):实测 {}".format(reads.call_count))
            self.assertLessEqual(pairs.call_count, 80 * 79 // 2,
                                 "每对只评一次:实测 {}".format(pairs.call_count))
            self.assertEqual(len(queue["items"]), 80)
            print("\n[F09] 80 logical skills: reads={} pair_scores={} wall={:.3f}s".format(
                reads.call_count, pairs.call_count, elapsed))


if __name__ == "__main__":
    unittest.main()
