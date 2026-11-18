/* 검색 문구·Liner AI 이미지 또는 로컬 사진으로 카드 3장을 만든다. 직접 고른 사진은 서버로 보내지 않는다. */
(() => {
  'use strict';

  const SIZE = { width: 1080, height: 1350 };
  const FONT = '"Pretendard Variable", Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif';
  const THEMES = {
    natural: { label: '자연광', filter: 'none', tint: 'rgba(0,0,0,0)' },
    warm: { label: '따뜻하게', filter: 'saturate(0.94) sepia(0.08)', tint: 'rgba(111,57,13,0.05)' },
    calm: { label: '차분하게', filter: 'saturate(0.76)', tint: 'rgba(18,32,38,0.08)' },
  };
  const LAYOUTS = { bottom_left: '왼쪽 아래', top_left: '왼쪽 위', center: '가운데' };
  const DEFAULT_LAYOUTS = ['bottom_left', 'top_left', 'bottom_left'];
  const MAX_PHOTO_BYTES = 15 * 1024 * 1024;
  const MAX_PHOTO_PIXELS = 40000000;
  const MAX_PHOTO_SIDE = 12000;
  const IMAGE_WAIT_MS = 480000;
  const IMAGE_POLL_MS = 2000;
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
      editor.download.disabled = session.busy || session.photoLoading[index]
        || !session.photos[index] || !card.headline.trim() || !card.body.trim();
    });
  }

  function setBusy(session, busy) {
    session.busy = busy;
    session.root.setAttribute('aria-busy', String(busy));
    session.generate.disabled = busy || !session.sources.length;
    session.generate.textContent = busy ? '문구 만드는 중' : session.cards.length ? '문구 다시 만들기' : '문구 3장 만들기';
    session.cancelButton.hidden = !busy;
    session.root.querySelectorAll('[data-card-editable]').forEach((node) => { node.disabled = busy; });
    updateDownloads(session);
    updateImageControls(session);
  }

  function cancel() {
    if (!current) return;
    stopImageWaits(current);
    stopPhotoLoads(current);
    current.operation += 1;
    current.controller?.abort();
    current.controller = null;
    setBusy(current, false);
    setMessage(current, '');
  }

  function releasePhoto(photo) {
    if (!photo) return;
    photo.image.width = 0;
    photo.image.height = 0;
  }

  function stopPhotoLoads(session) {
    session.photoOperations = session.photoOperations.map((operation) => operation + 1);
    session.photoLoading.forEach((loading, index) => {
      if (loading) session.photoMessages[index] = session.photos[index]
        ? { text: session.photos[index].name, error: false } : null;
    });
    session.photoLoading.fill(false);
    session.editors.forEach((_editor, index) => updatePhotoControls(session, index));
  }

  function disposePhotos(session) {
    session.photos.forEach(releasePhoto);
    session.photos = [];
    (session.imageJobs || []).forEach((job) => { if (job) job.image = null; });
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
    const cards = data.cards.map((card, index) => {
      if (typeof card?.headline !== 'string' || typeof card?.body !== 'string'
        || !card.headline.trim() || !card.body.trim()
        || [...card.headline].length > 40 || [...card.body].length > 180) {
        throw new Error('카드 문구를 정리하지 못했어요. 다시 만들어 주세요.');
      }
      const sourceURLs = Array.isArray(card.source_urls)
        ? card.source_urls.map(httpURL).filter((url) => session.knownSources.has(url)) : [];
      if (!sourceURLs.length) throw new Error('카드의 참고 출처를 확인하지 못했어요. 다시 만들어 주세요.');
      return {
        headline: card.headline.trim(), body: card.body.trim(), source_urls: [...new Set(sourceURLs)],
        visual_prompt: typeof card.visual_prompt === 'string' ? [...card.visual_prompt].slice(0, 1800).join('') : '',
        layout: Object.hasOwn(LAYOUTS, card.layout) ? card.layout : DEFAULT_LAYOUTS[index],
      };
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
    stopPhotoLoads(session);
    session.controller = new AbortController();
    const controller = session.controller;
    const operation = ++session.operation;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 150000);
    setBusy(session, true);
    setMessage(session, '검색 자료로 짧은 문구와 이미지 연출안 3개를 만들고 있어요.');
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
      session.cards.forEach((card, index) => { session.layouts[index] ||= card.layout; });
      session.caption = result.caption;
      session.warnings = result.warnings;
      buildEditor(session);
      await loadFonts(session);
      if (!active(session) || session.operation !== operation) return;
      drawAll(session);
      setMessage(session, '문구 3장을 만들었어요. AI 이미지를 만들거나 매장 사진을 넣어 완성하세요.');
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
    const messages = [...session.warnings];
    if (session.cards.some((card) => [card.headline, card.body].some((text) => (text.match(/\n/g) || []).length > 5))) {
      messages.push('줄바꿈이 많으면 이미지에서는 문단을 이어서 표시해요.');
    }
    if (session.cards.some((card) => [...card.headline].length > 24 || [...card.body].length > 64)) {
      messages.push('사진이 잘 보이도록 제목은 24자, 본문은 64자 안팎으로 줄여 보세요.');
    }
    session.warning.textContent = [...new Set(messages)].join(' ');
    if (session.warningPanel) session.warningPanel.hidden = !session.warning.textContent;
  }

  function terminalImageJob(job) {
    return job?.status === 'complete' || job?.status === 'error';
  }

  function updateImageControls(session) {
    const jobs = session.imageJobs || [];
    session.editors.forEach((editor, index) => {
      if (!editor.aiButton) return;
      const job = jobs[index];
      const applied = job && session.photos[index]?.requestId === job.requestId;
      const canApply = job?.status === 'complete' && job.image && !applied;
      editor.aiButton.textContent = job?.waiting || job?.applying ? 'AI 이미지 만드는 중'
        : canApply ? '생성 이미지 적용'
          : job && !terminalImageJob(job) ? '결과 다시 확인'
            : job ? 'AI 이미지 다시 만들기' : 'AI 이미지 만들기';
      editor.aiButton.disabled = session.busy || job?.waiting || job?.applying || !session.cards[index]?.visual_prompt;
      editor.aiStop.hidden = !job?.waiting && !job?.applying;
      editor.aiMessage.textContent = job?.message || 'Liner 이미지 API로 연출 시안을 만들어요. 실제 매장 사진은 아니에요.';
      editor.aiMessage.classList.toggle('is-error', Boolean(job?.messageError));
      editor.aiMessage.setAttribute('role', job?.messageError ? 'alert' : 'status');
    });
    if (session.imageBatch) {
      session.imageBatch.disabled = session.busy || session.cards.length !== 3
        || session.cards.some((card) => !card.visual_prompt)
        || jobs.some((job) => job && (!terminalImageJob(job) || job.waiting || job.applying));
    }
  }

  function pauseImageJob(session, index) {
    const job = session.imageJobs?.[index];
    if (!job || (!job.waiting && !job.applying)) return;
    job.waitVersion += 1;
    job.controller?.abort();
    job.controller = null;
    job.waiting = false;
    job.applying = false;
    job.message = terminalImageJob(job)
      ? '이미지는 완성됐어요. 생성 이미지 적용 버튼으로 이어서 확인하세요.'
      : '화면의 대기만 중단했어요. 서버 생성은 계속되며 결과를 다시 확인할 수 있어요.';
    job.messageError = false;
    updateImageControls(session);
  }

  function stopImageWaits(session) {
    (session.imageJobs || []).forEach((_job, index) => pauseImageJob(session, index));
  }

  function pollDelay(signal) {
    return new Promise((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
        const error = new Error('대기 중단'); error.name = 'AbortError'; reject(error);
      };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, IMAGE_POLL_MS);
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, { once: true });
    });
  }

  async function imageResponse(responsePromise, controller) {
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await responsePromise;
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('이미지 응답을 확인하지 못했어요.'); }
      return { response, data };
    } finally { clearTimeout(timer); }
  }

  function generatedImageFile(image, index) {
    if (!image || image.provenance !== 'ai_concept'
      || !['image/png', 'image/jpeg', 'image/webp'].includes(image.media_type)
      || typeof image.b64_json !== 'string' || image.b64_json.length > 21 * 1024 * 1024) {
      throw new Error('생성 이미지 형식을 확인하지 못했어요.');
    }
    const encoded = image.b64_json.replace(/\s/g, '');
    if (!encoded || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) throw new Error('생성 이미지가 온전하지 않아요.');
    const binary = atob(encoded);
    if (binary.length > MAX_PHOTO_BYTES) throw new Error('생성 이미지가 너무 커서 불러오지 못했어요.');
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const extension = image.media_type === 'image/jpeg' ? 'jpg' : image.media_type.split('/')[1];
    return new File([bytes], `AI_연출_이미지_${index + 1}.${extension}`, { type: image.media_type });
  }

  async function applyGeneratedImage(session, index, job, explicit = false) {
    if (!active(session) || session.imageJobs[index] !== job || job.status !== 'complete' || !job.image) return;
    if (!explicit && (session.cards[index] !== job.card || session.photoOperations[index] !== job.photoOperation || session.busy)) {
      job.message = '이미지를 만들었어요. 사진이나 문구가 바뀌어 자동 적용하지 않았어요.';
      return;
    }
    const version = job.waitVersion;
    job.applying = true;
    updateImageControls(session);
    try {
      const file = generatedImageFile(job.image, index);
      const applied = await loadPhoto(session, index, file, {
        provenance: 'ai_concept', requestId: job.requestId,
        allowed: () => session.imageJobs[index] === job && job.waitVersion === version,
      });
      if (active(session) && session.imageJobs[index] === job && job.waitVersion === version) {
        job.message = applied ? 'AI 연출 이미지를 적용했어요. 실제 메뉴·매장 사진과 다를 수 있어요.'
          : '이미지는 완성됐지만 적용하지 못했어요. 기존 사진은 유지했어요.';
        job.messageError = !applied;
      }
    } catch (_) {
      if (active(session) && session.imageJobs[index] === job && job.waitVersion === version) {
        job.message = '완성된 이미지를 읽지 못했어요. 기존 사진은 유지했어요.';
        job.messageError = true;
      }
    } finally {
      if (session.imageJobs[index] === job && job.waitVersion === version) job.applying = false;
      updateImageControls(session);
    }
  }

  async function waitForImage(session, index, job, firstResponse = null) {
    const version = ++job.waitVersion;
    const controller = new AbortController();
    job.controller = controller;
    job.waiting = true;
    job.messageError = false;
    const deadline = Date.now() + IMAGE_WAIT_MS;
    const deadlineTimer = setTimeout(() => controller.abort(), IMAGE_WAIT_MS);
    const valid = () => active(session) && session.imageJobs[index] === job && job.waitVersion === version;
    updateImageControls(session);
    try {
      let nextResponse = firstResponse ? firstResponse(controller) : null;
      let first = Boolean(firstResponse);
      while (valid()) {
        if (Date.now() >= deadline) throw new Error('이미지 대기 시간이 지났어요. 같은 요청의 결과를 다시 확인하세요.');
        const { response, data } = await imageResponse(nextResponse || fetch(`/api/card-images/${encodeURIComponent(job.requestId)}`, {
          signal: controller.signal,
        }), controller);
        nextResponse = null;
        if (!valid()) return;
        if (!response.ok) {
          // 확실한 접수 거절만 종료 처리한다. 통신 실패·조회 오류는 새 유료 요청을 만들지 않는다.
          if (first && [400, 401, 403, 404, 409, 413, 422, 429].includes(response.status)) {
            job.status = 'error';
            job.message = typeof data?.detail === 'string' ? data.detail.slice(0, 500) : '이미지 요청이 접수되지 않았어요.';
            job.messageError = true;
            return;
          }
          if (!first && [404, 410].includes(response.status)) {
            job.status = 'error';
            job.message = '앞선 요청의 결과를 더 이상 조회할 수 없어요. 새 이미지 만들기는 크레딧을 다시 사용해요.';
            job.messageError = true;
            return;
          }
          throw new Error('결과를 확인하지 못했어요. 같은 요청의 결과를 다시 확인하세요.');
        }
        first = false;
        if (data?.request_id !== job.requestId || !['pending', 'complete', 'error'].includes(data?.status)) {
          throw new Error('이미지 응답을 확인하지 못했어요. 결과 다시 확인으로 이어서 확인하세요.');
        }
        job.status = data.status;
        if (data.status === 'error') {
          job.message = typeof data.detail === 'string' ? data.detail.slice(0, 500) : '이미지를 만들지 못했어요.';
          job.messageError = true;
          return;
        }
        if (data.status === 'complete') {
          if (!data.image) {
            job.status = 'pending';
            throw new Error('완성된 이미지가 응답에 없어요. 결과를 다시 확인해 주세요.');
          }
          job.image = data.image;
          await applyGeneratedImage(session, index, job);
          return;
        }
        job.message = 'AI 이미지를 만들고 있어요. 기다리기를 멈춰도 서버 생성은 계속돼요.';
        updateImageControls(session);
        await pollDelay(controller.signal);
      }
    } catch (error) {
      if (valid()) {
        job.message = error.name === 'AbortError'
          ? '연결 대기가 길어졌어요. 새 요청 없이 결과를 다시 확인할 수 있어요.'
          : error instanceof Error && /[가-힣]/.test(error.message) ? error.message
            : '연결이 끊겼어요. 새 요청 없이 결과를 다시 확인할 수 있어요.';
        job.messageError = true;
      }
    } finally {
      clearTimeout(deadlineTimer);
      if (valid()) {
        job.waiting = false;
        job.controller = null;
        updateImageControls(session);
      }
    }
  }

  function startImageJob(session, index, apiKey = '') {
    const previous = session.imageJobs[index];
    const card = session.cards[index];
    if (!active(session) || session.busy || !card?.visual_prompt
      || (previous && (!terminalImageJob(previous) || previous.waiting || previous.applying))) return Promise.resolve();
    const job = {
      requestId: crypto.randomUUID(), status: 'pending', card,
      photoOperation: session.photoOperations[index], waitVersion: 0,
      waiting: false, applying: false, image: null, message: 'AI 이미지 요청을 접수하고 있어요.', messageError: false,
    };
    session.imageJobs[index] = job;
    const payload = { request_id: job.requestId, prompt: card.visual_prompt };
    if (apiKey) payload.api_key = apiKey;
    apiKey = '';
    return waitForImage(session, index, job, (controller) => {
      try {
        return fetch('/api/card-images', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload), signal: controller.signal,
        });
      } finally { delete payload.api_key; }
    });
  }

  function startImages(session, indices) {
    const selected = [...new Set(indices)].filter((index) => Number.isInteger(index) && index >= 0 && index < 3);
    if (!active(session) || session.busy || !selected.length || selected.some((index) => !session.cards[index]?.visual_prompt
      || (session.imageJobs[index] && (!terminalImageJob(session.imageJobs[index]) || session.imageJobs[index].waiting || session.imageJobs[index].applying)))) {
      return Promise.resolve();
    }
    const keyField = document.getElementById('api_key');
    let key = keyField?.value.trim() || '';
    if (session.requestKeyUsed && !key) {
      setMessage(session, '이미지를 만들려면 API 설정에 일회용 키를 다시 입력해 주세요.', true);
      session.keyButton.hidden = false;
      return Promise.resolve();
    }
    if (keyField) keyField.value = '';
    session.keyButton.hidden = true;
    const pending = selected.map((index) => startImageJob(session, index, key));
    key = '';
    return Promise.allSettled(pending);
  }

  function imageAction(session, index) {
    const job = session.imageJobs[index];
    if (session.busy || job?.waiting || job?.applying) return;
    if (job && !terminalImageJob(job)) {
      job.message = '앞선 이미지 요청의 결과를 다시 확인하고 있어요.';
      return waitForImage(session, index, job);
    }
    if (job?.status === 'complete' && job.image && session.photos[index]?.requestId !== job.requestId) {
      return applyGeneratedImage(session, index, job, true);
    }
    return startImages(session, [index]);
  }

  function imageControls(session, index) {
    const box = element('div', 'cs-image-controls');
    const aiButton = button('AI 이미지 만들기', 'cs-button cs-ai-button', () => { void imageAction(session, index); });
    const aiStop = button('기다리기 중단', 'cs-button cs-ai-stop', () => pauseImageJob(session, index));
    const aiMessage = element('p', 'cs-image-message');
    aiMessage.setAttribute('aria-live', 'polite');
    box.append(aiButton, aiStop, aiMessage);
    return { imageBox: box, aiButton, aiStop, aiMessage };
  }

  function photoDimensions(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const ascii = (start, length) => String.fromCharCode(...bytes.slice(start, start + length));
    if (bytes.length >= 24 && bytes[0] === 137 && ascii(1, 3) === 'PNG'
      && bytes[4] === 13 && bytes[5] === 10 && bytes[6] === 26 && bytes[7] === 10
      && ascii(12, 4) === 'IHDR') {
      return { type: 'image/png', width: view.getUint32(16), height: view.getUint32(20) };
    }
    if (bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216) {
      let offset = 2;
      while (offset + 3 < bytes.length) {
        if (bytes[offset] !== 255) break;
        while (bytes[offset + 1] === 255) offset += 1;
        const marker = bytes[offset + 1];
        if (marker === 217 || marker === 218) break;
        if (marker === 1 || (marker >= 208 && marker <= 216)) { offset += 2; continue; }
        const length = view.getUint16(offset + 2);
        if (length < 2 || offset + length + 2 > bytes.length) break;
        if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker)
          && length >= 8) {
          return { type: 'image/jpeg', width: view.getUint16(offset + 7), height: view.getUint16(offset + 5) };
        }
        offset += length + 2;
      }
    }
    if (bytes.length >= 30 && ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP') {
      const chunk = ascii(12, 4);
      if (chunk === 'VP8X') {
        if (bytes[20] & 2) throw new Error('움직이는 사진 대신 정지 사진을 골라 주세요.');
        const uint24 = (offset) => bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
        return { type: 'image/webp', width: uint24(24) + 1, height: uint24(27) + 1 };
      }
      if (chunk === 'VP8L' && bytes[20] === 47) {
        const bits = view.getUint32(21, true);
        return { type: 'image/webp', width: (bits & 16383) + 1, height: ((bits >>> 14) & 16383) + 1 };
      }
      if (chunk === 'VP8 ' && bytes[23] === 157 && bytes[24] === 1 && bytes[25] === 42) {
        return { type: 'image/webp', width: view.getUint16(26, true) & 16383, height: view.getUint16(28, true) & 16383 };
      }
    }
    throw new Error('JPG, PNG, WebP 형식의 정상적인 사진을 골라 주세요.');
  }

  function checkDimensions(width, height) {
    if (!width || !height || width * height > MAX_PHOTO_PIXELS
      || width > MAX_PHOTO_SIDE || height > MAX_PHOTO_SIDE) {
      throw new Error('사진이 너무 커요. 4천만 화소·긴 변 12,000px 이하로 줄여 주세요.');
    }
  }

  async function loadPhoto(session, index, file, options = {}) {
    const allowed = options.allowed || (() => true);
    if (!file || session.busy || !allowed()) return false;
    const operation = session.operation;
    const photoOperation = ++session.photoOperations[index];
    const stillCurrent = () => active(session) && session.operation === operation
      && session.photoOperations[index] === photoOperation && allowed();
    session.photoLoading[index] = true;
    session.photoMessages[index] = { text: '사진을 읽고 있어요.', error: false };
    updatePhotoControls(session, index);
    updateDownloads(session);
    let url;
    let decoded;
    try {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || !file.size || file.size > MAX_PHOTO_BYTES) {
        throw new Error('15MB 이하의 JPG, PNG, WebP 사진을 골라 주세요.');
      }
      const header = photoDimensions(new Uint8Array(await file.arrayBuffer()));
      if (header.type !== file.type) throw new Error('사진의 확장자와 실제 형식이 달라요. 다시 저장한 사진을 골라 주세요.');
      checkDimensions(header.width, header.height);
      if (!stillCurrent()) return;
      url = URL.createObjectURL(file);
      decoded = new Image();
      decoded.src = url;
      await decoded.decode();
      checkDimensions(decoded.naturalWidth, decoded.naturalHeight);
      if (!stillCurrent()) return;
      const scale = Math.min(1, 3000 / Math.max(decoded.naturalWidth, decoded.naturalHeight));
      const image = document.createElement('canvas');
      image.width = Math.max(1, Math.round(decoded.naturalWidth * scale));
      image.height = Math.max(1, Math.round(decoded.naturalHeight * scale));
      const ctx = image.getContext('2d');
      if (!ctx) throw new Error('이 브라우저에서 사진을 읽지 못했어요.');
      ctx.drawImage(decoded, 0, 0, image.width, image.height);
      releasePhoto(session.photos[index]);
      const provenance = options.provenance === 'ai_concept' ? 'ai_concept' : 'user_photo';
      session.photos[index] = { image, name: file.name, focusX: 50, focusY: 50, provenance,
        requestId: provenance === 'ai_concept' ? options.requestId : null };
      session.photoMessages[index] = { text: provenance === 'ai_concept' ? 'AI 연출 이미지 · 실제 매장 사진이 아니에요.' : file.name, error: false };
      drawCard(session, index);
      return true;
    } catch (error) {
      if (stillCurrent()) {
        session.photoMessages[index] = { text: error instanceof Error && /[가-힣]/.test(error.message)
          ? error.message : '사진을 읽지 못했어요. 다른 사진을 골라 주세요.', error: true };
      }
    } finally {
      if (url) URL.revokeObjectURL(url);
      if (decoded) decoded.src = '';
      if (active(session) && session.operation === operation && session.photoOperations[index] === photoOperation) {
        if (!allowed()) session.photoMessages[index] = session.photos[index]
          ? { text: session.photos[index].name, error: false } : null;
        session.photoLoading[index] = false;
        updatePhotoControls(session, index);
        updateDownloads(session);
        updateImageControls(session);
      }
    }
  }

  function updatePhotoControls(session, index) {
    const editor = session.editors[index];
    if (!editor?.photoButton) return;
    const photo = session.photos[index];
    const loading = session.photoLoading[index];
    const message = session.photoMessages[index];
    editor.photoButton.textContent = loading ? '사진 읽는 중' : photo ? `${index + 1}장 사진 바꾸기` : `${index + 1}장 사진 넣기`;
    editor.photoRemove.hidden = !photo;
    editor.cropDetails.hidden = !photo;
    editor.photoMessage.textContent = message?.text || '사진은 이 브라우저에서만 사용해요.';
    editor.photoMessage.classList.toggle('is-error', Boolean(message?.error));
    editor.photoMessage.setAttribute('role', message?.error ? 'alert' : 'status');
    editor.focusX.value = String(photo?.focusX ?? 50);
    editor.focusY.value = String(photo?.focusY ?? 50);
  }

  function photoControls(session, index) {
    const box = element('div', 'cs-photo-controls');
    const input = element('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp';
    input.hidden = true;
    input.dataset.cardEditable = '';
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = '';
      void loadPhoto(session, index, file);
    });
    const controls = element('div', 'cs-photo-actions');
    const photoButton = button(`${index + 1}장 사진 넣기`, 'cs-button cs-photo-button', () => input.click());
    const photoRemove = button('사진 빼기', 'cs-button cs-photo-remove', () => {
      session.photoOperations[index] += 1;
      session.photoLoading[index] = false;
      releasePhoto(session.photos[index]);
      session.photos[index] = null;
      session.photoMessages[index] = null;
      updatePhotoControls(session, index);
      updateDownloads(session);
      updateImageControls(session);
      drawCard(session, index);
    });
    photoButton.dataset.cardEditable = '';
    photoRemove.dataset.cardEditable = '';
    controls.append(photoButton, photoRemove);
    const photoMessage = element('p', 'cs-photo-message');
    const cropDetails = element('details', 'cs-crop');
    cropDetails.append(element('summary', '', '사진 위치 조절'));
    const ranges = {};
    for (const [key, label] of [['focusX', '좌우'], ['focusY', '위아래']]) {
      const rangeLabel = element('label', 'cs-crop-label', label);
      const range = element('input', 'cs-range');
      range.type = 'range';
      range.min = '0'; range.max = '100'; range.step = '1'; range.value = '50';
      range.setAttribute('aria-label', `${index + 1}장 사진 ${label} 위치`);
      range.dataset.cardEditable = '';
      range.addEventListener('input', () => {
        if (session.photos[index]) {
          session.photos[index][key] = Number(range.value);
          drawCard(session, index);
        }
      });
      rangeLabel.append(range);
      cropDetails.append(rangeLabel);
      ranges[key] = range;
    }
    const layoutLabel = element('label', 'cs-layout-label', '문구 위치');
    const layout = element('select', 'cs-select');
    layout.setAttribute('aria-label', `${index + 1}장 문구 위치`);
    layout.dataset.cardEditable = '';
    Object.entries(LAYOUTS).forEach(([value, text]) => {
      const option = element('option', '', text); option.value = value; layout.append(option);
    });
    layout.value = session.layouts[index] || DEFAULT_LAYOUTS[index];
    layout.addEventListener('change', () => { session.layouts[index] = layout.value; drawCard(session, index); });
    layoutLabel.append(layout);
    box.append(input, controls, photoMessage, cropDetails, layoutLabel);
    return { box, photoButton, photoRemove, photoMessage, cropDetails, ...ranges };
  }

  function visualBrief(card, session) {
    if (!card.visual_prompt) return null;
    const details = element('details', 'cs-sources cs-brief');
    details.append(element('summary', '', '사진 연출안'));
    const text = element('p', 'cs-brief-text', card.visual_prompt);
    const copy = button('연출안 복사', 'cs-button cs-copy', async () => {
      try {
        await navigator.clipboard.writeText(card.visual_prompt);
        copy.textContent = '복사했어요';
        setTimeout(() => { if (copy.isConnected) copy.textContent = '연출안 복사'; }, 1800);
      } catch (_) { setMessage(session, '복사하지 못했어요. 연출안 문장을 직접 선택해 주세요.', true); }
    });
    copy.dataset.cardEditable = '';
    details.append(text, copy, element('p', 'cs-photo-message', 'AI 이미지 버튼을 누르면 이 연출안을 Liner 이미지 API로 보내요.'));
    return details;
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
    const imageToolbar = element('div', 'cs-image-toolbar');
    session.imageBatch = button('AI 이미지 3장 만들기', 'cs-button cs-ai-batch', () => { void startImages(session, [0, 1, 2]); });
    imageToolbar.append(session.imageBatch,
      element('p', 'cs-image-cost', 'Liner 크레딧 사용 · 새 이미지 생성마다 추가 사용해요. 실제 매장 사진을 만들지는 않아요.'));
    const toolbar = element('fieldset', 'cs-themes');
    toolbar.append(element('legend', '', '사진 분위기'));
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
    session.warningPanel = element('details', 'cs-warning-panel');
    session.warningPanel.append(element('summary', '', '발행 전 확인할 내용'), session.warning);
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
      const photo = photoControls(session, index);
      const image = imageControls(session, index);
      article.append(canvas, image.imageBox, photo.box,
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
      const brief = visualBrief(card, session);
      if (brief) article.append(brief);
      grid.append(article);
      session.editors.push({ canvas, download, ...photo, ...image });
      updatePhotoControls(session, index);
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
    session.editor.append(imageToolbar, toolbar, session.warningPanel, grid, captionBox,
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

  function drawPhoto(ctx, photo) {
    const image = photo.image;
    const scale = Math.max(SIZE.width / image.width, SIZE.height / image.height);
    const width = SIZE.width / scale;
    const height = SIZE.height / scale;
    const x = (image.width - width) * photo.focusX / 100;
    const y = (image.height - height) * photo.focusY / 100;
    ctx.drawImage(image, x, y, width, height, 0, 0, SIZE.width, SIZE.height);
  }

  function drawCard(session, index) {
    const card = session.cards[index];
    const canvas = session.editors[index]?.canvas;
    if (!card || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const photo = session.photos[index];
    const theme = THEMES[session.theme];
    const layout = session.layouts[index] || DEFAULT_LAYOUTS[index];
    const padding = 72;
    const width = SIZE.width - padding * 2;
    ctx.clearRect(0, 0, SIZE.width, SIZE.height);
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    if (!photo) {
      ctx.fillStyle = '#ece9e3';
      ctx.fillRect(0, 0, SIZE.width, SIZE.height);
      ctx.textAlign = 'center';
      ctx.fillStyle = '#746e65';
      ctx.font = `500 42px ${FONT}`;
      ctx.fillText('사진을 넣어주세요', SIZE.width / 2, 604);
      ctx.fillStyle = '#968d81';
      ctx.font = `400 28px ${FONT}`;
      ctx.fillText('사진이 화면을 꽉 채워요', SIZE.width / 2, 678);
      ctx.textAlign = 'left';
      canvas.setAttribute('aria-label', `${index + 1}장 사진을 넣어주세요. 사진을 넣으면 문구와 함께 보여요.`);
      canvas.textContent = `${index + 1}장 사진을 넣어주세요.`;
      return;
    }
    ctx.fillStyle = '#24211c';
    ctx.fillRect(0, 0, SIZE.width, SIZE.height);
    ctx.save();
    if ('filter' in ctx) ctx.filter = theme.filter;
    drawPhoto(ctx, photo);
    ctx.restore();
    ctx.fillStyle = theme.tint;
    ctx.fillRect(0, 0, SIZE.width, SIZE.height);
    const shade = ctx.createLinearGradient(0, 0, 0, SIZE.height);
    if (layout === 'top_left') {
      shade.addColorStop(0, 'rgba(14,13,11,0.74)');
      shade.addColorStop(0.43, 'rgba(14,13,11,0.45)');
      shade.addColorStop(0.74, 'rgba(14,13,11,0.05)');
      shade.addColorStop(1, 'rgba(14,13,11,0.12)');
    } else if (layout === 'center') {
      shade.addColorStop(0, 'rgba(14,13,11,0.30)');
      shade.addColorStop(0.32, 'rgba(14,13,11,0.48)');
      shade.addColorStop(0.68, 'rgba(14,13,11,0.54)');
      shade.addColorStop(1, 'rgba(14,13,11,0.18)');
    } else {
      shade.addColorStop(0, 'rgba(14,13,11,0.32)');
      shade.addColorStop(0.28, 'rgba(14,13,11,0.03)');
      shade.addColorStop(0.52, 'rgba(14,13,11,0.15)');
      shade.addColorStop(1, 'rgba(14,13,11,0.80)');
    }
    ctx.fillStyle = shade;
    ctx.fillRect(0, 0, SIZE.width, SIZE.height);
    const brand = fitText(ctx, session.storeName, 780, 40, 26, 1.2, 500);
    textBlock(ctx, brand, padding, 66, '#f5f0e7', 500);
    ctx.textAlign = 'right';
    ctx.fillStyle = '#f5f0e7';
    ctx.font = `400 22px ${FONT}`;
    ctx.fillText(`${String(index + 1).padStart(2, '0')} / 03`, SIZE.width - padding, 68);
    ctx.textAlign = layout === 'center' ? 'center' : 'left';
    const title = fitText(ctx, card.headline, width, 252, 76, 1.18, 650);
    const body = fitText(ctx, card.body, width, 170, 36, 1.48, 400);
    const totalHeight = title.height + 28 + body.height;
    const top = layout === 'top_left' ? 180 : layout === 'center'
      ? (SIZE.height - totalHeight) / 2 : SIZE.height - padding - totalHeight;
    const textX = layout === 'center' ? SIZE.width / 2 : padding;
    ctx.shadowColor = 'rgba(0,0,0,0.16)';
    ctx.shadowBlur = 10;
    ctx.shadowOffsetY = 2;
    textBlock(ctx, title, textX, top, '#fffaf2', 650);
    textBlock(ctx, body, textX, top + title.height + 28, '#f6f0e7', 400);
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;
    ctx.textAlign = 'left';
    if (photo.provenance === 'ai_concept') {
      ctx.font = `400 24px ${FONT}`;
      const label = 'AI 연출 이미지';
      const labelWidth = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(14,13,11,0.66)';
      ctx.fillRect(SIZE.width - padding - labelWidth - 16, SIZE.height - 48, labelWidth + 24, 36);
      ctx.textAlign = 'right';
      ctx.fillStyle = '#fffaf2';
      ctx.fillText(label, SIZE.width - padding, SIZE.height - 43);
      ctx.textAlign = 'left';
    }
    canvas.setAttribute('aria-label', `${index + 1}장 사진 카드 미리보기. ${card.headline}. ${card.body}`);
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
        document.fonts.load(`650 76px ${FONT}`, text),
        document.fonts.load(`400 36px ${FONT}`, text),
        document.fonts.ready,
      ]),
      new Promise((resolve) => { timer = setTimeout(resolve, 6000); }),
    ]);
    clearTimeout(timer);
  }

  async function savePNG(session, index) {
    const editor = session.editors[index];
    const card = session.cards[index];
    const photo = session.photos[index];
    const operation = session.operation;
    if (!editor || editor.download.disabled || !photo) return;
    editor.download.disabled = true;
    editor.download.textContent = '저장 준비 중';
    try {
      await loadFonts(session);
      if (!active(session) || session.operation !== operation || session.cards[index] !== card || session.photos[index] !== photo) return;
      drawCard(session, index);
      const blob = await new Promise((resolve) => editor.canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('이미지를 저장하지 못했어요. 다시 시도해 주세요.');
      if (!active(session) || session.operation !== operation || session.cards[index] !== card || session.photos[index] !== photo) return;
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
    if (current) disposePhotos(current);
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
      cards: [], editors: [], caption: '', warnings: [], theme: 'warm', busy: false, operation: 0, controller: null,
      photos: [null, null, null], photoOperations: [0, 0, 0], photoLoading: [false, false, false],
      photoMessages: [null, null, null], layouts: [null, null, null],
      imageJobs: [null, null, null], imageBatch: null,
    };
    current = session;
    const header = element('div', 'cs-heading');
    const title = element('h2', 'card-title', '사진으로 완성하는 카드뉴스');
    title.id = 'card-studio-title';
    root.setAttribute('aria-labelledby', title.id);
    header.append(title, element('p', 'cs-subtitle', '짧은 문구에 AI 연출 이미지나 매장 사진을 더해요. 이미지는 화면을 꽉 채워 저장해요.'));
    const actions = element('div', 'cs-actions');
    session.generate = button('문구 3장 만들기', 'cs-button cs-primary', () => generate(session));
    session.cancelButton = button('문구 생성 중단', 'cs-button cs-secondary', () => {
      cancel();
      setMessage(session, '문구 생성을 중단했어요.');
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
    root.append(header, actions, element('p', 'cs-photo-privacy', '직접 고른 사진은 이 브라우저에서만 사용해요. JPG · PNG · WebP, 장당 15MB까지.'), session.message, session.editor);
    if (!session.sources.length) setMessage(session, '확인한 매장 출처가 있어야 카드를 만들 수 있어요. 다시 진단해 주세요.');
    setBusy(session, false);
  }

  document.fonts?.addEventListener('loadingdone', () => {
    if (current && active(current) && current.cards.length) drawAll(current);
  });
  window.addEventListener('pagehide', (event) => {
    if (!event.persisted && current) { cancel(); disposePhotos(current); }
  });
  window.CardStudio = { render, cancel };
})();
