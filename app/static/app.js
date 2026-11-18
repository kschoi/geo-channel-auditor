/* Geo Channel Auditor — 프런트엔드 로직 (외부 프레임워크 없음) */
(() => {
  'use strict';

  const SAMPLES = JSON.parse(document.getElementById('samples-data')?.textContent || '[]');
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const els = {
    form: $('#audit-form'),
    query: $('#query'),
    location: $('#location'),
    industry: $('#industry'),
    storeName: $('#store_name'),
    channelText: $('#channel_text'),
    apiKey: $('#api_key'),
    charCount: $('#char-count'),
    collectHint: $('#collect-hint'),
    runBtn: $('#run-btn'),
    runLabel: $('#run-btn-label'),
    sampleList: $('#sample-list'),
    toggleAdvanced: $('#toggle-advanced'),
    toggleApi: $('#toggle-api'),
    advanced: $('#advanced'),
    apiPanel: $('#api-panel'),
    progress: $('#progress'),
    stepList: $('#step-list'),
    progressHint: $('#progress-hint'),
    cancelBtn: $('#cancel-btn'),
    results: $('#results'),
    error: $('#error'),
    errorDetail: $('#error-detail'),
    modeBadge: $('#mode-badge'),
    modeLabel: $('#mode-label'),
  };

  const state = { running: false, result: null, tab: 'instagram', copyStore: [], controller: null };

  /* 진행 단계는 짧은 제목만 노출한다. 세부 수치는 결과의 '자세히 보기'에 있다. */
  const STEP_DEFS = ['매장 자료 검색', '검색 자료 확인', '항목별 근거 정리'];

  const CHANNEL_LABEL = { instagram: '인스타그램', naver_place: '네이버 플레이스', blog_faq: '블로그 FAQ' };
  const CITE_COLORS = ['#3182f6', '#7aa7f0', '#a9c6f3', '#cddcf3', '#dfe4ea'];
  const ENDPOINT_LABEL = {
    'search.web': 'Search',
    'agents.search': 'Search Agent',
    'chat.completions': 'Router',
    'credits.balance': 'Credits',
  };
  const CHANNEL_MIN_CHARS = 80;

  /* ------------------------------------------------------------------ */
  /* 유틸                                                               */
  /* ------------------------------------------------------------------ */
  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const fmtMs = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
  const fmtPct = (value) => (Number(value) || 0).toFixed(1);
  const barClass = (score) => (score >= 70 ? '' : score >= 50 ? 'is-mid' : 'is-low');

  function channelTextOf(sample) {
    const labels = { instagram: '[Instagram]', naver_place: '[Naver Place]', blog: '[Blog]' };
    return Object.keys(labels)
      .map((key) => (sample.channels?.[key] ? `${labels[key]}\n${sample.channels[key]}` : ''))
      .filter(Boolean)
      .join('\n\n');
  }

  /* ------------------------------------------------------------------ */
  /* 화면 상태                                                          */
  /* ------------------------------------------------------------------ */
  function showPanel(name) {
    ['progress', 'results', 'error'].forEach((key) => {
      els[key].classList.toggle('hidden', key !== name);
    });
  }

  function hidePanels() {
    ['progress', 'results', 'error'].forEach((key) => els[key].classList.add('hidden'));
  }

  function showError(message) {
    els.errorDetail.textContent = message;
    showPanel('error');
    els.error.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function setRunning(running) {
    state.running = running;
    els.form.setAttribute('aria-busy', String(running));
    els.runBtn.disabled = running;
    els.runBtn.classList.toggle('is-loading', running);
    els.runLabel.textContent = running ? '진단 중' : '진단 시작하기';
    $$('.pill', els.sampleList).forEach((btn) => { btn.disabled = running; });
  }

  function setMode(mode, degraded = false) {
    const live = mode === 'live';
    els.modeBadge.dataset.mode = degraded ? 'fallback' : live ? 'live' : 'demo';
    els.modeLabel.textContent = degraded ? (live ? 'LIVE · 일부 실패' : 'DEMO FALLBACK') : live ? 'LIVE' : 'API 키 필요';
  }

  function togglePanel(button, panel) {
    const open = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(open));
    panel.classList.toggle('hidden', !open);
  }

  function syncChannelMeta() {
    const length = (els.channelText.value || '').trim().length;
    els.charCount.textContent = length.toLocaleString('ko-KR');
    if (length >= CHANNEL_MIN_CHARS) {
      els.collectHint.textContent = '입력한 설명을 검색 자료 정리에 참고해요.';
    } else if (length > 0) {
      els.collectHint.textContent = '입력한 설명을 검색 자료 정리에 참고해요.';
    } else {
      els.collectHint.textContent = '비워두면 공개 채널을 자동으로 찾아 진단해요.';
    }
  }

  /* ------------------------------------------------------------------ */
  /* 진행 표시                                                          */
  /* ------------------------------------------------------------------ */
  function renderStepList() {
    els.stepList.innerHTML = STEP_DEFS.map((title, index) => `
      <li class="step" data-step="${index + 1}" data-status="pending">
        <span class="step-dot">${index + 1}</span>
        <span class="step-title">${esc(title)}</span>
        <span class="step-time" data-role="time"></span>
      </li>`).join('');
  }

  function updateStep(index, status, latency) {
    const node = $(`.step[data-step="${index}"]`, els.stepList);
    if (!node) return;
    node.dataset.status = status;
    if (status === 'done') {
      $('[data-role="time"]', node).textContent = latency != null ? fmtMs(latency) : '';
      $('.step-dot', node).textContent = '✓';
    } else if (status === 'running') {
      $('[data-role="time"]', node).textContent = '';
    }
  }

  /* ------------------------------------------------------------------ */
  /* 실행                                                               */
  /* ------------------------------------------------------------------ */
  async function runAudit(payload) {
    if (state.running) return;
    window.CardStudio?.cancel();
    setRunning(true);
    state.controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; state.controller?.abort(); }, 420000);
    els.progressHint.textContent = '공개 정보를 확인하고 있어요.';
    const slowHint = setTimeout(() => {
      els.progressHint.textContent = '아직 분석 중이에요. 검색 자료 정리까지 몇 분 걸릴 수 있어요.';
    }, 20000);
    state.result = null;
    state.copyStore = [];
    if (payload.query != null) els.query.value = payload.query;
    if (payload.channel_text != null) els.channelText.value = payload.channel_text;
    syncChannelMeta();
    renderStepList();
    showPanel('progress');
    els.progress.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    try {
      const response = await fetch('/api/audit/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: state.controller.signal,
      });
      if (!response.ok) {
        throw new Error(await describeHttpError(response));
      }
      await consumeStream(response);
    } catch (error) {
      showError(error.name === 'AbortError'
        ? (timedOut ? '응답 시간이 너무 길어 중단했어요. 잠시 후 다시 시도해 주세요.' : '진단을 중단했어요. 검색어를 바꿔 다시 시작할 수 있어요.')
        : '진단을 완료하지 못했어요. ' + (error.message || '연결 상태를 확인하고 다시 시도해 주세요.'));
    } finally {
      clearTimeout(timeout);
      clearTimeout(slowHint);
      state.controller = null;
      setRunning(false);
    }
  }

  async function describeHttpError(response) {
    let detail = `요청이 실패했어요 (HTTP ${response.status}).`;
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
        if (handleEvent(chunk) === 'error') failed = true;
      }
    }
    if (!state.result && !failed) showError('결과를 받지 못했어요. 잠시 후 다시 시도해 주세요.');
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
      setMode(data.mode);
      return null;
    }
    if (event === 'step') {
      updateStep(data.index, data.status, data.latency_ms);
      return null;
    }
    if (event === 'result') {
      state.result = data;
      renderResult(data);
      return null;
    }
    if (event === 'error') {
      showError(data.detail || '진단 중 오류가 발생했어요.');
      return 'error';
    }
    return null;
  }

  /* ------------------------------------------------------------------ */
  /* 결과 — 대상 매장 · 확인할 일 · 항목별 근거 · 접힌 상세              */
  /* ------------------------------------------------------------------ */
  function renderResult(r) {
    setMode(r.mode, r.degraded);
    const prioritized = (r.contents || []).find((c) => c.priority_badge);
    state.tab = prioritized?.channel || r.contents?.[0]?.channel || 'instagram';

    const sections = r.evidence_review ? [
      renderReviewOverview(r),
      r.degraded ? renderNotice(r) : '',
      renderReviewActions(r),
      renderReviewFields(r),
      '<section id="card-studio" class="card"></section>',
      renderDetail(r),
    ] : [
      renderNotice(r), renderEvidence(r), renderScore(r), renderCitation(r),
      renderGaps(r), renderContents(r),
      '<section id="card-studio" class="card"></section>', renderDetail(r),
    ];
    els.results.innerHTML = sections.join('');
    showPanel('results');
    window.CardStudio?.render(r);
    bindTabs();
    bindReviewLinks();
    bindCopyButtons();
    animateBars();
    els.results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  const REVIEW_STATUS = {
    found: '자료에 있음', different: '내용이 다름',
    needs_check: '직접 확인 필요', not_found: '찾지 못함',
  };
  const REVIEW_FIELDS = new Set(['address', 'hours', 'price', 'parking', 'reservation', 'menu']);
  const EVIDENCE_KIND = {
    search_summary: '검색 결과 요약', search_title: '검색 결과 제목', agent_excerpt: '에이전트 인용문',
  };

  function sourceURL(value) {
    try {
      const url = new URL(String(value || ''));
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) { return ''; }
  }

  function reviewTime(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return '시각 확인 안 됨';
    return new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(date);
  }

  function sourceDate(value) {
    return value ? `게시일 ${String(value).slice(0, 100)}` : '게시일 미확인';
  }

  function renderReviewOverview(r) {
    const review = r.evidence_review;
    const input = r.input || {};
    const sourceCount = Number(review.stats?.source_count ?? r.sources?.length) || 0;
    return `<section class="card review-overview">
      <p class="review-kicker">매장 검색 자료 검토</p>
      <h2 class="review-store">${esc(input.store_name || '선택한 매장')}</h2>
      <p class="review-location">${esc([input.location, input.industry].filter(Boolean).join(' · '))}</p>
      <ul class="review-meta">
        <li>자료 <b>${sourceCount}건</b></li>
        <li>검토 시각 ${esc(reviewTime(r.generated_at))}</li>
      </ul>
      ${review.scope ? `<p class="review-scope">${esc(review.scope)}</p>` : ''}
    </section>`;
  }

  function renderReviewActions(r) {
    const review = r.evidence_review;
    const actions = [...(review.actions || [])]
      .sort((a, b) => (Number(a.priority) || 99) - (Number(b.priority) || 99)).slice(0, 3);
    if (!actions.length) return `<section class="card review-actions">
      <h2 class="card-title">먼저 확인할 일</h2>
      <p class="empty-message">우선 확인할 항목을 따로 찾지 못했어요. 아래 자료의 내용과 날짜를 살펴보세요.</p>
    </section>`;
    const rows = actions.map((action, index) => {
      const field = (review.fields || []).find((item) => item.key === action.field);
      const linked = field && REVIEW_FIELDS.has(field.key);
      const linkLabel = field?.observations?.length ? '근거 보기' : '확인 항목 보기';
      return `<li class="review-action">
        <span class="review-action-number" aria-hidden="true">${index + 1}</span>
        <div class="review-action-body">
          <h3 class="review-action-title">${esc(action.title || action.label)}</h3>
          ${action.reason ? `<p class="review-action-reason">${esc(action.reason)}</p>` : ''}
          <div class="review-action-task"><span>할 일</span><p>${esc(action.action || '매장에서 실제 정보를 확인해 주세요.')}</p></div>
          <div class="review-action-footer">
            <span>${esc(action.channel || '운영 중인 매장 채널')}</span>
            ${linked ? `<a href="#review-${field.key}" data-review-target="${field.key}">${linkLabel}</a>` : ''}
          </div>
        </div>
      </li>`;
    }).join('');
    return `<section class="card review-actions">
      <div class="card-head"><h2 class="card-title">먼저 확인할 일</h2><span class="card-meta">${actions.length}가지</span></div>
      <ol class="review-action-list">${rows}</ol>
    </section>`;
  }

  function observationContext(observation) {
    return [...new Set([observation.subject, observation.scope].filter(Boolean))].join(' · ');
  }

  function observationQualification(observation) {
    const defaults = { uncertain: '추가 확인 필요', historical: '과거 정보', undated: '게시일 미확인' };
    return Object.hasOwn(defaults, observation.qualification)
      ? observation.qualification_label || defaults[observation.qualification] : '';
  }

  function renderValuePreview(field) {
    const seen = new Set();
    const unique = (field.observations || []).filter((item) => {
      if (!item.value) return false;
      const key = JSON.stringify([item.subject || '', item.scope || '', item.value]);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (!unique.length) return '';
    let ordered = unique;
    if (field.key === 'hours') {
      // 같은 시간의 반복 자료보다 서로 다른 시간 값을 먼저 보여 준다. 적용 요일도 함께 표시한다.
      const values = new Set();
      const distinct = unique.filter((item) => {
        if (values.has(item.value)) return false;
        values.add(item.value);
        return true;
      });
      ordered = [...distinct, ...unique.filter((item) => !distinct.includes(item))];
    }
    const rows = ordered.slice(0, 3).map((item) => {
      const context = observationContext(item);
      const qualification = observationQualification(item);
      return `<li class="review-value-row">
        ${context ? `<span class="review-value-context">${esc(context)}</span>` : ''}
        <strong class="review-value-text">${esc(item.value)}</strong>
        ${qualification ? `<span class="review-value-note">${esc(qualification)}</span>` : ''}
      </li>`;
    }).join('');
    return `<ul class="review-values" aria-label="자료에서 찾은 값">${rows}</ul>`;
  }

  function renderObservation(observation, sources) {
    const source = sources.find((item) => (observation.source_id && item.source_id === observation.source_id)
      || (observation.source_url && item.url === observation.source_url));
    const url = sourceURL(observation.source_url || source?.url);
    const kind = observation.evidence_label || EVIDENCE_KIND[observation.evidence_kind] || '검색 자료';
    const qualification = observationQualification(observation);
    const context = observationContext(observation);
    const title = observation.source_title || source?.title || '출처 열기';
    return `<li class="review-proof">
      <div class="review-proof-labels"><span>${esc(kind)}</span>${qualification ? `<span class="review-proof-caution">${esc(qualification)}</span>` : ''}</div>
      ${observation.value ? `<p class="review-proof-value">${context ? `<span>${esc(context)} · </span>` : ''}${esc(observation.value)}</p>` : ''}
      ${observation.quote ? `<p class="review-proof-quote">${esc(observation.quote)}</p>` : '<p class="review-proof-missing">근거 문장이 연결되지 않았어요.</p>'}
      ${url ? `<a class="review-proof-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}<span class="sr-only"> (새 창)</span></a>` : `<p class="review-proof-missing">출처 링크를 확인하지 못했어요.</p>`}
      <p class="review-proof-meta">${esc(sourceDate(observation.source_date || source?.date))} · ${esc(source?.publisher_label || '운영 주체 미확인')}</p>
    </li>`;
  }

  function renderReviewFields(r) {
    const fields = r.evidence_review.fields || [];
    const sources = r.sources || [];
    const rows = fields.map((field, index) => {
      const status = Object.hasOwn(REVIEW_STATUS, field.status) ? field.status : 'needs_check';
      const observations = field.observations || [];
      const id = REVIEW_FIELDS.has(field.key) ? `review-${field.key}` : `review-field-${index}`;
      return `<li class="review-field" id="${id}">
        <div class="review-field-heading"><h3>${esc(field.label || field.key)}</h3>
          <span class="review-status review-status-${status}">${esc(field.status_label || REVIEW_STATUS[status])}</span></div>
        <p class="review-field-summary">${esc(field.summary || '검색 자료에서 확인한 내용을 살펴보세요.')}</p>
        ${renderValuePreview(field)}
        ${observations.length ? `<details class="review-proofs"><summary>근거 ${observations.length}개 보기</summary>
          <ul class="review-proof-list">${observations.map((item) => renderObservation(item, sources)).join('')}</ul>
        </details>` : ''}
      </li>`;
    }).join('');
    return `<section class="card review-checklist">
      <div class="card-head"><h2 class="card-title">항목별 확인 내용</h2><span class="card-meta">${fields.length}개 항목</span></div>
      <ul class="review-field-list">${rows}</ul>
    </section>`;
  }

  function bindReviewLinks() {
    $$('[data-review-target]', els.results).forEach((link) => {
      link.addEventListener('click', (event) => {
        const key = link.dataset.reviewTarget;
        if (!REVIEW_FIELDS.has(key)) return;
        const field = document.getElementById(`review-${key}`);
        if (!field) return;
        event.preventDefault();
        const details = $('details', field);
        if (details) details.open = true;
        field.tabIndex = -1;
        field.focus({ preventScroll: true });
        field.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }

  function renderNotice(r) {
    const warnings = r.warnings || [];
    if (r.degraded || warnings.length) {
      const title = r.degraded && r.mode === 'demo' ? '일부 결과는 데모예요' : '결과를 확인해 주세요';
      return `<section class="card notice-card" role="alert"><h2 class="card-title">${title}</h2>
        <p>${esc(warnings[0] || '일부 정보를 확인하지 못했어요. 잠시 후 다시 진단해 주세요.')}</p>
        ${r.mode === 'demo' ? '<p>실제 매장 진단으로 사용하지 마세요. 다시 진단하려면 위의 시작 버튼을 눌러 주세요.</p>' : ''}</section>`;
    }
    return '';
  }

  function renderEvidence(r) {
    const evidence = r.store_evidence;
    if (!evidence) return '';
    const sources = evidence.sources || [];
    return `<section class="card evidence-card">
      <h2 class="card-title">검색으로 확인한 매장 자료</h2>
      <p class="source-scope">${esc(evidence.summary || '검색된 공개 자료를 바탕으로 제안해요. 정보가 없으면 확인이 필요하다고 표시해요.')}</p>
      ${sources.slice(0, 3).map((src) => `<a class="evidence-link" href="${esc(src.url)}" target="_blank" rel="noreferrer">${esc(src.title || src.hostname)}</a>`).join('')}
    </section>`;
  }

  function renderScore(r) {
    const score = r.overall_score || 0;
    const input = r.input || {};
    const meta = [input.location, input.industry, r.mode === 'live' ? '실시간' : '데모 데이터']
      .filter(Boolean).join(' · ');
    const metrics = (r.scores || []).map((item) => `
      <li class="metric">
        <span class="metric-label">${esc(item.label)}</span>
        <span class="metric-score">${item.score}</span>
        <span class="metric-bar"><i class="${barClass(item.score)}" data-width="${item.score}%"></i></span>
      </li>`).join('');

    return `
    <section class="card">
      <p class="result-store">${esc(input.store_name || '')}</p>
      <p class="result-sub">${esc(meta)}</p>
      <div class="score-line">
        <span class="score-big">${score}</span>
        <span class="score-unit">점</span>
      </div>
      <p class="score-summary">${esc(r.summary || '')}</p>
      <ul class="metrics">${metrics}</ul>
    </section>`;
  }

  function renderCitation(r, nested = false) {
    const wrapper = nested ? 'citation-detail' : 'card';
    const citation = r.citation_analysis;
    const channels = citation?.channels || [];
    if (!citation || !channels.length || !citation.total) return `<section class="${wrapper}">
      <h2 class="card-title">이번에 찾은 자료 구성</h2>
      <p class="empty-message">분석할 출처를 찾지 못했어요. 매장 이름을 더 정확히 입력하거나 채널 글을 직접 넣어 주세요.</p></section>`;

    const segments = channels
      .filter((channel) => channel.count > 0)
      .map((channel) => {
        const index = channels.indexOf(channel);
        return `<i style="width:${channel.share_pct}%;background:${CITE_COLORS[index % CITE_COLORS.length]}" title="${esc(channel.label)} ${fmtPct(channel.share_pct)}%"></i>`;
      }).join('');

    const rows = channels.map((channel, index) => `
      <li class="cite-row ${channel.count ? '' : 'is-zero'}">
        <span class="cite-dot" style="background:${channel.count ? CITE_COLORS[index % CITE_COLORS.length] : '#e5e8eb'}"></span>
        <span class="cite-label">${esc(channel.label)}</span>
        <span class="cite-count">${channel.count}건</span>
        <span class="cite-pct">${fmtPct(channel.share_pct)}%</span>
      </li>`).join('');

    return `
    <section class="${wrapper}">
      <div class="card-head">
        <h2 class="card-title">이번에 찾은 자료 구성</h2>
        <span class="card-meta">출처 ${citation.total}건</span>
      </div>
      <p class="source-scope">검색 결과·에이전트 인용 자료 · URL 중복 제외. 노출량이나 홍보 성과를 측정한 값은 아니에요.</p>
      <div class="prop-bar" role="img" aria-label="이번에 찾은 자료의 채널별 URL 비중">${segments}</div>
      <ul class="cite-list">${rows}</ul>
    </section>`;
  }

  function renderGaps(r) {
    const gaps = r.gaps || [];
    if (!gaps.length) return '<section class="card"><h2 class="card-title">빠진 정보</h2><p class="empty-message">진단 내용을 받지 못했어요. 다시 진단해 주세요.</p></section>';
    const items = gaps.map((gap, index) => `
      <li class="gap-item">
        <span class="gap-num">${index + 1}</span>
        <div>
          <p class="gap-title">${esc(gap.title)}</p>
          <p class="gap-desc">${esc(gap.reason || '')}</p>
          ${gap.fix_hint ? `<p class="gap-fix">${esc(gap.fix_hint)}</p>` : ''}
        </div>
      </li>`).join('');

    return `
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">빠진 정보 ${gaps.length}가지</h2>
        <span class="card-meta">상권 검색 수요 기준</span>
      </div>
      <ol class="gap-list">${items}</ol>
    </section>`;
  }

  function renderContents(r) {
    const contents = r.contents || [];
    if (!contents.length) return '';
    state.copyStore = contents.map((c) => c.body || '');
    const active = contents.some((c) => c.channel === state.tab) ? state.tab : contents[0].channel;
    const top = (r.recommended_channels || [])[0];

    const segments = contents.map((c) => `
      <button class="segment" role="tab" type="button" data-tab="${esc(c.channel)}"
        aria-selected="${c.channel === active ? 'true' : 'false'}">
        <span class="segment-name">${esc(CHANNEL_LABEL[c.channel] || c.channel_label || c.channel)}</span>
        ${c.priority_badge ? '<span class="tab-badge">추천</span>' : ''}
      </button>`).join('');

    const panels = contents.map((c) => `
      <div data-panel="${esc(c.channel)}" class="${c.channel === active ? '' : 'hidden'}">
        <h3 class="content-title">${esc(c.title || '')}</h3>
        ${c.summary ? `<p class="content-summary">${esc(c.summary)}</p>` : ''}
        <div class="content-body">${esc(c.body || '')}</div>
        ${c.hashtags?.length ? `<div class="tags">${c.hashtags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join('')}</div>` : ''}
        ${c.caution ? `<p class="caution">${esc(c.caution)}</p>` : ''}
        <button class="copy-btn" type="button" data-copy="${contents.indexOf(c)}">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <rect x="7" y="2.5" width="9.5" height="11" rx="1.8" stroke="currentColor" stroke-width="1.6" />
            <path d="M13 16.5H4.9A1.4 1.4 0 0 1 3.5 15.1V7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
          복사하기
        </button>
      </div>`).join('');

    return `
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">추천 채널 콘텐츠</h2>
        <span class="card-meta">사실 확인 후 발행</span>
      </div>
      ${top ? `
      <p class="rec-line">
        <span class="rec-badge">${esc(top.priority_badge || `${top.rank}순위`)} ${esc(top.label)}</span>
        <span>${esc(top.reason || '')}</span>
      </p>` : ''}
      <div class="segments" role="tablist">${segments}</div>
      ${panels}
    </section>`;
  }

  /* 접힌 상세 — 자료 구성, 전체 출처, 검색 요약, API 로그 */
  function renderDetail(r) {
    const blocks = [];
    const warnings = r.warnings || [];
    const collected = r.collected_channel || {};
    const demand = (r.demand?.answer || '').trim();
    const sources = r.sources || [];
    const pipeline = r.pipeline || {};

    if (r.evidence_review) blocks.push(renderCitation(r, true));

    if (warnings.length) {
      blocks.push(`
        <div>
          <p class="more-section-title">참고</p>
          <ul class="reason-list">${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>
        </div>`);
    }

    if (collected.summary) {
      const collectedQueries = (collected.queries || []).join(' · ');
      blocks.push(`
        <div>
          <p class="more-section-title">매장 채널 수집</p>
          <p class="reason-list"><span>${esc(collected.label || '')} · 출처 ${collected.source_count || 0}건 · 입력 ${(collected.input_chars || 0).toLocaleString('ko-KR')}자</span></p>
          ${collectedQueries ? `<p class="source-meta">${esc(collectedQueries)}</p>` : ''}
        </div>`);
    }

    if (demand) {
      blocks.push(`
        <div>
          <p class="more-section-title">검색 자료 요약</p>
          <p class="reason-list"><span>${esc(demand)}</span></p>
        </div>`);
    }

    const recommendations = r.recommended_channels || [];
    if (recommendations.length) {
      blocks.push(`
        <div>
          <p class="more-section-title">추천 채널 순위 전체</p>
          <ul class="reason-list">
            ${recommendations.map((item) => `
              <li><span><b>${item.rank}순위 ${esc(item.label)}</b> · 인용 ${fmtPct(item.share_pct)}% — ${esc(item.reason)}</span></li>`).join('')}
          </ul>
        </div>`);
    }

    if (sources.length) {
      blocks.push(`
        <div>
          <p class="more-section-title">출처 ${sources.length}건</p>
          <div class="source-list">
            ${sources.map((src) => `
              <a class="source-row" href="${esc(sourceURL(src.url) || '#')}" target="_blank" rel="noopener noreferrer" title="${esc(src.title)}">
                <img src="${esc(src.faviconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" />
                <span style="min-width:0">
                  <span class="source-title">${esc(src.title)}</span>
                  <span class="source-meta">${esc(src.hostname)} · ${esc(src.publisher_label || '운영 주체 미확인')}${src.demo ? ' · DEMO' : ''}</span>
                  <span class="source-meta">${esc(sourceDate(src.date))}</span>
                </span>
              </a>`).join('')}
          </div>
        </div>`);
    }

    const calls = pipeline.calls || [];
    if (calls.length) {
      blocks.push(`
        <div>
          <p class="more-section-title">API 로그</p>
          <div class="stat-row">
            <span>호출 <b>${pipeline.totals?.total ?? calls.length}회</b></span>
            <span>모드 <b>${pipeline.mode === 'live' ? 'LIVE' : 'DEMO'}</b></span>
            <span>소요 <b>${fmtMs(pipeline.latency_ms || 0)}</b></span>
          </div>
          <div class="pipe-steps" style="margin-top:8px">
            ${(pipeline.steps || []).map((step) => `
              <div class="pipe-step">
                <span>${step.index}. ${esc(String(step.title).split(' · ')[0])}</span>
                <span>${fmtMs(step.latency_ms)}</span>
              </div>`).join('')}
          </div>
          <div class="call-list" style="margin-top:8px">
            ${calls.map((call) => `
              <div class="call-row">
                <span>${esc(ENDPOINT_LABEL[call.endpoint] || call.endpoint)}</span>
                <span>${call.mode === 'live' ? 'LIVE' : 'DEMO'}</span>
                <span class="status-${esc(call.status)}">${esc(call.status)}</span>
                <span>${call.mode === 'demo' ? '—' : fmtMs(call.latency_ms)}</span>
              </div>`).join('')}
          </div>
        </div>`);
    }

    const raw = esc(JSON.stringify(
      { ...r, input: { ...r.input, channel_text: (r.input?.channel_text || '').slice(0, 400) } },
      null, 2,
    ));

    return `
    <details class="card more">
      <summary>자세히 보기 (출처 · API 로그)</summary>
      <div class="more-body">
        ${blocks.join('')}
        <details class="raw">
          <summary>원시 결과 JSON</summary>
          <pre class="raw-json">${raw}</pre>
        </details>
      </div>
    </details>`;
  }

  /* ------------------------------------------------------------------ */
  /* 인터랙션                                                           */
  /* ------------------------------------------------------------------ */
  function bindTabs() {
    const tabs = $$('.segment', els.results);
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
        btn.textContent = ok ? '복사했어요' : '복사에 실패했어요';
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
      $$('[data-width]', els.results).forEach((bar) => {
        bar.style.width = bar.dataset.width || '0';
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 이벤트 등록                                                        */
  /* ------------------------------------------------------------------ */
  els.toggleAdvanced.addEventListener('click', () => togglePanel(els.toggleAdvanced, els.advanced));
  els.toggleApi.addEventListener('click', () => togglePanel(els.toggleApi, els.apiPanel));
  els.channelText.addEventListener('input', syncChannelMeta);
  els.cancelBtn.addEventListener('click', () => state.controller?.abort());

  $$('.pill', els.sampleList).forEach((btn) => {
    btn.addEventListener('click', () => {
      const query = btn.dataset.query || '';
      [els.location, els.industry, els.storeName, els.channelText].forEach((field) => { field.value = ''; });
      els.query.value = query;
      const payload = {
        query,
        location: els.location.value.trim(),
        industry: els.industry.value.trim(),
        store_name: els.storeName.value.trim(),
        channel_text: els.channelText.value.trim(),
        auto_collect: true,
      };
      const sampleId = btn.dataset.sampleId;
      if (sampleId) payload.sample_id = sampleId;
      const apiKey = els.apiKey.value.trim();
      if (apiKey) payload.api_key = apiKey;
      els.apiKey.value = '';
      hidePanels();
      runAudit(payload);
    });
  });

  els.form.addEventListener('submit', (event) => {
    event.preventDefault();
    const query = els.query.value.trim();
    const location = els.location.value.trim();
    const industry = els.industry.value.trim();

    if (!query && (!location || !industry)) {
      showError('동네나 매장 이름을 한 줄로 입력해 주세요.');
      els.query.focus();
      return;
    }

    const payload = {
      query,
      location,
      industry,
      store_name: els.storeName.value.trim(),
      channel_text: els.channelText.value.trim(),
      auto_collect: true,
    };
    const apiKey = els.apiKey.value.trim();
    if (apiKey) payload.api_key = apiKey;
    els.apiKey.value = '';
    hidePanels();
    runAudit(payload);
  });

  syncChannelMeta();
})();
