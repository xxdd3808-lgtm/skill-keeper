function token(){return new URLSearchParams(location.search).get('t');}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function toast(m){let t=document.getElementById('toast');t.textContent=m;t.className='show';clearTimeout(t._h);t._h=setTimeout(()=>t.className='',4000);}
function copyText(s){(navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject()).then(()=>toast('已复制命令')).catch(()=>{const ta=document.createElement('textarea');ta.value=s;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}ta.remove();toast('已复制命令');});}
function openJump(hash){
  if(!hash||hash.charAt(0)!=='#')return;
  const target=document.getElementById(hash.slice(1));if(!target)return;
  const reveal=()=>{
    for(let p=target;p;p=p.parentElement){
      if((p.tagName||'').toLowerCase()==='details'){
        p.open=true;p.setAttribute('open','');
      }
    }
  };
  reveal();setTimeout(reveal,0);
  history.replaceState(null,'',hash);
  target.classList.remove('target-flash');void target.offsetWidth;target.classList.add('target-flash');
  target.scrollIntoView({behavior:'smooth',block:'start'});
}
window.addEventListener('hashchange',()=>openJump(location.hash));
window.addEventListener('DOMContentLoaded',()=>{if(location.hash)openJump(location.hash);});
if(location.hash)openJump(location.hash);
async function post(path,body){const t=token();const r=await fetch(path+(path.includes('?')?'&':'?')+'t='+encodeURIComponent(t),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let j=null;try{j=await r.json();}catch(e){}return {status:r.status,j:j};}
function afterApply(ar,b,okMsg){
  if(ar.j&&ar.j.ok){
    if(ar.j.snapshot_status==='stale'){
      toast('✅ 变更已提交(事务完成),但报告刷新失败 — 点「🔄 刷新报告」重试,不要重复执行变更');
    }else{
      toast(okMsg);setTimeout(()=>location.reload(),1500);
    }
  }else{toast('❌ '+(ar.j&&ar.j.error||'执行失败'));b.disabled=false;}
}
function showVetPanel(p){
  const old=document.getElementById('vet-panel');if(old)old.remove();
  const vetCmd='skill-keeper manage vet '+p.plan_id+' --verdict safe --evidence "【必填】替换为你实际核查过的依据:已读候选全文/来源核对/差异结论"';
  const applyCmd='skill-keeper manage apply '+p.plan_id+' --digest '+p.digest+' --confirm';
  const host=document.getElementById('vet-panel-host')||document.body;
  const div=document.createElement('div');
  div.className='vet-panel';div.id='vet-panel';
  div.innerHTML='<div class="card"><div class="card-t"><b>🔄 更新计划已建立(30 分钟内有效):</b> '+esc(p.summary)+'</div>'
    +'<p>候选: '+esc((p.repo||'?')+'@'+(p.commit_sha||'fixed-candidate'))+' · digest '+esc(p.digest)+'</p>'
    +'<p><b>第 1 步 安检</b>(需要可核查证据;网页点击不构成安检):'
    +' <button class="btn" data-act="copy" data-cmd="'+esc(vetCmd)+'">📋 复制安检命令</button></p>'
    +'<p><code>'+esc(vetCmd)+'</code></p>'
    +'<p><b>第 2 步 执行</b>: <button class="btn" data-act="vet-continue" data-plan="'+esc(p.plan_id)+'" data-digest="'+esc(p.digest)+'">▶ 我已完成安检,继续执行</button>'
    +' <button class="btn" data-act="copy" data-cmd="'+esc(applyCmd)+'">📋 复制执行命令</button></p>'
    +'<p class="mut">计划不可变;随时可关掉页面,稍后在终端运行上面的命令续办。</p></div>';
  host.prepend(div);
  div.scrollIntoView({behavior:'smooth',block:'center'});
}
document.addEventListener('click',async e=>{
  const jump=e.target.closest('a[data-jump]');
  if(jump){e.preventDefault();openJump(jump.getAttribute('href'));return;}
  const b=e.target.closest('button[data-act]');if(!b)return;
  const act=b.dataset.act,id=b.dataset.id;
  if(act==='copy'){copyText(b.dataset.cmd||'');toast('已复制命令');return;}
  if(!token()){copyText(b.dataset.cmd||'');toast('静态模式:已复制等价命令');return;}
  if(act==='refresh'){
    if(!confirm('重跑扫描并刷新报告?(只读操作)'))return;
    b.disabled=true;
    const r=await post('/api/rescan',{});
    if(r.j&&r.j.ok){toast('✅ 已重扫并刷新报告,即将重新加载');setTimeout(()=>location.reload(),1200);}
    else{toast('❌ '+(r.j&&r.j.error||'刷新失败'));b.disabled=false;}
    return;
  }
  if(act==='remove'){
    if(!confirm('为「'+b.dataset.name+'」生成删除计划?'))return;
    b.disabled=true;
    const pr=await post('/api/plan',{action:'remove',instance_ids:[id],reason:'报告建议(网页一键)'});
    if(!pr.j||!pr.j.ok){toast('❌ 生成计划失败:'+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    const p=pr.j;
    if(!confirm('计划摘要:'+p.summary+'\n确认执行 digest: '+p.digest+'\n(先自动备份;失败自动回滚)')){b.disabled=false;return;}
    const ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true});
    afterApply(ar,b,'✅ 已执行,稍后自动刷新');
    return;
  }
  if(act==='restore'){
    if(!confirm('为备份 '+b.dataset.backup+' 生成恢复计划?'))return;
    b.disabled=true;
    const pr=await post('/api/restore-plan',{backup_id:b.dataset.backup});
    if(!pr.j||!pr.j.ok){toast('❌ '+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    const p=pr.j;
    if(!confirm('计划摘要:'+p.summary+'\n确认恢复 digest: '+p.digest+'\n(目标已存在则冲突失败,不覆盖)')){b.disabled=false;return;}
    const ar=await post('/api/apply',{plan_id:p.plan_id,digest:p.digest,confirm:true});
    afterApply(ar,b,'✅ 已恢复,稍后自动刷新');
    return;
  }
  if(act==='update'){
    if(!confirm('为「'+b.dataset.name+'」生成更新计划?(候选须已暂存;安检通过并确认后才会执行)'))return;
    b.disabled=true;
    const pr=await post('/api/plan',{action:'update',instance_id:id});
    if(!pr.j||!pr.j.ok){toast('❌ 生成更新计划失败:'+(pr.j&&pr.j.error||'请求失败'));b.disabled=false;return;}
    b.disabled=false;
    showVetPanel(pr.j);
    return;
  }
  if(act==='vet-continue'){
    const plan=b.dataset.plan,digest=b.dataset.digest;
    b.disabled=true;
    let ar=await post('/api/apply',{plan_id:plan,digest:digest,confirm:true});
    if(ar.j&&ar.j.error&&String(ar.j.error).indexOf('安检')>=0){
      toast('❌ 尚未安检:先在终端运行第 1 步的 vet 命令,再点继续');b.disabled=false;return;
    }
    if(ar.j&&ar.j.error&&String(ar.j.error).indexOf('warning')>=0){
      if(!confirm('候选安检为 warning: '+ar.j.error+'\n确认接受风险并继续?')){b.disabled=false;return;}
      ar=await post('/api/apply',{plan_id:plan,digest:digest,confirm:true,accept_warning:true});
    }
    afterApply(ar,b,'✅ 已更新,稍后自动刷新');
    return;
  }
  if(act==='ignore'){
    if(!confirm('忽略这个问题?'))return;
    const r=await post('/api/ignore',{name:b.dataset.name,match:b.dataset.match,confirm:true});
    toast(r.j&&r.j.ok?'✅ 已忽略':'❌ '+(r.j&&r.j.error||'失败'));
    if(r.j&&r.j.ok)setTimeout(()=>location.reload(),1200);
  }
});
