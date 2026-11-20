/* Explicit synthetic reactions. These never enter observed review or resident counts. */
(() => {
  'use strict';
  const LABELS={positive:'관심',negative:'망설임',mixed:'관심·망설임',unknown:'판단 보류'};
  const AGES=[['20s','20대'],['30s','30대'],['40s','40대'],['50s','50대'],['60plus','60대 이상']];
  const SEXES=[['female','여성'],['male','남성']];
  const el=(tag,cls,text)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=String(text);return x;};
  const add=(x,...ys)=>{ys.flat().forEach(y=>{if(y)x.append(y);});return x;};
  const note=t=>el('p','p-note',t);
  function render(result,inspect) {
    const panel=el('section','p-panel p-scenario');panel.id='perception-scenario';
    add(panel,add(el('div','p-section-head'),add(el('div'),el('span','p-eyebrow','가상 실험 · 실제 고객 조사 아님'),el('h2','','취향이 다른 사람이라면?'),note('공개 합성 인물 10명에게 같은 매장 근거를 보여줘요. 연령·성별 대신 각자의 음식 취향으로 반응을 살펴봐요.'))));
    const personas=el('div','p-persona-preview');
    const controls=el('div','p-scenario-controls');
    const run=el('button','p-button p-primary','가상 반응 살펴보기');run.id='scenario-run';run.type='button';run.disabled=true;
    const cancel=el('button','p-button p-secondary','중단');cancel.type='button';cancel.hidden=true;
    const status=note('가상 인물의 취향을 불러오고 있어요.');status.setAttribute('role','status');
    add(controls,run,cancel,status);
    const content=el('div','p-scenario-result');
    const keyDetails=el('details','p-detail');
    const keyLabel=el('label','','실험에 사용할 Liner API 키');keyLabel.htmlFor='scenario-api-key';
    const key=el('input');key.id='scenario-api-key';key.type='password';key.autocomplete='off';key.placeholder='비우면 서버 키 사용';
    add(keyDetails,el('summary','','API 설정'),add(el('div','p-field'),keyLabel,key));
    const attribution=note('NVIDIA Nemotron-Personas-Korea · CC BY 4.0 · 전국 합성 인물에서 연령·성별별 1명씩 고른 예시예요. 이 동네의 실제 손님이나 대표 표본이 아니에요.');
    const source=el('a','p-source-link','합성 데이터 출처 ↗');source.href='https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea';source.target='_blank';source.rel='noopener noreferrer';
    add(panel,personas,controls,content,keyDetails,attribution,source);
    let controller;
    function avatar(p) {
      const tile=el('article','p-persona-tile');
      const face=add(el('div',`p-persona-avatar p-avatar-${Number(p.id?.replace(/\D/g,''))%4||0}`),el('span','p-avatar-head'),el('span','p-avatar-body'));
      face.setAttribute('aria-hidden','true');
      add(tile,face,el('strong','',`${p.gender==='female'?'여성':'남성'} · ${p.age}세`),el('span','',`가상 인물 ${p.id}`));
      return tile;
    }
    async function load() {
      try {
        const r=await fetch('/api/scenarios/personas');if(!r.ok)throw new Error('가상 인물을 불러오지 못했어요.');const data=await r.json();
        if(!panel.isConnected)return;
        (Array.isArray(data.personas)?data.personas:[]).forEach(p=>personas.append(avatar(p)));
        run.disabled=!result.scenario_token;
        status.textContent=result.scenario_token?'반응을 만든 뒤, 실제 손님에게 물어볼 질문을 골라보세요.':'매장 후기를 먼저 분석하면 실험할 수 있어요.';
      }catch(e){status.textContent=e.message;}
    }
    cancel.addEventListener('click',()=>controller?.abort());
    run.addEventListener('click',async()=>{
      if(controller)return;
      controller=new AbortController();run.disabled=true;run.textContent='취향과 매장 근거를 함께 읽는 중…';cancel.hidden=false;
      status.textContent='가상 인물 10명의 반응을 만들고 있어요.';
      const secret=key.value.trim();key.value='';
      const timer=setTimeout(()=>controller?.abort(),190000);
      let needsKey=false;
      try {
        const r=await fetch('/api/scenarios/react',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report_token:result.scenario_token,...(secret?{api_key:secret}:{})}),signal:controller.signal});
        const data=await r.json();
        if(!r.ok){ needsKey=r.status===422 && typeof data.detail==='string' && data.detail.includes('API 키가 필요'); throw new Error(typeof data.detail==='string'?data.detail:'가상 반응을 만들지 못했어요.'); }
        if(!panel.isConnected)return;
        if(data.status!=='synthetic_simulation')throw new Error('가상 실험 결과 형식을 확인할 수 없어요.');
        content.replaceChildren(reactions(data,inspect));personas.hidden=true;
        run.textContent='가상 반응 생성 완료';status.textContent='결과는 가설이에요. 실제 손님에게 확인할 질문으로 써보세요.';
      }catch(e){
        status.textContent=e.name==='AbortError'?'실험을 중단했어요. 새로 매장을 분석하면 다시 실험할 수 있어요.':String(e.message).split(secret||'\u0000').join('[숨김]').slice(0,300);
        run.disabled=!needsKey;run.textContent=needsKey?'키 입력 후 다시 실행':'실험을 마치지 못했어요';
        if(needsKey){keyDetails.open=true;key.focus();}
      }finally{clearTimeout(timer);controller=null;cancel.hidden=true;}
    });
    setTimeout(()=>{if(panel.isConnected)load();},0);
    return panel;
  }
  function reactions(data,inspect) {
    const root=el('div');
    const rows=Array.isArray(data.personas)?data.personas:[];
    const summary=el('div','p-simulation-summary');
    for(const [id,label]of Object.entries(LABELS)){
      const value=data.summary?.[id];
      summary.append(add(el('div',`p-simulation-stat p-sim-${id}`),el('span','',label),el('strong','',Number.isSafeInteger(value)?`${value}명`:'—')));
    }
    const grid=el('div','p-scenario-grid');grid.append(el('span'));AGES.forEach(([,label])=>grid.append(el('span','p-demo-label',label)));
    const focus=el('div','p-persona-detail');focus.id='scenario-persona-detail';focus.setAttribute('aria-live','polite');
    function select(p,scroll=false) {
      const reaction=p.reaction||{};const sentiment=Object.hasOwn(LABELS,reaction.sentiment)?reaction.sentiment:'unknown';
      const card=el('article','p-persona-response');
      add(card,el('span','p-eyebrow',`가상 인물 ${p.id} · ${p.gender==='female'?'여성':'남성'} ${p.age}세 · 전국 합성 예시`),el('h3','',LABELS[sentiment]));
      add(card,el('blockquote','p-persona-preference',reaction.preference_quote||p.culinary_persona||'취향 정보 확인 필요'),el('p','p-persona-reason',reaction.reason||'확인한 매장 근거만으로 반응을 정하기 어려워요.'));
      if(reaction.question)add(card,add(el('div','p-interview-question'),el('span','','실제 손님에게 물어볼 질문'),el('strong','',reaction.question)));
      const ids=Array.isArray(reaction.evidence_ids)?reaction.evidence_ids:[];
      if(ids.length){const b=el('button','p-text-button','이 반응에 연결된 매장 근거 →');b.type='button';b.addEventListener('click',()=>inspect({ids}));card.append(b);}
      focus.replaceChildren(card);
      if(scroll)focus.scrollIntoView({behavior:'auto',block:'center'});
    }
    SEXES.forEach(([sex,label])=>{
      grid.append(el('span','p-demo-label',label));
      AGES.forEach(([age,ageLabel])=>{
        const p=rows.find(p=>p.gender===sex&&p.age_group===age);
        const sentiment=p?.reaction?.sentiment;const status=Object.hasOwn(LABELS,sentiment)?sentiment:'unknown';
        const button=el('button',`p-scenario-cell p-sim-${status}`);button.type='button';
        button.setAttribute('aria-label',`가상 ${label} ${ageLabel} 1명 · ${LABELS[status]} · 이유 보기`);
        add(button,el('span','p-scenario-mark',status==='positive'?'+':status==='negative'?'−':status==='mixed'?'±':'?'),el('strong','',LABELS[status]),el('small','','가상 1명'));
        button.disabled=!p;if(p)button.addEventListener('click',()=>select(p,true));grid.append(button);
      });
    });
    add(root,summary,grid,note('칸마다 가상 인물 1명의 반응이에요. 같은 연령·성별 집단의 비율이나 실제 방문 확률로 해석하지 않아요.'),focus);
    if(rows[0])select(rows[0]);
    const table=el('table','p-table');table.append(el('caption','','가상 인물별 반응 · 실제 고객 감정 아님'));
    const head=el('tr');['인물','성별','나이','합성 설정 지역','가상 반응'].forEach(t=>{const th=el('th','',t);th.scope='col';head.append(th);});table.append(add(el('thead'),head));
    const body=el('tbody');rows.forEach(p=>{const tr=el('tr');[p.id,p.gender==='female'?'여성':'남성',`${p.age}세`,p.district,LABELS[p.reaction?.sentiment]||LABELS.unknown].forEach(t=>tr.append(el('td','',t)));body.append(tr);});table.append(body);
    const wrap=add(el('div','p-table-wrap'),table);wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','가상 반응 숫자 표');
    add(root,add(el('details','p-detail'),el('summary','','가상 반응 표 보기'),wrap));
    return root;
  }
  globalThis.ScenarioView=Object.freeze({render,reactions});
})();
