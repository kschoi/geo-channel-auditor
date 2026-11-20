/* Public evidence only. All remote strings become text nodes, never HTML. */
(() => {
  'use strict';
  const SENTIMENTS = { positive: '긍정', negative: '부정', mixed: '혼합', neutral: '중립' };
  const ASPECTS = { quality: '제품·품질', service: '서비스', value: '가격·가치', ambience: '분위기', access: '접근성', wait: '대기', cleanliness: '청결' };
  const AGE_GROUPS = [['10s', '10대'], ['20s', '20대'], ['30s', '30대'], ['40s', '40대'], ['50s', '50대'], ['60plus', '60대+']];
  const GENDERS = [['female', '여성'], ['male', '남성']];
  const list = value => Array.isArray(value) ? value : [];
  const isCount = value => Number.isSafeInteger(value) && value >= 0;
  const count = value => isCount(value) ? value.toLocaleString('ko-KR') : '—';
  const ratio = (value, total) => isCount(value) && isCount(total) && total > 0 && value <= total ? value / total * 100 : null;
  const percent = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? `${value.toFixed(1)}%` : '—';
  const density = value => value === null || !Number.isFinite(value) || value < 0 || value > 100 ? 'unknown' : value === 0 ? '0' : value <= 25 ? '1' : value <= 50 ? '2' : value <= 75 ? '3' : '4';
  const safeUrl = value => {
    try {
      const url = new URL(String(value));
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) { return ''; }
  };
  const safeDetail = (value, secret = '') => {
    const text = typeof value === 'string' ? value : '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
    return (secret ? text.split(secret).join('[숨김]') : text).slice(0, 600);
  };
  const parseSSE = buffer => {
    const blocks = buffer.split(/\r?\n\r?\n/);
    const remainder = blocks.pop();
    const events = [];
    for (const block of blocks) {
      let event = 'message';
      const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        try { events.push({ event, data: JSON.parse(data.join('\n')) }); }
        catch (_) { throw new Error('분석 응답이 중간에 끊겼어요. 다시 시도해 주세요.'); }
      }
    }
    return { events, remainder };
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function append(parent, ...children) {
    for (const child of children.flat()) if (child) parent.append(child);
    return parent;
  }
  function link(title, url, className = 'p-source-link') {
    const safe = safeUrl(url);
    if (!safe) return el('span', 'p-note', title);
    const node = el('a', className, title);
    node.href = safe;
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
    return node;
  }
  function badge(text, className = '') { return el('span', `p-badge ${className}`, text); }
  function note(text) { return el('p', 'p-note', text); }
  function empty(title, description) {
    return append(el('div', 'p-empty'), el('strong', '', title), el('p', '', description));
  }
  function section(title, description, id) {
    const panel = el('section', 'p-panel');
    if (id) panel.id = id;
    const heading = el('h2', '', title);
    if (id) { heading.id = `${id}-title`; panel.setAttribute('aria-labelledby', heading.id); }
    append(panel, append(el('div', 'p-section-head'), append(el('div'), heading, description ? note(description) : null)));
    return panel;
  }
  function table(headers, rows, caption) {
    const wrapper = el('div', 'p-table-wrap');
    wrapper.tabIndex = 0;
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('aria-label', caption);
    const tableNode = el('table', 'p-table');
    append(tableNode, el('caption', '', caption));
    const head = append(el('thead'), append(el('tr'), headers.map(value => {
      const th = el('th', '', value); th.scope = 'col'; return th;
    })));
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      row.forEach((value, index) => {
        const cell = el(index === 0 ? 'th' : 'td', '', value);
        if (index === 0) cell.scope = 'row';
        tr.append(cell);
      });
      body.append(tr);
    }
    append(tableNode, head, body);
    return append(wrapper, tableNode);
  }
  function details(title, ...children) { return append(el('details', 'p-detail'), el('summary', '', title), children); }
  function hatch(level) {
    const square = el('span', `p-hatch p-hatch-${level}`, level === 'unknown' ? '—' : '');
    square.setAttribute('aria-hidden', 'true');
    return square;
  }

  function summaryPanel(result, target) {
    const summary = target.summary || {};
    const panel = section('확인한 후기에서 보이는 인식', '게시일이 분석 기간에 포함된 고객 경험 문서만 집계해요.', 'perception-overview');
    const content = el('div', 'p-summary');
    const left = el('div');
    const total = summary.documents;
    let headline = '아직 판단할 근거가 부족해요';
    if (isCount(total) && total > 0) {
      const negative = isCount(summary.negative) ? summary.negative : 0;
      const positive = isCount(summary.positive) ? summary.positive : 0;
      headline = positive > negative ? '좋은 경험과 아쉬운 지점을 함께 봐요' : negative > positive ? '불편이 언급된 경험부터 살펴봐요' : '서로 다른 경험의 이유를 살펴봐요';
    }
    append(left, badge('검색으로 확인한 문서 기준'), el('h3', '', headline), note('문서 한 건을 한 명의 고객으로 세지 않아요. 한 문서에 긍정과 부정이 함께 있으면 ‘혼합’으로 표시해요.'));
    const right = el('div');
    append(right, el('span', 'p-eyebrow', '기간 내 고객 경험 문서'));
    const number = el('div', 'p-summary-number', count(total));
    number.append(el('small', '', '건'));
    right.append(number);
    if (isCount(total) && total > 0) {
      const bar = el('div', 'p-segment-bar');
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', Object.entries(SENTIMENTS).map(([id, label]) => `${label} ${count(summary[id])}건`).join(', '));
      for (const id of Object.keys(SENTIMENTS)) {
        const part = el('span', `p-segment-${id}`);
        const width = ratio(summary[id], total);
        part.style.width = `${width === null ? 0 : width}%`;
        bar.append(part);
      }
      right.append(bar);
    } else right.append(note('게시일과 고객 경험을 함께 확인한 자료가 없어요. 아래에서 날짜 미확인 근거도 따로 볼 수 있어요.'));
    const counts = el('div', 'p-counts');
    for (const [id, label] of Object.entries(SENTIMENTS)) {
      const item = el('div');
      const value = el('div', `p-count-value p-${id}`, count(summary[id]));
      value.append(el('small', '', ` · ${percent(ratio(summary[id], total))}`));
      append(item, el('span', 'p-count-label', label), value);
      counts.append(item);
    }
    right.append(counts);
    append(content, left, right);
    panel.append(content);
    const foot = el('div', 'p-summary-foot');
    append(foot,
      el('span', '', `날짜 미확인 고객 경험 ${count(summary.undated_documents)}건`),
      el('span', '', `홍보·기사 등 ${count(summary.non_customer_documents)}건`),
      el('span', '', `감정 판단 못 함 ${count(summary.unclassified_documents)}건`));
    panel.append(foot);
    return panel;
  }

  function comparisonPanel(result, inspect) {
    const stores = list(result.stores);
    const comparison = result.comparison || {};
    const panel = section(stores.length > 1 ? '어떤 경험에서 차이가 날까?' : '어떤 경험이 언급될까?', '사각형을 누르면 판단에 사용한 문장을 볼 수 있어요.', 'perception-comparison');
    if (comparison.reason) panel.append(el('p', 'p-comparison-note', comparison.reason));
    const rows = list(comparison.rows).length ? list(comparison.rows) : list(stores[0]?.aspects).map(aspect => ({ aspect: aspect.id, label: aspect.label, stores: [{ store_id: stores[0].id, ...aspect }] }));
    if (!rows.length) {
      panel.append(empty('비교할 항목이 아직 없어요', '매장이 일치하는 공개 후기를 확보하면 품질, 서비스, 가격 등의 근거를 나눠 보여줘요.'));
      return panel;
    }
    const matrix = el('div', 'p-matrix');
    matrix.style.setProperty('--p-stores', String(Math.max(1, Math.min(stores.length, 3))));
    const header = append(el('div', 'p-matrix-row p-matrix-header'), el('span', 'p-matrix-aspect', '경험'));
    for (const store of stores) {
      const name = el('div', 'p-matrix-store', store.name);
      name.append(el('small', '', store.id === 'target' ? '우리 매장' : '경쟁점'));
      if (store.coverage) name.append(el('small', '', `조회 ${count(store.coverage.succeeded)}/${count(store.coverage.attempted)}`));
      header.append(name);
    }
    matrix.append(header);
    const numericRows = [];
    for (const row of rows) {
      const limited = stores.length > 1 && row.comparable !== true;
      const label = el('div', 'p-matrix-aspect', row.label || ASPECTS[row.aspect] || row.aspect);
      if (limited) label.append(el('span', 'p-row-limit', '비교 제한'));
      const line = append(el('div', `p-matrix-row${limited ? ' p-matrix-limited' : ''}`), label);
      for (const store of stores) {
        const values = list(row.stores).find(item => item.store_id === store.id) || {};
        const negative = ratio(values.negative, values.documents);
        const cell = el('button', 'p-matrix-cell');
        cell.type = 'button';
        cell.setAttribute('aria-label', `${store.name}, ${row.label || row.aspect}, ${count(values.documents)}건 중 부정 ${count(values.negative)}건, ${percent(negative)}.${limited ? " 이 항목은 자료 수나 출처 구성 차이로 비교가 제한돼요." : ""} 근거 보기`);
        cell.addEventListener('click', () => inspect({ store: store.id, aspect: row.aspect, voice: 'customer_experience', period: 'in_period' }));
        const text = append(el('span'), el('span', 'p-matrix-value', negative === null ? '근거 없음' : `부정 ${percent(negative)}`), el('span', 'p-matrix-meta', `${count(values.documents)}건 · 긍정 ${count(values.positive)}`));
        append(cell, hatch(density(negative)), text);
        line.append(cell);
        numericRows.push([row.label || row.aspect, store.name, count(values.documents), count(values.positive), count(values.negative), count(values.mixed), count(values.neutral), percent(negative), limited ? "비교 제한" : stores.length > 1 ? "비교 가능" : "단독 분석"]);
      }
      matrix.append(line);
    }
    panel.append(matrix);
    const legend = el('div', 'p-legend');
    for (const [level, label] of [['0', '부정 0%'], ['1', '0% 초과~25%'], ['2', '25% 초과~50%'], ['3', '50% 초과~75%'], ['4', '75% 초과'], ['unknown', '근거 없음']]) legend.append(append(el('span'), hatch(level), el('span', '', label)));
    append(panel, legend, note('빗금이 촘촘할수록 부정 문서 비율이 높아요. 분모에는 긍정·부정·혼합·중립을 모두 포함해요. 항목 사이에는 같은 문서가 겹칠 수 있어요. 점선 행은 자료 수나 출처 구성 차이로 비교가 제한돼요.'));
    panel.append(details('숫자로 비교하기', table(['항목', '매장', '문서', '긍정', '부정', '혼합', '중립', '부정 비율', '비교 범위'], numericRows, '기간 내 고객 경험 문서 · 건 / %')));
    return panel;
  }

  function decisionsPanel(result, inspect) {
    const panel = section('먼저 바꿀 일', '우리 매장의 근거에서 다음 행동을 골랐어요.', 'perception-decisions');
    const decisions = list(result.decisions).filter(item => item.store_id === 'target');
    if (!decisions.length) {
      panel.append(empty('제안보다 근거 확인이 먼저예요', '구체적인 고객 경험이 확인되면 그 내용에 맞춰 개선할 일을 제안해요. 자료가 없다는 이유로 매장의 약점을 만들지는 않아요.'));
      return panel;
    }
    const items = el('div', 'p-decisions');
    for (const [index, item] of decisions.entries()) {
      const article = el('article', 'p-decision');
      const button = el('button', 'p-text-button', `근거 ${list(item.evidence_ids).length}개 확인 →`);
      button.type = 'button';
      button.addEventListener('click', () => inspect({ ids: list(item.evidence_ids) }));
      append(article, badge(`${String(index + 1).padStart(2, '0')} · ${ASPECTS[item.aspect] || '확인할 일'}`), el('h3', '', item.title), el('p', '', item.reason), el('p', 'p-action', item.action), button);
      items.append(article);
    }
    panel.append(items);
    return panel;
  }

  function demographicGrid(cells, population = false) {
    const grid = el('div', 'p-demo-grid');
    grid.setAttribute('role', 'group');
    grid.setAttribute('aria-label', population ? '공개 지역 인구의 성별·연령별 수' : '리뷰 작성자의 성별·연령별 수');
    grid.append(el('span'));
    AGE_GROUPS.forEach(([, label]) => grid.append(el('span', 'p-demo-label', label)));
    for (const [gender, label] of GENDERS) {
      grid.append(el('span', 'p-demo-label', label));
      for (const [age, ageLabel] of AGE_GROUPS) {
        const cell = list(cells).find(item => item.gender === gender && item.age_group === age);
        // Public reviews never provide inferred demographics; only the separate official population grid may have counts.
        const known = population && isCount(cell?.count);
        const node = el('div', `p-demo-cell ${known ? 'p-demo-known' : ''}`, known ? count(cell.count) : '—');
        node.setAttribute('aria-label', `${label} ${ageLabel}: ${known ? `${count(cell.count)}명` : '미확인'}`);
        node.append(el('small', '', known ? '명' : '미확인'));
        grid.append(node);
      }
    }
    return grid;
  }

  function demographicsPanel(target) {
    const panel = section('성별·연령별로도 다를까?', '공개 리뷰에서 확인할 수 없는 작성자 정보는 미확인으로 남겨요.', 'perception-demographics');
    const layout = el('div', 'p-demographic-layout');
    const info = append(el('div', 'p-info'), el('h3', '', '성별·나이를 추측하지 않아요'), el('p', '', target.demographics?.reason || '이름, 사진, 말투로 리뷰 작성자의 성별과 나이를 알 수는 없어요. 현재 공개 근거로는 집단별 긍정·부정 비율을 계산할 수 없어요.'));
    const viz = append(el('div'), demographicGrid([], false), note('— = 미확인 · 0명이라는 뜻이 아니에요.'));
    append(layout, info, viz);
    panel.append(layout);
    panel.append(details('성별·연령별 숫자 표', table(['성별', '연령', '작성자 수', '긍정', '부정'], GENDERS.flatMap(([, gender]) => AGE_GROUPS.map(([, age]) => [gender, age, '미확인', '미확인', '미확인'])), '공개 리뷰 작성자 정보 · 확인되지 않은 값은 집계하지 않음')));
    return panel;
  }

  function marketPanel(market = {}) {
    const panel = section('상권의 배경도 함께 봐요', '지역 통계는 매장의 실제 고객 수나 고객 만족도를 뜻하지 않아요.', 'perception-market');
    const layout = el('div', 'p-market-top');
    const facts = el('div', 'p-market-facts');
    if (!list(market.facts).length) facts.append(empty('지역·기간이 맞는 수치는 아직 미확인이에요', '공개 문서에서 지역, 기준일, 단위와 숫자가 함께 확인돼야 표시해요. 다른 동네의 통계나 성별·연령별 합계를 조합해 채우지 않아요.'));
    for (const fact of list(market.facts)) {
      const article = el('article', 'p-market-fact');
      const value = el('strong', '', typeof fact.value === 'number' ? fact.value.toLocaleString('ko-KR') : fact.value);
      value.append(el('small', '', fact.unit));
      append(article, el('h3', '', fact.label), value, note(`${fact.region || '지역 미확인'} · ${fact.period || '기준일 미확인'}`), el('blockquote', '', fact.quote), link(fact.title || '통계 근거 보기', fact.url));
      facts.append(article);
    }
    const demographics = market.demographics || {};
    const demo = append(el('div'), el('h3', 'p-note', demographics.metric_label || '지역 인구 · 매장 고객 수 아님'), note([demographics.region, demographics.period].filter(Boolean).join(' · ') || '지역·기준일 미확인'), demographicGrid(demographics.cells, true), note('실제 성별×연령 교차값이 공개된 칸만 채워요.'));
    append(layout, facts, demo);
    panel.append(layout);
    const refs = el('div', 'p-reference-links');
    for (const ref of list(market.references)) refs.append(link(ref.title, ref.url));
    if (refs.childNodes.length) panel.append(refs);
    const rows = GENDERS.flatMap(([gender, label]) => AGE_GROUPS.map(([age, ageLabel]) => {
      const cell = list(demographics.cells).find(item => item.gender === gender && item.age_group === age);
      return [label, ageLabel, isCount(cell?.count) ? count(cell.count) : '미확인', demographics.region || '미확인', demographics.period || '미확인'];
    }));
    panel.append(details('지역 인구 표와 해석 범위', table(['성별', '연령', '인구', '지역', '기준일'], rows, '공개 지역 인구 · 명'), append(el('ul', 'p-warning-list'), list(market.limitations).map(item => el('li', '', item)))));
    return panel;
  }

  function evidencePanel(result) {
    const panel = section('판단의 근거를 직접 확인해요', 'AI가 분류한 원문 조각이에요. 기사·홍보 문구와 고객 경험을 구분해 볼 수 있어요.', 'perception-evidence');
    const all = list(result.stores).flatMap(store => list(store.evidence).map(item => ({ ...item, store_name: store.name })));
    const filters = el('div', 'p-evidence-filters');
    const selects = {};
    const definitions = [
      ['store', '매장', [['', '모든 매장'], ...list(result.stores).map(store => [store.id, store.name])]],
      ['aspect', '경험', [['', '모든 항목'], ...Object.entries(ASPECTS)]],
      ['sentiment', '감정', [['', '모든 감정'], ...Object.entries(SENTIMENTS)]],
      ['period', '게시일', [['', '모든 근거'], ['in_period', '분석 기간 내'], ['unknown', '날짜 미확인']]],
      ['voice', '문서 성격', [['', '모든 성격'], ['customer_experience', '고객 경험'], ['owner_claim', '운영자 주장'], ['editorial', '기사·편집글'], ['unclear', '성격 미확인']]],
    ];
    let focusedIds = null;
    let limit = 8;
    for (const [key, label, options] of definitions) {
      const field = el('div', 'p-filter');
      const labelNode = el('label', '', label); labelNode.htmlFor = `perception-filter-${key}`;
      const select = el('select'); select.id = labelNode.htmlFor;
      options.forEach(([value, text]) => { const option = el('option', '', text); option.value = value; select.append(option); });
      select.addEventListener('change', () => { focusedIds = null; limit = 8; update(); });
      selects[key] = select;
      append(field, labelNode, select); filters.append(field);
    }
    const countNode = el('p', 'p-evidence-count');
    countNode.setAttribute('aria-live', 'polite');
    const reset = el('button', 'p-text-button', '전체 근거 보기'); reset.type = 'button'; reset.hidden = true;
    reset.addEventListener('click', () => { focusedIds = null; Object.values(selects).forEach(select => { select.value = ''; }); limit = 8; update(); });
    const items = el('div', 'p-evidence-list');
    const more = el('button', 'p-button p-secondary p-evidence-more', '근거 더 보기'); more.type = 'button';
    more.addEventListener('click', () => { limit += 8; update(); });
    append(panel, filters, countNode, reset, items, more);
    function update() {
      const values = Object.fromEntries(Object.entries(selects).map(([key, node]) => [key, node.value]));
      const filtered = all.filter(item => (!focusedIds || focusedIds.includes(item.id)) && (!values.store || item.store_id === values.store) && (!values.aspect || item.aspect === values.aspect) && (!values.sentiment || item.sentiment === values.sentiment) && (!values.period || item.period_status === values.period) && (!values.voice || item.voice === values.voice));
      countNode.textContent = `${filtered.length}개 원문 조각 · 한 문서에서 여러 조각이 나올 수 있어요.`;
      reset.hidden = !focusedIds && !Object.values(values).some(Boolean);
      items.replaceChildren();
      if (!filtered.length) items.append(empty('이 조건에 맞는 근거가 없어요', '다른 매장이나 항목을 선택하거나 전체 근거를 확인해 보세요.'));
      for (const item of filtered.slice(0, limit)) {
        const article = el('article', 'p-evidence-item');
        const sentiment = Object.hasOwn(SENTIMENTS, item.sentiment) ? item.sentiment : 'neutral';
        const meta = append(el('div', 'p-evidence-meta'), badge(SENTIMENTS[sentiment], `p-sentiment-${sentiment}`), el('span', '', item.aspect_label || ASPECTS[item.aspect] || '항목 미확인'), el('span', '', item.store_name));
        append(article, meta, el('blockquote', '', item.quote), link(item.title || '원문 보기', item.url), el('p', 'p-evidence-source', `${item.voice_label || '문서 성격 미확인'} · ${item.date || '날짜 미확인'} · ${item.period_status === 'in_period' ? '집계 기간 내' : '기간 집계에서 제외'}`));
        items.append(article);
      }
      more.hidden = filtered.length <= limit;
    }
    function inspect(filter) {
      focusedIds = filter.ids || null;
      Object.entries(selects).forEach(([key, select]) => { select.value = filter[key] || ''; });
      limit = 8; update();
      panel.scrollIntoView({ behavior: 'auto', block: 'start' });
      selects.store.focus({ preventScroll: true });
    }
    update();
    return { panel, inspect };
  }

  function sourcesPanel(result) {
    const panel = section('수집 범위와 분석 기준', '공개 검색에서 찾은 자료의 범위예요. 해당 사이트의 전체 리뷰 수와는 달라요.', 'perception-method');
    const coverage = result.coverage || {};
    const totals = el('div', 'p-summary-foot');
    append(totals, el('span', '', `채택 문서 ${count(coverage.documents)}건`), el('span', '', `매장·지역 불일치로 제외 ${count(coverage.excluded_documents)}건`), el('span', '', `검색 성공 ${count(coverage.successful_calls)} / ${count(coverage.search_calls)}회`));
    panel.append(totals);
    const rows = list(result.stores).flatMap(store => list(store.channels).map(channel => [store.name, channel.label || channel.id, count(channel.documents)]));
    if (rows.length) panel.append(details('매장별 확인된 채널', table(['매장', '채널', '문서'], rows, '확인한 공개 문서 수 · 고객 수나 전체 리뷰 수가 아님')));
    const sourceList = el('ul', 'p-source-list');
    const sources = [...list(result.sources), ...list(result.market?.sources)];
    const seen = new Set();
    for (const source of sources) {
      const identity = source.source_id || source.url;
      if (seen.has(identity)) continue;
      seen.add(identity);
      append(sourceList, append(el('li'), link(source.title || source.url, source.url), el('p', '', `${source.source_id || ''} · ${source.date || '날짜 미확인'} · ${source.period_status === 'in_period' ? '기간 내' : source.period_status === 'out_of_period' ? '기간 밖' : '기간 확인 필요'}`)));
    }
    panel.append(details(`전체 출처 ${seen.size}건`, sourceList));
    const notes = [...list(result.methodology), ...list(coverage.notes), ...list(result.warnings)];
    panel.append(details('집계 방법과 확인하지 못한 내용', append(el('ul', 'p-warning-list'), [...new Set(notes)].map(text => el('li', '', text)))));
    const calls = list(result.pipeline?.calls).map(call => [String(call.endpoint || call.name || call.path || 'API 호출'), String(call.status || '미확인'), typeof call.latency_ms === 'number' ? `${Math.round(call.latency_ms)}ms` : typeof call.elapsed_ms === 'number' ? `${Math.round(call.elapsed_ms)}ms` : typeof call.duration_ms === 'number' ? `${Math.round(call.duration_ms)}ms` : '—']);
    panel.append(details('API 호출 기록', table(['API', '상태', '소요 시간'], calls, '인증 정보와 요청 본문은 표시하지 않음')));
    return panel;
  }

  function render(result, container) {
    const stores = list(result.stores).slice(0, 3);
    const target = stores.find(store => store.id === 'target') || stores[0];
    if (!target) throw new Error('매장 분석 응답이 비어 있어요. 검색어를 확인하고 다시 시도해 주세요.');
    const fragment = document.createDocumentFragment();
    const heading = el('div', 'p-report-head');
    append(heading, append(el('div'), el('span', 'p-eyebrow', '고객 인식 리포트'), el('h2', '', target.name), note(`${target.location || ''} · ${result.period?.start || '날짜 미확인'} — ${result.period?.end || '날짜 미확인'}`)), badge(result.comparison?.status === 'comparable' && !list(result.comparison?.rows).some(row => row.comparable === false) ? '같은 기준으로 비교' : stores.length > 1 ? '비교 범위 확인 필요' : '공개 자료 분석', stores.length > 1 && result.comparison?.status !== 'comparable' ? 'p-warning' : ''));
    const evidence = evidencePanel(result);
    const split = append(el('div', 'p-report-grid'), comparisonPanel(result, evidence.inspect), decisionsPanel(result, evidence.inspect));
    fragment.append(heading);
    if (list(result.warnings).length) {
      const warning = append(el('section', 'p-panel p-warning-panel'), el('h2', '', '일부 자료를 확인하지 못했어요'));
      warning.setAttribute('role', 'status');
      append(warning, append(el('ul', 'p-warning-list'), list(result.warnings).slice(0, 4).map(text => el('li', '', text))));
      if (list(result.warnings).length > 4) warning.append(note('나머지 내용은 아래 수집 범위와 분석 기준에서 확인할 수 있어요.'));
      fragment.append(warning);
    }
    append(fragment, summaryPanel(result, target), split, demographicsPanel(target), marketPanel(result.market), evidence.panel, sourcesPanel(result));
    const bridge = el('section', 'p-panel p-card-bridge');
    const next = el('a', 'p-button p-primary', '검색 정보 확인 · 카드뉴스 만들기 →');
    next.href = `/source-audit?query=${encodeURIComponent(result.input?.query || target.query || `${target.location || ''} ${target.name || ''}`)}`;
    append(bridge, append(el('div'), el('h2', '', '다음은 매장에 맞는 콘텐츠로'), note('우리 매장의 검색 정보를 확인하고 카드뉴스 3장을 만들 수 있어요.')), next);
    fragment.append(bridge);
    container.replaceChildren(fragment);
    container.hidden = false;
    return { inspect: evidence.inspect };
  }

  function init() {
    const byId = id => document.getElementById(`perception-${id}`);
    const form = byId('form');
    if (!form) return;
    let controller = null;
    let requestSequence = 0;
    let canceled = false;
    let timeout = false;
    let history = [];
    function progress(message) {
      byId('progress-title').textContent = message;
      if (!history.includes(message)) history.push(message);
      history = history.slice(-3);
      byId('progress-steps').replaceChildren(...history.map(item => el('li', '', item)));
    }
    function showError(message, title = '분석을 마치지 못했어요') {
      byId('error-title').textContent = title;
      byId('error-detail').textContent = message;
      byId('error').hidden = false;
    }
    function busy(value) {
      byId('run').disabled = value;
      byId('run').textContent = value ? '분석 중…' : '고객 인식 살펴보기 →';
      form.setAttribute('aria-busy', String(value));
      form.querySelectorAll('input, select, .p-chip').forEach(node => { node.disabled = value; });
      byId('progress').hidden = !value;
    }
    byId('cancel').addEventListener('click', () => { canceled = true; controller?.abort(); });
    document.querySelectorAll('[data-perception-query]').forEach(chip => chip.addEventListener('click', () => {
      byId('query').value = chip.dataset.perceptionQuery;
      form.requestSubmit();
    }));
    const initialQuery = new URLSearchParams(location.search).get('query');
    if (initialQuery) byId('query').value = initialQuery.slice(0, 300);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (controller) return;
      const query = byId('query').value.trim();
      if (!query) { byId('query').focus(); return; }
      const requestId = ++requestSequence;
      const key = byId('api-key').value.trim();
      const payload = { query, competitors: ['competitor-1', 'competitor-2'].map(id => byId(id).value.trim()).filter(Boolean), period_days: Number(byId('period').value) };
      if (key) payload.api_key = key;
      byId('api-key').value = '';
      byId('error').hidden = true;
      byId('results').hidden = true;
      byId('intro').hidden = true;
      controller = new AbortController();
      canceled = false; timeout = false; history = [];
      busy(true);
      progress('매장과 지점을 확인하고 있어요');
      byId('progress-detail').textContent = '공개 자료를 찾고, 매장과 게시일이 맞는 근거를 골라요.';
      const timer = setTimeout(() => { timeout = true; controller?.abort(); }, 420000);
      const slowTimer = setTimeout(() => { byId('progress-detail').textContent = '공개 검색 응답을 기다리고 있어요. 여러 매장을 비교하면 조금 더 걸릴 수 있어요.'; }, 20000);
      let reader;
      try {
        const response = await fetch('/api/perception/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal });
        if (!response.ok) {
          let detail = '';
          try { detail = (await response.json()).detail; } catch (_) { /* Never display a raw upstream body. */ }
          throw new Error(safeDetail(detail || `요청을 처리하지 못했어요 (${response.status}). 입력과 API 연결을 확인해 주세요.`, key));
        }
        if (!response.body) throw new Error('이 브라우저에서 진행 상태를 읽을 수 없어요. 최신 브라우저에서 다시 시도해 주세요.');
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let completed = false;
        while (!completed) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          if (buffer.length > 2000000) throw new Error('분석 응답이 너무 커서 표시하지 못했어요. 매장을 하나씩 분석해 주세요.');
          const parsed = parseSSE(buffer); buffer = parsed.remainder;
          for (const item of parsed.events) {
            if (requestId !== requestSequence) return;
            if (item.event === 'error') throw new Error(safeDetail(item.data?.detail, key));
            if (item.event === 'step' && typeof item.data?.message === 'string') progress(safeDetail(item.data.message, key));
            if (item.event === 'result') {
              render(item.data, byId('results')); completed = true;
              byId('results').focus({ preventScroll: true });
              byId('results').scrollIntoView({ behavior: 'auto', block: 'start' });
            }
          }
        }
        if (!completed) throw new Error('완료 결과를 받기 전에 연결이 끊겼어요. 다시 시도해 주세요.');
      } catch (error) {
        if (requestId !== requestSequence) return;
        if (canceled) showError('검색을 중단했어요. 검색어와 비교할 매장을 바꿔 다시 시작할 수 있어요.', '분석을 중단했어요');
        else if (timeout) showError('응답 대기 시간이 길어 분석을 중단했어요. 비교할 매장을 줄이거나 잠시 후 다시 시도해 주세요.');
        else showError(safeDetail(error instanceof Error ? error.message : '', key));
      } finally {
        clearTimeout(timer); clearTimeout(slowTimer);
        if (reader) { try { await reader.cancel(); } catch (_) { /* Connection may already be closed. */ } }
        if (requestId === requestSequence) { controller = null; busy(false); }
      }
    });
  }

  // Pure helpers and a DOM renderer are exposed for deterministic contract/security tests.
  globalThis.PerceptionView = Object.freeze({ safeUrl, safeDetail, count, ratio, percent, density, parseSSE, render, demographicGrid });
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
  }
})();
