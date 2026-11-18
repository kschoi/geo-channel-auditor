const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const sourcePath = require('node:path').join(__dirname, '..', 'app', 'static', 'cards.js');
let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace('window.CardStudio = { render, cancel };',
  'window.CardStudio = { render, cancel, photoDimensions, checkDimensions, drawPhoto, loadPhoto, savePNG, stopPhotoLoads, updateDownloads, validateResult, drawCard, startImageJob, startImages, imageAction, pauseImageJob, stopImageWaits, generatedImageFile, setCurrent: (s) => { current = s; } };');
const revoked = [], created = [], blobs = new Map(), decode = new Map();
function canvas() { return { width: 0, height: 0, getContext: () => ({ drawImage() {} }) }; }
class FakeImage {
  set src(value) {
    this.url = value;
    const file = blobs.get(value);
    if (file) {
      this.file = file;
      this.naturalWidth = 1080;
      this.naturalHeight = 1350;
    }
  }
  decode() { return decode.get(this.file.tag)?.promise || Promise.resolve(); }
}
const URLMock = class extends URL {};
URLMock.createObjectURL = (file) => { const url = `blob:${created.length}`; created.push(url); blobs.set(url, file); return url; };
URLMock.revokeObjectURL = (url) => { revoked.push(url); blobs.delete(url); };
let network = async () => { throw new Error('Unexpected network call'); };
let fakeNow = 0;
const keyField = { value: '' };
const requests = [];
const context = vm.createContext({
  document: { createElement: canvas, getElementById: (id) => id === 'api_key' ? keyField : null }, window: { addEventListener() {} },
  URL: URLMock, Image: FakeImage, clearTimeout, navigator: {},
  setTimeout: (callback, ms) => setTimeout(callback, ms === 2000 ? 0 : ms),
  AbortController, File: require('node:buffer').File, atob,
  crypto: { randomUUID: require('node:crypto').randomUUID },
  Date: class extends Date { static now() { return fakeNow; } },
  fetch: (url, options = {}) => { requests.push({ url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null }); return network(url, options); },
});
vm.runInContext(source, context);
const api = context.window.CardStudio;
function headerPNG(width = 1080, height = 1350) {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10], 0);
  bytes.set([73, 72, 68, 82], 12);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width); view.setUint32(20, height);
  return bytes;
}
function file(tag, changes = {}) {
  const bytes = headerPNG();
  return { tag, name: tag + '.png', type: 'image/png', size: bytes.length, arrayBuffer: async () => bytes.buffer, ...changes };
}
function session(photo = null) {
  return { root: { isConnected: true }, busy: false, operation: 0,
    photoOperations: [0, 0, 0], photoLoading: [false, false, false], photoMessages: [null, null, null],
    photos: [photo, null, null], editors: [], cards: [{ headline: '제목', body: '본문', visual_prompt: '사진 연출안' }],
    imageJobs: [null, null, null], keyButton: { hidden: true },
    message: { classList: { toggle() {} }, setAttribute() {} } };
}
function deferred(tag) {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  decode.set(tag, { promise, resolve, reject });
  return decode.get(tag);
}
const tick = () => new Promise((resolve) => setImmediate(resolve));


function reply(status, data) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
function complete(requestId) {
  return { request_id: requestId, status: 'complete', image: {
    b64_json: Buffer.from(headerPNG()).toString('base64'), media_type: 'image/png',
    model: 'test-image', cost_usd: 0.01, provenance: 'ai_concept',
  } };
}
function networkDeferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function startState() {
  requests.length = 0; fakeNow = 0; keyField.value = '';
  const state = session(); api.setCurrent(state); return state;
}
async function imageNetworkTests() {
  // Duplicate clicks while POST is pending must not issue another billable request.
  let state = startState(), first = networkDeferred();
  network = () => first.promise;
  const original = api.startImages(state, [0]);
  const originalId = state.imageJobs[0].requestId;
  await api.startImages(state, [0]);
  assert.equal(requests.length, 1);
  first.resolve(reply(202, complete(originalId)));
  await original;
  assert.equal(state.photos[0].provenance, 'ai_concept');
  assert.equal(state.photos[0].requestId, originalId);

  // A failed poll is resumed with GET and the same request ID, never another POST.
  state = startState();
  network = async (url, options) => {
    if (options.method === 'POST') return reply(202, { request_id: JSON.parse(options.body).request_id, status: 'pending' });
    throw new Error('offline');
  };
  await api.startImages(state, [0]);
  const resumeId = state.imageJobs[0].requestId;
  assert.equal(state.imageJobs[0].status, 'pending');
  assert.equal(state.imageJobs[0].waiting, false);
  assert.equal(requests.filter(r => r.method === 'POST').length, 1);
  network = async () => reply(200, complete(resumeId));
  await api.imageAction(state, 0);
  assert.equal(state.photos[0].requestId, resumeId);
  assert.equal(requests.filter(r => r.method === 'POST').length, 1);

  // Local cancellation retains the ID. A late response cannot apply an image.
  state = startState(); first = networkDeferred(); network = () => first.promise;
  const cancelled = api.startImages(state, [0]);
  const cancelledId = state.imageJobs[0].requestId;
  api.pauseImageJob(state, 0);
  assert.equal(state.imageJobs[0].status, 'pending');
  assert.match(state.imageJobs[0].message, /서버 생성은 계속/);
  await api.startImages(state, [0]);
  assert.equal(requests.length, 1);
  first.resolve(reply(202, complete(cancelledId)));
  await cancelled;
  assert.equal(state.photos[0], null);
  network = async () => reply(200, complete(cancelledId));
  await api.imageAction(state, 0);
  assert.equal(state.photos[0].requestId, cancelledId);
  assert.equal(requests.filter(r => r.method === 'POST').length, 1);

  // Uploading a local picture during generation prevents automatic replacement.
  state = startState(); first = networkDeferred(); network = () => first.promise;
  const awaitingPhoto = api.startImages(state, [0]);
  const uploadRaceId = state.imageJobs[0].requestId;
  await api.loadPhoto(state, 0, file('owner-photo'));
  first.resolve(reply(202, complete(uploadRaceId)));
  await awaitingPhoto;
  assert.equal(state.photos[0].name, 'owner-photo.png');
  assert.equal(state.photos[0].provenance, 'user_photo');
  assert.match(state.imageJobs[0].message, /자동 적용하지/);
  await api.imageAction(state, 0);
  assert.equal(state.photos[0].requestId, uploadRaceId);
  assert.equal(requests.length, 1, 'explicitly applying an existing image is not a new API request');
  await api.loadPhoto(state, 0, file('owner-photo-again'));
  assert.equal(state.photos[0].provenance, 'user_photo');
  assert.equal(state.photos[0].requestId, null);

  // A new copy set must not receive an old prompt's image without an explicit click.
  state = startState(); first = networkDeferred(); network = () => first.promise;
  const awaitingCopy = api.startImages(state, [0]);
  const copyRaceId = state.imageJobs[0].requestId;
  state.cards[0] = { headline: '새 문구', body: '새 본문', visual_prompt: '새 사진' };
  first.resolve(reply(202, complete(copyRaceId)));
  await awaitingCopy;
  assert.equal(state.photos[0], null);
  assert.equal(state.imageJobs[0].status, 'complete');

  // Detaching the previous result prevents a late response from adding a photo.
  state = startState(); first = networkDeferred(); network = () => first.promise;
  const detached = api.startImages(state, [0]);
  const detachedId = state.imageJobs[0].requestId;
  state.root.isConnected = false;
  first.resolve(reply(202, complete(detachedId)));
  await detached;
  assert.equal(state.photos[0], null);

  // A missing/expired job is terminal and needs a separate, explicit new request.
  for (const status of [404, 410]) {
    state = startState();
    network = async (_url, options) => options.method === 'POST'
      ? reply(202, { request_id: JSON.parse(options.body).request_id, status: 'pending' })
      : reply(status, { detail: 'gone' });
    await api.startImages(state, [0]);
    assert.equal(state.imageJobs[0].status, 'error');
    assert.equal(requests.filter(r => r.method === 'POST').length, 1);
  }

  // Deadline exhaustion preserves the request for retrieval and does not retry POST.
  state = startState();
  network = async (_url, options) => {
    fakeNow = 480001;
    return reply(202, { request_id: JSON.parse(options.body).request_id, status: 'pending' });
  };
  await api.startImages(state, [0]);
  assert.equal(state.imageJobs[0].status, 'pending');
  assert.match(state.imageJobs[0].message, /대기 시간이 지났/);
  assert.equal(requests.length, 1);

  // One key snapshot is used for the three POSTs, then cleared from the input and state.
  state = startState(); state.cards = [0, 1, 2].map(i => ({ headline: '제목' + i, body: '본문', visual_prompt: '연출안' + i }));
  state.requestKeyUsed = true; keyField.value = 'one-time-test-key';
  network = async (_url, options) => reply(202, complete(JSON.parse(options.body).request_id));
  await api.startImages(state, [0, 1, 2, 2, 4]);
  assert.equal(requests.length, 3);
  assert.equal(new Set(requests.map(r => r.body.request_id)).size, 3);
  assert.ok(requests.every(r => r.body.api_key === 'one-time-test-key'));
  assert.equal(keyField.value, '');
  assert.ok(!JSON.stringify(state).includes('one-time-test-key'));
  assert.ok(state.photos.every(photo => photo.provenance === 'ai_concept'));

  // Malformed image bytes cannot destroy the existing photo or trigger another POST.
  state = startState(); await api.loadPhoto(state, 0, file('keep-me'));
  const keep = state.photos[0];
  network = async (_url, options) => {
    const data = complete(JSON.parse(options.body).request_id); data.image.b64_json = '<bad>';
    return reply(202, data);
  };
  await api.startImages(state, [0]);
  assert.equal(state.photos[0], keep);
  assert.equal(state.imageJobs[0].status, 'complete');
  assert.match(state.imageJobs[0].message, /기존 사진은 유지/);
  assert.equal(requests.length, 1);

  // Provenance must appear in the canvas output for AI images, and disappear for owner photos.
  const drawn = [];
  const ctx = { clearRect() {}, fillRect() {}, drawImage() {}, save() {}, restore() {},
    fillText: (text) => drawn.push(text), measureText: (text) => ({ width: text.length * 24 }),
    createLinearGradient: () => ({ addColorStop() {} }) };
  state = session({ image: { width: 1080, height: 1350 }, focusX: 50, focusY: 50, provenance: 'ai_concept' });
  state.theme = 'warm'; state.layouts = ['bottom_left']; state.storeName = '디델리';
  state.editors = [{ canvas: { getContext: () => ctx, setAttribute() {} } }];
  api.drawCard(state, 0);
  assert.ok(drawn.includes('AI 연출 이미지'));
  drawn.length = 0; state.photos[0].provenance = 'user_photo';
  api.drawCard(state, 0);
  assert.ok(!drawn.includes('AI 연출 이미지'));
}

(async () => {
  const parsed = api.photoDimensions(headerPNG());
  assert.equal(parsed.type, 'image/png'); assert.equal(parsed.width, 1080); assert.equal(parsed.height, 1350);
  const jpeg = Uint8Array.from([255, 216, 255, 192, 0, 11, 8, 5, 70, 4, 56, 1, 1, 17, 0]);
  assert.equal(api.photoDimensions(jpeg).height, 1350);
  const webp = new Uint8Array(30);
  webp.set(Buffer.from('RIFF'), 0); webp.set(Buffer.from('WEBPVP8X'), 8);
  webp[24] = 55; webp[25] = 4; webp[27] = 69; webp[28] = 5;
  assert.equal(api.photoDimensions(webp).width, 1080);
  webp[20] = 2;
  assert.throws(() => api.photoDimensions(webp), /정지 사진/);
  assert.throws(() => api.photoDimensions(new Uint8Array(Buffer.from('<svg/>'))), /정상적인 사진/);
  for (const dimensions of [[0, 1], [12001, 1], [8000, 6000], [10, 13000]]) {
    assert.throws(() => api.checkDimensions(...dimensions), /사진이 너무 커요/);
  }
  api.checkDimensions(6000, 4000);
  const crop = [];
  api.drawPhoto({ drawImage(...args) { crop.push(args); } }, { image: { width: 3000, height: 1500 }, focusX: 100, focusY: 50 });
  assert.equal(crop[0][1], 1800); // right edge crop: 3000 - 1200
  assert.equal(crop[0][3], 1200); assert.equal(crop[0][4], 1500);
  assert.equal(crop[0][7], 1080); assert.equal(crop[0][8], 1350);

  const prior = { image: canvas(), name: 'prior.png', focusX: 35, focusY: 60 };
  prior.image.width = 1080; prior.image.height = 1350;
  let state = session(prior); api.setCurrent(state);
  await api.loadPhoto(state, 0, file('too-large', { size: 16 * 1024 * 1024 }));
  assert.equal(state.photos[0], prior); assert.equal(prior.image.width, 1080);
  assert.match(state.photoMessages[0].text, /15MB/);
  await api.loadPhoto(state, 0, file('mismatch', { type: 'image/jpeg' }));
  assert.equal(state.photos[0], prior); assert.match(state.photoMessages[0].text, /실제 형식/);
  const broken = deferred('broken');
  const loadingBroken = api.loadPhoto(state, 0, file('broken'));
  await tick(); broken.reject(new Error('native decoder failure')); await loadingBroken;
  assert.equal(state.photos[0], prior); assert.match(state.photoMessages[0].text, /다른 사진/);
  assert.ok(!state.photoLoading[0]);

  const old = deferred('old'), newer = deferred('new');
  const loadOld = api.loadPhoto(state, 0, file('old'));
  await tick();
  const loadNew = api.loadPhoto(state, 0, file('new'));
  await tick(); newer.resolve(); await loadNew;
  assert.equal(state.photos[0].name, 'new.png');
  assert.equal(prior.image.width, 0);
  old.resolve(); await loadOld;
  assert.equal(state.photos[0].name, 'new.png');

  const interrupted = deferred('interrupted');
  const loadInterrupted = api.loadPhoto(state, 0, file('interrupted'));
  await tick(); api.stopPhotoLoads(state); state.operation += 1;
  interrupted.resolve(); await loadInterrupted;
  assert.equal(state.photos[0].name, 'new.png'); assert.equal(state.photoLoading[0], false);
  assert.equal(created.length, revoked.length);
  assert.equal(new Set(revoked).size, revoked.length);

  state = session(); state.editors = [{ download: {} }];
  api.updateDownloads(state); assert.equal(state.editors[0].download.disabled, true);
  state.photos[0] = { image: canvas() };
  api.updateDownloads(state); assert.equal(state.editors[0].download.disabled, false);
  state.photoLoading[0] = true;
  api.updateDownloads(state); assert.equal(state.editors[0].download.disabled, true);
  // A new audit/cancel invalidates a PNG that is still waiting for its font.
  let resolveFonts;
  context.document.fonts = { load: () => Promise.resolve(), ready: new Promise(resolve => { resolveFonts = resolve; }) };
  state = session({ image: canvas() });
  state.storeName = '디델리'; state.location = '산본';
  let exported = 0;
  state.editors = [{ download: { disabled: false }, canvas: { getContext: () => null, toBlob: (done) => { exported += 1; done({}); } } }];
  api.setCurrent(state);
  const pendingSave = api.savePNG(state, 0);
  state.operation += 1;
  resolveFonts();
  await pendingSave;
  assert.equal(exported, 0, 'cancelled export must not start encoding');
  await imageNetworkTests();
  console.log('PASS: photo validation/crop/races/cancelled export + image API deduplication, resume GET, pause, timeout, stale-result protection, provenance, batch key handling');
})().catch((error) => { console.error(error); process.exitCode = 1; });
