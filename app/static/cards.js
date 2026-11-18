/* 검색 근거로 만든 카드 3장을 편집하고 PNG로 저장한다. 외부 이미지·저장소를 쓰지 않는다. */
(() => {
  'use strict';

  const SIZE = { width: 1080, height: 1350 };
  const FONT = '"Pretendard Variable", Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif';
  const THEMES = {
    blue: { label: '블루', paper: '#edf4ff', ink: '#172f54', sub: '#425b7b', accent: '#2473e8', soft: '#dce9ff' },
    mint: { label: '민트', paper: '#eaf6f1', ink: '#173f35', sub: '#41665b', accent: '#167c60', soft: '#d6ece2' },
    ink: { label: '차콜', paper: '#202731', ink: '#ffffff', sub: '#d3dbe6', accent: '#9dc0ff', soft: '#313f54' },
  };
  let current = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function button(text, className, handler) {
    const node = element('button', className, text);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  }

  function httpURL(value) {
    try {
      const url = new URL(String(value || ''));
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) {
      return '';
    }
  }

  function active(session) {
    return current === session && session.root.isConnected;
  }

  function setMessage(session, text, error = false) {
    session.message.textContent = text;
    session.message.classList.toggle('is-error', error);
    session.message.setAttribute('role', error ? 'alert' : 'status');
    session.message.hidden = !text;
  }

  function updateDownloads(session) {
    session.editors.forEach((editor, index) => {
      const card = session.cards[index];
      editor.download.disabled = session.busy || !card.headline.trim() || !card.body.trim();
    });
  }

  function setBusy(session, busy) {
    session.busy = busy;
    session.root.setAttribute('aria-busy', String(busy));
    session.generate.disabled = busy || !session.sources.length;
    session.generate.textContent = busy ? '카드 만드는 중' : session.cards.length ? '다시 만들기' : '카드뉴스 만들기';
    session.cancelButton.hidden = !busy;
    session.root.querySelectorAll('[data-card-editable]').forEach((node) => { node.disabled = busy; });
    updateDownloads(session);
  }

  function cancel() {
    if (!current) return;
    current.operation += 1;
    current.controller?.abort();
    current.controller = null;
    setBusy(current, false);
    setMessage(current, '');
  }

  function showKeySettings() {
    const panel = document.getElementById('api-panel');
    const toggle = document.getElementById('toggle-api');
    panel?.classList.remove('hidden');
    toggle?.setAttribute('aria-expanded', 'true');
    document.getElementById('api_key')?.focus();
    panel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function validateResult(data, session) {
    if (!data || !Array.isArray(data.cards) || data.cards.length !== 3) {
      throw new Error('카드 3장을 완성하지 못했어요. 다시 만들어 주세요.');
    }
    const cards = data.cards.map((card) => {
      if (typeof card?.headline !== 'string' || typeof card?.body !== 'string'
        || !card.headline.trim() || !card.body.trim()
        || [...card.headline].length > 40 || [...card.body].length > 180) {
        throw new Error('카드 문구를 정리하지 못했어요. 다시 만들어 주세요.');
      }
      const sourceURLs = Array.isArray(card.source_urls)
        ? card.source_urls.map(httpURL).filter((url) => session.knownSources.has(url)) : [];
      if (!sourceURLs.length) throw new Error('카드의 참고 출처를 확인하지 못했어요. 다시 만들어 주세요.');
      return { headline: card.headline.trim(), body: card.body.trim(), source_urls: [...new Set(sourceURLs)] };
    });
    return {
      cards,
      caption: typeof data.caption === 'string' ? [...data.caption].slice(0, 1500).join('') : '',
      warnings: Array.isArray(data.warnings) ? data.warnings.filter((item) => typeof item === 'string') : [],
    };
  }

  async function generate(session) {
    if (session.busy || !session.sources.length) return;
    const keyField = document.getElementById('api_key');
    if (session.requestKeyUsed && !keyField?.value.trim()) {
      setMessage(session, '일회용 키는 저장하지 않아요. API 설정에 키를 다시 입력해 주세요.', true);
      session.keyButton.hidden = false;
      return;
    }
    const payload = {
      store_name: session.storeName,
      location: session.location,
      evidence_sources: session.sources,
      context: session.context,
    };
    if (keyField?.value.trim()) payload.api_key = keyField.value.trim();
    if (keyField) keyField.value = '';
    session.keyButton.hidden = true;
    session.controller = new AbortController();
    const controller = session.controller;
    const operation = ++session.operation;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 150000);
    setBusy(session, true);
    setMessage(session, '확인한 출처로 카드 3장을 만들고 있어요. 최대 2분 정도 걸릴 수 있어요.');
    try {
      const request = fetch('/api/cards', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), signal: controller.signal,
      });
      delete payload.api_key;
      const response = await request;
      let data;
      try { data = await response.json(); } catch (_) {
        throw new Error('카드 생성 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.');
      }
      if (!response.ok) {
        throw new Error(typeof data?.detail === 'string' ? data.detail : '카드를 만들지 못했어요. 입력 내용과 API 설정을 확인해 주세요.');
      }
      if (!active(session) || session.operation !== operation) return;
      const result = validateResult(data, session);
      session.cards = result.cards;
      session.caption = result.caption;
      session.warnings = result.warnings;
      buildEditor(session);
      await loadFonts(session);
      if (!active(session) || session.operation !== operation) return;
      drawAll(session);
      setMessage(session, '3장을 만들었어요. 문구를 다듬고 PNG로 저장하세요.');
    } catch (error) {
      if (!active(session) || session.operation !== operation) return;
      const message = timedOut ? '카드 생성 시간이 초과됐어요. 잠시 후 다시 만들어 주세요.'
        : error.name === 'AbortError' ? '카드 만들기를 취소했어요.'
          : error.message || '연결이 끊겼어요. 잠시 후 다시 시도해 주세요.';
      setMessage(session, message, error.name !== 'AbortError' || timedOut);
    } finally {
      delete payload.api_key;
      clearTimeout(timeout);
      if (active(session) && session.operation === operation) {
        session.controller = null;
        setBusy(session, false);
      }
    }
  }

  function makeField(session, card, index, key, label, limit, rows) {
    const field = element('div', 'cs-field');
    const heading = element('div', 'cs-field-heading');
    const name = element('label', '', label);
    const id = `card-${index + 1}-${key}`;
    name.htmlFor = id;
    const count = element('span', 'cs-count');
    count.id = `${id}-count`;
    const input = element('textarea', 'cs-input');
    input.id = id;
    input.rows = rows;
    input.maxLength = limit * 2;
    input.value = card[key];
    input.dataset.cardEditable = '';
    input.setAttribute('aria-describedby', count.id);
    input.spellcheck = false;
    const updateCount = () => { count.textContent = `${[...input.value].length}/${limit}`; };
    updateCount();
    input.addEventListener('input', () => {
      const chars = [...input.value];
      if (chars.length > limit) input.value = chars.slice(0, limit).join('');
      card[key] = input.value;
      updateCount();
      drawCard(session, index);
      updateWarnings(session);
      updateDownloads(session);
    });
    heading.append(name, count);
    field.append(heading, input);
    return field;
  }

  function updateWarnings(session) {
    const unverified = /확인\s*필요/.test([...session.cards.map((card) => `${card.headline} ${card.body}`), session.caption].join(' '));
    const messages = [...session.warnings];
    if (unverified) messages.unshift('확인이 필요한 정보가 있어요. 발행 전에 실제 내용으로 고쳐 주세요.');
    else messages.unshift('발행 전에 매장 정보와 문구를 한 번 더 확인해 주세요.');
    if (session.cards.some((card) => [card.headline, card.body].some((text) => (text.match(/\n/g) || []).length > 5))) {
      messages.push('줄바꿈이 많으면 이미지에서는 문단을 이어서 표시해요.');
    }
    session.warning.textContent = [...new Set(messages)].join(' ');
  }

  async function copyCaption(session, control) {
    try {
      await navigator.clipboard.writeText(session.caption);
      control.textContent = '복사했어요';
      setTimeout(() => { if (control.isConnected) control.textContent = '캡션 복사'; }, 1800);
    } catch (_) {
      session.captionInput.focus();
      session.captionInput.select();
      setMessage(session, '캡션을 선택했어요. 복사해서 사용해 주세요.');
    }
  }

  function buildEditor(session) {
    session.editor.replaceChildren();
    session.editors = [];
    const toolbar = element('fieldset', 'cs-themes');
    toolbar.append(element('legend', '', '카드 색상'));
    const choices = element('div', 'cs-theme-options');
    Object.entries(THEMES).forEach(([key, theme]) => {
      const control = button(theme.label, `cs-theme cs-theme-${key}`, () => {
        session.theme = key;
        choices.querySelectorAll('button').forEach((item) => item.setAttribute('aria-pressed', String(item === control)));
        drawAll(session);
      });
      control.dataset.cardEditable = '';
      control.setAttribute('aria-pressed', String(session.theme === key));
      choices.append(control);
    });
    toolbar.append(choices);
    session.warning = element('p', 'cs-warning');
    session.warning.setAttribute('role', 'note');
    const grid = element('div', 'cs-grid');
    session.cards.forEach((card, index) => {
      const article = element('article', 'cs-slide');
      article.append(element('h3', 'cs-slide-title', `${index + 1}장`));
      const canvas = element('canvas', 'cs-preview');
      canvas.width = SIZE.width;
      canvas.height = SIZE.height;
      canvas.setAttribute('role', 'img');
      canvas.textContent = `${card.headline}\n${card.body}`;
      const download = button(`${index + 1}장 PNG 저장`, 'cs-button cs-download', () => savePNG(session, index));
      download.dataset.cardEditable = '';
      article.append(canvas,
        makeField(session, card, index, 'headline', '제목', 40, 2),
        makeField(session, card, index, 'body', '본문', 180, 5), download);
      const details = element('details', 'cs-sources');
      details.append(element('summary', '', `참고 출처 ${card.source_urls.length}개`));
      const list = element('ul', 'cs-source-list');
      card.source_urls.forEach((url) => {
        const item = element('li');
        const source = session.knownSources.get(url);
        const link = element('a', '', source?.title || new URL(url).hostname);
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(link);
        list.append(item);
      });
      details.append(list);
      article.append(details);
      grid.append(article);
      session.editors.push({ canvas, download });
    });
    const captionBox = element('div', 'cs-caption');
    const captionHead = element('div', 'cs-caption-heading');
    const captionLabel = element('label', '', '게시글 캡션');
    captionLabel.htmlFor = 'card-caption';
    const copy = button('캡션 복사', 'cs-button cs-copy', () => copyCaption(session, copy));
    copy.dataset.cardEditable = '';
    captionHead.append(captionLabel, copy);
    const captionInput = element('textarea', 'cs-input');
    captionInput.id = 'card-caption';
    captionInput.rows = 5;
    captionInput.maxLength = 1500;
    captionInput.value = session.caption;
    captionInput.dataset.cardEditable = '';
    captionInput.addEventListener('input', () => { session.caption = captionInput.value; updateWarnings(session); });
    session.captionInput = captionInput;
    captionBox.append(captionHead, captionInput);
    session.editor.append(toolbar, session.warning, grid, captionBox,
      element('p', 'cs-export-hint', 'PNG · 1080 × 1350px · 인스타그램 4:5 비율'));
    updateWarnings(session);
    drawAll(session);
    setBusy(session, session.busy);
  }

  function wrapText(ctx, text, width) {
    const lines = [];
    let displayText = String(text).replace(/\r/g, '').trim().replace(/\n{3,}/g, '\n\n');
    if ((displayText.match(/\n/g) || []).length > 5) displayText = displayText.replace(/\s*\n\s*/g, ' ');
    displayText.split('\n').forEach((paragraph) => {
      let line = '';
      for (const char of [...paragraph]) {
        if (line && ctx.measureText(line + char).width > width) {
          const breakAt = line.lastIndexOf(' ');
          if (breakAt > line.length / 2) {
            lines.push(line.slice(0, breakAt));
            line = line.slice(breakAt + 1) + char;
          } else {
            lines.push(line.trimEnd());
            line = char.trimStart();
          }
        } else line += char;
      }
      lines.push(line.trimEnd());
    });
    return lines;
  }

  function fitText(ctx, text, width, height, startSize, lineRatio, weight) {
    let size = startSize;
    let lines = [];
    while (size >= 1) {
      ctx.font = `${weight} ${size}px ${FONT}`;
      lines = wrapText(ctx, text, width);
      if (lines.length * size * lineRatio <= height) break;
      size -= 1;
    }
    return { size, lines, lineHeight: size * lineRatio, height: lines.length * size * lineRatio };
  }

  function textBlock(ctx, layout, x, y, color, weight) {
    ctx.fillStyle = color;
    ctx.font = `${weight} ${layout.size}px ${FONT}`;
    layout.lines.forEach((line, index) => ctx.fillText(line, x, y + index * layout.lineHeight));
  }

  function drawCard(session, index) {
    const card = session.cards[index];
    const canvas = session.editors[index]?.canvas;
    if (!card || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const theme = THEMES[session.theme];
    const padding = 80;
    const width = SIZE.width - padding * 2;
    ctx.clearRect(0, 0, SIZE.width, SIZE.height);
    ctx.fillStyle = theme.paper;
    ctx.fillRect(0, 0, SIZE.width, SIZE.height);
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    ctx.fillStyle = theme.soft;
    ctx.beginPath();
    ctx.arc(1000, 240, 225, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = theme.accent;
    ctx.font = `700 30px ${FONT}`;
    ctx.fillText(`${String(index + 1).padStart(2, '0')} / 03`, padding, 80);
    const location = fitText(ctx, session.location, 650, 48, 28, 1.2, 500);
    ctx.textAlign = 'right';
    textBlock(ctx, location, SIZE.width - padding, 82, theme.sub, 500);
    ctx.textAlign = 'left';
    ctx.fillStyle = theme.accent;
    ctx.fillRect(padding, 155, 56, 7);
    const title = fitText(ctx, card.headline, width, 430, 100, 1.22, 800);
    textBlock(ctx, title, padding, 226, theme.ink, 800);
    const bodyTop = Math.max(500, 226 + title.height + 70);
    const body = fitText(ctx, card.body, width, 1120 - bodyTop, 46, 1.55, 500);
    textBlock(ctx, body, padding, bodyTop, theme.ink, 500);
    ctx.fillStyle = theme.sub;
    ctx.globalAlpha = 0.28;
    ctx.fillRect(padding, 1187, width, 1);
    ctx.globalAlpha = 1;
    const store = fitText(ctx, session.storeName, 760, 80, 32, 1.2, 700);
    textBlock(ctx, store, padding, 1223, theme.ink, 700);
    ctx.fillStyle = theme.sub;
    ctx.textAlign = 'right';
    ctx.font = `500 26px ${FONT}`;
    ctx.fillText(`${index + 1} / 3`, SIZE.width - padding, 1227);
    ctx.textAlign = 'left';
    canvas.setAttribute('aria-label', `${index + 1}장 미리보기. ${card.headline}. ${card.body}`);
    canvas.textContent = `${card.headline}\n${card.body}`;
  }

  function drawAll(session) {
    session.cards.forEach((_card, index) => drawCard(session, index));
  }

  async function loadFonts(session) {
    if (!document.fonts) return;
    const text = session.cards.map((card) => `${card.headline}${card.body}`).join('') + session.storeName + session.location;
    let timer;
    await Promise.race([
      Promise.allSettled([
        document.fonts.load(`800 100px ${FONT}`, text),
        document.fonts.load(`500 46px ${FONT}`, text),
        document.fonts.ready,
      ]),
      new Promise((resolve) => { timer = setTimeout(resolve, 6000); }),
    ]);
    clearTimeout(timer);
  }

  async function savePNG(session, index) {
    const editor = session.editors[index];
    const card = session.cards[index];
    if (!editor || editor.download.disabled) return;
    editor.download.disabled = true;
    editor.download.textContent = '저장 준비 중';
    try {
      await loadFonts(session);
      if (!active(session) || session.cards[index] !== card) return;
      drawCard(session, index);
      const blob = await new Promise((resolve) => editor.canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('이미지를 저장하지 못했어요. 다시 시도해 주세요.');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const filename = session.storeName.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '').slice(0, 60) || '가게';
      link.href = url;
      link.download = `${filename}_카드뉴스_${index + 1}.png`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      setMessage(session, `${index + 1}장 PNG 저장을 시작했어요.`);
    } catch (error) {
      if (active(session)) setMessage(session, error.message || '이미지를 저장하지 못했어요.', true);
    } finally {
      if (active(session)) {
        editor.download.textContent = `${index + 1}장 PNG 저장`;
        updateDownloads(session);
      }
    }
  }

  function render(result) {
    cancel();
    const root = document.getElementById('card-studio');
    if (!root) { current = null; return; }
    root.replaceChildren();
    root.classList.add('cs-studio');
    const knownSources = new Map();
    const cardSources = result?.evidence_review ? result.evidence_review.content_sources : result?.sources;
    (Array.isArray(cardSources) ? cardSources : []).forEach((source) => {
      const url = httpURL(source?.url);
      if (!url || url.length > 2048 || source.demo || knownSources.has(url) || knownSources.size >= 30) return;
      knownSources.set(url, { url, title: String(source.title || '').slice(0, 500), description: String(source.description || '').slice(0, 3000) });
    });
    const session = {
      root, knownSources, sources: [...knownSources.values()],
      storeName: String(result?.input?.store_name || ''), location: String(result?.input?.location || ''),
      requestKeyUsed: result?.input?.api_key_source === 'request',
      context: String(result?.contents?.find((item) => item.channel === 'instagram')?.body || '').slice(0, 12000),
      cards: [], editors: [], caption: '', warnings: [], theme: 'blue', busy: false, operation: 0, controller: null,
    };
    current = session;
    const header = element('div', 'cs-heading');
    const title = element('h2', 'card-title', '검색한 정보로 카드뉴스까지');
    title.id = 'card-studio-title';
    root.setAttribute('aria-labelledby', title.id);
    header.append(title, element('p', 'cs-subtitle', '문구를 만들고 카드 틀에 넣어 PNG로 저장해요. 매장 사진을 생성하는 기능은 아니에요.'));
    const actions = element('div', 'cs-actions');
    session.generate = button('카드뉴스 만들기', 'cs-button cs-primary', () => generate(session));
    session.cancelButton = button('취소', 'cs-button cs-secondary', () => {
      cancel();
      setMessage(session, '카드 만들기를 취소했어요.');
    });
    session.cancelButton.hidden = true;
    actions.append(session.generate, session.cancelButton);
    session.keyButton = button('API 설정 열기', 'cs-button cs-secondary', showKeySettings);
    session.keyButton.hidden = true;
    actions.append(session.keyButton);
    session.message = element('p', 'cs-message');
    session.message.setAttribute('aria-live', 'polite');
    session.message.hidden = true;
    session.editor = element('div', 'cs-editor');
    root.append(header, actions, session.message, session.editor);
    if (!session.sources.length) setMessage(session, '확인한 매장 출처가 있어야 카드를 만들 수 있어요. 다시 진단해 주세요.');
    setBusy(session, false);
  }

  document.fonts?.addEventListener('loadingdone', () => {
    if (current && active(current) && current.cards.length) drawAll(current);
  });
  window.CardStudio = { render, cancel };
})();
