import json,sys,os,tempfile,unittest,subprocess,base64
from pathlib import Path
from unittest.mock import patch
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scripts import scan,report,serve
from scripts.core import changes,transactions,runtime
from scripts.core.io import FileLock,atomic_write_json
from scripts.core.policy import load_policy,check_action
from scripts.core.github import cached_repo_snapshot
from scripts.check_updates import stage_candidate
from tests.helpers import write_skill
from tests.test_change_remove import change_env
from tests.test_change_update import update_env
out={}
t=unittest.TestCase();e=change_env(t)
try:
 p=e.remove_plan();hold=transactions.holding_path(e.skill_path.parent,p.plan_id,'demo');os.rename(e.skill_path,hold)
 s=transactions.new_state(p.to_dict(),'remove',[{'instance_id':e.iid,'path':str(e.skill_path),'kind':'dir','original_hash':e.inventory['instances'][0]['tree_hash'],'holding_path':hold,'moved':True,'published':False}]);s['phase']='mutating';transactions.write_transaction(e.context,s)
 with FileLock(e.context.lock_path):r=changes.recover_transaction(p.plan_id,e.context)
 out['recover_without_lock']={'recovery_while_exclusive_lock_held':r['phase'],'target_restored':e.skill_path.exists()}
finally:t.doCleanups()
t=unittest.TestCase();e=update_env(t)
try:
 atomic_write_json(e.data/'inventory.json',e.inventory)
 atomic_write_json(e.data/'updates.json',{'differs':[{'instance_id':e.iid,'staging_path':str(e.staging),'candidate_hash':e.v2_hash,'repo':'example/demo','commit_sha':'fixed'}]})
 ctx=serve.ServiceContext(e.data,home=e.home,backup_dir=e.context.backup_dir)
 first=serve._handle_plan(ctx,{'action':'update','instance_id':e.iid})
 changes.record_candidate_vet(first['plan_id'],e.v2_hash,'safe',['audit'],plans_dir=e.plans_dir)
 new=serve._handle_plan(ctx,{'action':'update','instance_id':e.iid})
 try:serve._handle_apply(ctx,{'plan_id':new['plan_id'],'digest':new['digest'],'confirm':True});result='success'
 except changes.ChangeError as err:result=str(err)
 out['web_update_vet']={'fresh_click_creates_another_plan':first['plan_id']!=new['plan_id'],'prior_candidate_vetted':True,'result':result}
finally:t.doCleanups()
with tempfile.TemporaryDirectory() as td:
 h=Path(td);d=h/'data';d.mkdir();p=write_skill(h/'.agents/skills','demo')
 (d/'client-locations.json').write_text('{broken')
 inv=scan.build_inventory(h,d)
 out['corrupt_locations']={'config_issues':inv['config_issues'],'observation_complete':inv['observation']['complete'],'health_status':inv['health_status']}
 (d/'client-locations.json').unlink()
 (d/'known-sources.json').write_text(json.dumps({'demo':'self-built'}))
 policy=load_policy(d);out['malformed_protection']={'policy_healthy':policy['healthy'],'remove_allowed':check_action('remove',inv['instances'][0],inv['locations'][0],policy)['allowed']}
 (d/'known-sources.json').unlink()
 inv['observation']['complete']=False;inv['observation']['issues']=[{'code':'instance-unreadable'}]
 atomic_write_json(d/'inventory.json',inv)
 env=dict(os.environ,SKILL_KEEPER_HOME=str(h),SKILL_KEEPER_DATA=str(d),SKILL_KEEPER_STAGING=str(h/'cache'))
 r=subprocess.run([sys.executable,str(REPO/'scripts/report.py'),'--json'],env=env,capture_output=True,text=True)
 out['partial_inventory_report']={'exit_code':r.returncode,'operational_ok':json.loads(r.stdout)['operational_ok']}
 old={'differs':[{'instance_id':'old','candidate_hash':'a'}]};atomic_write_json(d/'updates.json',old);(d/'inventory.json').unlink()
 r=subprocess.run([sys.executable,str(REPO/'scripts/check_updates.py'),'--json'],env=env,capture_output=True,text=True)
 out['missing_inventory_update_check']={'exit_code':r.returncode,'prior_result_preserved':json.loads((d/'updates.json').read_text())==old}
calls=[];body=b'---\nname: demo\ndescription: Demo\n---\nbody\n'
def runner(args):
 p=args[0];calls.append(p)
 if '/git/trees/' in p:return 0,json.dumps({'tree':[{'path':'s/SKILL.md','type':'blob','mode':'100644','sha':'b'}]})
 if '/git/blobs/' in p:return 0,json.dumps({'encoding':'base64','content':base64.b64encode(body).decode(),'size':len(body)})
 if '/commits/' in p:return 0,json.dumps({'sha':'fixed'})
 if '/contributors' in p:return 0,'[]'
 return 0,'{}'
with tempfile.TemporaryDirectory() as td:
 h=Path(td);out['repeated_network_calls']={}
 for k in range(2):
  start=len(calls);cached_repo_snapshot('example/demo',h/'reputation.json',runner);out['repeated_network_calls']['metadata_calls_'+str(k+1)]=len(calls)-start
 for k in range(2):
  start=len(calls);r=stage_candidate('example/demo','s','fixed',h/'staging',runner);out['repeated_network_calls']['stage_calls_'+str(k+1)]=len(calls)-start
print(json.dumps(out,ensure_ascii=False,indent=2))
