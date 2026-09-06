"""Read-only audit of production code; mutations use TemporaryDirectory fixtures."""
import sys, json, os, tempfile, subprocess, base64, unittest, time
from pathlib import Path
from unittest.mock import patch
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scripts import scan, report, serve
from scripts.core import changes, fingerprint, transactions, runtime
from scripts.core.github import fetch_skill_tree
from scripts.core.reviews import build_review_queue, record_review
from scripts.core.review_state import evaluate_review
from scripts.core.io import atomic_write_json
from tests.helpers import write_skill, make_plugin_cache
from tests.test_change_remove import change_env
from tests.test_change_update import update_env
from scripts.core.service import AppService
results={}
def capture(name, f):
 try: results[name]=f()
 except Exception as e: results[name]={'probe_error':type(e).__name__, 'message':str(e)}

def github():
 body=b'---\nname: demo\ndescription: demo\n---\nbody\n'
 def run_case(nested,wrapped):
  tree=[{'path':'s/SKILL.md','type':'blob','mode':'100644','sha':'a'}]
  if nested: tree.extend([{'path':'s/scripts','type':'tree','mode':'040000','sha':'b'},{'path':'s/scripts/run.py','type':'blob','mode':'100644','sha':'c'}])
  def runner(args):
   if '/git/trees/' in args[0]: return 0,json.dumps({'tree':tree,'truncated':False})
   enc=(base64.encodebytes if wrapped else base64.b64encode)(body).decode()
   return 0,json.dumps({'encoding':'base64','content':enc,'size':len(body)})
  with tempfile.TemporaryDirectory() as td: return fetch_skill_tree('example/demo','s','fixed',Path(td)/'candidate',runner)
 return {'normal_tree':run_case(True,False),'wrapped_base64':run_case(False,True),'flat_control_ok':run_case(False,False)['ok']}
capture('github_contract',github)

def review():
 with tempfile.TemporaryDirectory() as td:
  h=Path(td); d=h/'data'; d.mkdir(); p=write_skill(h/'.agents/skills','demo'); alt=write_skill(h/'.agents/skills','alt')
  inv=scan.build_inventory(h,d); q=build_review_queue(inv); item=next(x for x in q['items'] if x['name']=='demo'); a=next(x for x in q['items'] if x['name']=='alt')
  rec=record_review(q,{'instance_id':item['instance_id'],'verdict':'建议删除','reason':'local replacement','alternatives':[a['logical_id']],'loss_if_removed':'none','confidence':'高','evidence':['coverage: local','inspection: complete'],'safety':'safe'},'audit')
  import shutil; shutil.rmtree(alt)
  now=scan.build_inventory(h,d); view=report.build_view(now,None,{'value_reviews':[rec]}); rows=[x for group in view['verdict_rows'].values() for x in group]
  gone={'evaluator':evaluate_review(rec,now,{}, {})['status'],'report_stale':rows[0]['stale'],'report_verdict':rows[0]['rec']['verdict'],'green_badge':'安检 safe' in report.review_card_html(view,rows[0],'remove')}
  (p/'new.py').write_text('changed'); changed=scan.build_inventory(h,d); v=report.build_view(changed,None,{'value_reviews':[rec]})
  return {'alternative_removed':gone,'target_changed':{'evaluator':evaluate_review(rec,changed,{}, {})['status'],'report_unreviewed':len(v['unreviewed']),'report_historical_verdict_rows':sum(len(x) for x in v['verdict_rows'].values())}}
capture('review_lifecycle',review)

def loads():
 with tempfile.TemporaryDirectory() as td:
  h=Path(td); d=h/'data';d.mkdir()
  r1=write_skill(h/'projects/a/.claude/skills','demo').parent
  r2=write_skill(h/'projects/b/.claude/skills','demo').parent
  (d/'workspace-locations.txt').write_text(str(r1)+'\n'+str(r2)+'\n')
  inv=scan.build_inventory(h,d)
  ws={'legacy_client_entries':inv['client_load']['claude-code']['entries'],'context_eligible':inv['observation']['load_contexts']['claude-code']['eligible'],'duplicate_findings':sum(x['code']=='duplicate-load' for x in inv['findings'])}
  make_plugin_cache(h/'.zcode/cli/plugins/cache','p','1.0','one',nested=True)
  make_plugin_cache(h/'.zcode/cli/plugins/cache','p','2.0','one',nested=True)
  inv=scan.build_inventory(h,d)
  return {'two_distinct_workspaces':ws,'old_plugin_version':{'client_entries':inv['client_load']['zcode']['entries'],'context_eligible':inv['observation']['load_contexts']['zcode']['eligible']}}
capture('load_consistency',loads)

def hashes():
 with tempfile.TemporaryDirectory() as td:
  h=Path(td);d=h/'data';d.mkdir(); p=write_skill(h/'.agents/skills','demo')
  link=h/'.claude/skills/demo';link.parent.mkdir(parents=True);link.symlink_to(p)
  paths=[];real=fingerprint._file_sha256
  def spy(path): paths.append(os.path.realpath(path));return real(path)
  with patch.object(fingerprint,'_file_sha256',side_effect=spy): inv=scan.build_inventory(h,d)
  before=fingerprint.tree_hash(p);os.chmod(p,0o700);after=fingerprint.tree_hash(p)
  write_skill(h/'.agents/skills','other')
  with patch.object(scan,'tree_hash',side_effect=OSError('fixture unreadable')): failed=scan.build_inventory(h,d)
  return {'physical_skill_files':len(set(paths)),'file_hash_calls':len(paths),'root_chmod_changes_hash':before!=after,'unreadable_skills':sum(x['is_skill'] for x in failed['instances']),'unreadable_logical_count':len(failed['logical_skills'])}
capture('fingerprint_and_identity',hashes)

def paths_probe():
 with tempfile.TemporaryDirectory() as td:
  h=Path(td);d=h/'.skill-keeper/data'
  with patch.dict(os.environ,{'SKILL_KEEPER_HOME':str(h)},clear=True), patch.object(runtime,'BASE',h/'install'):
   p=runtime.RuntimePaths();ctx=serve.ServiceContext(p.data_dir,home=h)
   from scripts.check_updates import staging_root_for
   return {'cli_backup_relative':str(p.backup_dir.relative_to(h)), 'web_backup_relative':str(ctx.backup_dir.relative_to(h)), 'declared_staging_relative':str(p.staging_dir.relative_to(h)), 'actual_staging_relative':str(staging_root_for().relative_to(h))}
capture('runtime_paths',paths_probe)

def timeout_after_commit():
 t=unittest.TestCase();env=change_env(t)
 try:
  atomic_write_json(env.data/'inventory.json',env.inventory)
  service=AppService(runtime.RuntimePaths(home=env.home,data_dir=env.data,backup_dir=env.context.backup_dir))
  plan=env.remove_plan()
  with patch('scripts.core.runtime.subprocess.run',side_effect=subprocess.TimeoutExpired('scan',300)):
   try: out=service.apply_action(plan.plan_id,plan.digest,True); response=out
   except Exception as e: response=type(e).__name__
  return {'service_response':response,'target_removed':not env.skill_path.exists(),'transaction_phase':changes.read_transaction(plan.plan_id,env.context)['phase']}
 finally:t.doCleanups()
capture('post_commit_refresh_timeout',timeout_after_commit)

def active_transaction():
 t=unittest.TestCase();env=change_env(t)
 try:
  first=env.remove_plan()
  # Persist an outstanding recovery state as after interrupted mutation.
  state=transactions.new_state(first.to_dict(),'remove',[]);state['phase']='recovery-required'; transactions.write_transaction(env.context,state)
  second=env.remove_plan();result=changes.apply_plan(second.plan_id,second.digest,True,env.context)
  return {'prior_phase':changes.read_transaction(first.plan_id,env.context)['phase'],'new_plan_allowed':result['ok'],'target_removed':not env.skill_path.exists()}
 finally:t.doCleanups()
capture('outstanding_transaction_gate',active_transaction)

def update_old_inventory():
 t=unittest.TestCase();env=update_env(t)
 try:
  (env.skill_path/'added-after-scan.txt').write_text('current user content')
  current=fingerprint.tree_hash(env.skill_path);plan=env.create_plan();changes.record_candidate_vet(plan.plan_id,env.v2_hash,'safe',['audit'],plans_dir=env.plans_dir)
  env.context.verify_after_apply=lambda:False
  try: changes.apply_plan(plan.plan_id,plan.digest,True,env.context);err='none'
  except changes.ChangeError as e:err=str(e)
  state=changes.read_transaction(plan.plan_id,env.context)
  return {'plan_binds_current_hash':dict(plan.preconditions)['tree_hash:'+env.iid]==current,'restored_actual_content':fingerprint.tree_hash(env.skill_path)==current,'rollback_phase':state['phase'],'error':err}
 finally:t.doCleanups()
capture('update_stale_inventory_rollback',update_old_inventory)
print(json.dumps(results,ensure_ascii=False,indent=2))
