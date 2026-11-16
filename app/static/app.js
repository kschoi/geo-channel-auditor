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
    collectHint: $('#collect-hint'),
    channelSummaryMeta: $('#channel-summary-meta'),
    channelDisclosure: $('#channel-disclosure'),
    runBtn: $('#run-btn'),
    runLabel: $('#run-btn-label'),
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
    { title: 'Liner Search · 상권 수요 수집', detail: '상권 위치·업종 검색과 공개 채널 자동 수집을 함께 수행합니다.' },
    { title: 'Liner Search Agent · 고객 기대 정보 분석', detail: '수집한 출처를 바탕으로 고객이 방문 전에 확인하려는 정보를 정리합니다.' },
    { title: 'Liner Router · 결핍 진단 및 콘텐츠 처방', detail: '채널 텍스트·인용 비중을 대조해 결핍 3개와 채널 우선순위, 콘텐츠를 생성합니다.' },
  ];

  const CHANNEL_LABEL = { instagram: 'Instagram', naver_place: 'Naver Place', blog_faq: 'Blog FAQ' };
  const SEVERITY_LABEL = { high: '심각', medium: '보통', low: '낮음' };
  const ENDPOINT_LABEL = {
    'search.web': 'Liner Search',
    'agents.search': 'Search Agent',
    'chat.completions': 'Router',
    'credits.balance': 'Credits',
  };
  /* 인용 채널 비중 바 색 — 1순위는 포인트 컬러, 나머지는 절제된 그레이 스케일 */
  const CITE_COLORS = ['#059669', '#111827', '#4b5563', '#9ca3af', '#d1d5db'];

  /* ------------------------------------------------------------------ */
  /* 유틸                                                               */
  /* ------------------------------------------------------------------ */
  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const scoreBar = (score) => (score >= 70 ? 'bar-good' : score >= 50 ? 'bar-mid' : 'bar-low');
  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
  const fmtDate = (value) => String(value || '').replace('T', ' ').slice(0, 16);
  const fmtPct = (value) => (Number(value) || 0).toFixed(1);

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
  const CHANNEL_MIN_CHARS = 80;

  function syncChannelMeta() {
    const length = (els.channelText.value || '').trim().length;
    els.charCount.textContent = length.toLocaleString('ko-KR');
    if (length >= CHANNEL_MIN_CHARS) {
      els.collectHint.textContent = '직접 입력한 채널 문구로 진단합니다';
      els.collectHint.classList.remove('is-empty');
      els.channelSummaryMeta.textContent = '직접 입력';
    } else if (length > 0) {
      els.collectHint.textContent = '입력 문구가 짧아 공개 채널 자동 수집을 함께 사용합니다';
      els.collectHint.classList.remove('is-empty');
      els.channelSummaryMeta.textContent = '직접 + 자동';
    } else {
      els.collectHint.textContent = '매장명·상권 위치만으로 실행됩니다 · 공개 채널 자동 수집';
      els.collectHint.classList.add('is-empty');
      els.channelSummaryMeta.textContent = '자동 수집';
    }
  }

  function setActiveSample(id) {
    state.activeSampleId = id;
    $$('.sample-chip', els.sampleList).forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.sampleId === id));
    });
  }

  function fillSample(sample) {
    els.location.value = sample.location || '';
    els.industry.value = sample.industry || '';
    els.storeName.value = sample.store_name || '';
    els.channelText.value = channelTextOf(sample);
    setActiveSample(sample.id);
    syncChannelMeta();
  }

  function setRunning(running) {
    state.running = running;
    els.runBtn.disabled = running;
    els.runBtn.classList.toggle('is-loading', running);
    els.runLabel.textContent = running ? '진단 중' : '진단 시작';
    $$('.sample-chip', els.sampleList).forEach((btn) => { btn.disabled = running; });
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

  function modeBadgeHtml(mode, degraded) {
    return mode === 'live'
      ? '<span class="badge badge-live">LIVE LINER API</span>'
      : `<span class="badge badge-${degraded ? 'fallback' : 'demo'}">${degraded ? 'DEMO FALLBACK' : 'DEMO MOCK MODE'}</span>`;
  }

  /* ------------------------------------------------------------------ */
  /* 진행 표시                                                          */
  /* ------------------------------------------------------------------ */
  function renderStepList() {
    els.stepList.innerHTML = STEP_DEFS.map((step, index) => `
      <li class="step" data-step="${index + 1}" data-status="pending">
        <span class="step-dot">${index + 1}</span>
        <div class="step-main">
          <div class="step-row">
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
      $('.step-dot', node).textContent = '✓';
    }
    if (status === 'running') timeEl.textContent = '진행 중';
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
    }
    syncChannelMeta();
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
    const firstRecommended = (r.contents || []).find((c) => c.priority_badge);
    state.tab = firstRecommended?.channel || r.contents?.[0]?.channel || 'instagram';

    els.results.innerHTML = [
      renderHeaderCard(r),
      renderWarnings(r),
      renderCollectedChannel(r),
      renderCitation(r),
      renderRecommendations(r),
      renderDemand(r),
      renderGaps(r),
      renderContents(r),
      renderSources(r),
      renderPipeline(r),
    ].join('');
    showPanel('results');
    bindTabs();
    bindCopyButtons();
    animateBars();
  }

  function renderHeaderCard(r) {
    const score = r.overall_score || 0;
    const collected = r.collected_channel || {};
    const tiles = (r.scores || []).map((item) => `
      <div class="score-tile">
        <p class="score-tile-label">${esc(item.label)}</p>
        <p class="score-tile-value">${item.score}</p>
        <div class="score-bar"><i class="${scoreBar(item.score)}" data-width="${item.score}%"></i></div>
        <p class="score-comment">${esc(item.comment)}</p>
      </div>`).join('');

    return `
    <section class="card report fade-up">
      <div class="report-top">
        <div class="report-main">
          <div class="meta">
            ${modeBadgeHtml(r.mode, r.degraded)}
            <span class="meta-item">${esc(r.input.industry)}</span>
            <span class="meta-item">채널 ${esc(collected.label || '직접 입력')}</span>
            <span class="meta-item">출처 ${(r.sources || []).length}건</span>
          </div>
          <h2 class="report-title">${esc(r.input.store_name)}</h2>
          <p class="report-loc">${esc(r.input.location)}</p>
          <p class="report-summary">${esc(r.summary)}</p>
          <p class="report-time">${esc(fmtDate(r.generated_at))} 기준 · ${r.mode === 'live' ? '실시간 Liner API' : '가상 데이터 기반 데모'}</p>
        </div>
        <div class="score-block">
          <p class="score-label">채널 점수</p>
          <p class="score-value">${score}<span>/100</span></p>
          <div class="score-bar lg"><i class="${scoreBar(score)}" data-width="${score}%"></i></div>
        </div>
      </div>
      <div class="score-grid">${tiles}</div>
    </section>`;
  }

  function renderWarnings(r) {
    if (!r.warnings?.length) return '';
    return `
      <div class="note fade-in">
        <p class="note-title">참고</p>
        <ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>
      </div>`;
  }

  /* 수집된 매장 채널 현황 (manual / auto / hybrid) */
  function renderCollectedChannel(r) {
    const collected = r.collected_channel;
    if (!collected) return '';
    const modeClass = collected.mode === 'manual'
      ? 'collect-manual'
      : collected.mode === 'hybrid' ? 'collect-hybrid' : 'collect-auto';
    const queries = (collected.queries || [])
      .map((query) => `<span class="chip chip-mono">${esc(query)}</span>`).join('');
    const breakdown = (collected.breakdown || [])
      .map((item) => `<span class="chip">${esc(item.label)} ${item.count}</span>`).join('');
    const counts = [];
    if (collected.source_count > 0) counts.push(`자동 수집 출처 ${collected.source_count}건`);
    counts.push(`입력 문구 ${(collected.input_chars || 0).toLocaleString('ko-KR')}자`);
    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">수집된 매장 채널</h3>
          <span class="collect-badge ${modeClass}">${esc(collected.label)}</span>
        </div>
        <p class="body-text sm">${esc(collected.summary)}</p>
        ${queries ? `<div class="chips">${queries}</div>` : ''}
        ${breakdown ? `<div class="chips">${breakdown}</div>` : ''}
        <p class="fineprint">${esc(counts.join(' · '))}</p>
      </section>`;
  }

  /* Liner 인용 출처 분석 — Liner Search + Liner Search Agent가 인용한 출처만 집계한다. */
  function renderCitation(r) {
    const citation = r.citation_analysis;
    const channels = citation?.channels || [];
    if (!citation || !channels.length) return '';
    const segments = channels
      .map((channel, index) => ({ channel, color: CITE_COLORS[index % CITE_COLORS.length] }))
      .filter((item) => item.channel.count > 0)
      .map((item) => `<i style="width:${item.channel.share_pct}%;background:${item.color}" title="${esc(item.channel.label)} ${fmtPct(item.channel.share_pct)}%"></i>`)
      .join('');
    const rows = channels.map((channel, index) => {
      const color = CITE_COLORS[index % CITE_COLORS.length];
      const domains = (channel.domains || []).join(' · ');
      return `
        <li class="cite-row ${channel.count ? '' : 'is-zero'}">
          <span class="cite-dot" style="background:${channel.count ? color : '#e5e7eb'}"></span>
          <span class="cite-label">${esc(channel.label)}</span>
          <span class="cite-count">${channel.count}건</span>
          <span class="cite-pct">${fmtPct(channel.share_pct)}%</span>
          <span class="cite-domains">${domains ? esc(domains) : '수집 없음'}</span>
        </li>`;
    }).join('');
    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">Liner 인용 출처 분석</h3>
          <span class="section-hint">Liner 검색·에이전트 인용 · ${citation.total}건</span>
        </div>
        <p class="body-text sm">${esc(citation.summary)}</p>
        <div class="prop-bar" role="img" aria-label="채널별 인용 비중">${segments}</div>
        <ul class="cite-list">${rows}</ul>
      </section>`;
  }

  /* 채널 우선순위 추천 */
  function renderRecommendations(r) {
    const recommendations = r.recommended_channels || [];
    if (!recommendations.length) return '';
    const [top, ...rest] = recommendations;
    const rows = rest.map((item) => `
      <li class="rec-row">
        <span class="rec-num">${item.rank}</span>
        <div class="min-w-0">
          <div class="rec-row-head">
            <span class="rec-row-label">${esc(item.label)}</span>
            <span class="rec-row-share">${fmtPct(item.share_pct)}%</span>
          </div>
          <p class="rec-reason sm">${esc(item.reason)}</p>
        </div>
      </li>`).join('');
    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">채널 우선순위 추천</h3>
          <span class="section-hint">Liner 인용 비중 기준</span>
        </div>
        <div class="rec-hero">
          <div class="rec-hero-head">
            <span class="rec-badge">1순위 추천</span>
            <span class="rec-hero-label">${esc(top.label)}</span>
          </div>
          <p class="rec-metric">Liner 인용 비중 ${fmtPct(top.share_pct)}% · ${top.count}건</p>
          <p class="rec-reason">${esc(top.reason)}</p>
        </div>
        ${rows ? `<ul class="rec-list">${rows}</ul>` : ''}
      </section>`;
  }

  function renderDemand(r) {
    const answer = (r.demand?.answer || '').trim();
    if (!answer) return '';
    const chips = (r.demand.references || []).map((ref) => `
      <a class="source-chip" href="${esc(ref.url)}" target="_blank" rel="noreferrer" title="${esc(ref.title)}">
        <img src="${esc(ref.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
        <span class="truncate">${esc(ref.hostname || ref.title)}</span>
      </a>`).join('');
    const queries = (r.queries || []).map((q) => `<span class="chip chip-mono">${esc(q)}</span>`).join('');
    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">상권 고객 수요</h3>
          <span class="section-hint">Liner Search Agent</span>
        </div>
        <p class="body-text">${esc(answer)}</p>
        ${queries ? `<div class="chips">${queries}</div>` : ''}
        ${chips ? `<div class="chips">${chips}</div>` : ''}
      </section>`;
  }

  function renderGaps(r) {
    const gaps = r.gaps || [];
    if (!gaps.length) return '';
    const cards = gaps.map((gap, index) => {
      const severity = String(gap.severity || '').toLowerCase();
      const severityLabel = SEVERITY_LABEL[severity] || severity.toUpperCase();
      return `
      <article class="card gap">
        <div class="gap-head">
          <span class="gap-num">${String(index + 1).padStart(2, '0')}</span>
          <div class="gap-body">
            <div class="gap-title-row">
              <h4 class="gap-title">${esc(gap.title)}</h4>
              <span class="sev sev-${esc(severity)}">${esc(severityLabel)}</span>
              ${gap.dimension_label ? `<span class="chip">${esc(gap.dimension_label)}</span>` : ''}
            </div>
            <p class="gap-reason">${esc(gap.reason)}</p>
            ${gap.demand_evidence ? `
              <div class="evidence">
                <b>상권 검색 수요 근거</b>${esc(gap.demand_evidence)}
              </div>` : ''}
            ${gap.sources?.length ? `
              <div class="chips">
                ${gap.sources.map((src) => `
                  <a class="source-chip" href="${esc(src.url)}" target="_blank" rel="noreferrer" title="${esc(src.description || src.title)}">
                    <img src="${esc(src.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
                    <span class="truncate">${esc(src.title)}</span>
                    ${src.demo ? '<span class="demo-flag">DEMO</span>' : ''}
                  </a>`).join('')}
              </div>` : ''}
            ${gap.fix_hint ? `<p class="gap-fix"><b>처방</b> · ${esc(gap.fix_hint)}</p>` : ''}
          </div>
        </div>
      </article>`;
    }).join('');

    return `
      <section class="fade-up">
        <div class="section-head">
          <h3 class="section-title">3대 정보 결핍</h3>
          <span class="section-hint">상권 검색 수요 대비 채널 누락 지점</span>
        </div>
        <div class="gap-list">${cards}</div>
      </section>`;
  }

  function renderContents(r) {
    const contents = r.contents || [];
    if (!contents.length) return '';
    state.copyStore = contents.map((c) => c.body || '');
    const active = contents.some((c) => c.channel === state.tab) ? state.tab : contents[0].channel;

    const tabs = contents.map((c) => `
      <button class="tab" role="tab" data-tab="${esc(c.channel)}"
        aria-selected="${c.channel === active ? 'true' : 'false'}">
        <span>${esc(CHANNEL_LABEL[c.channel] || c.channel)}</span>
        ${c.priority_badge ? `<span class="tab-badge">${esc(c.priority_badge)}</span>` : ''}
      </button>`).join('');

    const panels = contents.map((c) => `
      <div data-panel="${esc(c.channel)}" class="${c.channel === active ? '' : 'hidden'}">
        <div class="panel-head-row">
          <div class="min-w-0">
            <h4 class="content-title">${esc(c.title)}</h4>
            <p class="content-summary">${esc(c.summary)}</p>
          </div>
          <button class="copy-btn" data-copy="${contents.indexOf(c)}" type="button">
            <svg width="11" height="11" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <rect x="7" y="2.5" width="9.5" height="11" rx="1.6" stroke="currentColor" stroke-width="1.4" />
              <path d="M13 16.5H4.9A1.4 1.4 0 0 1 3.5 15.1V7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
            복사
          </button>
        </div>
        ${c.priority_reason ? `
          <p class="content-priority">
            <span class="rec-badge sm">${esc(c.priority_badge || '추천')}</span>
            <span>${esc(c.priority_reason)}</span>
          </p>` : ''}
        <div class="content-area"><div class="content-text">${esc(c.body)}</div></div>
        ${c.hashtags?.length ? `<div class="tags">${c.hashtags.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
        ${c.caution ? `<p class="caution"><b>주의</b> · ${esc(c.caution)}</p>` : ''}
      </div>`).join('');

    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">채널별 보완 콘텐츠</h3>
          <span class="section-hint">복사해서 바로 발행</span>
        </div>
        <div class="tabs" role="tablist">${tabs}</div>
        ${panels}
      </section>`;
  }

  function renderSources(r) {
    const sources = r.sources || [];
    if (!sources.length) return '';
    const rows = sources.map((src) => `
      <a href="${esc(src.url)}" target="_blank" rel="noreferrer" class="source-row">
        <img src="${esc(src.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
        <span class="min-w-0">
          <span class="source-title">${esc(src.title)}</span>
          <span class="source-meta">${esc(src.hostname)}${src.date ? ` · ${esc(src.date)}` : ''}${src.demo ? ' · DEMO' : ''}</span>
        </span>
      </a>`).join('');
    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">수집 출처</h3>
          <span class="section-hint">Liner Search + Search Agent · ${sources.length}건</span>
        </div>
        <div class="source-list">${rows}</div>
      </section>`;
  }

  function renderPipeline(r) {
    const pipeline = r.pipeline || {};
    const calls = pipeline.calls || [];
    const totals = pipeline.totals || {};
    const rows = calls.map((call) => `
      <div class="call-row">
        <span class="mono truncate">${esc(ENDPOINT_LABEL[call.endpoint] || call.endpoint)}</span>
        <span class="mono ${call.mode === 'live' ? 'mode-live' : 'mode-demo'}">${call.mode === 'live' ? 'LIVE' : 'DEMO'}</span>
        <span class="mono status-${esc(call.status)}">${esc(call.status)}</span>
        <span class="mono">${call.mode === 'demo' ? '—' : fmtMs(call.latency_ms)}</span>
        <span class="mono truncate">${esc(call.request_id || call.detail || '')}</span>
      </div>`).join('');

    const stepRows = (pipeline.steps || []).map((step) => `
      <div class="pipe-step">
        <span><b>${step.index}.</b> ${esc(step.title.split(' · ')[0])}</span>
        <span class="mono">${fmtMs(step.latency_ms)}</span>
      </div>`).join('');

    const raw = esc(JSON.stringify({ ...r, input: { ...r.input, channel_text: r.input.channel_text?.slice(0, 400) } }, null, 2));
    const isLive = pipeline.mode === 'live';

    return `
      <section class="card pad fade-up">
        <div class="section-head">
          <h3 class="section-title">파이프라인</h3>
          <span class="section-hint">어떤 Liner API가 몇 번 호출되었는지</span>
        </div>
        <div class="stat-grid">
          <div class="stat">
            <p class="stat-label">API 호출</p>
            <p class="stat-value">${totals.total ?? calls.length}<span>회</span></p>
          </div>
          <div class="stat">
            <p class="stat-label">실행 모드</p>
            <p class="stat-value ${isLive ? 'is-live' : 'is-demo'}">${isLive ? 'LIVE' : 'DEMO'}</p>
          </div>
          <div class="stat">
            <p class="stat-label">총 소요</p>
            <p class="stat-value">${fmtMs(pipeline.latency_ms || 0)}</p>
          </div>
        </div>
        <div class="pipe-steps">${stepRows}</div>
        <div class="table">
          <div class="call-row head">
            <span>Endpoint</span><span>Mode</span><span>Status</span><span>Latency</span><span>Request</span>
          </div>
          ${rows}
        </div>
        <p class="fineprint">model ${esc(pipeline.model || '')} · base ${esc(pipeline.base_url || '')} · API 키는 응답·로그에 포함되지 않습니다.</p>
        <details class="raw">
          <summary>원시 결과 JSON 보기</summary>
          <pre class="mono raw-json">${raw}</pre>
        </details>
      </section>`;
  }

  /* ------------------------------------------------------------------ */
  /* 인터랙션                                                           */
  /* ------------------------------------------------------------------ */
  function bindTabs() {
    const tabs = $$('.tab', els.results);
    const panels = $$('[data-panel]', els.results);
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        state.tab = tab.dataset.tab;
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
  els.channelText.addEventListener('input', () => { syncChannelMeta(); setActiveSample(null); });
  [els.location, els.industry, els.storeName].forEach((field) => {
    field.addEventListener('input', () => setActiveSample(null));
  });

  $$('.sample-chip', els.sampleList).forEach((btn) => {
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
      // 비워 보내면 서버가 공개 채널을 자동 수집한다.
      channel_text: els.channelText.value.trim(),
      auto_collect: true,
      sample_id: state.activeSampleId || null,
    };
    const apiKey = els.apiKey.value.trim();
    if (apiKey) payload.api_key = apiKey;
    if (!payload.location || !payload.industry) {
      showError('상권 위치와 업종을 입력해 주세요.');
      els.location.focus();
      return;
    }
    runAudit(payload);
  });

  syncChannelMeta();
})();
