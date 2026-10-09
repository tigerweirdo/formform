/* ====================================================================== sekmeler: Genel, Videolar, Öğrenme, Akış, Sağlık */
var TABS = [
  { id: 'genel', label: 'Genel', render: renderOverview },
  { id: 'videolar', label: 'Videolar', render: renderVideos },
  { id: 'ogrenme', label: 'Öğrenme', render: renderLearning },
  { id: 'akis', label: 'Akış', render: renderPipeline },
  { id: 'saglik', label: 'Sağlık', render: renderHealth },
  { id: 'kontrol', label: 'Kontrol', render: renderControl }
];
function go(id) {
  if (!TABS.some(function (t) { return t.id === id; })) id = 'genel';
  S.tab = id; store.set('tab', id);
  try { if (location.hash !== '#' + id) location.hash = id; } catch (e) { /* hash kullanılamıyor */ }
  render(true);
  window.scrollTo(0, 0);
}

function tile(label, value, sub, extra, kind) {
  return h('div', { class: 'kpi' + (kind ? ' kpi-' + kind : '') },
    h('div', { class: 'kpi-l', text: label }), h('div', { class: 'kpi-v', text: value }),
    sub ? h('div', { class: 'kpi-s', text: sub }) : null, extra || null);
}
function runRow(item, now) {
  var r = item.run, t = FF.parseTimeMs(r.created_at), dur = FF.runDuration(r);
  return h('li', { class: 'row' },
    h('div', { class: 'row-main' }, h('span', { class: 'mono key', text: item.w.key }), runChip(r),
      h('span', { class: 'muted', text: r.event === 'schedule' ? 'zamanlı' : (r.event === 'workflow_dispatch' ? 'elle' : r.event) })),
    h('div', { class: 'row-side' }, h('span', { text: FF.fmtIstShort(t) }), h('span', { class: 'muted', text: FF.finite(dur) ? FF.fmtDur(dur) : FF.fmtRel(t, now) }),
      link('Aç', r.html_url, 'lnk lnk-s')));
}

// ====================================================================== GENEL
function renderOverview(V) {
  var root = h('div', { class: 'stack-y' });
  root.appendChild(statusCard(V));
  root.appendChild(kpiRow(V));
  root.appendChild(h('div', { class: 'grid2' }, upcomingCard(V), recentRunsCard(V)));
  root.appendChild(scoreChart(V));
  root.appendChild(h('div', { class: 'grid2' }, queueCard(V), learningGlance(V)));
  return root;
}

function statusCard(V) {
  var loaded = Object.keys(V.src).some(function (k) { return V.src[k].ok; });
  var kind, headline;
  if (S.mode === 'live' && !loaded) { kind = 'info'; headline = 'Veriler okunuyor'; }
  else if (V.level === 'crit') { kind = 'crit'; headline = 'Sorun var'; }
  else if (V.level === 'warn') { kind = 'warn'; headline = 'Dikkat gerekiyor'; }
  else { kind = 'ok'; headline = 'Sistem sağlıklı'; }
  var rank = { crit: 0, warn: 1 };
  var items = V.health.filter(function (c) { return c.level === 'crit' || c.level === 'warn'; }).sort(function (a, b) { return rank[a.level] - rank[b.level]; });
  var crit = items.filter(function (c) { return c.level === 'crit'; }).length;
  var list = items.length
    ? h('ul', { class: 'issues' }, items.slice(0, 4).map(function (c) {
        return h('li', null, levelChip(c.level), h('div', null, h('strong', { text: c.title }), c.hint ? h('p', { text: c.hint }) : null));
      }))
    : h('p', { class: 'calm', text: loaded ? 'Geciken ölçüm, başarısız çalışma, takılı release ya da süresi yaklaşan jeton görünmüyor.' : 'GitHub’dan ilk yanıt bekleniyor.' });
  return h('section', { class: 'card status status-' + kind },
    h('div', { class: 'status-h' },
      h('div', null, h('p', { class: 'eyebrow', text: 'Sistem durumu' }), h('h2', { class: 'headline', text: headline }),
        items.length ? h('p', { class: 'sub', text: crit + ' sorun, ' + (items.length - crit) + ' uyarı' }) : null),
      items.length > 4 || items.length ? h('button', { class: 'btn', type: 'button', onclick: function () { go('saglik'); } }, 'Tüm denetimler') : null),
    list);
}

function kpiRow(V) {
  var st = V.st, tiles = [];
  tiles.push(tile('Yayınlanan video', String(st.published.length), 'Son 7 günde ' + st.posted7 + ' / ' + C.EXPECTED_POSTS_7D));
  var next = st.milestones.filter(function (m) { return !m.done; })[0];
  tiles.push(tile('Skorlanan video', String(st.scored.length), next ? next.n + ' videoda: ' + next.title : 'Tüm öğrenme eşikleri aşıldı',
    next ? meter(st.scored.length, next.n, 'accent', 'Sonraki eşiğe ilerleme') : null));
  var delta = FF.finite(st.change) ? (st.change >= 0 ? '+' : '') + FF.fmtNum(st.change, 0) + '% önceki ' + st.prevN + ' videoya göre' : (st.scored.length ? 'Karşılaştırma için 3+ önceki video gerekir' : 'İlk skorlar yayından ~3 gün sonra');
  var series = V.vids.filter(function (v) { return v.scored; }).slice(0, 12).reverse().map(function (v) { return v.score; });
  tiles.push(tile('Son ' + (st.lastN || 7) + ' skor ortalaması', FF.finite(st.lastMean) ? FF.fmtNum(st.lastMean, 1) : '–', delta, sparkline(series, 120, 28)));
  tiles.push(tile('Son paylaşım', st.last ? FF.fmtDur(st.hoursSinceLast * HOUR) + ' önce' : '–', st.last ? st.last.video_id : 'Henüz yayın yok', null, st.last && st.hoursSinceLast > C.POST_STALE_H ? 'crit' : null));
  var np = V.events.filter(function (e) { return e.w.key === 'publish'; })[0];
  tiles.push(tile('Sıradaki yayın', np ? FF.fmtRel(np.at, V.now) : '–', np ? 'Plan ' + FF.fmtIstShort(np.at) + (np.delay ? ' · tahmini ~' + FF.fmtClock(np.est) : '') : ''));
  var minutes = actionsTile(V);
  if (minutes) tiles.push(minutes);
  var tk = V.token;
  tiles.push(FF.finite(tk.leftDays)
    ? tile('Instagram jetonu', FF.fmtNum(tk.leftDays, 0) + ' gün', 'Bitiş ' + FF.fmtIst(tk.exp), meter(tk.leftDays, C.TOKEN_LIFETIME_DAYS, tk.leftDays < 10 ? 'crit' : (tk.leftDays < 20 ? 'warn' : 'good'), 'Jetonun kalan ömrü'), tk.leftDays < 10 ? 'crit' : null)
    : tile('Instagram jetonu', 'Yenilenmedi', 'İlk jeton kullanılıyor, bitiş kayıtlı değil'));
  return h('div', { class: 'kpis' }, tiles);
}
function actionsTile(V) {
  var tracked = SOURCES.some(function (s) { return s.key === 'billing'; });
  var b = V.d.billing;
  if (!b && !tracked) return null;
  if (!b) {
    var src = V.src && V.src.billing;
    if (src && src.error) return tile('Actions dakikası', '–', 'Kota okunamadı', null, 'crit');
    return tile('Actions dakikası', '…', 'GitHub kotası okunuyor');
  }
  if (!b.ok) return tile('Actions dakikası', '–', 'Anahtara Plan → Read-only izni ekleyin', null, 'crit');
  if (!FF.finite(b.remaining) || !FF.finite(b.included)) {
    return tile('Actions dakikası', FF.fmtNum(b.used, 0) + ' dk', 'Bu ay kullanılan');
  }
  var low = b.included > 0 && b.remaining < b.included * 0.15;
  var kind = b.remaining <= 0 || low ? 'crit' : 'good';
  return tile('Actions dakikası', FF.fmtNum(b.remaining, 0) + ' dk',
    FF.fmtNum(b.used, 0) + ' / ' + FF.fmtNum(b.included, 0) + ' kullanıldı' + (b.paid > 0 ? ' · ücretli ' + FF.fmtNum(b.paid, 0) : ''),
    meter(Math.min(b.used, b.included), b.included || 1, kind, 'Kullanılan ücretsiz Actions dakikası'),
    b.remaining <= 0 ? 'crit' : null);
}

function upcomingCard(V) {
  var rows = V.events.slice(0, 8).map(function (e) {
    return h('li', { class: 'row' },
      h('div', { class: 'row-main' }, h('span', { class: 'mono key', text: e.w.key }), h('span', { text: e.w.short })),
      h('div', { class: 'row-side' }, h('span', { text: FF.fmtIstShort(e.at) }),
        h('span', { class: 'muted', text: e.delay ? 'tahmini ~' + FF.fmtClock(e.est) : FF.fmtRel(e.at, V.now) })));
  });
  return card('Sıradaki işler', 'Planlanan saatler Berlin saatiyledir. GitHub gecikme yaşattıysa tahmini saat son çalışmalardaki tipik gecikmeyle hesaplanır.', h('ul', { class: 'rows' }, rows));
}
function recentRunsCard(V) {
  var rows = V.allRuns.slice(0, 8).map(function (it) { return runRow(it, V.now); });
  return card('Son çalışmalar', 'Beş workflow’un en yeni çalışmaları', rows.length ? h('ul', { class: 'rows' }, rows) : empty('Henüz çalışma yok', 'İlk zamanlı çalışmayı bekliyor.'),
    { right: h('button', { class: 'btn btn-ghost', type: 'button', onclick: function () { go('akis'); } }, 'Akış') });
}

var REL = { render: ['info', 'Yayına hazır'], claimed: ['warn', 'Üstlenildi'], published: ['ok', 'Yayınlandı'], other: ['mute', 'Diğer'] };
function queueCard(V) {
  var rows = V.releases.slice(0, 6).map(function (r) {
    var stuck = r.state === 'claimed' && V.now - r.atMs > HOUR, k = REL[r.state] || REL.other;
    return h('li', { class: 'row' }, h('div', { class: 'row-main' }, chip(stuck ? 'crit' : k[0], stuck ? 'Takılı' : k[1]), h('span', { class: 'mono', text: r.videoId || r.name })),
      h('div', { class: 'row-side' }, h('span', { class: 'muted', text: FF.fmtRel(r.atMs, V.now) }), link('Aç', r.url, 'lnk lnk-s')));
  });
  var body = h('div', null,
    h('p', { class: 'lead' }, h('strong', { text: String(V.ready.length) }), ' render yayına hazır. ', V.ready.length ? '' : 'Yayın saatinde hazır render yoksa publish hata verir.'),
    rows.length ? h('ul', { class: 'rows' }, rows) : empty('Release yok', 'render workflow’u ilk videoyu çizince burada görünür.'), srcNote(['releases']));
  return card('Render kuyruğu', 'render → release → publish sırası', body);
}
function learningGlance(V) {
  var o = V.odds, m = o.modes, st = V.st;
  var body = h('div', null,
    h('p', { class: 'lead' }, 'Sonraki videonun nasıl seçileceği:'),
    stackedBar([{ label: 'Keşif', value: m.explore, color: 'var(--s2)', text: FF.fmtNum(m.explore * 100, 0) + '%' },
                { label: 'Elit melezleme', value: m.elite, color: 'var(--s1)', text: FF.fmtNum(m.elite * 100, 0) + '%' },
                { label: 'Ağırlıklı seçim', value: m.weights, color: 'var(--s3)', text: FF.fmtNum(m.weights * 100, 0) + '%' }]),
    h('p', { class: 'note', text: o.pool.enabled ? 'Elit melezleme açık: ' + o.pool.size + ' skorlu video var.' : 'Elit melezleme kapalı: en az ' + C.MIN_SCORED_FOR_ELITES + ' skorlu video gerekir (şu an ' + o.pool.size + '). Payı ağırlıklı seçime gidiyor.' }),
    st.learned.length ? h('p', { class: 'note', text: 'Öğrenilmiş ağırlık: ' + V.st.weights.length + ' seçenek.' }) : h('p', { class: 'note', text: 'Henüz öğrenilmiş ağırlık yok: tüm seçenekler eşit olasılıklı.' }));
  return card('Öğrenme', 'Evrim motorunun şu anki durumu', body, { right: h('button', { class: 'btn btn-ghost', type: 'button', onclick: function () { go('ogrenme'); } }, 'Ayrıntı') });
}

// ====================================================================== VİDEOLAR
var COLS = [
  { key: 'id', label: 'Video', sort: function (v) { return v.id; } },
  { key: 'pub', label: 'Yayın', sort: function (v) { return v.pubMs; } },
  { key: 'format', label: 'Format', sort: function (v) { return v.duration; } },
  { key: 'structure', label: 'Yapı', sort: function (v) { return v.row.structure; } },
  { key: 'palette', label: 'Palet', sort: function (v) { return v.row.palette; } },
  { key: 'mode', label: 'Mod', sort: function (v) { return v.row.selection_mode; } },
  { key: 'v24', label: '24 sa', num: true, sort: function (v) { return v.views24; } },
  { key: 'v72', label: '72 sa', num: true, sort: function (v) { return v.views72; } },
  { key: 'watch', label: 'İzlenme sn', num: true, sort: function (v) { return v.watch; } },
  { key: 'score', label: 'Skor', num: true, sort: function (v) { return v.score; } },
  { key: 'state', label: 'Durum', sort: function (v) { return v.scored ? 3 : (v.m.has72 ? 2 : (v.m.has24 ? 1 : 0)); } }
];
function videoState(v, now) {
  if (v.scored) return chip('ok', 'Skorlandı');
  if (v.m.has72) return chip('info', 'Skor bekliyor');
  var s = v.m.state72;
  if (s === 'late') return chip('crit', '72 sa gecikti');
  if (s === 'due') return chip('warn', '72 sa ölçülecek');
  if (!v.m.has24 && v.m.state24 === 'late') return chip('warn', '24 sa gecikti');
  if (!v.m.has24 && v.m.state24 === 'due') return chip('warn', '24 sa ölçülecek');
  return chip('wait', v.m.has24 ? '72 sa: ' + FF.fmtRel(v.m.due72, now) : '24 sa: ' + FF.fmtRel(v.m.due24, now));
}
function filterVideos(V) {
  var f = S.filters, q = f.q.trim().toLowerCase();
  var list = V.vids.filter(function (v) {
    var r = v.row;
    if (q && (v.id + ' ' + r.structure + ' ' + r.palette + ' ' + r.motion_quality).toLowerCase().indexOf(q) < 0) return false;
    if (f.format && r.loop_strategy !== f.format) return false;
    if (f.mode && r.selection_mode !== f.mode) return false;
    if (f.state === 'scored' && !v.scored) return false;
    if (f.state === 'wait' && (v.scored || v.m.state72 === 'late' || v.m.state24 === 'late')) return false;
    if (f.state === 'late' && !(v.m.state72 === 'late' || v.m.state24 === 'late' || v.m.state72 === 'due')) return false;
    return true;
  });
  var col = COLS.filter(function (c) { return c.key === S.sort.key; })[0] || COLS[1], dir = S.sort.dir;
  return list.sort(function (a, b) {
    var x = col.sort(a), y = col.sort(b), xe = x == null || (typeof x === 'number' && !isFinite(x)), ye = y == null || (typeof y === 'number' && !isFinite(y));
    if (xe && ye) return 0; if (xe) return 1; if (ye) return -1;            // boşlar hep sonda
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
}
function selectBox(id, label, options, value, onChange) {
  var sel = h('select', { id: id, 'aria-label': label });
  [{ value: '', label: label + ': hepsi' }].concat(options).forEach(function (o) { sel.appendChild(h('option', { value: o.value, text: o.label })); });
  sel.value = value;
  sel.addEventListener('change', function () { onChange(sel.value); });
  return sel;
}
function renderVideos(V) {
  var list = filterVideos(V), f = S.filters;
  var search = h('input', { id: 'vq', type: 'search', placeholder: 'Ara: video, yapı, palet', value: f.q, 'aria-label': 'Video ara' });
  search.addEventListener('input', function () { f.q = search.value; refreshTable(); });
  var filters = h('div', { class: 'filters' }, search,
    selectBox('vf', 'Format', LIB.format.map(function (x) { return { value: x, label: x }; }), f.format, function (v) { f.format = v; scheduleRender(); }),
    selectBox('vm', 'Mod', [{ value: 'exploration', label: 'Keşif' }, { value: 'exploitation', label: 'Öğrenilmiş seçim' }], f.mode, function (v) { f.mode = v; scheduleRender(); }),
    selectBox('vs', 'Durum', [{ value: 'scored', label: 'Skorlandı' }, { value: 'wait', label: 'Ölçüm bekliyor' }, { value: 'late', label: 'Ölçüm gecikti' }], f.state, function (v) { f.state = v; scheduleRender(); }),
    (f.q || f.format || f.mode || f.state) ? h('button', { class: 'btn btn-ghost', type: 'button', onclick: function () { S.filters = { q: '', format: '', mode: '', state: '' }; scheduleRender(); } }, 'Süzgeçleri temizle') : null);
  var count = h('p', { class: 'sub', id: 'vcount' });
  var tableHost = h('div', { class: 'tablewrap' });
  function refreshTable() {
    var l = filterVideos(V);
    count.textContent = l.length + ' video listeleniyor (toplam ' + V.vids.length + ')';
    clear(tableHost);
    tableHost.appendChild(videoTable(V, l));
  }
  refreshTable();
  var body = V.vids.length ? h('div', null, filters, count, tableHost) : empty('Henüz yayınlanmış video yok', 'İlk video yayınlanınca burada görünür. Yayın 08:00 ve 20:00’de, ya da Kontrol sekmesinden publish’i elle çalıştırarak.');
  return card('Videolar', 'Satıra tıklayınca tarif, ölçümler, skor ayrıntısı ve seçim bilgisi açılır.', h('div', null, body, srcNote(['videos'])));
}
function videoTable(V, list) {
  var head = h('tr', null, COLS.map(function (c) {
    var on = S.sort.key === c.key, th = h('th', { class: (c.num ? 'num ' : '') + (on ? 'sorted' : ''), 'aria-sort': on ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none' },
      h('button', { type: 'button', class: 'thbtn', onclick: function () { S.sort = { key: c.key, dir: on ? -S.sort.dir : -1 }; scheduleRender(); } }, c.label, on ? h('span', { class: 'arrow', text: S.sort.dir > 0 ? ' ▲' : ' ▼' }) : null));
    return th;
  }));
  var rows = list.map(function (v) {
    var r = v.row, tr = h('tr', { tabindex: '0', class: 'clickable', role: 'button', 'aria-label': v.id + ' ayrıntısını aç' },
      h('td', { class: 'mono', text: v.id }), h('td', { text: FF.fmtIst(v.pubMs) }),
      h('td', null, h('span', { class: 'dot', style: 'background:' + (FORMAT_COLOR[r.loop_strategy] || 'var(--muted)') }), r.loop_strategy || '–'),
      h('td', { text: r.structure || '–' }), h('td', { text: r.palette || '–' }),
      h('td', null, h('span', { class: 'dot', style: 'background:' + (MODE_COLOR[r.selection_mode] || 'var(--muted)') }), r.selection_mode === 'exploration' ? 'Keşif' : (r.selection_mode === 'exploitation' ? 'Öğrenilmiş' : (r.selection_mode || '–'))),
      h('td', { class: 'num', text: FF.finite(v.views24) ? FF.fmtCompact(v.views24) : '–' }),
      h('td', { class: 'num', text: FF.finite(v.views72) ? FF.fmtCompact(v.views72) : '–' }),
      h('td', { class: 'num', text: FF.finite(v.watch) ? FF.fmtNum(v.watch, 1) : '–' }),
      h('td', { class: 'num strong', text: v.scored ? FF.fmtNum(v.score, 1) : '–' }),
      h('td', null, videoState(v, V.now)));
    tr.addEventListener('click', function () { openVideo(v.id); });
    tr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openVideo(v.id); } });
    return tr;
  });
  return list.length ? h('table', { class: 'tbl vt' }, h('thead', null, head), h('tbody', null, rows)) : empty('Eşleşen video yok', 'Süzgeçleri gevşetin.');
}

// ------------------------------------------------------------------ video ayrıntı paneli
function openVideo(id) { S.openVideo = id; S.returnFocus = document.activeElement; render(true); var c = $('drawerClose'); if (c) c.focus(); }
function closeVideo() { S.openVideo = null; render(true); if (S.returnFocus && S.returnFocus.focus) { try { S.returnFocus.focus(); } catch (e) { /* öğe kalmadı */ } } }
function kv(label, value) { return h('div', { class: 'kv' }, h('dt', { text: label }), h('dd', { text: value })); }
function renderDrawer(V) {
  var host = $('drawer');
  if (!S.openVideo) { host.hidden = true; clear(host); return; }
  var v = V.vids.filter(function (x) { return x.id === S.openVideo; })[0];
  clear(host);
  if (!v) { S.openVideo = null; host.hidden = true; return; }
  host.hidden = false;
  var r = v.row, now = V.now, m = v.m, p = v.parts;
  function step(label, time, state, detail) { return h('li', { class: 'step' }, h('div', null, h('strong', { text: label }), h('span', { class: 'muted', text: ' ' + time })), state, detail ? h('small', { text: detail }) : null); }
  var steps = h('ol', { class: 'steps' },
    step('Yayın', FF.fmtIst(m.pubMs), chip('ok', 'Paylaşıldı')),
    step('24. saat ölçümü', FF.fmtIst(m.due24), m.has24 ? chip('ok', 'Ölçüldü') : (m.state24 === 'wait' ? chip('wait', FF.fmtRel(m.due24, now)) : chip(m.state24 === 'late' ? 'crit' : 'warn', m.state24 === 'late' ? 'Gecikti' : 'Sırada'))),
    step('72. saat ölçümü ve skor', FF.fmtIst(m.due72), v.scored ? chip('ok', 'Skorlandı') : (m.has72 ? chip('info', 'Skor bekliyor') : (m.state72 === 'wait' ? chip('wait', FF.fmtRel(m.due72, now)) : chip(m.state72 === 'late' ? 'crit' : 'warn', m.state72 === 'late' ? 'Gecikti' : 'Sırada')))));
  var metrics = h('dl', { class: 'kvs' }, kv('24 sa izlenme', FF.finite(v.views24) ? FF.fmtNum(v.views24) : '–'), kv('72 sa izlenme', FF.finite(v.views72) ? FF.fmtNum(v.views72) : '–'),
    kv('Beğeni', r.likes === '' ? '–' : FF.fmtNum(FF.num(r.likes))), kv('Yorum', r.comments === '' ? '–' : FF.fmtNum(FF.num(r.comments))),
    kv('Paylaşım', r.shares === '' ? '–' : FF.fmtNum(FF.num(r.shares))), kv('Kaydetme', r.saves === '' ? '–' : FF.fmtNum(FF.num(r.saves))),
    kv('Ort. izlenme süresi', FF.finite(v.watch) ? FF.fmtNum(v.watch, 2) + ' sn' : '–'), kv('İzlenen oran', FF.finite(v.watch) && v.duration ? '%' + FF.fmtNum(v.watch / v.duration * 100, 0) : '–'));
  var score = null;
  if (v.scored) {
    var parts = p ? [{ label: 'Erişim', value: p.reach * p.weights[0], color: 'var(--s1)', text: FF.fmtNum(p.reach * p.weights[0], 1) },
                     { label: 'Etkileşim', value: p.adjusted * p.weights[1], color: 'var(--s2)', text: FF.fmtNum(p.adjusted * p.weights[1], 1) },
                     { label: 'Tutma', value: p.retention * p.weights[2], color: 'var(--s3)', text: FF.fmtNum(p.retention * p.weights[2], 1) }] : null;
    score = h('div', { class: 'block' }, h('h3', { text: 'Skor' }),
      h('div', { class: 'scorebig' }, h('b', { text: FF.fmtNum(v.score, 1) }), h('span', { class: 'muted', text: '/ 100 · sıra ' + v.rank + '/' + v.scoredCount })),
      parts ? stackedBar(parts) : null,
      p ? h('p', { class: 'note', text: p.hasWatch ? 'Formül: %45 erişim + %30 etkileşim + %25 tutma.' : 'İzlenme süresi verisi yok: eski formül (%65 erişim, %35 etkileşim) kullanıldı.' }) : null,
      FF.finite(v.rel) ? h('p', { class: 'note', text: 'Trend arındırılmış: ×' + FF.fmtNum(v.rel, 2) + ' (önceki ' + C.TREND_WINDOW + ' videonun ortalamasına oranı; ağırlıklar bu değerden hesaplanır).' }) : null);
  }
  var mats = h('ol', { class: 'chips ordered' }, v.materials.map(function (x, i) { return h('li', null, h('b', { text: String(i + 1) }), x); }));
  var trs = h('ol', { class: 'plain' }, v.transformations.map(function (x) { return h('li', { text: x }); }));
  var rel = V.releases.filter(function (x) { return x.videoId === v.id; })[0];
  host.appendChild(h('div', { class: 'scrim', onclick: closeVideo }));
  host.appendChild(h('aside', { class: 'panel', role: 'dialog', 'aria-modal': 'true', 'aria-label': v.id + ' ayrıntısı' },
    h('div', { class: 'panel-h' }, h('div', null, h('p', { class: 'eyebrow', text: 'Video' }), h('h2', { class: 'mono', text: v.id })),
      h('button', { class: 'btn', id: 'drawerClose', type: 'button', onclick: closeVideo }, 'Kapat')),
    h('div', { class: 'panel-b' },
      h('div', { class: 'chips' }, chip('info', r.loop_strategy || 'format yok'), chip(r.selection_mode === 'exploitation' ? 'ok' : 'warn', MODE_NAME[r.selection_mode] || r.selection_mode || 'mod yok'), videoState(v, now)),
      h('div', { class: 'block' }, h('h3', { text: 'Ölçüm takvimi' }), steps),
      score,
      h('div', { class: 'block' }, h('h3', { text: 'Ölçümler' }), metrics),
      h('div', { class: 'block' }, h('h3', { text: 'Tarif' }),
        h('dl', { class: 'kvs' }, kv('Yapı', r.structure || '–'), kv('Palet', r.palette || '–'), kv('Hareket', r.motion_quality || '–'), kv('Işık', r.lighting || '–')),
        h('h4', { text: 'Malzemeler (aşama sırasıyla)' }), mats, h('h4', { text: 'Dönüşümler' }), trs),
      h('div', { class: 'block' }, h('h3', { text: 'Seçim' }),
        h('p', { class: 'note', text: 'Seçildiği andaki ağırlıklar (1 = nötr): yapı ' + FF.fmtNum(FF.num(r.structure_weight), 2) + ', palet ' + FF.fmtNum(FF.num(r.palette_weight), 2) + ', hareket ' + FF.fmtNum(FF.num(r.motion_weight), 2) + ', ışık ' + FF.fmtNum(FF.num(r.lighting_weight), 2) + '.' })),
      h('div', { class: 'block' }, h('h3', { text: 'Açıklama (caption)' }), h('pre', { class: 'caption', text: r.caption || '–' })),
      h('div', { class: 'block' }, h('h3', { text: 'Kayıtlar' }),
        h('div', { class: 'idrow' }, h('span', { class: 'muted', text: 'Instagram kimliği' }), h('code', { text: r.instagram_id || '–' }), r.instagram_id ? copyBtn(r.instagram_id, 'Instagram kimliğini kopyala') : null),
        rel ? h('p', null, link('Release’i GitHub’da aç', rel.url)) : null))));
}

// ====================================================================== ÖĞRENME
function renderLearning(V) {
  var root = h('div', { class: 'stack-y' });
  root.appendChild(h('div', { class: 'grid2' }, progressCard(V), modeCard(V)));
  root.appendChild(oddsCard(V));
  root.appendChild(weightsCard(V));
  root.appendChild(h('div', { class: 'grid2' }, eliteCard(V), formatHourCard(V)));
  root.appendChild(h('div', { class: 'grid2' }, calcCard(V), rulesCard()));
  return root;
}
function progressCard(V) {
  var st = V.st, n = st.scored.length, L = st.learn || {};
  var list = h('ul', { class: 'miles' }, st.milestones.map(function (m) {
    return h('li', { class: m.done ? 'done' : '' }, chip(m.done ? 'ok' : 'wait', m.done ? 'Tamam' : (m.n - n) + ' video kaldı'), h('div', null, h('strong', { text: m.n + ' skorlu video · ' + m.title }), h('p', { text: m.text })));
  }));
  var upd = V.d.dna.length ? V.d.dna[0].updated_at : '';
  var sub = n + ' skorlu video · ' + V.d.dna.length + ' DNA satırı' + (upd ? ' · son hesap ' + FF.fmtIst(FF.parseTimeMs(upd)) : '');
  if (L.published) sub += ' · ' + L.published + ' yayın';
  var bar, note = null;
  if (n > 0) {
    bar = meter(n, 30, 'accent', 'Skorlu video sayısı');
    var waiting = (L.published || 0) - n;
    if (waiting > 0) {
      var due = FF.finite(L.nextDue) && L.nextDue > V.now ? FF.fmtRel(L.nextDue, V.now) : 'sıradaki öğrenmede';
      note = h('p', { class: 'note', text: L.published + ' yayından ' + n + ' tanesi skorlu; ' + waiting + ' video 72 saatlik ölçümü bekliyor (skor yayından 72 saat sonra yazılır, ' + L.has24 + ' videonun 24 saatlik ölçümü tamam). Sıradaki skor ' + due + '.' });
    }
  }
  else if (L.published) {
    bar = meter(Math.max(0, Math.min(L.nextAgeH, 72)), 72, 'accent', 'İlk skora ilerleme');
    var when = FF.finite(L.nextDue) && L.nextDue > V.now ? FF.fmtRel(L.nextDue, V.now) : 'sıradaki öğrenmede';
    note = h('p', { class: 'note', text: 'Skor, yayından 72 saat sonra yazılır; DNA satırı ilk skordan sonra gelir. ' + L.has24 + ' videonun 24 saatlik ölçümü tamam. İlk skor ' + when + '.' });
  } else {
    bar = meter(0, 30, 'accent', 'Skorlu video sayısı');
    note = h('p', { class: 'note', text: 'Henüz yayın yok. İlk skor, ilk paylaşımdan 72 saat sonra gelir.' });
  }
  return card('Öğrenme ilerlemesi', sub, h('div', null, bar, note, list, srcNote(['videos', 'dna'])));
}
function modeCard(V) {
  var rows = V.st.byMode.map(function (g) {
    return { label: MODE_NAME[g.key] || g.key, value: g.mean, color: MODE_COLOR[g.key] || 'var(--muted)', valueText: FF.fmtNum(g.mean, 1), note: 'n=' + g.n + (g.n < C.MIN_PER_BUCKET ? ' · az veri' : ''), faint: g.n < C.MIN_PER_BUCKET,
             tip: [MODE_NAME[g.key] || g.key, ['Ortalama skor', FF.fmtNum(g.mean, 1)], ['Skorlu video', String(g.n)]] };
  });
  var ex = V.st.byMode.filter(function (g) { return g.key === 'exploration'; })[0], ep = V.st.byMode.filter(function (g) { return g.key === 'exploitation'; })[0];
  var verdict = 'Karşılaştırma için her iki modda da skorlu video gerekir.';
  if (ex && ep) {
    if (Math.min(ex.n, ep.n) < C.MIN_PER_BUCKET) verdict = 'Her modda en az ' + C.MIN_PER_BUCKET + ' skorlu video olmadan fark güvenilir değil (şu an ' + ex.n + ' ve ' + ep.n + ').';
    else {
      var gap = (ep.mean - ex.mean) / Math.max(Math.min(ep.mean, ex.mean), 1) * 100;
      verdict = Math.abs(gap) < 10 ? 'Fark %10’dan küçük: öğrenme henüz belirgin bir kazanç göstermiyor.'
        : (gap > 0 ? 'Öğrenilmiş seçim keşiften %' + FF.fmtNum(gap, 0) + ' daha iyi: öğrenme işe yarıyor.' : 'Keşif, öğrenilmiş seçimden %' + FF.fmtNum(-gap, 0) + ' daha iyi: ağırlıklar yanıltıyor olabilir.');
    }
  }
  return card('Keşif ve öğrenilmiş seçim', 'Ortalama performans skoru, moda göre', h('div', null, rows.length ? hbars(rows, 100) : empty('Henüz skorlu video yok', 'İlk skorlar yayından yaklaşık 3 gün sonra gelir.'), h('p', { class: 'note', text: verdict })));
}
function oddsCard(V) {
  var o = V.odds, m = o.modes, dims = [['structure', 'Yapı'], ['palette', 'Palet'], ['motion', 'Hareket'], ['lighting', 'Işık'], ['format', 'Süre'], ['hook', 'Açılış cümlesi'], ['sound', 'Ses']];
  var grid = h('div', { class: 'odds' }, dims.map(function (d) {
    var rows = o.dims[d[0]] || [], open = !!S.oddsOpen[d[0]], shown = open ? rows : rows.slice(0, 5), uni = rows.length ? 1 / rows.length : 0;
    var bars = hbars(shown.map(function (r) {
      return { label: r.value, value: r.p, color: d[0] === 'format' ? (FORMAT_COLOR[r.value] || 'var(--s1)') : 'var(--s1)', valueText: FF.fmtNum(r.p * 100, 1) + '%',
               note: r.known ? 'ağırlık ' + FF.fmtNum(r.weight, 2) : null,
               tip: [r.value, ['Seçilme olasılığı', FF.fmtNum(r.p * 100, 1) + '%'], ['Eşit dağılım olsaydı', FF.fmtNum(uni * 100, 1) + '%'], ['Ağırlık', r.known ? FF.fmtNum(r.weight, 3) : '1 (denenmedi)']] };
    }), Math.max.apply(null, rows.map(function (r) { return r.p; }).concat([0.01])));
    return h('div', { class: 'odds-d' }, h('h3', { text: d[1] }), bars,
      rows.length > 5 ? h('button', { class: 'btn btn-ghost btn-xs', type: 'button', onclick: function () { S.oddsOpen[d[0]] = !open; scheduleRender(); } }, open ? 'İlk 5’i göster' : 'Tümünü göster (' + rows.length + ')') : null);
  }));
  var flat = !o.pool.enabled && V.st.weights.length === 0;
  return card('Sonraki videoda ne seçilir?', 'Evrim motorunun kurallarından hesaplanan tahmini olasılıklar. Gerçek seçim rastgeledir; bu, uzun vadeli dağılımdır.',
    h('div', null,
      stackedBar([{ label: 'Keşif (hepsi rastgele)', value: m.explore, color: 'var(--s2)', text: FF.fmtNum(m.explore * 100, 0) + '%' },
                  { label: 'Elit melezleme (en iyi 5 videodan biri ebeveyn)', value: m.elite, color: 'var(--s1)', text: FF.fmtNum(m.elite * 100, 0) + '%' },
                  { label: 'Ağırlıklı seçim', value: m.weights, color: 'var(--s3)', text: FF.fmtNum(m.weights * 100, 0) + '%' }]),
      flat ? h('p', { class: 'note', text: 'Henüz öğrenilmiş ağırlık ve elit havuzu yok: bütün seçenekler eşit olasılıklı. Dağılım, skorlar geldikçe şekillenir.' }) : null,
      grid));
}
function weightsCard(V) {
  var dim = S.dnaDim, lookup = {}, rows = [];
  V.st.weights.forEach(function (w) { if (w.dimension === dim) lookup[w.value] = w; });
  (LIB[dim] || []).forEach(function (val) {
    var w = lookup[val];
    rows.push(w ? { value: val, weight: w.weight, n: w.n, avg: w.avg, known: true } : { value: val, weight: 1, n: 0, avg: NaN, known: false });
    delete lookup[val];
  });
  Object.keys(lookup).forEach(function (val) { var w = lookup[val]; rows.push({ value: val + ' (kütüphanede yok)', weight: w.weight, n: w.n, avg: w.avg, known: true }); });
  rows.sort(function (a, b) { return (b.weight - a.weight) || (b.n - a.n); });
  var tried = rows.filter(function (r) { return r.known; }).length;
  var tabs = segmented(FF.DIMENSIONS.map(function (d) { return { value: d, label: FF.DIM_NAMES[d] }; }), dim, function (v) { S.dnaDim = v; scheduleRender(); }, 'Boyut');
  return card('DNA ağırlıkları', FF.DIM_NAMES[dim] + ': ' + tried + '/' + rows.length + ' seçenek denendi. Ağırlık 1 nötrdür, 1’in üstü sistemin daha sık seçeceği, altı daha seyrek seçeceği anlamına gelir.',
    h('div', null, tabs, rows.length ? weightsChart(rows) : empty('Bu boyutta seçenek yok'),
      h('p', { class: 'note', text: 'Soluk çubuklar ' + C.MIN_SAMPLES_LEARNED + ' örnekten az veriye dayanır. Ağırlıklar ham skordan değil, trend arındırılmış skordan hesaplanır ve ' + C.MIN_WEIGHT + '–' + C.MAX_WEIGHT + ' aralığındadır.' }), srcNote(['dna'])));
}
function eliteCard(V) {
  var pool = V.odds.pool;
  var body = pool.enabled
    ? h('ol', { class: 'elite' }, pool.parents.map(function (p) {
        var r = p.row, row = h('li', null, h('div', { class: 'elite-h' }, h('span', { class: 'mono', text: r.video_id }), h('b', { text: FF.fmtNum(FF.num(r.performance_score), 1) }), h('span', { class: 'muted', text: 'ebeveyn olma şansı %' + FF.fmtNum(p.share * 100, 0) })),
          h('p', { class: 'muted', text: [r.structure, r.palette, r.loop_strategy].filter(Boolean).join(' · ') }));
        row.tabIndex = 0; row.addEventListener('click', function () { openVideo(r.video_id); });
        return row;
      }))
    : empty('Elit melezleme kapalı', 'En az ' + C.MIN_SCORED_FOR_ELITES + ' skorlu video gerekir; şu an ' + pool.size + '.');
  return card('Elit havuzu', 'En yüksek skorlu ' + C.ELITE_POOL + ' video, yeni videoların ebeveyn adayları', body);
}
function formatHourCard(V) {
  var st = V.st;
  var fmt = st.byFormat.map(function (g) {
    return { label: g.key, value: g.mean, color: FORMAT_COLOR[g.key] || 'var(--muted)', valueText: FF.fmtNum(g.mean, 1), faint: g.n < C.MIN_PER_BUCKET,
             note: 'n=' + g.n + (FF.finite(g.medianRatio) ? ' · medyan izlenen oran %' + FF.fmtNum(g.medianRatio * 100, 0) : '') + (g.n < C.MIN_PER_BUCKET ? ' · az veri' : ''),
             tip: [g.key, ['Ortalama skor', FF.fmtNum(g.mean, 1)], ['Skorlu video', String(g.n)], ['Medyan izlenen oran', FF.finite(g.medianRatio) ? '%' + FF.fmtNum(g.medianRatio * 100, 0) : '–']] };
  });
  var hr = [{ label: 'Sabah 08:00', hr: st.hours.morning }, { label: 'Akşam 20:00', hr: st.hours.evening }].filter(function (x) { return x.hr.n; }).map(function (x) {
    return { label: x.label, value: x.hr.mean, color: 'var(--s1)', valueText: FF.fmtNum(x.hr.mean, 1), note: 'n=' + x.hr.n, faint: x.hr.n < 10 };
  });
  var hourNote = !hr.length ? '' : (!st.hours.reliable ? 'Her saatte en az 10 skorlu video olmadan fark güvenilir değil.'
    : (Math.abs(st.hours.gap) < 10 ? 'Fark %10’dan küçük: saatler arasında anlamlı fark yok.' : (st.hours.morning.mean > st.hours.evening.mean ? 'Sabah' : 'Akşam') + ' yayınları %' + FF.fmtNum(Math.abs(st.hours.gap), 0) + ' daha iyi.'));
  return card('Format ve yayın saati', 'Ortalama performans skoru',
    h('div', null, fmt.length ? hbars(fmt, 100) : empty('Henüz skorlu video yok'), hr.length ? h('div', { class: 'sep' }) : null, hr.length ? hbars(hr, 100) : null, hourNote ? h('p', { class: 'note', text: hourNote }) : null));
}

function calcCard(V) {
  var vals = { fmt: 'seamless_15s', views: '900', likes: '80', comments: '5', shares: '9', saves: '14', watch: '6.5' };
  var out = h('div', { class: 'calc-out', 'aria-live': 'polite' });
  function field(id, label, key, extra) {
    var inp = h('input', { id: 'calc-' + id, type: 'number', min: '0', step: extra || '1', value: vals[key], inputmode: 'decimal' });
    inp.addEventListener('input', function () { vals[key] = inp.value; recalc(); });
    return h('label', { class: 'fld' }, h('span', { text: label }), inp);
  }
  var fsel = h('select', { id: 'calc-format', 'aria-label': 'Format' }, LIB.format.map(function (f) { return h('option', { value: f, text: f }); }));
  fsel.value = vals.fmt;
  fsel.addEventListener('change', function () { vals.fmt = fsel.value; recalc(); });
  function recalc() {
    var p = FF.scoreParts({ views_72h: vals.views, likes: vals.likes, comments: vals.comments, shares: vals.shares, saves: vals.saves, avg_watch_time: vals.watch, loop_strategy: vals.fmt });
    clear(out);
    out.appendChild(h('div', { class: 'scorebig' }, h('b', { text: FF.fmtNum(p.score, 1) }), h('span', { class: 'muted', text: '/ 100 tahmini skor' })));
    out.appendChild(stackedBar([{ label: 'Erişim', value: p.reach * p.weights[0], color: 'var(--s1)', text: FF.fmtNum(p.reach * p.weights[0], 1) + ' (' + FF.fmtNum(p.reach, 0) + '/100 × %' + FF.fmtNum(p.weights[0] * 100, 0) + ')' },
      { label: 'Etkileşim', value: p.adjusted * p.weights[1], color: 'var(--s2)', text: FF.fmtNum(p.adjusted * p.weights[1], 1) + ' (' + FF.fmtNum(p.adjusted, 0) + '/100 × %' + FF.fmtNum(p.weights[1] * 100, 0) + ')' },
      { label: 'Tutma', value: p.retention * p.weights[2], color: 'var(--s3)', text: FF.fmtNum(p.retention * p.weights[2], 1) + ' (' + FF.fmtNum(p.retention, 0) + '/100 × %' + FF.fmtNum(p.weights[2] * 100, 0) + ')' }]));
    out.appendChild(h('p', { class: 'note', text: p.hasWatch ? 'İzlenme süresi verildi: erişim %45, etkileşim %30, tutma %25.' : 'İzlenme süresi yok (ya da 0): eski formül, erişim %65 ve etkileşim %35.' }));
    if (p.views < 100) out.appendChild(h('p', { class: 'note', text: '100’den az izlenmede etkileşim ve tutma güvenle (izlenme/100) küçültülür, böylece şans eseri yüksek oran sistemi yanıltmaz.' }));
  }
  recalc();
  return card('Skor hesaplayıcı', 'Bir videonun 72 saatlik rakamlarını girin, sistemin vereceği skoru görün. Gerçek formülü kullanır.',
    h('div', { class: 'calc' }, h('div', { class: 'calc-in' }, h('label', { class: 'fld' }, h('span', { text: 'Format' }), fsel),
      field('views', '72 sa izlenme', 'views'), field('likes', 'Beğeni', 'likes'), field('comments', 'Yorum', 'comments'), field('shares', 'Paylaşım', 'shares'), field('saves', 'Kaydetme', 'saves'), field('watch', 'Ort. izlenme (sn)', 'watch', '0.1')), out));
}
function rulesCard() {
  var rows = [
    ['Keşif oranı', '%' + FF.fmtNum(C.EXPLORATION_RATE * 100, 0), 'Her şey rastgele seçilir (kalıcı gereksinim)'],
    ['Elit payı (öğrenilmiş seçim içinde)', '%' + FF.fmtNum(C.ELITE_RATE * 100, 0), 'En az ' + C.MIN_SCORED_FOR_ELITES + ' skorlu video varsa açılır'],
    ['Elit havuzu', String(C.ELITE_POOL), 'En iyi videolardan biri skorla orantılı seçilir'],
    ['Gen mutasyonu', '%' + FF.fmtNum(C.GENE_MUTATION * 100, 0) + ' / %' + FF.fmtNum(C.LIST_MUTATION * 100, 0), 'Tek değerli genler / liste elemanları'],
    ['Ağırlık aralığı', C.MIN_WEIGHT + '–' + C.MAX_WEIGHT, '1 = nötr'],
    ['Ön bilgi gücü', String(C.PRIOR_STRENGTH), 'Az örnekli seçenekler nötre çekilir'],
    ['Trend penceresi', C.TREND_WINDOW + ' video', 'Skor, önceki videoların ortalamasına oranlanır'],
    ['Yayın saatleri', '08:00 ve 20:00', 'Berlin, kalıcı gereksinim']
  ];
  return card('Kurallar', 'Kodda sabit değerler. Değiştirmek için README.md ve kod birlikte güncellenir; panel yalnız gösterir.',
    h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('tbody', null, rows.map(function (r) { return h('tr', null, h('th', { scope: 'row', text: r[0] }), h('td', { class: 'mono', text: r[1] }), h('td', { class: 'muted', text: r[2] })); })))));
}

// ====================================================================== AKIŞ
function renderPipeline(V) {
  var root = h('div', { class: 'stack-y' });
  root.appendChild(card('Günün hattı', 'Berlin saatiyle. Mavi çizgiler paylaşım hedefidir (08:00 ve 20:00). Sarı şeritler, son çalışmalardaki tipik GitHub gecikmesidir.',
    h('div', null, dayClock(V), h('p', { class: 'note', text: 'Ayın 1’i ve 15’i jeton yenileme, pazartesi haftalık özet de çalışır.' }))));
  root.appendChild(h('div', { class: 'wfgrid' }, W.map(function (w) { return workflowCard(V, w); })));
  root.appendChild(schedulerCard(V));
  root.appendChild(releasesCard(V));
  root.appendChild(runsCard(V));
  return root;
}
function workflowCard(V, w) {
  var runs = V.d.runs[w.key] || [], a = V.adh[w.key], last = runs[0], next = V.events.filter(function (e) { return e.w.key === w.key; })[0];
  var strip = h('div', { class: 'runstrip', 'aria-label': 'Son 12 çalışma' }, runs.slice(0, 12).reverse().map(function (r) {
    var st = FF.runStatus(r), i = h('i', { class: 'rs rs-' + st, tabindex: '0' });
    tipOn(i, function () { return [FF.fmtIstShort(FF.parseTimeMs(r.created_at)), ['Durum', { ok: 'Başarılı', fail: 'Başarısız', cancel: 'İptal', running: 'Çalışıyor', queued: 'Sırada' }[st]], ['Süre', FF.finite(FF.runDuration(r)) ? FF.fmtDur(FF.runDuration(r)) : '–']]; });
    return i;
  }));
  var adhText = a.total ? a.ontime + ' zamanında, ' + a.late + ' gecikmeli, ' + a.verylate + ' çok gecikmeli, ' + a.missed + ' kaçırıldı (' + a.total + ' tur)' : 'Değerlendirilecek tur yok';
  return h('section', { class: 'card wf' },
    h('div', { class: 'card-h' }, h('div', null, h('h2', { text: w.name }), h('p', { class: 'sub', text: w.short + ' · ' + FF.describeCron(w.cron) }))),
    h('p', { class: 'wf-d', text: w.does }),
    h('dl', { class: 'kvs one' },
      kv('Sıradaki', next ? FF.fmtIstShort(next.at) + ' (' + FF.fmtRel(next.at, V.now) + ')' : '–'),
      kv('Son çalışma', last ? FF.fmtIstShort(FF.parseTimeMs(last.created_at)) + ' · ' + (FF.finite(FF.runDuration(last)) ? FF.fmtDur(FF.runDuration(last)) : FF.fmtRel(FF.parseTimeMs(last.created_at), V.now)) : 'henüz yok'),
      kv('Zamanlayıcı', adhText), kv('Tipik gecikme', a.total && FF.finite(a.medianDelay) ? FF.fmtDur(a.medianDelay) : '–'), kv('Zaman aşımı', w.timeout + ' dk')),
    h('div', { class: 'wf-f' }, last ? runChip(last) : chip('mute', 'Çalışma yok'), strip,
      h('span', { class: 'wf-links' }, link('GitHub’da aç', GH + '/actions/workflows/' + w.file), h('button', { class: 'btn btn-ghost btn-xs', type: 'button', onclick: function () { go('kontrol'); } }, 'Elle çalıştır'))));
}
function schedulerCard(V) {
  var days = Number(S.view.sched || 7);
  var seg = segmented([{ value: '3', label: '3 gün' }, { value: '7', label: '7 gün' }, { value: '14', label: '14 gün' }], String(days), function (v) { S.view.sched = v; scheduleRender(); }, 'Aralık');
  var tot = { total: 0, missed: 0, late: 0, verylate: 0 };
  W.forEach(function (w) { var a = V.adh[w.key]; tot.total += a.total; tot.missed += a.missed; tot.late += a.late; tot.verylate += a.verylate; });
  var rows = W.map(function (w) {
    var a = V.adh[w.key];
    return h('tr', null, h('th', { scope: 'row', class: 'mono', text: w.key }), h('td', { class: 'num', text: String(a.total) }), h('td', { class: 'num', text: String(a.ontime) }), h('td', { class: 'num', text: String(a.late + a.verylate) }),
      h('td', { class: 'num', text: String(a.missed) }), h('td', { class: 'num', text: FF.finite(a.medianDelay) ? FF.fmtDur(a.medianDelay) : '–' }), h('td', { class: 'num', text: FF.finite(a.maxDelay) ? FF.fmtDur(a.maxDelay) : '–' }));
  });
  var verdict = !tot.total ? 'Henüz değerlendirilecek zamanlı tur yok.'
    : (tot.missed + tot.verylate === 0 ? 'Son turlar zamanında çalışmış.' : 'Son ' + tot.total + ' turun ' + tot.missed + ' tanesi hiç çalışmadı, ' + tot.verylate + ' tanesi 90 dakikadan fazla gecikti. GitHub zamanlanmış çalışmaları yoğunlukta geciktirebilir ya da atlayabilir; paylaşım saati bu yüzden kayabilir.');
  return card('Zamanlayıcı güvenilirliği', 'Her tur için planlanan saat (işaret) ile gerçek çalışma (çizginin ucu). Çizgi uzadıkça gecikme büyür.',
    h('div', null, kindLegend(), stripChart(V, days), h('p', { class: 'note ' + (tot.missed + tot.verylate ? 'note-warn' : ''), text: verdict }),
      h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Workflow', 'Tur', 'Zamanında', 'Geç', 'Kaçırıldı', 'Medyan gecikme', 'En büyük'].map(function (x, i) { return h('th', { class: i ? 'num' : '', text: x }); }))), h('tbody', null, rows))),
      srcNote(W.map(function (w) { return 'runs:' + w.key; }))), { right: seg });
}
function releasesCard(V) {
  var rows = V.releases.slice(0, 12).map(function (r) {
    var stuck = r.state === 'claimed' && V.now - r.atMs > HOUR, k = REL[r.state] || REL.other;
    return h('tr', null, h('td', null, chip(stuck ? 'crit' : k[0], stuck ? 'Takılı' : k[1])), h('td', { class: 'mono', text: r.videoId || r.name }), h('td', { text: FF.fmtIst(r.atMs) }), h('td', { class: 'muted', text: FF.fmtRel(r.atMs, V.now) }), h('td', null, link('Aç', r.url, 'lnk lnk-s')));
  });
  return card('Release kuyruğu', 'Her render bir release olur: “render” hazır, “claimed” üstlenilmiş, “published” yayınlanmış. Render en çok 30 release saklar.',
    h('div', null, rows.length ? h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Durum', 'Video', 'Zaman', '', ''].map(function (x) { return h('th', { text: x }); }))), h('tbody', null, rows))) : empty('Release yok'), srcNote(['releases'])));
}
function runsCard(V) {
  var f = S.runFilter, list = V.allRuns.filter(function (x) { return !f || x.w.key === f; }).slice(0, 30);
  var sel = selectBox('rf', 'Workflow', W.map(function (w) { return { value: w.key, label: w.key }; }), f, function (v) { S.runFilter = v; scheduleRender(); });
  var rows = list.map(function (it) {
    var r = it.run, t = FF.parseTimeMs(r.created_at), active = FF.runStatus(r) === 'running' || FF.runStatus(r) === 'queued';
    return h('tr', null, h('td', { class: 'mono', text: it.w.key }), h('td', { text: FF.fmtIst(t) }), h('td', { text: r.event === 'schedule' ? 'zamanlı' : (r.event === 'workflow_dispatch' ? 'elle' : r.event) }),
      h('td', null, runChip(r)), h('td', { class: 'num', text: FF.finite(FF.runDuration(r)) ? FF.fmtDur(FF.runDuration(r)) : '–' }),
      h('td', null, link('Aç', r.html_url, 'lnk lnk-s'), active ? h('button', { class: 'btn btn-xs btn-danger', type: 'button', onclick: function () { go('kontrol'); } }, 'İptal…') : null));
  });
  return card('Çalışma geçmişi', 'En yeni 30 çalışma', h('div', null, h('div', { class: 'filters' }, sel),
    rows.length ? h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Workflow', 'Zaman', 'Tetik', 'Durum', 'Süre', ''].map(function (x, i) { return h('th', { class: i === 4 ? 'num' : '', text: x }); }))), h('tbody', null, rows))) : empty('Çalışma yok'),
    srcNote(W.map(function (w) { return 'runs:' + w.key; }))));
}

// ====================================================================== SAĞLIK
function renderHealth(V) {
  var root = h('div', { class: 'stack-y' }), order = { crit: 0, warn: 1, info: 2, unknown: 2, ok: 3 };
  var groups = [['crit', 'Sorunlar'], ['warn', 'Uyarılar'], ['info', 'Bilgi'], ['ok', 'Sağlıklı']];
  var sorted = V.health.slice().sort(function (a, b) { return order[a.level] - order[b.level]; });
  if (!sorted.length) root.appendChild(card('Sağlık denetimleri', 'Veriler gelince burada listelenir', empty('Denetlenecek veri yok', S.mode === 'live' ? 'GitHub’dan ilk yanıt bekleniyor.' : 'Örnek veri yok.')));
  groups.forEach(function (g) {
    var items = sorted.filter(function (c) { return (g[0] === 'info' ? (c.level === 'info' || c.level === 'unknown') : c.level === g[0]); });
    if (!items.length) return;
    root.appendChild(card(g[1], items.length + ' denetim', h('ul', { class: 'checks' }, items.map(function (c) {
      var links = [];
      if (c.url) links.push(link('Çalışmayı aç', c.url));
      if (c.workflow) links.push(link('Actions’ta aç', GH + '/actions/workflows/' + c.workflow + '.yml'));
      (c.issues || []).forEach(function (i) { links.push(link('#' + i.number, i.url)); });
      (c.releases || []).forEach(function (r) { links.push(link(r.videoId, r.url)); });
      return h('li', { class: 'check check-' + c.level }, levelChip(c.level), h('div', { class: 'check-b' }, h('strong', { text: c.title }), c.detail ? h('p', { class: 'muted', text: c.detail }) : null,
        c.hint ? h('p', { class: 'hint' }, h('b', { text: 'Ne yapmalı: ' }), c.hint) : null, links.length ? h('div', { class: 'links' }, links) : null));
    })), { cls: 'card-' + g[0] }));
  });
  var rows = SOURCES.map(function (src) {
    var s = srcState(src.key), kind, text;
    if (S.mode === 'demo') { kind = 'info'; text = 'Örnek veri'; }
    else if (s.error && s.ok) { kind = 'warn'; text = 'Eski veri'; }
    else if (s.error) { kind = 'crit'; text = 'Hata'; }
    else if (s.pending) { kind = 'wait'; text = 'Bekleniyor'; }
    else { kind = 'ok'; text = 'Güncel'; }
    var e = s.error ? explainError(s.error) : null;
    return h('tr', null, h('td', { text: src.label }), h('td', null, chip(kind, text)), h('td', { class: 'muted', text: s.at && S.mode === 'live' ? FF.fmtRel(s.at, V.now) : '–' }), h('td', { class: 'muted', text: e ? e.title : '' }));
  });
  root.appendChild(card('Veri kaynakları', S.mode === 'live' ? (STANDALONE ? 'Panel GitHub’a erişim anahtarınızla bağlanır ve iki dakikada bir yeniler.' : 'Panel GitHub bağlayıcınız üzerinden okur ve iki dakikada bir yeniler.') : 'Örnek kipinde GitHub’a bağlanılmaz.',
    h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, ['Kaynak', 'Durum', 'Güncelleme', 'Not'].map(function (x) { return h('th', { text: x }); }))), h('tbody', null, rows)))));
  return root;
}
