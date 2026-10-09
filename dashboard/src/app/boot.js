/* ====================================================================== çizim ve açılış */
function renderHeader(V) {
  var pill = $('statusPill'), loaded = Object.keys(V.src).some(function (k) { return V.src[k].ok; });
  var kind = S.mode === 'live' && !loaded ? 'info' : (V.level === 'crit' ? 'crit' : (V.level === 'warn' ? 'warn' : 'ok'));
  var text = S.mode === 'live' && !loaded ? 'Bağlanıyor' : (V.level === 'crit' ? 'Sorun var' : (V.level === 'warn' ? 'Dikkat' : 'Sağlıklı'));
  if (S.setup) { kind = 'info'; text = 'Anahtar gerekli'; }
  clear(pill); pill.appendChild(chip(kind, text));
  var seg = $('modeSeg'); clear(seg);
  seg.appendChild(h('button', { type: 'button', class: S.mode === 'live' ? 'on' : '', 'aria-pressed': S.mode === 'live' ? 'true' : 'false', disabled: S.mcp === null || S.mcp === undefined && S.mode !== 'live', onclick: function () { setMode('live'); } }, 'Canlı'));
  seg.appendChild(h('button', { type: 'button', class: S.mode === 'demo' ? 'on' : '', 'aria-pressed': S.mode === 'demo' ? 'true' : 'false', onclick: function () { setMode('demo'); } }, 'Örnek veri'));
  $('keyBtn').hidden = !STANDALONE;
  var rb = $('refreshBtn'); rb.disabled = S.refreshing || (S.mode === 'live' && (!S.mcp || (STANDALONE && !S.token))); rb.textContent = S.refreshing ? 'Yenileniyor…' : 'Yenile';
  var at = 0;
  Object.keys(S.src).forEach(function (k) { if (S.src[k].at && S.src[k].at > at) at = S.src[k].at; });
  $('stamp').textContent = S.mode === 'demo' ? 'Örnek veri · gerçek değil' : (at ? 'Güncellendi ' + FF.fmtClock(at) : '');
}
function renderBanner(V) {
  var b = $('banner'), info = null;
  if (S.setup) { clear(b); b.hidden = true; return; }
  if (S.mode === 'demo') {
    info = { kind: 'demo', title: 'Örnek veri gösteriliyor. Bu sayfadaki hiçbir sayı gerçek değil.',
             text: (S.demoReason ? S.demoReason + '. ' : '') + 'Gerçek veri henüz azsa panelin dolu halini görmek için kullanılır. Tüm hesaplar gerçek mantıkla yapılır.',
             action: S.mcp ? { label: 'Canlı veriye dön', fn: function () { setMode('live'); } } : null };
  } else if (S.mcp === undefined) {
    info = { kind: 'info', title: 'GitHub’a bağlanılıyor…', text: 'Bağlayıcı hazır olunca veriler okunur.' };
  } else {
    var keys = SOURCES.map(function (s) { return s.key; }), states = keys.map(srcState);
    var anyResp = keys.some(function (k) { return S.src[k]; });
    var failing = states.filter(function (s) { return s.error; });
    if (!STANDALONE && !anyResp && Date.now() - S.firstCallAt > 3500) info = { kind: 'info', title: 'GitHub erişimi için izin bekleniyor', text: 'Açılan pencerede bu sayfaya GitHub erişimi verin. Yanıt gelmezse Yenile’ye basın.' };
    else if (failing.length && failing.length >= Math.ceil(states.length * 0.8) && failing.every(function (s) { return s.error.code === failing[0].error.code; })) {
      var e = explainError(failing[0].error); info = { kind: 'warn', title: e.title, text: e.fix };
    } else if (failing.length) info = { kind: 'warn', title: failing.length + ' veri kaynağı okunamadı', text: 'Etkilenen bölümlerde son iyi veri (varsa) gösterilir. Ayrıntı: Sağlık sekmesi → Veri kaynakları.', action: { label: 'Sağlık', fn: function () { go('saglik'); } } };
  }
  if (!info && STANDALONE && S.stickyNote) info = { kind: 'warn', title: 'Dikkat.', text: S.stickyNote };
  clear(b);
  if (!info) { b.hidden = true; return; }
  b.hidden = false; b.className = 'banner banner-' + info.kind;
  b.appendChild(h('div', null, h('strong', { text: info.title }), h('span', { text: ' ' + info.text })));
  if (info.action) b.appendChild(h('button', { class: 'btn', type: 'button', onclick: info.action.fn }, info.action.label));
}
function renderTabs(V) {
  var nav = $('tabs'); clear(nav); nav.hidden = !!S.setup;
  var bad = V.health.filter(function (c) { return c.level === 'crit' || c.level === 'warn'; }).length;
  TABS.forEach(function (t, i) {
    var on = t.id === S.tab, b = h('button', { type: 'button', role: 'tab', id: 'tab-' + t.id, class: on ? 'on' : '', 'aria-selected': on ? 'true' : 'false', 'aria-controls': 'main', tabindex: on ? '0' : '-1',
      onclick: function () { go(t.id); } }, t.label, t.id === 'saglik' && bad ? h('span', { class: 'badge', text: String(bad), 'aria-label': bad + ' uyarı' }) : null);
    b.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' ? 1 : (e.key === 'ArrowLeft' ? -1 : 0);
      if (!d) return;
      e.preventDefault(); var n = TABS[(i + d + TABS.length) % TABS.length]; go(n.id); var nb = $('tab-' + n.id); if (nb) nb.focus();
    });
    nav.appendChild(b);
  });
}

function typing(el) {                 // yazı yazılan alan: arka plan yenilemesi bunu bozmamalı (işaret ve seçim kutuları dışında)
  return !!el && (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && /^(text|search|number|password|)$/.test(el.type)));
}
function render(force) {
  var ae = document.activeElement, main = $('main');
  if (!force && typing(ae) && main.contains(ae)) { S.dirty = true; return; }   // yazarken sayfa elinizden alınmaz
  var focusId = ae && ae.id && main.contains(ae) ? ae.id : '';
  S.dirty = false;
  var V = derive(), y = window.scrollY;
  renderHeader(V); renderBanner(V); renderTabs(V);
  var tab = TABS.filter(function (t) { return t.id === S.tab; })[0] || TABS[0];
  main.setAttribute('aria-labelledby', 'tab-' + tab.id);
  clear(main);
  try { main.appendChild(S.setup ? renderSetup(V) : tab.render(V)); }
  catch (err) {
    if (window.console) console.error(err);
    main.appendChild(card('Bu sekme çizilemedi', 'Diğer sekmeler çalışmaya devam eder.', h('div', null, h('p', { class: 'note note-warn', text: String((err && err.message) || err) }), h('button', { class: 'btn', type: 'button', onclick: function () { go('genel'); } }, 'Genel’e dön'))));
  }
  renderDrawer(V);
  document.body.classList.toggle('noscroll', !!S.openVideo);
  if (focusId) { var again = $(focusId); if (again && again !== document.activeElement) again.focus(); }   // klavye kullanıcısı yerini kaybetmez
  if (!force) window.scrollTo(0, y);
}

function enterDemo(reason) {
  S.mode = 'demo'; S.demoReason = reason || ''; S.setup = false;
  S.demo = FF.makeDemo(Date.now(), LIB, 7); S.now = Date.now(); S.ver++;
  render(true);
}
function setMode(m) {
  if (m === S.mode) return;
  if (m === 'live') { if (!S.mcp) return; S.mode = 'live'; S.demoReason = ''; S.setup = STANDALONE && !S.token; S.ver++; render(true); }
  else enterDemo(S.mcp ? 'Örnek veri seçildi' : S.demoReason);
}
function refresh() {
  if (S.mode === 'demo') { S.demo = FF.makeDemo(Date.now(), LIB, 7); S.now = Date.now(); S.ver++; render(true); return; }
  if (!S.mcp) return;
  S.refreshing = true; render(true);
  var done = function () { setTimeout(function () { S.refreshing = false; scheduleRender(); }, 1200); };
  S.mcp.invalidate(SERVER).then(done, done);
}

function boot() {
  var hash = String(location.hash || '').replace('#', '');
  var saved = store.get('tab', 'genel');
  S.tab = TABS.some(function (t) { return t.id === hash; }) ? hash : (TABS.some(function (t) { return t.id === saved; }) ? saved : 'genel');
  $('refreshBtn').addEventListener('click', refresh);
  window.addEventListener('hashchange', function () { var id = String(location.hash || '').replace('#', ''); if (id !== S.tab && TABS.some(function (t) { return t.id === id; })) go(id); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && S.openVideo) closeVideo(); });
  document.addEventListener('focusout', function () { if (S.dirty) setTimeout(function () { if (!typing(document.activeElement)) render(); }, 0); });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { S.now = Date.now(); scheduleRender(); } });
  setInterval(function () { S.now = Date.now(); scheduleRender(); }, 30000);
  render(true);
  if (STANDALONE) { startStandalone(); return; }
  var api = window.claude;
  if (!api || typeof api.use !== 'function') { enterDemo('Bu sayfa Claude dışında açıldı, GitHub bağlayıcısı yok'); return; }
  S.firstCallAt = Date.now();
  api.use('mcp').then(function (mcp) {
    if (!mcp) { S.mcp = null; enterDemo('GitHub bağlayıcısı bu görünümde kullanılamıyor'); return; }
    S.mcp = mcp;
    SOURCES.forEach(watchSource);
    S.ver++; scheduleRender();
  }, function () { S.mcp = null; enterDemo('GitHub bağlayıcısı başlatılamadı'); });
}
boot();
