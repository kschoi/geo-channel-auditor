/* Geo Channel Auditor — 프런트엔드 로직 (외부 프레임워크 없음) */
(() => {
  'use strict';

  const SAMPLES = JSON.parse(document.getElementById('samples-data')?.textContent || '[]');
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const els = {
    form: $('#audit-form'),
    location: $('#location'),
    industry: $('#industry'),
    storeName: $('#store_name'),
    channelText: $('#channel_text'),
    apiKey: $('#api_key'),
    charCount: $('#char-count'),
    runBtn: $('#run-btn'),
    runLabel: $('#run-btn-label'),
    runIcon: $('#run-btn-icon'),
    sampleList: $('#sample-list'),
    empty: $('#empty'),
    progress: $('#progress'),
    results: $('#results'),
    error: $('#error'),
    errorDetail: $('#error-detail'),
    stepList: $('#step-list'),
    progressMode: $('#progress-mode'),
    modeBadge: $('#mode-badge'),
  };

  const state = { running: false, result: null, tab: 'instagram', activeSampleId: null, copyStore: [] };

  const STEP_DEFS = [
    { title: 'Liner Search · 상권 수요 수집', detail: '상권 위치·업종으로 검색 쿼리를 만들어 실시간 수요와 경쟁 출처를 모읍니다.' },
    { title: 'Liner Search Agent · 고객 기대 정보 분석', detail: '수집한 출처를 바탕으로 고객이 방문 전에 확인하려는 정보를 정리합니다.' },
    { title: 'Liner Router · 결핍 진단 및 콘텐츠 처방', detail: '채널 텍스트와 상권 수요를 대조해 결핍 3개와 채널별 콘텐츠를 생성합니다.' },
  ];

  const CHANNEL_LABEL = { instagram: 'Instagram', naver_place: 'Naver Place', blog_faq: 'Blog FAQ' };
  const ENDPOINT_LABEL = {
    'search.web': 'Liner Search',
    'agents.search': 'Search Agent',
    'chat.completions': 'Router',
    'credits.balance': 'Credits',
  };

  /* ------------------------------------------------------------------ */
  /* 유틸                                                               */
  /* ------------------------------------------------------------------ */
  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const scoreColor = (score) => (score >= 70 ? '#059669' : score >= 50 ? '#d97706' : '#e11d48');
  const scoreBar = (score) => (score >= 70 ? 'bar-emerald' : score >= 50 ? 'bar-amber' : 'bar-rose');
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

  function channelTextOf(sample) {
    const labels = { instagram: '[Instagram]', naver_place: '[Naver Place]', blog: '[Blog]' };
    return Object.keys(labels)
      .map((key) => (sample.channels?.[key] ? `${labels[key]}\n${sample.channels[key]}` : ''))
      .filter(Boolean)
      .join('\n\n');
  }

  /* ------------------------------------------------------------------ */
  /* 입력                                                               */
  /* ------------------------------------------------------------------ */
  function syncCharCount() {
    els.charCount.textContent = (els.channelText.value || '').length.toLocaleString('ko-KR');
  }

  function setActiveSample(id) {
    state.activeSampleId = id;
    $$('.sample-btn', els.sampleList).forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.sampleId === id));
    });
  }

  function fillSample(sample) {
    els.location.value = sample.location || '';
    els.industry.value = sample.industry || '';
    els.storeName.value = sample.store_name || '';
    els.channelText.value = channelTextOf(sample);
    setActiveSample(sample.id);
    syncCharCount();
  }

  function setRunning(running) {
    state.running = running;
    els.runBtn.disabled = running;
    els.runLabel.textContent = running ? '진단 중…' : '진단 시작';
    els.runIcon.classList.toggle('animate-spin', running);
    $$('.sample-btn', els.sampleList).forEach((btn) => { btn.disabled = running; });
  }

  function showPanel(name) {
    ['empty', 'progress', 'results', 'error'].forEach((key) => {
      els[key].classList.toggle('hidden', key !== name);
    });
  }

  function showError(message) {
    els.errorDetail.textContent = message;
    showPanel('error');
  }

  function setHeaderBadge(mode, degraded) {
    els.modeBadge.className = 'badge ' + (mode === 'live' ? 'badge-live' : degraded ? 'badge-fallback' : 'badge-demo');
    els.modeBadge.textContent = mode === 'live'
      ? 'LIVE LINER API'
      : degraded ? 'DEMO FALLBACK' : 'DEMO MOCK MODE';
  }

  function setProgressMode(mode) {
    els.progressMode.className = 'badge ' + (mode === 'live' ? 'badge-live' : 'badge-demo');
    els.progressMode.textContent = mode === 'live' ? 'LIVE LINER API' : 'DEMO MOCK MODE';
  }

  /* ------------------------------------------------------------------ */
  /* 진행 표시                                                          */
  /* ------------------------------------------------------------------ */
  function renderStepList() {
    els.stepList.innerHTML = STEP_DEFS.map((step, index) => `
      <li class="step" data-step="${index + 1}" data-status="pending">
        <span class="step-dot">${index + 1}</span>
        <div class="min-w-0 flex-1">
          <div class="flex items-baseline justify-between gap-2">
            <p class="step-title">${esc(step.title)}</p>
            <span class="step-time" data-role="time"></span>
          </div>
          <p class="step-detail" data-role="detail">${esc(step.detail)}</p>
        </div>
      </li>`).join('');
  }

  function updateStep(index, status, detail, latency) {
    const node = $(`.step[data-step="${index}"]`, els.stepList);
    if (!node) return;
    node.dataset.status = status;
    const detailEl = $('[data-role="detail"]', node);
    if (detail) detailEl.textContent = detail;
    const timeEl = $('[data-role="time"]', node);
    if (status === 'done') {
      timeEl.textContent = latency != null ? fmtMs(latency) : '';
      const dot = $('.step-dot', node);
      dot.textContent = '✓';
    }
    if (status === 'running') {
      timeEl.textContent = '진행 중';
      const title = $('.step-title', node);
      if (!title.dataset.original) title.dataset.original = title.textContent;
    }
  }

  /* ------------------------------------------------------------------ */
  /* 실행                                                               */
  /* ------------------------------------------------------------------ */
  async function runAudit(payload) {
    if (state.running) return;
    setRunning(true);
    state.result = null;
    state.copyStore = [];
    els.location.value = payload.location ?? els.location.value;
    els.industry.value = payload.industry ?? els.industry.value;
    if (payload.channel_text != null) {
      els.channelText.value = payload.channel_text;
      syncCharCount();
    }
    renderStepList();
    showPanel('progress');
    setProgressMode('demo');

    try {
      const response = await fetch('/api/audit/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await describeHttpError(response));
      }
      await consumeStream(response);
    } catch (error) {
      showError(error.message || '네트워크 오류가 발생했습니다.');
    } finally {
      setRunning(false);
    }
  }

  async function describeHttpError(response) {
    let detail = `요청이 실패했습니다 (HTTP ${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) detail = body.detail[0].msg;
    } catch (_) { /* 본문이 JSON이 아닌 경우 기본 메시지 */ }
    return detail;
  }

  async function consumeStream(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let failed = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const handled = handleEvent(chunk);
        if (handled === 'error') failed = true;
      }
    }
    if (!state.result && !failed) showError('결과를 받지 못했습니다. 잠시 후 다시 시도해 주세요.');
  }

  function handleEvent(chunk) {
    let event = 'message';
    const dataLines = [];
    chunk.split('\n').forEach((line) => {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return null;
    let data;
    try { data = JSON.parse(dataLines.join('\n')); } catch (_) { return null; }

    if (event === 'open') {
      setProgressMode(data.mode === 'live' ? 'live' : 'demo');
      return null;
    }
    if (event === 'step') {
      updateStep(data.index, data.status, data.detail, data.latency_ms);
      return null;
    }
    if (event === 'result') {
      state.result = data;
      renderResult(data);
      return null;
    }
    if (event === 'error') {
      showError(data.detail || '진단 중 오류가 발생했습니다.');
      return 'error';
    }
    return null;
  }

  /* ------------------------------------------------------------------ */
  /* 결과 렌더링                                                        */
  /* ------------------------------------------------------------------ */
  function renderResult(r) {
    setHeaderBadge(r.mode, r.degraded);
    state.tab = (r.contents?.[0]?.channel) || 'instagram';

    const header = renderHeaderCard(r);
    const warnings = renderWarnings(r);
    const demand = renderDemand(r);
    const gaps = renderGaps(r);
    const contents = renderContents(r);
    const sources = renderSources(r);
    const pipeline = renderPipeline(r);

    els.results.innerHTML = header + warnings + demand + gaps + contents + sources + pipeline;
    showPanel('results');
    bindTabs(contents);
    bindCopyButtons();
    animateBars();
  }

  function renderHeaderCard(r) {
    const circumference = 2 * Math.PI * 50;
    const offset = circumference * (1 - (r.overall_score || 0) / 100);
    const color = scoreColor(r.overall_score || 0);
    const modeBadge = r.mode === 'live'
      ? '<span class="badge badge-live">LIVE LINER API</span>'
      : `<span class="badge badge-${r.degraded ? 'fallback' : 'demo'}">${r.degraded ? 'DEMO FALLBACK' : 'DEMO MOCK MODE'}</span>`;

    const tiles = (r.scores || []).map((item) => `
      <div class="score-tile">
        <div class="score-tile-top">
          <span>${esc(item.label)}</span>
          <b style="color:${scoreColor(item.score)}">${item.score}</b>
        </div>
        <div class="score-bar"><i class="${scoreBar(item.score)}" data-width="${item.score}%" style="width:0"></i></div>
        <p class="score-comment">${esc(item.comment)}</p>
      </div>`).join('');

    return `
    <section class="card p-5 sm:p-6 fade-up">
      <div class="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-1.5">
            ${modeBadge}
            <span class="badge badge-soft">${esc(r.input.industry)}</span>
            <span class="badge badge-soft">${(r.sources || []).length}개 출처 대조</span>
          </div>
          <h2 class="mt-2.5 text-[19px] font-bold leading-snug tracking-tight text-slate-900">${esc(r.input.store_name)}</h2>
          <p class="mt-1 text-[11.5px] text-slate-500">${esc(r.input.location)}</p>
          <p class="mt-3 max-w-xl text-[12.5px] leading-relaxed text-slate-600">${esc(r.summary)}</p>
          <p class="mt-2 text-[10.5px] text-slate-400">${esc((r.generated_at || '').replace('T', ' ').slice(0, 16))} 생성 · ${r.mode === 'live' ? '실시간 Liner API' : '가상 데이터 기반 데모'}</p>
        </div>
        <div class="flex shrink-0 items-center gap-4 sm:flex-col sm:items-center">
          <div class="gauge-wrap">
            <svg width="118" height="118" viewBox="0 0 118 118" class="-rotate-90" aria-hidden="true">
              <circle cx="59" cy="59" r="50" fill="none" stroke="#eef2f7" stroke-width="9"></circle>
              <circle cx="59" cy="59" r="50" fill="none" stroke="${color}" stroke-width="9" stroke-linecap="round"
                stroke-dasharray="${circumference.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"></circle>
            </svg>
            <div class="gauge-value">
              <b style="color:${color}">${r.overall_score}</b>
              <span>CHANNEL SCORE</span>
            </div>
          </div>
        </div>
      </div>
      <div class="mt-5 grid grid-cols-2 gap-2 lg:grid-cols-4">${tiles}</div>
    </section>`;
  }

  function renderWarnings(r) {
    if (!r.warnings?.length) return '';
    return `
      <div class="card card-warn p-4 fade-in">
        <div class="flex gap-2.5">
          <span class="text-[15px]">🔎</span>
          <div>
            <p class="text-[11.5px] font-semibold text-amber-900">참고</p>
            <ul class="mt-1 space-y-1 text-[11px] leading-relaxed text-amber-800">
              ${r.warnings.map((w) => `<li>· ${esc(w)}</li>`).join('')}
            </ul>
          </div>
        </div>
      </div>`;
  }

  function renderDemand(r) {
    const answer = (r.demand?.answer || '').trim();
    if (!answer) return '';
    const chips = (r.demand.references || []).map((ref) => `
      <a class="source-chip" href="${esc(ref.url)}" target="_blank" rel="noreferrer" title="${esc(ref.title)}">
        <img src="${esc(ref.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
        <span class="truncate max-w-[190px]">${esc(ref.hostname || ref.title)}</span>
      </a>`).join('');
    const queries = (r.queries || []).map((q) => `<span class="chip">🔍 ${esc(q)}</span>`).join('');
    return `
      <section class="card p-5 fade-up">
        <div class="section-head"><h3>상권 고객 수요 요약</h3><span class="hint">Liner Search Agent</span></div>
        <div class="content-body">${esc(answer)}</div>
        <div class="mt-3 flex flex-wrap gap-1.5">${queries}</div>
        ${chips ? `<div class="mt-3 flex flex-wrap gap-1.5">${chips}</div>` : ''}
      </section>`;
  }

  function renderGaps(r) {
    const gaps = r.gaps || [];
    if (!gaps.length) return '';
    const cards = gaps.map((gap, index) => `
      <article class="gap-card">
        <div class="flex items-start gap-3">
          <span class="gap-index">${String(index + 1).padStart(2, '0')}</span>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <h4 class="text-[13px] font-semibold text-slate-900">${esc(gap.title)}</h4>
              <span class="sev sev-${esc(gap.severity)}">${esc((gap.severity || '').toUpperCase())}</span>
              ${gap.dimension_label ? `<span class="chip">${esc(gap.dimension_label)}</span>` : ''}
            </div>
            <p class="mt-1.5 text-[11.5px] leading-relaxed text-slate-600">${esc(gap.reason)}</p>
            ${gap.demand_evidence ? `
              <div class="evidence">
                <b>상권 검색 수요 근거</b>${esc(gap.demand_evidence)}
              </div>` : ''}
            ${gap.sources?.length ? `
              <div class="mt-2 flex flex-wrap gap-1.5">
                ${gap.sources.map((src) => `
                  <a class="source-chip" href="${esc(src.url)}" target="_blank" rel="noreferrer" title="${esc(src.description || src.title)}">
                    <img src="${esc(src.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
                    <span class="truncate max-w-[210px]">${esc(src.title)}</span>
                    ${src.demo ? '<span class="text-[8.5px] font-bold text-amber-600">DEMO</span>' : ''}
                  </a>`).join('')}
              </div>` : ''}
            ${gap.fix_hint ? `<p class="mt-2 text-[10.8px] text-slate-500"><b class="text-slate-700">처방</b> · ${esc(gap.fix_hint)}</p>` : ''}
          </div>
        </div>
      </article>`).join('');
    return `
      <section class="fade-up">
        <div class="section-head">
          <h3>3대 정보 결핍</h3>
          <span class="hint">상권 검색 수요 대비 채널 누락 지점</span>
        </div>
        <div class="space-y-2.5">${cards}</div>
      </section>`;
  }

  function renderContents(r) {
    const contents = r.contents || [];
    if (!contents.length) return '';
    state.copyStore = contents.map((c) => c.body || '');

    const tabs = contents.map((c, index) => `
      <button class="tab-btn" role="tab" data-tab="${esc(c.channel)}"
        aria-selected="${index === 0 ? 'true' : 'false'}">${esc(CHANNEL_LABEL[c.channel] || c.channel)}</button>`).join('');

    const panels = contents.map((c, index) => `
      <div data-panel="${esc(c.channel)}" class="${index === 0 ? '' : 'hidden'}">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h4 class="text-[13.5px] font-semibold leading-snug text-slate-900">${esc(c.title)}</h4>
            <p class="mt-1 text-[11.5px] leading-relaxed text-slate-500">${esc(c.summary)}</p>
          </div>
          <button class="copy-btn shrink-0" data-copy="${index}" type="button">
            <svg class="h-3 w-3" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M7 3.5A1.5 1.5 0 018.5 2h5A1.5 1.5 0 0115 3.5v9a1.5 1.5 0 01-1.5 1.5h-5A1.5 1.5 0 017 12.5v-9z"/><path d="M4 6.5A1.5 1.5 0 012.5 8v8.5A1.5 1.5 0 004 18h6a1.5 1.5 0 001.5-1.5V16h-3A3.5 3.5 0 015 12.5v-6z"/></svg>
            복사
          </button>
        </div>
        <div class="content-body-area mt-3"><div class="content-body">${esc(c.body)}</div></div>
        ${c.hashtags?.length ? `<div class="mt-2 flex flex-wrap gap-1">${c.hashtags.map((t) => `<span class="chip">${esc(t)}</span>`).join('')}</div>` : ''}
        ${c.caution ? `
          <div class="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-[10.5px] leading-relaxed text-amber-800 ring-1 ring-inset ring-amber-200/70">
            <b>주의</b> · ${esc(c.caution)}
          </div>` : ''}
      </div>`).join('');

    return `
      <section class="card p-5 fade-up">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="section-head mb-0"><h3>채널별 보완 콘텐츠</h3><span class="hint">복사해서 바로 발행</span></div>
          <div class="flex gap-1 rounded-xl bg-slate-50 p-1 ring-1 ring-inset ring-slate-200/70" role="tablist">${tabs}</div>
        </div>
        <div class="mt-4">${panels}</div>
      </section>`;
  }

  function renderSources(r) {
    const sources = r.sources || [];
    if (!sources.length) return '';
    const rows = sources.map((src) => `
      <a href="${esc(src.url)}" target="_blank" rel="noreferrer"
         class="flex gap-2 rounded-lg border border-slate-200/80 p-2 transition hover:border-slate-300 hover:bg-slate-50">
        <img src="${esc(src.faviconUrl)}" alt="" class="mt-0.5 h-3.5 w-3.5 shrink-0 rounded" loading="lazy" referrerpolicy="no-referrer" />
        <span class="min-w-0">
          <span class="block truncate text-[11px] font-medium text-slate-800">${esc(src.title)}</span>
          <span class="mt-0.5 flex items-center gap-1.5 text-[9.5px] text-slate-400">
            <span>${esc(src.hostname)}</span>
            ${src.date ? `<span>· ${esc(src.date)}</span>` : ''}
            ${src.demo ? '<span class="font-bold text-amber-600">· DEMO</span>' : ''}
          </span>
        </span>
      </a>`).join('');
    return `
      <section class="card p-5 fade-up">
        <div class="section-head"><h3>수집 출처</h3><span class="hint">Liner Search + Search Agent · ${sources.length}건</span></div>
        <div class="grid gap-1.5 sm:grid-cols-2">${rows}</div>
      </section>`;
  }

  function renderPipeline(r) {
    const pipeline = r.pipeline || {};
    const calls = pipeline.calls || [];
    const totals = pipeline.totals || {};
    const rows = calls.map((call) => `
      <div class="call-row">
        <span class="mono truncate text-slate-700">${esc(ENDPOINT_LABEL[call.endpoint] || call.endpoint)}</span>
        <span class="${call.mode === 'live' ? 'text-emerald-600' : 'text-amber-600'} font-medium">${call.mode === 'live' ? 'LIVE' : 'DEMO'}</span>
        <span class="${call.status === 'ok' ? 'text-emerald-600' : call.status === 'fallback' ? 'text-amber-600' : 'text-rose-600'}">${esc(call.status)}</span>
        <span class="mono text-slate-500">${call.mode === 'demo' ? '—' : fmtMs(call.latency_ms)}</span>
        <span class="mono truncate text-[9.5px] text-slate-400">${esc(call.request_id || call.detail || '')}</span>
      </div>`).join('');

    const stepRows = (pipeline.steps || []).map((step) => `
      <div class="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-3 py-1.5">
        <span class="text-[10.8px] text-slate-600"><b class="text-slate-800">${step.index}.</b> ${esc(step.title.split(' · ')[0])}</span>
        <span class="mono text-[10px] text-slate-500">${fmtMs(step.latency_ms)}</span>
      </div>`).join('');

    const raw = esc(JSON.stringify({ ...r, input: { ...r.input, channel_text: r.input.channel_text?.slice(0, 400) } }, null, 2));

    return `
      <section class="card p-5 fade-up">
        <div class="section-head"><h3>API 파이프라인 투명성</h3><span class="hint">어떤 Liner API가 몇 번 호출되었는지</span></div>
        <div class="grid grid-cols-3 gap-2">
          <div class="rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
            <p class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">API 호출</p>
            <p class="mt-1 text-[17px] font-bold tabular-nums">${totals.total ?? calls.length}<span class="text-[10px] font-medium text-slate-400">회</span></p>
          </div>
          <div class="rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
            <p class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">실행 모드</p>
            <p class="mt-1 text-[13px] font-bold ${pipeline.mode === 'live' ? 'text-emerald-600' : 'text-amber-600'}">${pipeline.mode === 'live' ? 'LIVE' : 'DEMO'}</p>
          </div>
          <div class="rounded-xl bg-slate-50 p-3 ring-1 ring-inset ring-slate-200/70">
            <p class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">총 소요</p>
            <p class="mt-1 text-[17px] font-bold tabular-nums">${fmtMs(pipeline.latency_ms || 0)}</p>
          </div>
        </div>
        <div class="mt-3 space-y-1">${stepRows}</div>
        <div class="mt-4 overflow-hidden rounded-xl border border-slate-200/80">
          <div class="call-row head">
            <span>ENDPOINT</span><span>MODE</span><span>STATUS</span><span>LATENCY</span><span>REQUEST ID</span>
          </div>
          ${rows}
        </div>
        <p class="mt-2 text-[9.5px] text-slate-400">model ${esc(pipeline.model || '')} · base ${esc(pipeline.base_url || '')} · API 키는 응답·로그에 포함되지 않습니다.</p>
        <details class="raw mt-3">
          <summary>원시 결과 JSON 보기</summary>
          <pre class="mono mt-2 max-h-72 overflow-auto rounded-xl bg-slate-900 p-3 text-[9.5px] leading-relaxed text-slate-200">${raw}</pre>
        </details>
      </section>`;
  }

  /* ------------------------------------------------------------------ */
  /* 인터랙션                                                           */
  /* ------------------------------------------------------------------ */
  function bindTabs() {
    const tabs = $$('.tab-btn', els.results);
    const panels = $$('[data-panel]', els.results);
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((t) => t.setAttribute('aria-selected', String(t === tab)));
        panels.forEach((panel) => panel.classList.toggle('hidden', panel.dataset.panel !== tab.dataset.tab));
      });
    });
  }

  function bindCopyButtons() {
    $$('.copy-btn', els.results).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const text = state.copyStore[Number(btn.dataset.copy)] || '';
        const ok = await copyText(text);
        const original = btn.innerHTML;
        btn.classList.toggle('copied', ok);
        btn.textContent = ok ? '복사됨' : '복사 실패';
        setTimeout(() => { btn.innerHTML = original; btn.classList.remove('copied'); }, 1600);
      });
    });
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(area);
      return ok;
    } catch (_) {
      return false;
    }
  }

  function animateBars() {
    requestAnimationFrame(() => {
      $$('.score-bar > i', els.results).forEach((bar) => {
        bar.style.width = bar.dataset.width || '0';
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 이벤트 등록                                                        */
  /* ------------------------------------------------------------------ */
  els.channelText.addEventListener('input', () => { syncCharCount(); setActiveSample(null); });
  [els.location, els.industry, els.storeName].forEach((field) => {
    field.addEventListener('input', () => setActiveSample(null));
  });

  $$('.sample-btn', els.sampleList).forEach((btn) => {
    btn.addEventListener('click', () => {
      const sample = SAMPLES.find((s) => s.id === btn.dataset.sampleId);
      if (!sample) return;
      fillSample(sample);
      runAudit({
        sample_id: sample.id,
        location: sample.location,
        industry: sample.industry,
        store_name: sample.store_name,
        channel_text: channelTextOf(sample),
      });
    });
  });

  els.form.addEventListener('submit', (event) => {
    event.preventDefault();
    const payload = {
      location: els.location.value.trim(),
      industry: els.industry.value.trim(),
      store_name: els.storeName.value.trim(),
      channel_text: els.channelText.value.trim(),
      sample_id: state.activeSampleId || null,
    };
    const apiKey = els.apiKey.value.trim();
    if (apiKey) payload.api_key = apiKey;
    if (!payload.location || !payload.industry) {
      showError('상권 위치와 업종을 입력해 주세요.');
      els.location.focus();
      return;
    }
    if (!payload.channel_text) {
      showError('진단할 외부 채널 텍스트를 입력해 주세요.');
      els.channelText.focus();
      return;
    }
    runAudit(payload);
  });

  syncCharCount();
})();
