/* Official resident population is shown separately from reviewers and simulations. */
(() => {
  'use strict';
  const AGES = [['10s','10대'],['20s','20대'],['30s','30대'],['40s','40대'],['50s','50대'],['60plus','60대 이상']];
  const SEXES = [['female','여성'],['male','남성']];
  const n = v => Number.isSafeInteger(v) && v >= 0 ? v.toLocaleString('ko-KR') : '—';
  const el = (tag, cls, text) => { const x=document.createElement(tag); if(cls)x.className=cls; if(text!==undefined)x.textContent=String(text); return x; };
  const add = (x,...ys) => { ys.flat().forEach(y=>{if(y)x.append(y);});return x; };
  const note = t=>el('p','p-note',t);
  async function json(url) { const r=await fetch(url); if(!r.ok)throw new Error('인구 자료를 불러오지 못했어요. 다시 선택해 주세요.');return r.json(); }
  function render(result) {
    const panel=el('section','p-panel p-population');panel.id='perception-population';
    add(panel,add(el('div','p-section-head'),add(el('div'),el('span','p-eyebrow','공식 인구 통계'),el('h2','','누가 사는 동네일까?'),note('행정동을 골라 주변 인구를 살펴봐요. 매장 고객 수와는 달라요.'))));
    const form=el('form','p-population-search');
    const label=el('label','sr-only','행정구역 검색');label.htmlFor='population-query';
    const input=el('input');input.type='search';input.id='population-query';input.maxLength=80;input.required=true;
    input.placeholder='예: 산본, 성수, 서교동';input.value=result.input?.location || result.stores?.[0]?.location || '';
    const button=el('button','p-button p-secondary','동네 찾기');button.type='submit';
    add(form,label,input,button);
    const choices=el('div','p-region-choices');choices.setAttribute('aria-label','행정구역 선택');
    const message=el('p','p-note p-population-status');message.setAttribute('role','status');
    const content=el('div','p-population-content');
    add(panel,form,message,choices,content);
    let seq=0,chosen='';
    async function choose(code) {
      const id=++seq;message.textContent='선택한 동네의 인구를 불러오고 있어요.';
      // Remove old results as soon as the selection changes; never pair a new label with old data.
      content.replaceChildren();chosen='';
      choices.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed','false'));
      try {
        const data=await json(`/api/population/${encodeURIComponent(code)}`);
        if(id!==seq || !panel.isConnected)return;
        if(data.source_type!=='official_resident_population' || !Array.isArray(data.cells))throw new Error('공식 인구 자료 형식을 확인할 수 없어요.');
        chosen=code;message.textContent='';
        choices.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.code===chosen)));
        content.replaceChildren(population(data));
      } catch(e){if(id===seq)message.textContent=e.message;}
    }
    async function search() {
      const q=input.value.trim();if(!q)return;
      const id=++seq;button.disabled=true;choices.replaceChildren();content.replaceChildren();
      message.textContent='행정구역을 찾고 있어요.';
      try {
        const data=await json(`/api/population/regions?q=${encodeURIComponent(q)}`);
        if(id!==seq || !panel.isConnected)return;
        const regions=Array.isArray(data.regions)?data.regions:[];
        message.textContent=regions.length?'살펴볼 행정구역을 선택해 주세요.':'행정동 이름으로 다시 찾아보세요. 예: 홍대 → 서교동';
        regions.forEach(region=>{
          const b=el('button','p-region-choice',region.name);b.type='button';b.dataset.code=region.code;b.setAttribute('aria-pressed','false');
          b.addEventListener('click',()=>choose(region.code));choices.append(b);
        });
      } catch(e){if(id===seq)message.textContent=e.message;}
      finally{if(id===seq)button.disabled=false;}
    }
    form.addEventListener('submit',e=>{e.preventDefault();search();});
    // Defer until the report is attached; a later report must not inherit these results.
    setTimeout(()=>{if(panel.isConnected && input.value)search();},0);
    return panel;
  }
  function population(data) {
    const box=el('div');
    const heading=add(el('div','p-population-heading'),add(el('div'),el('h3','',data.region.name),note(`${data.period_label} · ${data.metric}`)),add(el('div','p-population-total'),el('strong','',n(data.display_total)),el('span','','명 · 10세 이상')));
    const cells=data.cells;
    const get=(sex,age)=>cells.find(c=>c.gender===sex && c.age_group===age)?.count;
    const max=Math.max(1,...cells.map(c=>Number.isSafeInteger(c.count)&&c.count>=0?c.count:0));
    const chart=el('div','p-population-chart');chart.setAttribute('role','img');chart.setAttribute('aria-label',`${data.region.name} 성별·연령별 주민등록 인구. 아래 숫자 표에서 자세히 볼 수 있어요.`);
    add(chart,add(el('div','p-pyramid-labels'),el('span','p-female','여성'),el('span','','연령'),el('span','p-male','남성')));
    [...AGES].reverse().forEach(([age,label])=>{
      const row=el('div','p-pyramid-row');
      for(const [sex]of SEXES){
        const value=get(sex,age);
        const bar=el('div',`p-pyramid-bar p-pyramid-${sex}`);
        const fill=el('span');fill.style.width=`${Number.isSafeInteger(value)&&value>=0?Math.min(100,value/max*100):0}%`;
        add(bar,fill,el('strong','',n(value)));
        row.append(bar);if(sex==='female')row.append(el('span','p-pyramid-age',label));
      }
      chart.append(row);
    });
    const heat=el('div','p-population-heat');heat.append(el('span'));AGES.forEach(([,t])=>heat.append(el('span','p-demo-label',t)));
    const table=el('table','p-table');table.append(el('caption','',`${data.region.name} · ${data.period_label} · 10세 이상 / 명`));
    const tr=el('tr');['성별','연령','인구','10세 이상 인구 중 비율'].forEach(t=>{const th=el('th','',t);th.scope='col';tr.append(th);});table.append(add(el('thead'),tr));
    const tbody=el('tbody');
    SEXES.forEach(([sex,label])=>{
      heat.append(el('span','p-demo-label',label));
      AGES.forEach(([age,ageLabel])=>{
        const value=get(sex,age), cell=cells.find(c=>c.gender===sex&&c.age_group===age);
        const valid=Number.isSafeInteger(value)&&value>=0;
        const level=valid?Math.min(4,Math.ceil(value/max*4)):'unknown';
        const square=add(el('div',`p-population-square p-population-density-${level}`),el('strong','',n(value)),el('small','','명'));
        square.setAttribute('aria-label',`${label} ${ageLabel} ${n(value)}명`);heat.append(square);
        const row=el('tr');const th=el('th','',label);th.scope='row';row.append(th);
        [ageLabel,n(value),typeof cell?.share_pct==='number'?`${cell.share_pct.toFixed(1)}%`:'—'].forEach(t=>row.append(el('td','',t)));tbody.append(row);
      });
    });table.append(tbody);
    const detail=el('details','p-detail');detail.id='population-table';
    const wrap=el('div','p-table-wrap');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','지역 인구 숫자 표');wrap.append(table);
    add(detail,el('summary','','숫자 표와 출처 보기'),wrap,note(`전체 연령 ${n(data.total)}명 · 10세 미만 ${n(data.under10?.total)}명은 위 그림에서 제외했어요.`));
    const source=el('a','p-source-link','행정안전부 주민등록 인구통계 ↗');source.href='https://jumin.mois.go.kr/ageStatMonth.do';source.target='_blank';source.rel='noopener noreferrer';
    detail.append(source);
    add(box,heading,add(el('div','p-population-viz'),chart,add(el('div'),heat,note('빗금이 촘촘할수록 표시된 칸 가운데 인구가 많아요. 만족도나 방문 확률을 뜻하지 않아요.'))),detail);
    return box;
  }
  globalThis.PopulationView=Object.freeze({render,population});
})();
