"""Bounded reproduction using the repository's existing dense-overlap corpus.

Examples: python3 benchmark.py --sizes 80 200 400 --timeout 90
Each size runs in a fresh process and temporary HOME fixture. No production state writes.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def worker(n, fixture_root=None):
    from tests.test_overlap_cost import OverlapCostTests
    from scripts.core.overlap import build_overlap_index
    from scripts.core.reviews import build_review_queue
    with tempfile.TemporaryDirectory(prefix="sk-audit-benchmark-", dir=fixture_root) as td:
        inv = OverlapCostTests()._inventory_n(td, n)
        start = time.perf_counter()
        index = build_overlap_index(inv)
        index_seconds = time.perf_counter() - start
        del index
        print(json.dumps({"n": n, "phase": "index_complete",
                          "index_seconds": round(index_seconds, 3)}), flush=True)
        start = time.perf_counter()
        queue = build_review_queue(inv)
        queue_seconds = time.perf_counter() - start
        rss = None
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss /= 1048576 if sys.platform == "darwin" else 1024
        except ImportError:
            pass
        print(json.dumps({"n": n, "phase": "queue_complete",
                          "queue_seconds": round(queue_seconds, 3),
                          "items": len(queue["items"]),
                          "rss_max_MiB": round(rss, 1) if rss is not None else None}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+", default=[80, 200, 400])
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--worker", type=int)
    ap.add_argument("--fixture-root")
    args = ap.parse_args()
    if args.worker is not None:
        worker(args.worker, args.fixture_root)
        return
    for n in args.sizes:
        # The parent owns the outer directory, so timeout-killed workers leave no fixture residue.
        fixture = tempfile.TemporaryDirectory(prefix="sk-audit-owned-")
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(n),
                   "--fixture-root", fixture.name]
        try:
            run = subprocess.run(command, capture_output=True, text=True,
                                 timeout=args.timeout)
            print(run.stdout, end="")
            if run.returncode:
                print(json.dumps({"n": n, "exit_code": run.returncode,
                                  "error": run.stderr[-800:]}))
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            print(stdout, end="")
            print(json.dumps({"n": n, "status": "timeout",
                              "process_seconds_at_least": args.timeout}))
        finally:
            fixture.cleanup()


if __name__ == "__main__":
    main()
