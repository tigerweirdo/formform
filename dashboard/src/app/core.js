/* ====================================================================== çekirdek: yardımcılar, durum, veri katmanı */
var OWNER = 'tigerweirdo', REPO_NAME = 'formform';
var GH = 'https://github.com/' + OWNER + '/' + REPO_NAME;
var SERVER = 'github';                 // bağlayıcının görünen adı (manifest.json ile aynı)
var CONFIRM_WORD = 'PAYLAŞ';
var REFRESH_MS = 120000;               // watchTool alt sınırı 30 sn; iki dakikada bir yenilenir
var RUN_PAGE = 50;
var W = FF.WORKFLOWS, C = FF.C, MIN = FF.MIN, HOUR = FF.HOUR, DAY = FF.DAY;

// Bu hatalarda veri geri çekilir (yetki sorunu). Diğerleri geçicidir: son iyi veri kalır.
var DENIAL = { needs_reauth: 1, server_not_connected: 1, selection_required: 1, blocked_by_policy: 1, approval_required: 1,
               not_in_manifest: 1, consent_required: 1, not_granted: 1, capability_disabled: 1, capability_removed: 1,
               token_missing: 1, forbidden: 1 };

// ------------------------------------------------------------------ DOM yardımcıları (yalnız textContent, HTML metni yok)
function safeUrl(u) { return typeof u === 'string' && /^https:\/\/github\.com\//.test(u); }
function setAttrs(el, attrs) {
  if (!attrs) return;
  Object.keys(attrs).forEach(function (k) {
    var v = attrs[k];
    if (v == null || v === false) return;
    if (k === 'class') el.setAttribute('class', v);
    else if (k === 'text') el.textContent = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'href') { if (safeUrl(v)) { el.setAttribute('href', v); el.setAttribute('target', '_blank'); el.setAttribute('rel', 'noopener noreferrer'); } }
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'indeterminate') el[k] = v;
    else el.setAttribute(k, v === true ? '' : String(v));
  });
}
function append(el, c) {
  if (c == null || c === false) return;
  if (Array.isArray(c)) { c.forEach(function (x) { append(el, x); }); return; }
  el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
}
function h(tag, attrs) {
  var el = document.createElement(tag);
  setAttrs(el, attrs);
  for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
  return el;
}
var SVGNS = 'http://www.w3.org/2000/svg';
function sv(tag, attrs) {
  var el = document.createElementNS(SVGNS, tag);
  if (attrs) Object.keys(attrs).forEach(function (k) {
    var v = attrs[k];
    if (v == null || v === false) return;
    if (k === 'text') el.textContent = v; else el.setAttribute(k, String(v));
  });
  for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function $(id) { return document.getElementById(id); }
function nz(v, d) { return FF.finite(v) ? v : d; }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

var store = {
  get: function (k, d) { try { var v = window.localStorage.getItem('ff-' + k); return v == null ? d : v; } catch (e) { return d; } },
  set: function (k, v) { try { window.localStorage.setItem('ff-' + k, v); } catch (e) { /* kalıcı depolama yok */ } }
};

// ------------------------------------------------------------------ durum
function emptyData() {
  var runs = {};
  W.forEach(function (w) { runs[w.key] = []; });
  return { videos: [], dna: [], config: {}, billing: null, workflows: [], runs: runs, releases: [], issues: [], commits: [] };
}
var S = {
  mode: 'live', mcp: undefined, now: Date.now(), ver: 0,
  live: emptyData(), demo: null, src: {}, have: {}, firstCallAt: 0,
  tab: 'genel', refreshing: false, dirty: false,
  filters: { q: '', format: '', mode: '', state: '' }, sort: { key: 'pub', dir: -1 },
  metric: store.get('metric', 'score'), view: {}, dnaDim: 'structure', oddsOpen: {}, runFilter: '',
  openVideo: null, ctrl: {}, timers: {},
  token: '', setup: false, unsubs: []          // bağımsız sürüm: erişim anahtarı ve kurulum ekranı
};
function D() { return S.mode === 'demo' ? S.demo : S.live; }
// Gerçek (canlı) işlem yapılabilir mi: bağlantı var, örnek kipinde değil, bağımsız sürümde anahtar girilmiş
function liveOk() { return S.mode === 'live' && !!S.mcp && (!STANDALONE || !!S.token); }

// ------------------------------------------------------------------ veri kaynakları (hepsi salt okunur)
function fileSource(key, path, parse, apply, clearFn) {
  return { key: key, tool: 'get_file_contents', label: path + ' (veri dalı)',
           input: { owner: OWNER, repo: REPO_NAME, path: path, ref: 'refs/heads/veri' },
           parse: function (r) {
             var t = FF.fileText(r);
             if (t == null) throw new Error(path + ' içeriği okunamadı (yanıt biçimi tanınmadı)');
             return parse(t);
           }, apply: apply, clear: clearFn };
}
var SOURCES = [
  fileSource('videos', 'videos.csv', FF.parseCSV, function (d, v) { d.videos = v; }, function (d) { d.videos = []; }),
  fileSource('dna', 'dna_weights.csv', FF.parseCSV, function (d, v) { d.dna = v; }, function (d) { d.dna = []; }),
  fileSource('config', 'config.json', FF.parseConfig, function (d, v) { d.config = v; }, function (d) { d.config = {}; }),
  { key: 'workflows', tool: 'actions_list', label: 'Workflow listesi', input: { method: 'list_workflows', owner: OWNER, repo: REPO_NAME },
    parse: function (r) { return FF.parseWorkflows(r.payload); }, apply: function (d, v) { d.workflows = v; }, clear: function (d) { d.workflows = []; } }
].concat(W.map(function (w) {
  return { key: 'runs:' + w.key, tool: 'actions_list', label: w.name + ' çalışmaları',
           input: { method: 'list_workflow_runs', owner: OWNER, repo: REPO_NAME, resource_id: w.file, perPage: RUN_PAGE },
           parse: function (r) { return FF.parseRuns(r.payload); },
           apply: function (d, v) { d.runs[w.key] = v; }, clear: function (d) { d.runs[w.key] = []; } };
}), [
  { key: 'releases', tool: 'list_releases', label: 'Release’ler (render kuyruğu)',
    input: { owner: OWNER, repo: REPO_NAME, perPage: 30, fields: ['id', 'tag_name', 'name', 'published_at', 'prerelease', 'draft', 'html_url'] },
    parse: function (r) { return FF.parseReleases(r.payload); }, apply: function (d, v) { d.releases = v; }, clear: function (d) { d.releases = []; } },
  { key: 'issues', tool: 'list_issues', label: 'Açık issue’lar (hata kayıtları)',
    input: { owner: OWNER, repo: REPO_NAME, state: 'OPEN', perPage: 30, fields: ['number', 'title', 'state', 'created_at', 'updated_at'] },
    parse: function (r) { return FF.parseIssues(r.payload).map(function (i) { if (!i.url) i.url = GH + '/issues/' + i.number; return i; }); }, apply: function (d, v) { d.issues = v; }, clear: function (d) { d.issues = []; } },
  { key: 'commits', tool: 'list_commits', label: 'Veri dalı geçmişi',
    input: { owner: OWNER, repo: REPO_NAME, sha: 'veri', perPage: 5, fields: ['sha', 'html_url', 'commit'] },
    parse: function (r) { return FF.parseCommits(r.payload); }, apply: function (d, v) { d.commits = v; }, clear: function (d) { d.commits = []; } }
]);

function srcState(key) {
  if (S.mode === 'demo') return { ok: true, at: S.now };
  var s = S.src[key] || {};
  return { ok: !!S.have[key], at: s.at, error: s.error, stale: s.stale, pending: !S.have[key] && !s.error };
}
function sourceMap() {
  var m = {};
  SOURCES.forEach(function (s) { m[s.key] = srcState(s.key); });
  return m;
}

function watchSource(src) {
  S.unsubs.push(S.mcp.watchTool(SERVER, src.tool, src.input, function (ev) {
    var prev = S.src[src.key] || {};
    if (ev.type === 'data') {
      try {
        src.apply(S.live, src.parse(ev.result));
        S.have[src.key] = true;
        S.src[src.key] = { at: (ev.result.cache && ev.result.cache.storedAt) || Date.now(), stale: !!(ev.result.cache && ev.result.cache.revalidating) };
      } catch (e) {
        S.src[src.key] = { at: prev.at, error: { code: 'parse', message: String((e && e.message) || e) } };
      }
    } else {
      var err = ev.error || { code: 'upstream_error', message: 'bilinmeyen hata' };
      if (DENIAL[err.code]) { src.clear(S.live); S.have[src.key] = false; }
      S.src[src.key] = { at: prev.at, error: err };
    }
    S.ver++;
    scheduleRender();
  }, { refetchInterval: REFRESH_MS }));
}

function explainStandalone(code, msg) {          // bağımsız sürümde bağlayıcı yok, anahtar var
  switch (code) {
    case 'needs_reauth': return { title: 'Anahtar geçersiz ya da süresi dolmuş', fix: 'Sağ üstteki Anahtar düğmesinden yeni bir anahtar girin.' };
    case 'forbidden': return { title: 'Anahtarın bu işlem için yetkisi yok', fix: 'Anahtarı oluştururken şu izinleri verin: Actions (Read and write), Contents (Read-only), Issues (Read-only). Eksikse Anahtar düğmesinden yenisini girin.' + (msg ? ' (GitHub: ' + msg.slice(0, 120) + ')' : '') };
    case 'network': return { title: 'GitHub’a ulaşılamadı', fix: 'İnternet bağlantınızı kontrol edin. Reklam engelleyici ya da VPN api.github.com’u engelliyor olabilir. Panel yeniden dener, son iyi veri gösterilir.' };
    case 'rate_limited': return { title: 'GitHub istek sınırına ulaşıldı', fix: 'Birkaç dakika sonra kendiliğinden yeniden denenecek.' };
    case 'token_missing': return { title: 'Anahtar girilmemiş', fix: 'Sağ üstteki Anahtar düğmesinden girin.' };
    default: return null;
  }
}
function explainError(err) {
  var code = (err && err.code) || 'upstream_error', msg = (err && err.message) || '';
  if (STANDALONE) { var se = explainStandalone(code, msg); if (se) return se; }
  switch (code) {
    case 'needs_reauth': return { title: 'GitHub bağlantısının süresi dolmuş', fix: 'claude.ai → Ayarlar → Bağlayıcılar bölümünden GitHub’ı yeniden bağlayın, sonra Yenile’ye basın.' };
    case 'server_not_connected': case 'selection_required': return { title: 'GitHub bağlayıcısı bulunamadı', fix: 'claude.ai → Ayarlar → Bağlayıcılar bölümünden GitHub’ı ekleyin (birden fazla varsa birini seçin), sonra Yenile’ye basın.' };
    case 'not_in_manifest': case 'consent_required': case 'not_granted': return { title: 'Bu sayfaya GitHub erişimi verilmemiş', fix: 'Sayfanın İzinler menüsünden GitHub’a izin verin, sonra Yenile’ye basın.' };
    case 'blocked_by_policy': case 'approval_required': return { title: 'Kuruluşunuzun ilkesi GitHub araçlarını engelliyor', fix: 'Yöneticinizden bu araçlara izin vermesini isteyin.' };
    case 'server_unavailable': case 'upstream_error': return { title: 'GitHub’a şu an ulaşılamıyor', fix: 'Panel birkaç dakika içinde kendiliğinden yeniden dener. Son iyi veri gösteriliyor.' };
    case 'tool_error': return { title: 'GitHub bir hata bildirdi', fix: msg ? msg.slice(0, 240) : 'Depo adı ve erişim iznini kontrol edin.' };
    case 'parse': return { title: 'Yanıt beklenen biçimde değil', fix: msg + ' Panel güncellenmeli olabilir.' };
    default: return { title: 'Beklenmeyen hata (' + code + ')', fix: msg.slice(0, 240) };
  }
}

// ------------------------------------------------------------------ türetilmiş değerler (bir kez hesaplanır)
function adherenceSince(w, runs, wfs, now) {
  var def = wfs.filter(function (x) { return x.path === '.github/workflows/' + w.file; })[0];
  var created = def ? FF.parseTimeMs(def.created_at) : NaN;
  var times = runs.map(function (r) { return FF.parseTimeMs(r.created_at); }).filter(isFinite);
  var oldest = times.length ? Math.min.apply(null, times) : NaN;
  var since = runs.length >= RUN_PAGE ? oldest - 5 * MIN : created;          // elde yalnız son çalışmalar varsa oradan başla
  if (!isFinite(since)) since = isFinite(oldest) ? oldest - 5 * MIN : now - 7 * DAY;
  return Math.max(since, now - 14 * DAY);
}

function derive() {
  var key = S.mode + ':' + S.ver + ':' + Math.floor(S.now / MIN) + ':' + (S.demo ? S.demo.videos.length : 0);
  if (S.dvKey === key) return S.dvVal;
  var d = D(), now = S.now, V = { now: now, d: d };
  V.src = sourceMap();
  V.st = FF.buildStats(d.videos, d.dna, now);
  V.vids = FF.enrichVideos(d.videos, now);
  V.adh = {};
  W.forEach(function (w) {
    var since = adherenceSince(w, d.runs[w.key] || [], d.workflows, now);
    V.adh[w.key] = FF.adherenceSlots(FF.expectedSlots(w, since, now), d.runs[w.key] || [], now);
  });
  V.health = FF.healthChecks({ nowMs: now, stats: V.st, state: d, sources: V.src, adherence: V.adh });
  V.level = FF.worst(V.health);
  V.odds = FF.selectionOdds(LIB, d.dna, d.videos);
  V.releases = d.releases.map(FF.parseRelease).sort(function (a, b) { return b.atMs - a.atMs; });
  V.ready = V.releases.filter(function (r) { return r.state === 'render'; });
  V.events = [];
  W.forEach(function (w) {
    var a = V.adh[w.key], est = (a && a.total >= 2 && a.medianDelay > C.DELAY_OK_MS) ? a.medianDelay : 0;
    FF.nextActive(w, now, 4).forEach(function (t) { V.events.push({ w: w, at: t, est: t + est, delay: est }); });
  });
  V.events.sort(function (a, b) { return a.at - b.at; });
  V.allRuns = [];
  W.forEach(function (w) { (d.runs[w.key] || []).forEach(function (r) { V.allRuns.push({ w: w, run: r }); }); });
  V.allRuns.sort(function (a, b) { return FF.parseTimeMs(b.run.created_at) - FF.parseTimeMs(a.run.created_at); });
  var cfg = d.config || {}, exp = FF.parseTimeMs(cfg.ig_token_expires_at), upd = FF.parseTimeMs(cfg.ig_token_updated_at);
  V.token = { exp: exp, upd: upd, leftDays: isFinite(exp) ? (exp - now) / DAY : NaN };
  S.dvKey = key; S.dvVal = V;
  return V;
}

// ------------------------------------------------------------------ görünüm ortak parçaları
function chip(kind, text, title) {
  var icons = { ok: 'M3 8.5l3 3 7-7', warn: 'M8 3v6M8 12v.5', crit: 'M4 4l8 8M12 4l-8 8', info: 'M8 7v5M8 4v.5', run: 'M5 3l8 5-8 5z', wait: 'M8 4v4l3 2', mute: 'M4 8h8' };
  var svg = sv('svg', { viewBox: '0 0 16 16', width: 12, height: 12, 'aria-hidden': 'true', class: 'chip-i' },
    sv('path', { d: icons[kind] || icons.info, fill: kind === 'run' ? 'currentColor' : 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  return h('span', { class: 'chip chip-' + kind, title: title || null }, svg, h('span', { text: text }));
}
function levelChip(level) {
  var map = { ok: ['ok', 'Sağlıklı'], info: ['info', 'Bilgi'], unknown: ['info', 'Bilinmiyor'], warn: ['warn', 'Dikkat'], crit: ['crit', 'Sorun'] };
  return chip(map[level][0], map[level][1]);
}
function runChip(run) {
  var st = FF.runStatus(run);
  var map = { ok: ['ok', 'Başarılı'], fail: ['crit', 'Başarısız'], cancel: ['mute', 'İptal'], running: ['run', 'Çalışıyor'], queued: ['wait', 'Sırada'], none: ['mute', 'Yok'] };
  return chip(map[st][0], map[st][1]);
}
function link(text, href, cls) { return safeUrl(href) ? h('a', { class: cls || 'lnk', href: href, text: text }) : h('span', { text: text }); }
function empty(title, text) { return h('div', { class: 'empty' }, h('strong', { text: title }), text ? h('p', { text: text }) : null); }
function card(title, sub, body, opts) {
  opts = opts || {};
  return h('section', { class: 'card' + (opts.cls ? ' ' + opts.cls : ''), id: opts.id || null },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: title }), sub ? h('p', { class: 'sub', text: sub }) : null), opts.right || null),
    body);
}
function srcNote(keys) {                     // bu bölümün beslendiği kaynaklardan biri başarısızsa kısa not
  if (S.mode === 'demo') return null;
  var bad = keys.map(function (k) { return { k: k, s: srcState(k) }; }).filter(function (x) { return x.s.error || x.s.pending; });
  if (!bad.length) return null;
  var failing = bad.filter(function (x) { return x.s.error; });
  if (!failing.length) return h('p', { class: 'note' }, 'Veri bekleniyor…');
  var e = explainError(failing[0].s.error);
  return h('p', { class: 'note note-warn' }, h('strong', { text: e.title + '. ' }), e.fix);
}
function copyBtn(text, label) {
  var b = h('button', { class: 'btn btn-ghost btn-xs', type: 'button', 'aria-label': label || 'Kopyala' }, 'Kopyala');
  b.addEventListener('click', function () {
    var done = function (ok) { b.textContent = ok ? 'Kopyalandı' : 'Seçip kopyalayın'; setTimeout(function () { b.textContent = 'Kopyala'; }, 1600); };
    try { navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); }); } catch (e) { done(false); }
  });
  return b;
}

// ------------------------------------------------------------------ ipucu kutusu (grafikler için, yalnız textContent)
var tipEl = null;
function tipOn(el, getLines) {
  function show(ev) {
    tipEl = tipEl || $('tip');
    var lines = getLines();
    if (!lines || !lines.length) return;
    clear(tipEl);
    lines.forEach(function (l, i) {
      if (typeof l === 'string') tipEl.appendChild(h('div', { class: i === 0 ? 'tip-t' : 'tip-l', text: l }));
      else tipEl.appendChild(h('div', { class: 'tip-r' }, h('b', { text: l[1] }), h('span', { text: l[0] })));
    });
    tipEl.hidden = false;
    var r = el.getBoundingClientRect(), x = ev && ev.clientX != null && ev.type !== 'focus' ? ev.clientX : r.left + r.width / 2;
    var y = ev && ev.clientY != null && ev.type !== 'focus' ? ev.clientY : r.top;
    var tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    tipEl.style.left = clamp(x + 12, 8, window.innerWidth - tw - 8) + 'px';
    tipEl.style.top = clamp(y - th - 12 < 8 ? y + 16 : y - th - 12, 8, window.innerHeight - th - 8) + 'px';
  }
  function hide() { tipEl = tipEl || $('tip'); tipEl.hidden = true; }
  el.addEventListener('pointerenter', show);
  el.addEventListener('pointermove', show);
  el.addEventListener('pointerleave', hide);
  el.addEventListener('focus', show);
  el.addEventListener('blur', hide);
}

// ------------------------------------------------------------------ boyuta uyan SVG (metin ölçeklenmesin diye genişlik ölçülüp yeniden çizilir)
function fitSvg(draw, height) {
  var wrap = h('div', { class: 'fit', style: 'min-height:' + (height || 120) + 'px' }), last = 0;
  function paint() {
    var w = Math.floor(wrap.clientWidth);
    if (!w || Math.abs(w - last) < 2) return;
    last = w; clear(wrap); wrap.appendChild(draw(w));
  }
  if (typeof ResizeObserver === 'function') new ResizeObserver(function () { window.requestAnimationFrame(paint); }).observe(wrap);
  else window.addEventListener('resize', paint);
  wrap.paint = paint;
  setTimeout(paint, 0);
  return wrap;
}
function niceMax(v) {
  if (!(v > 0)) return 1;
  var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}

// ------------------------------------------------------------------ çizim zamanlayıcısı
var renderQueued = false;
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  window.requestAnimationFrame(function () { renderQueued = false; render(); });
}
