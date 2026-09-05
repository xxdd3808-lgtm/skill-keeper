"""Task 3 中断恢复(F03):子进程在关键 rename 后 os._exit(77),父进程用
read_transaction / recover_transaction 恢复已授权的原状态。

- 删除:首个目标移走后崩溃 → 恢复回原位,原计划不得重放;
- 更新:旧目录移走后崩溃 → 旧版本回原位,候选不得被激活;
- 恢复:首个实体发布后崩溃 → 已发布实体撤销,回到"目标不存在"的原状态。
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.core.changes import (ChangeContext, ChangeError, apply_plan,
                                  read_transaction, recover_transaction)
from scripts.core.fingerprint import tree_hash

REPO_ROOT = Path(__file__).resolve().parents[1]

PRELUDE = '''
import os, sys, json
sys.path.insert(0, {root!r})
from tests.test_change_remove import change_env
from tests.test_change_update import update_env


class _T:
    def addCleanup(self, *a, **k):
        pass


def emit(payload):
    print(json.dumps(payload), flush=True)
'''.format(root=str(REPO_ROOT))


def run_child(code):
    return subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                          capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))


def child_context(payload):
    data = Path(payload["data_dir"])
    home = Path(payload["home"])
    return ChangeContext(data_dir=data, plans_dir=data / "change-plans",
                         backup_dir=home / "backups",
                         audit_path=data / "audit-v2.jsonl",
                         lock_path=data / ".change.lock", load_inventory=None)


class RemoveInterruptRecoveryTests(unittest.TestCase):
    def test_remove_crash_after_first_move_recovers_original(self):
        code = PRELUDE + '''
env = change_env(_T())
from scripts.core import changes
from scripts.core.fingerprint import tree_hash
plan = env.remove_plan()
emit({"plan_id": plan.plan_id, "digest": plan.digest,
      "home": str(env.home), "data_dir": str(env.data),
      "skill_path": str(env.skill_path), "hash": tree_hash(env.skill_path)})
real_rename = os.rename


def spy(src, dst):
    real_rename(src, dst)
    os._exit(77)  # 关键窗口:rename 已发生,事务记录未写


os.rename = spy
changes.apply_plan(plan.plan_id, plan.digest, True, env.context)
os._exit(0)  # 没走到注入点就算失败
'''
        r = run_child(code)
        self.assertEqual(r.returncode, 77, "子进程必须停在注入窗口: " + r.stderr[-400:])
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        ctx = child_context(payload)
        skill_path = Path(payload["skill_path"])
        self.assertFalse(os.path.lexists(skill_path), "子进程已把目标移走")

        state = read_transaction(payload["plan_id"], ctx)
        self.assertEqual(state["phase"], "mutating", "崩溃时事务必须处于 mutating")
        result = recover_transaction(payload["plan_id"], ctx)
        self.assertEqual(result["phase"], "rolled-back")
        self.assertTrue(skill_path.is_dir(), "恢复必须把目标移回原位")
        self.assertEqual(tree_hash(skill_path), payload["hash"])
        self.assertEqual([p.name for p in skill_path.parent.iterdir()
                          if p.name.startswith(".sk-txn-")], [], "保管目录必须清理")
        from scripts.core.audit import read_audit
        self.assertEqual(read_audit(ctx.audit_path)[-1]["status"], "recovered")
        # 原计划不得重放
        with self.assertRaises(ChangeError):
            apply_plan(payload["plan_id"], payload["digest"], True, ctx)


class UpdateInterruptRecoveryTests(unittest.TestCase):
    def test_update_crash_after_old_moved_restores_old_version(self):
        code = PRELUDE + '''
env = update_env(_T())
from scripts.core import changes
from scripts.core.changes import create_update_plan, record_candidate_vet
plan = create_update_plan(env.iid, {
    "instance_id": env.iid, "staging_path": str(env.staging),
    "candidate_hash": env.v2_hash, "source": "github", "repo": "example/demo",
    "source_dir": "skills/demo", "commit_sha": env.remote_head,
}, env.inventory, env.plans_dir)
record_candidate_vet(plan.plan_id, env.v2_hash, "safe", ["fixture"],
                     plans_dir=env.plans_dir)
emit({"plan_id": plan.plan_id, "digest": plan.digest,
      "home": str(env.home), "data_dir": str(env.data),
      "skill_path": str(env.skill_path), "hash": env.local_hash})
real_rename = os.rename


def spy(src, dst):
    real_rename(src, dst)
    os._exit(77)  # 旧目录已移入保管,新候选尚未就位


os.rename = spy
changes.apply_plan(plan.plan_id, plan.digest, True, env.context)
os._exit(0)
'''
        r = run_child(code)
        self.assertEqual(r.returncode, 77, "子进程必须停在注入窗口: " + r.stderr[-400:])
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        ctx = child_context(payload)
        skill_path = Path(payload["skill_path"])

        state = read_transaction(payload["plan_id"], ctx)
        self.assertEqual(state["phase"], "mutating")
        result = recover_transaction(payload["plan_id"], ctx)
        self.assertEqual(result["phase"], "rolled-back")
        self.assertEqual(tree_hash(skill_path), payload["hash"], "旧版本回到原位")
        self.assertEqual([p.name for p in skill_path.parent.iterdir()
                          if ".sk-txn-" in p.name], [], "候选保管目录不得残留(不得激活候选)")


class RestoreInterruptRecoveryTests(unittest.TestCase):
    def test_restore_crash_after_first_publish_undoes_published(self):
        code = PRELUDE + '''
from tests.test_backup_restore import two_location_skill_fixture
from scripts.core.backup import create_backup
from scripts.core.changes import ChangeContext, create_restore_plan
env = two_location_skill_fixture(_T())
backup = create_backup(env.plan, env.inventory, env.backup_dir)
data = env.backup_dir.parent / "data"
plan = create_restore_plan(backup["backup_id"], env.backup_dir,
                           data / "change-plans")
env.remove_targets()
emit({"plan_id": plan.plan_id, "digest": plan.digest,
      "home": str(env.backup_dir.parent), "data_dir": str(data),
      "demo": str(env.demo), "link": str(env.claude_root / "demo")})
import os
from scripts.core import changes
ctx = ChangeContext(data_dir=data, plans_dir=data / "change-plans",
                    backup_dir=env.backup_dir, audit_path=data / "audit-v2.jsonl",
                    lock_path=data / ".change.lock",
                    load_inventory=lambda: env.inventory)
real_replace = os.replace
DEMO = str(env.demo)


def spy(src, dst):
    real_replace(src, dst)
    if str(dst) == DEMO:  # 只拦截首个实体的发布,不碰状态文件的原子写
        os._exit(77)


os.replace = spy
changes.apply_plan(plan.plan_id, plan.digest, True, ctx)
os._exit(0)
'''
        r = run_child(code)
        self.assertEqual(r.returncode, 77, "子进程必须停在注入窗口: " + r.stderr[-500:])
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        ctx = child_context(payload)

        state = read_transaction(payload["plan_id"], ctx)
        self.assertEqual(state["phase"], "mutating")
        self.assertTrue(state["targets"], "恢复事务必须记录目标实体")
        result = recover_transaction(payload["plan_id"], ctx)
        self.assertEqual(result["phase"], "rolled-back")
        self.assertFalse(os.path.lexists(payload["demo"]),
                         "已发布的实体必须撤销(原状态是目标不存在)")
        self.assertFalse(os.path.lexists(payload["link"]))


class RecoveryContractTests(unittest.TestCase):
    def test_recover_without_transaction_is_clean_error(self):
        from tests.test_change_remove import change_env
        env = change_env(self)
        with self.assertRaises(ChangeError):
            recover_transaction("plan-does-not-exist", env.context)

    def test_corrupt_transaction_state_blocks_apply(self):
        from tests.test_change_remove import change_env
        env = change_env(self)
        plan = env.remove_plan()
        txn_dir = env.data / "transactions"
        txn_dir.mkdir(parents=True, exist_ok=True)
        (txn_dir / (plan.plan_id + ".json")).write_text("{broken", encoding="utf-8")
        with self.assertRaises(ChangeError):
            apply_plan(plan.plan_id, plan.digest, True, env.context)
        self.assertTrue(env.skill_path.exists(), "状态损坏必须拒绝执行")


if __name__ == "__main__":
    unittest.main()
