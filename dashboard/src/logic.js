/* FORM/FORM yönetim paneli: saf mantık, DOM yok.
 *
 * ops/scoring.py, ops/report.py ve ops/evolve.py ile aynı sonuçları verir. Eşitlik ops/tests/test_dashboard.py
 * içinde Node ile sınanır: burada bir sayı ya da formül değişirse Python tarafı da değişmeli, yoksa test kırılır.
 * Panelin gösterdiği her sayı buradan gelir, sayfa kodu (app.js) hesap yapmaz. */
var FF = (function () {
  'use strict';

  var MIN = 60000, HOUR = 3600000, DAY = 86400000;
  var TZ = 'Europe/Berlin';

  // Kalıcı kurallar (README.md). Değerler Python sabitleriyle testte karşılaştırılır.
  var C = {
    MIN_WEIGHT: 0.6, MAX_WEIGHT: 1.8, PRIOR_STRENGTH: 5, TREND_WINDOW: 12, TREND_MIN: 3,
    EXPLORATION_RATE: 0.30, ELITE_RATE: 0.50, ELITE_POOL: 5, MIN_SCORED_FOR_ELITES: 4,
    GENE_MUTATION: 0.25, LIST_MUTATION: 0.35,
    EXPECTED_POSTS_7D: 14, POST_STALE_H: 36, PENDING_72H_H: 80, MIN_PER_BUCKET: 8, MIN_SAMPLES_LEARNED: 3,
    DELAY_OK_MS: 20 * MIN, DELAY_BAD_MS: 90 * MIN, SLOT_GRACE_MS: 2 * HOUR,
    TOKEN_LIFETIME_DAYS: 60
  };

  // Workflow'lar. Ad, zaman aşımı ve elle girdiler .github/workflows ile, cron ifadeleri ops/schedule.py takvimiyle
  // testte karşılaştırılır. render, publish ve learn'in GitHub'da kendi zamanlayıcısı yoktur (saat başlatır, kaçanı
  // nöbetçi başlatır): onların cron'u yalnız takvimdir, yuvalar ve "sıradaki tur" bundan hesaplanır.
  // seasons: cron UTC’dir, offset o cron’un yazıldığı Berlin ofseti (2 yaz, 1 kış). null: ofsete bağlı değil.
  // cron alanı yaz (UTC+2) ifadesidir; describeCron bunu Berlin duvar saatine çevirir. İki ifade de aynı duvar saatidir.
  var WORKFLOWS = [
    { key: 'render', name: 'FORMFORM render', file: 'render.yml', cron: '30 4,16 * * *', timeout: 45, inputs: ['spec', 'clock', 'watch'],
      seasons: [{ cron: '30 4,16 * * *', offset: 2 }, { cron: '30 5,17 * * *', offset: 1 }],
      short: 'Tarif ve video', does: 'Evrim motoru yeni tarif üretir, video çizilir, doğrulanır, release olarak bırakılır.' },
    { key: 'publish', name: 'FORMFORM publish', file: 'publish.yml', cron: '45 5,17 * * *', timeout: 30, inputs: ['dry_run', 'clock', 'watch'],
      seasons: [{ cron: '45 5,17 * * *', offset: 2 }, { cron: '45 6,18 * * *', offset: 1 }],
      short: 'Instagram yayını', does: 'Hazır render üstlenilir, Cloudinary’ye yüklenir, Instagram’da Reel olarak paylaşılır, kayıt yazılır.' },
    { key: 'learn', name: 'FORMFORM learn', file: 'learn.yml', cron: '5 */3 * * *', timeout: 20, inputs: ['clock', 'watch'],
      seasons: [{ cron: '5 */3 * * *', offset: null }],
      short: 'Ölçüm ve öğrenme', does: '24 ve 72 saatlik ölçümleri çeker, performans skorunu ve DNA ağırlıklarını günceller.' },
    { key: 'token', name: 'FORMFORM token', file: 'token.yml', cron: '7 3 1,15 * *', timeout: 10, inputs: ['clock'],
      seasons: [{ cron: '7 3 1,15 * *', offset: 2 }, { cron: '7 4 1,15 * *', offset: 1 }],
      short: 'Jeton yenileme', does: 'Instagram jetonunu yeniler, şifreleyip veri dalına yazar.' },
    { key: 'report', name: 'FORMFORM report', file: 'report.yml', cron: '17 7 * * 1', timeout: 10, inputs: ['bildirim_denemesi', 'clock'],
      seasons: [{ cron: '17 7 * * 1', offset: 2 }, { cron: '17 8 * * 1', offset: 1 }],
      short: 'Haftalık özet', does: 'Haftalık özeti GitHub issue olarak açar (e-posta gelir), sonra kapatır.' }
  ];

  // ------------------------------------------------------------------ sayı ve metin yardımcıları
  function num(v) {                       // Python tarafındaki jsnum.num ile aynı: boş metin 0, geçersiz NaN
    if (v == null) return NaN;
    if (typeof v === 'boolean') return v ? 1 : 0;
    if (typeof v === 'number') return v;
    var t = String(v).trim();
    return t === '' ? 0 : Number(t);
  }
  function numOr0(v) { var n = num(v); return (isNaN(n) || n === 0) ? 0 : n; }
  function blank(v) { return v == null || String(v).trim() === ''; }
  function finite(x) { return typeof x === 'number' && isFinite(x); }
  function isNum(v) { return !blank(v) && finite(num(v)); }
  function jsRound(x) { return Math.floor(x + 0.5); }
  function mean(a) { if (!a.length) return NaN; var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return s / a.length; }
  function median(a) {
    if (!a.length) return NaN;
    var s = a.slice().sort(function (x, y) { return x - y; }), m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function parseTimeMs(t) {
    var s = String(t == null ? '' : t).trim();
    if (!s) return NaN;
    if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(s)) s += 'Z';         // saat dilimi yoksa UTC (Python tarafıyla aynı)
    return Date.parse(s);
  }
  function startsTest(id) { return String(id == null ? '' : id).indexOf('TEST_') === 0; }

  // ------------------------------------------------------------------ CSV
  function parseCSV(text) {
    var s = String(text == null ? '' : text), rows = [], row = [], field = '', q = false, i, c;
    if (s.charCodeAt(0) === 0xFEFF) s = s.slice(1);
    for (i = 0; i < s.length; i++) {
      c = s.charAt(i);
      if (q) {
        if (c === '"') { if (s.charAt(i + 1) === '"') { field += '"'; i++; } else q = false; }
        else field += c;
      } else if (c === '"') q = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
      else if (c !== '\r') field += c;
    }
    if (field !== '' || row.length) { row.push(field); rows.push(row); }
    if (!rows.length) return [];
    var head = rows[0];
    return rows.slice(1).filter(function (r) { return !(r.length === 1 && r[0] === ''); }).map(function (r) {
      var o = {};
      head.forEach(function (h, k) { o[h] = r[k] === undefined ? '' : r[k]; });
      return o;
    });
  }

  // ------------------------------------------------------------------ performans skoru (ops/scoring.py)
  function scoreParts(row) {
    var views = numOr0(row.views_72h), likes = numOr0(row.likes), comments = numOr0(row.comments),
        shares = numOr0(row.shares), saves = numOr0(row.saves);
    var reach = Math.min(100, 25 * Math.log10(views + 1));
    var points = likes + comments * 3 + shares * 4 + saves * 4;
    var rate = views > 0 ? points / views : 0;
    var engagement = Math.min(100, rate * 500);
    var confidence = Math.min(1, views / 100);
    var adjusted = engagement * confidence;
    var m = /(\d+)s$/.exec(String(row.loop_strategy == null ? '' : row.loop_strategy));
    var duration = m ? parseFloat(m[1]) : 0;
    var avgWatch = num(row.avg_watch_time);
    var hasWatch = duration > 0 && finite(avgWatch) && avgWatch > 0;
    var ratioScore = hasWatch ? Math.min(100, (avgWatch / duration) * 80) : 0;
    var absoluteScore = hasWatch ? Math.min(100, (avgWatch / 12) * 100) : 0;
    var retention = (0.5 * ratioScore + 0.5 * absoluteScore) * confidence;
    var w = hasWatch ? [0.45, 0.30, 0.25] : [0.65, 0.35, 0];
    var score = reach * w[0] + adjusted * w[1] + retention * w[2];
    return {
      views: views, likes: likes, comments: comments, shares: shares, saves: saves, points: points, rate: rate,
      reach: reach, engagement: engagement, confidence: confidence, adjusted: adjusted, retention: retention,
      ratioScore: ratioScore, absoluteScore: absoluteScore, duration: duration, avgWatch: avgWatch,
      hasWatch: hasWatch, weights: w, score: jsRound(score * 100) / 100
    };
  }
  function performanceScore(row) { return scoreParts(row).score; }

  // ------------------------------------------------------------------ DNA ağırlıkları (ops/scoring.py)
  var SINGLE = [['structure', 'structure'], ['palette', 'palette'], ['motion_quality', 'motion'],
                ['lighting', 'lighting'], ['loop_strategy', 'format']];
  var LISTS = [['materials', 'material'], ['transformations', 'transformation']];
  var DIMENSIONS = ['structure', 'palette', 'motion', 'lighting', 'format', 'material', 'transformation', 'hook', 'sound', 'tag'];
  var DIM_NAMES = { structure: 'Yapı', palette: 'Palet', motion: 'Hareket', lighting: 'Işık', format: 'Süre',
                    material: 'Malzeme', transformation: 'Dönüşüm', hook: 'Açılış cümlesi', sound: 'Ses', tag: 'Hashtag' };

  function genesOf(row) {
    var seen = {}, genes = [];
    function add(dimension, value) {
      var clean = String(value == null ? '' : value).trim(), key = dimension + '::' + clean;
      if (clean === '' || seen[key]) return;
      seen[key] = true;
      genes.push([dimension, clean]);
    }
    SINGLE.forEach(function (p) { add(p[1], row[p[0]]); });
    LISTS.forEach(function (p) {
      String(row[p[0]] == null ? '' : row[p[0]]).split(', ').forEach(function (part) { add(p[1], part); });
    });
    add('hook', String(row.caption == null ? '' : row.caption).split('\n')[0]);
    add('sound', row.sound);
    captionTags(row.caption).forEach(function (tag) { add('tag', tag); });
    return genes;
  }

  function orderedScored(rows) {         // TEST_ elenir, skoru sayı olanlar, yayın zamanına göre (zamansızlar sona)
    var valid = [];
    rows.forEach(function (r) {
      if (startsTest(r.video_id)) return;
      var sc = r.performance_score;
      if (sc == null || String(sc).trim() === '' || !finite(num(sc))) return;
      valid.push(r);
    });
    var idx = valid.map(function (r, i) {
      var t = parseTimeMs(r.published_at);
      return { t: isNaN(t) ? Infinity : t, i: i, r: r };
    });
    idx.sort(function (a, b) { return a.t < b.t ? -1 : (a.t > b.t ? 1 : a.i - b.i); });
    return idx.map(function (e) { return e.r; });
  }

  function trendRelative(ordered) {      // skoru önceki TREND_WINDOW videonun ortalamasına oranlar
    var raw = ordered.map(function (r) { return num(r.performance_score); });
    var globalAverage = mean(raw);
    var rel = raw.map(function (s, i) {
      var prev = raw.slice(Math.max(0, i - C.TREND_WINDOW), i);
      var base = prev.length >= C.TREND_MIN ? mean(prev) : globalAverage;
      return s / Math.max(base, 1);
    });
    return { raw: raw, rel: rel, globalAverage: globalAverage, globalRelative: mean(rel) };
  }

  function dnaWeights(rows, updatedAt) {
    var ordered = orderedScored(rows);
    if (!ordered.length) return [];
    var t = trendRelative(ordered), groups = {}, order = [];
    ordered.forEach(function (row, i) {
      genesOf(row).forEach(function (g) {
        var key = g[0] + '::' + g[1];
        if (!groups[key]) { groups[key] = { key: key, dimension: g[0], value: g[1], scores: [], relatives: [] }; order.push(key); }
        groups[key].scores.push(t.raw[i]);
        groups[key].relatives.push(t.rel[i]);
      });
    });
    return order.map(function (k) {
      var g = groups[k], n = g.scores.length;
      var adjusted = (n * mean(g.relatives) + C.PRIOR_STRENGTH * t.globalRelative) / (n + C.PRIOR_STRENGTH);
      var weight = t.globalRelative > 0 ? adjusted / t.globalRelative : 1.0;
      weight = Math.max(C.MIN_WEIGHT, Math.min(C.MAX_WEIGHT, weight));
      return { key: g.key, dimension: g.dimension, value: g.value,
               avg_score: jsRound(mean(g.scores) * 10) / 10, sample_count: n,
               weight: jsRound(weight * 1000) / 1000, updated_at: updatedAt || '' };
    });
  }

  // ------------------------------------------------------------------ ölçüm takvimi (ops/scoring.find_due)
  function measurement(row, nowMs) {
    var pub = parseTimeMs(row.published_at);
    var o = { pubMs: pub, ageH: isNaN(pub) ? NaN : (nowMs - pub) / HOUR, due24: pub + 24 * HOUR, due72: pub + 72 * HOUR };
    o.has24 = !blank(row.views_24h);
    o.has72 = !blank(row.views_72h);
    o.scored = !blank(row.performance_score);
    function st(has, due) { return has ? 'ok' : (nowMs < due ? 'wait' : (nowMs - due > 6 * HOUR ? 'late' : 'due')); }
    o.state24 = st(o.has24, o.due24);
    o.state72 = st(o.has72, o.due72);
    return o;
  }
  function findDue(rows, nowMs) {         // Python find_due ile aynı: 72h önce, sonra 24h
    var out = [];
    rows.forEach(function (row) {
      if (startsTest(row.video_id) || !row.instagram_id || !row.published_at) return;
      var pub = parseTimeMs(row.published_at);
      if (isNaN(pub)) return;
      var age = (nowMs - pub) / HOUR;
      if (age >= 72 && blank(row.views_72h)) { out.push({ video_id: row.video_id, snapshot_window: '72h' }); return; }
      if (age >= 24 && blank(row.views_24h)) out.push({ video_id: row.video_id, snapshot_window: '24h' });
    });
    return out;
  }

  // ------------------------------------------------------------------ saat ve süre biçimleri (Berlin)
  var _partsFmt = null;
  function istParts(ms) {
    if (!_partsFmt) _partsFmt = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hourCycle: 'h23', year: 'numeric',
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short' });
    var o = {};
    _partsFmt.formatToParts(new Date(ms)).forEach(function (p) { o[p.type] = p.value; });
    return o;
  }
  function istHour(ms) { return Number(istParts(ms).hour); }
  var TR_DAYS = { Mon: 'Pzt', Tue: 'Sal', Wed: 'Çar', Thu: 'Per', Fri: 'Cum', Sat: 'Cmt', Sun: 'Paz' };
  function fmtIst(ms) {
    if (!finite(ms)) return '–';
    var p = istParts(ms);
    return p.day + '.' + p.month + '.' + p.year + ' ' + p.hour + ':' + p.minute;
  }
  function fmtIstShort(ms) {
    if (!finite(ms)) return '–';
    var p = istParts(ms);
    return TR_DAYS[p.weekday] + ' ' + p.day + '.' + p.month + ' ' + p.hour + ':' + p.minute;
  }
  function fmtClock(ms) { var p = istParts(ms); return p.hour + ':' + p.minute; }
  function fmtDur(ms) {                    // 5 dk, 2 sa 10 dk, 3 gün 4 sa
    ms = Math.abs(ms);
    var m = Math.round(ms / MIN);
    if (m < 1) return '1 dakikadan az';
    if (m < 60) return m + ' dk';
    var h = Math.floor(m / 60), rm = m % 60;
    if (h < 24) return h + ' sa' + (rm ? ' ' + rm + ' dk' : '');
    var d = Math.floor(h / 24), rh = h % 24;
    return d + ' gün' + (rh ? ' ' + rh + ' sa' : '');
  }
  function fmtRel(ms, nowMs) {
    if (!finite(ms)) return '–';
    var d = ms - nowMs;
    if (Math.abs(d) < MIN) return 'şimdi';
    return fmtDur(d) + (d < 0 ? ' önce' : ' sonra');
  }
  function fmtNum(n, digits) {
    if (!finite(n)) return '–';
    return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: digits == null ? 0 : digits,
      minimumFractionDigits: 0 }).format(n);
  }
  function fmtCompact(n) {
    if (!finite(n)) return '–';
    if (Math.abs(n) >= 10000) return fmtNum(n / 1000, 1) + ' B';
    return fmtNum(n, 0);
  }

  // ------------------------------------------------------------------ cron (UTC, GitHub Actions)
  function cronField(f, lo, hi) {
    var set = {};
    String(f).split(',').forEach(function (part) {
      var range = part, step = 1, sp = part.split('/');
      if (sp.length === 2) { range = sp[0]; step = parseInt(sp[1], 10); }
      var a, b;
      if (range === '*') { a = lo; b = hi; }
      else if (range.indexOf('-') >= 0) { var r = range.split('-'); a = +r[0]; b = +r[1]; }
      else { a = +range; b = sp.length === 2 ? hi : a; }
      for (var v = a; v <= b; v += step) set[v] = true;
    });
    return { set: set, star: String(f).charAt(0) === '*' };
  }
  function parseCron(expr) {
    var p = String(expr).trim().split(/\s+/);
    if (p.length !== 5) throw new Error('cron beş alanlı olmalı: ' + expr);
    var dow = cronField(p[4], 0, 7);
    if (dow.set[7]) dow.set[0] = true;
    return { min: cronField(p[0], 0, 59), hour: cronField(p[1], 0, 23), dom: cronField(p[2], 1, 31),
             mon: cronField(p[3], 1, 12), dow: dow, raw: expr };
  }
  function cronMatch(spec, d) {
    if (!spec.min.set[d.getUTCMinutes()] || !spec.hour.set[d.getUTCHours()] || !spec.mon.set[d.getUTCMonth() + 1]) return false;
    var domOk = !!spec.dom.set[d.getUTCDate()], dowOk = !!spec.dow.set[d.getUTCDay()];
    if (!spec.dom.star && !spec.dow.star) return domOk || dowOk;       // POSIX: ikisi de kısıtlıysa VEYA
    return domOk && dowOk;
  }
  function slotsBetween(cron, fromMs, toMs) {          // fromMs ≤ slot < toMs
    var spec = parseCron(cron), out = [], t = Math.ceil(fromMs / MIN) * MIN;
    for (; t < toMs; t += MIN) if (cronMatch(spec, new Date(t))) out.push(t);
    return out;
  }
  function nextRuns(cron, fromMs, count) {
    var spec = parseCron(cron), out = [], t = (Math.floor(fromMs / MIN) + 1) * MIN, limit = fromMs + 70 * DAY;
    for (; t < limit && out.length < count; t += MIN) if (cronMatch(spec, new Date(t))) out.push(t);
    return out;
  }
  var CRON_OFFSET = {};
  WORKFLOWS.forEach(function (w) { (w.seasons || []).forEach(function (s) { if (s.offset != null) CRON_OFFSET[s.cron] = s.offset; }); });
  function offsetHours(ms) {
    var name = '';
    new Intl.DateTimeFormat('en-US', { timeZone: TZ, timeZoneName: 'shortOffset', hour: 'numeric' })
      .formatToParts(new Date(ms)).forEach(function (p) { if (p.type === 'timeZoneName') name = p.value; });
    var m = /(?:GMT|UTC)([+-])(\d{1,2})/.exec(name);
    if (!m) throw new Error('saat dilimi ofseti okunamadı: ' + name);
    return (m[1] === '-' ? -1 : 1) * Number(m[2]);
  }
  function seasonsOf(w) { return (w && w.seasons) || [{ cron: w.cron, offset: null }]; }
  function expectedSlots(w, fromMs, toMs) {
    var out = [];
    seasonsOf(w).forEach(function (s) {
      slotsBetween(s.cron, fromMs, toMs).forEach(function (t) {
        if (s.offset == null || offsetHours(t) === s.offset) out.push(t);
      });
    });
    out.sort(function (a, b) { return a - b; });
    return out;
  }
  function nextActive(w, fromMs, count) {
    var pool = [];
    seasonsOf(w).forEach(function (s) {
      nextRuns(s.cron, fromMs, count + 4).forEach(function (t) {
        if (s.offset == null || offsetHours(t) === s.offset) pool.push(t);
      });
    });
    pool.sort(function (a, b) { return a - b; });
    var out = [];
    pool.forEach(function (t) { if (!out.length || out[out.length - 1] !== t) out.push(t); });
    return out.slice(0, count);
  }
  function localDayStart(ms) {
    var off = offsetHours(ms) * HOUR;
    var start = Math.floor((ms + off) / DAY) * DAY - off;
    var off2 = offsetHours(start) * HOUR;
    if (off2 !== off) start = Math.floor((ms + off) / DAY) * DAY - off2;
    return start;
  }
  function localMidnights(fromMs, untilMs) {
    var t = localDayStart(fromMs);
    if (t < fromMs) t = localDayStart(t + 30 * HOUR);
    var out = [];
    while (t < untilMs) {
      out.push(t);
      var n = localDayStart(t + 30 * HOUR);
      if (n <= t) break;
      t = n;
    }
    return out;
  }
  function clockMarks(w, nowMs) {
    var offNow = offsetHours(nowMs), chosen = null, seasons = seasonsOf(w);
    for (var i = 0; i < seasons.length; i++) {
      if (seasons[i].offset == null || seasons[i].offset === offNow) { chosen = seasons[i]; break; }
    }
    if (!chosen) chosen = seasons[0];
    var useOff = chosen.offset == null ? offNow : chosen.offset;
    var spec = parseCron(chosen.cron), mins = Object.keys(spec.min.set).map(Number)[0];
    var mm = function (m) { return (m < 10 ? '0' : '') + m; };
    return Object.keys(spec.hour.set).map(Number).sort(function (a, b) { return a - b; }).map(function (uh) {
      var h = (uh + useOff) % 24;
      return { hour: h + mins / 60, label: mm(h) + ':' + mm(mins) };
    });
  }
  function describeCron(cron) {                        // cron’un kendi ofsetiyle Berlin duvar saati
    var p = String(cron).trim().split(/\s+/), spec = parseCron(cron);
    var mins = Object.keys(spec.min.set).map(Number), hours = Object.keys(spec.hour.set).map(Number);
    var mm = function (m) { return (m < 10 ? '0' : '') + m; };
    var off = CRON_OFFSET[String(cron).trim()];
    if (off == null) off = 0;
    var hh = function (h) { return ((h + off) % 24 < 10 ? '0' : '') + ((h + off) % 24); };
    if (p[1].indexOf('*/') === 0 && p[2] === '*' && p[4] === '*') return 'Her ' + p[1].slice(2) + ' saatte bir (her turun ' + mm(mins[0]) + '. dakikası)';
    var times = hours.map(function (h) { return hh(h) + ':' + mm(mins[0]); }).join(', ');
    if (p[2] === '*' && p[4] === '*') return 'Her gün ' + times;
    if (p[4] !== '*' && p[2] === '*') {
      var days = { 0: 'pazar', 1: 'pazartesi', 2: 'salı', 3: 'çarşamba', 4: 'perşembe', 5: 'cuma', 6: 'cumartesi', 7: 'pazar' };
      return 'Her ' + Object.keys(spec.dow.set).filter(function (d) { return d !== '7'; }).map(function (d) { return days[d]; }).join(', ') + ' ' + times;
    }
    if (p[2] !== '*' && p[4] === '*') return 'Ayın ' + Object.keys(spec.dom.set).join(' ve ') + '’i ' + times;
    return cron + ' (UTC)';
  }

  // ------------------------------------------------------------------ zamanlayıcı uyumu: planlanan tur / gerçek çalışma
  /* Her zamanlı çalışma, kendinden önceki en yakın boş tura eşlenir. Eşlenmeyen tur "kaçırıldı".
   * sinceMs: değerlendirme penceresinin başı (workflow kaydı ya da elde olan en eski çalışma). */
  function offSeasonRun(r) {
    // Başlık hangi cron’un çaldığını söyler. O anki Berlin ofseti başlıkla uyuşmuyorsa tur boştur.
    var title = String((r && (r.display_title || r.name)) || '');
    var want = title.indexOf('kış saati') >= 0 ? 1 : (title.indexOf('yaz saati') >= 0 ? 2 : 0);
    if (!want) return false;
    var t = parseTimeMs(r.created_at);
    return finite(t) && offsetHours(t) !== want;
  }
  function adherence(cron, runs, sinceMs, nowMs) {
    return adherenceSlots(slotsBetween(cron, sinceMs, nowMs), runs, nowMs);
  }
  function adherenceSlots(slots, runs, nowMs) {
    var times = runs.filter(function (r) {
      var title = String((r && (r.display_title || r.name)) || '');
      var clock = title.indexOf('(saat)') >= 0 || title.indexOf('(nöbet)') >= 0;   // saat ya da nöbetçi başlattı
      return (r.event === 'schedule' || clock) && r.conclusion !== 'cancelled' && r.conclusion !== 'skipped' && !offSeasonRun(r);
    }).map(function (r) { return parseTimeMs(r.created_at); }).filter(finite).sort(function (a, b) { return a - b; });
    var matched = slots.map(function () { return null; }), extra = 0;
    times.forEach(function (t) {
      var j = -1;
      for (var k = 0; k < slots.length; k++) { if (slots[k] <= t) j = k; else break; }
      while (j >= 0 && matched[j] !== null) j--;
      if (j < 0) extra++; else matched[j] = t;
    });
    var items = [];
    slots.forEach(function (s, i) {
      if (matched[i] === null && s >= nowMs - C.SLOT_GRACE_MS) return;   // bekleme payı dolmadan "kaçırıldı" denmez
      var delay = matched[i] === null ? null : matched[i] - s;
      var kind = delay === null ? 'missed' : (delay <= C.DELAY_OK_MS ? 'ontime' : (delay <= C.DELAY_BAD_MS ? 'late' : 'verylate'));
      items.push({ slot: s, runAt: matched[i], delay: delay, kind: kind });
    });
    var delays = items.filter(function (x) { return x.delay !== null; }).map(function (x) { return x.delay; });
    var count = function (k) { return items.filter(function (x) { return x.kind === k; }).length; };
    return { items: items, extra: extra, total: items.length, ontime: count('ontime'), late: count('late'),
             verylate: count('verylate'), missed: count('missed'),
             medianDelay: delays.length ? median(delays) : NaN, maxDelay: delays.length ? Math.max.apply(null, delays) : NaN };
  }

  // ------------------------------------------------------------------ istatistikler (ops/report.py'nin yapısal karşılığı)
  function realVideos(videos) { return videos.filter(function (r) { return r.video_id && !startsTest(r.video_id); }); }

  function buildStats(videos, weights, nowMs) {
    var vids = realVideos(videos);
    var wts = weights.filter(function (w) { return w.dimension && w.value; });
    var time = function (r) { return parseTimeMs(r.published_at); };
    var published = vids.filter(function (r) { return !isNaN(time(r)); }).sort(function (a, b) { return time(a) - time(b); });
    var scored = published.filter(function (r) { return isNum(r.performance_score); });
    var sc = function (r) { return num(r.performance_score); };
    var st = { videos: vids, published: published, scored: scored };

    st.posted7 = published.filter(function (r) { return nowMs - time(r) <= 7 * DAY; }).length;
    var lastN = scored.slice(-7), prevN = scored.slice(Math.max(0, scored.length - 14), Math.max(0, scored.length - 7));
    st.lastMean = lastN.length ? mean(lastN.map(sc)) : NaN;
    st.prevMean = prevN.length ? mean(prevN.map(sc)) : NaN;
    st.lastN = lastN.length; st.prevN = prevN.length;
    st.change = prevN.length >= 3 ? ((st.lastMean - st.prevMean) / Math.max(st.prevMean, 1)) * 100 : NaN;

    var group = function (list, keyFn) {
      var g = {}, order = [];
      list.forEach(function (r) { var k = keyFn(r); if (!g[k]) { g[k] = []; order.push(k); } g[k].push(r); });
      return order.map(function (k) { return { key: k, rows: g[k] }; });
    };
    st.byFormat = group(scored, function (r) { return String(r.loop_strategy || 'bilinmiyor'); })
      .sort(function (a, b) { return a.key < b.key ? -1 : 1; })
      .map(function (g) {
        var m = /(\d+)s$/.exec(g.key), dur = m ? parseFloat(m[1]) : 0;
        var ratios = dur ? g.rows.filter(function (r) { return isNum(r.avg_watch_time) && num(r.avg_watch_time) > 0; })
          .map(function (r) { return num(r.avg_watch_time) / dur; }) : [];
        return { key: g.key, n: g.rows.length, mean: mean(g.rows.map(sc)), duration: dur,
                 medianRatio: ratios.length ? median(ratios) : NaN, withWatch: ratios.length };
      });
    st.byMode = group(scored, function (r) { return String(r.selection_mode || 'bilinmiyor'); })
      .map(function (g) { return { key: g.key, n: g.rows.length, mean: mean(g.rows.map(sc)) }; });
    var morning = scored.filter(function (r) { return istHour(time(r)) < 14; });
    var evening = scored.filter(function (r) { return istHour(time(r)) >= 14; });
    st.hours = { morning: { n: morning.length, mean: morning.length ? mean(morning.map(sc)) : NaN },
                 evening: { n: evening.length, mean: evening.length ? mean(evening.map(sc)) : NaN } };
    if (morning.length && evening.length) {
      var am = st.hours.morning.mean, pm = st.hours.evening.mean;
      st.hours.gap = ((am - pm) / Math.max(Math.min(am, pm), 1)) * 100;
      st.hours.reliable = Math.min(morning.length, evening.length) >= 10;
    } else { st.hours.gap = NaN; st.hours.reliable = false; }

    st.weights = wts.filter(function (w) { return finite(num(w.weight)); }).map(function (w) {
      return { key: w.key, dimension: w.dimension, value: w.value, weight: num(w.weight), n: num(w.sample_count),
               avg: num(w.avg_score) };
    });
    st.learned = DIMENSIONS.map(function (d) {
      var rows = st.weights.filter(function (w) { return w.dimension === d && w.n >= C.MIN_SAMPLES_LEARNED; })
        .sort(function (a, b) { return b.weight - a.weight; });
      return { dimension: d, name: DIM_NAMES[d], rows: rows };
    }).filter(function (x) { return x.rows.length; });

    var last = published.length ? published[published.length - 1] : null;
    st.last = last;
    st.hoursSinceLast = last ? (nowMs - time(last)) / HOUR : NaN;
    st.pending72 = published.filter(function (r) { return nowMs - time(r) > C.PENDING_72H_H * HOUR && !isNum(r.views_72h); });
    st.noId = vids.filter(function (r) { return !r.instagram_id; }).length;
    st.withWatch = scored.filter(function (r) { return isNum(r.avg_watch_time) && num(r.avg_watch_time) > 0; }).length;
    st.milestones = [
      { n: C.MIN_SCORED_FOR_ELITES, title: 'Elit melezleme açılır', text: 'En az 4 skorlu video olunca evrim motoru en iyi videoların genlerini melezlemeye başlar.' },
      { n: C.MIN_PER_BUCKET * 2, title: 'Format kıyası güvenilir olur', text: 'Her formatta en az 8 skorlu video gerekir; üç format için yaklaşık 24.' },
      { n: 30, title: 'Skor katsayılarını ayarlama zamanı', text: 'İzlenme süresi katsayıları yaklaşık değerle başladı; 30 skorlu videoda gerçek dağılıma göre ayarlanır.' }
    ].map(function (m) { m.done = scored.length >= m.n; return m; });
    var waiting = published.filter(function (r) { return !isNum(r.performance_score); });
    var oldest = waiting.length ? waiting.slice().sort(function (a, b) { return time(a) - time(b); })[0] : null;
    st.learn = {
      published: published.length,
      has24: published.filter(function (r) { return isNum(r.views_24h); }).length,
      has72: published.filter(function (r) { return isNum(r.views_72h); }).length,
      nextId: oldest ? oldest.video_id : '',
      nextDue: oldest ? time(oldest) + 72 * HOUR : NaN,
      nextAgeH: oldest ? (nowMs - time(oldest)) / HOUR : NaN
    };
    return st;
  }

  // Tablodaki her video için tek satır: ölçüm durumu, skor ayrıntısı, trend arındırılmış skor
  function enrichVideos(videos, nowMs) {
    var real = realVideos(videos), ordered = orderedScored(real), t = trendRelative(ordered), relById = {}, rankById = {};
    ordered.forEach(function (r, i) { relById[r.video_id] = t.rel[i]; });
    ordered.slice().sort(function (a, b) { return num(b.performance_score) - num(a.performance_score); })
      .forEach(function (r, i) { rankById[r.video_id] = i + 1; });
    return real.map(function (r) {
      var m = measurement(r, nowMs), isS = isNum(r.performance_score);
      var fm = /(\d+)s$/.exec(String(r.loop_strategy || ''));
      return { row: r, id: r.video_id, pubMs: m.pubMs, m: m, scored: isS, score: isS ? num(r.performance_score) : NaN,
               rel: relById[r.video_id], rank: rankById[r.video_id] || null, scoredCount: ordered.length,
               parts: m.has72 ? scoreParts(r) : null, duration: fm ? parseFloat(fm[1]) : 0,
               views24: isNum(r.views_24h) ? num(r.views_24h) : NaN, views72: isNum(r.views_72h) ? num(r.views_72h) : NaN,
               watch: isNum(r.avg_watch_time) ? num(r.avg_watch_time) : NaN,
               materials: String(r.materials || '').split(', ').filter(Boolean),
               transformations: String(r.transformations || '').split(', ').filter(Boolean),
               hook: String(r.caption || '').split('\n')[0] };
    }).sort(function (a, b) { return (b.pubMs || 0) - (a.pubMs || 0); });
  }

  // ------------------------------------------------------------------ sonraki videonun tahmini seçim olasılıkları (ops/evolve.py)
  /* evolve.generate'in tek değerli genleri için kapalı form. Monte Carlo ile Python'a karşı sınanır.
   *   P(v) = explore·U(v) + (1-explore)·[(1-pe)·W(v) + pe·E(v)]
   *   U: eşit olasılık. W: ağırlık / ağırlıklar toplamı (bilinmeyen seçenek 1). pe: elit payı (4 skorlu videodan sonra 0,5).
   *   E: ebeveyn skorla orantılı seçilir, geni 0,75 olasılıkla aynen kalır, 0,25'te W'den yeniden çekilir. */
  function captionTags(caption) {
    var lines = String(caption == null ? '' : caption).trim().split('\n');
    var last = lines.length ? lines[lines.length - 1] : '';
    return last.split(/\s+/).filter(function (t) { return t.charAt(0) === '#' && t !== '#formform'; });
  }
  var ODDS_FIELDS = { structure: 'structure', palette: 'palette', motion: 'motion_quality', lighting: 'lighting', format: 'loop_strategy', sound: 'sound' };
  function weightLookup(dnaRows) {
    var m = {};
    dnaRows.forEach(function (row) {
      if (!row.dimension || !row.value) return;
      var w = num(row.weight);
      if (!finite(w) || w <= 0) return;
      m[String(row.dimension).trim() + '::' + String(row.value).trim()] = w;
    });
    return m;
  }
  function elitePool(videos) {
    var hist = videos.filter(function (r) { return r.video_id && !startsTest(r.video_id) && isNum(r.performance_score); });
    var top = hist.slice().sort(function (a, b) { return num(b.performance_score) - num(a.performance_score); }).slice(0, C.ELITE_POOL);
    var total = 0;
    top.forEach(function (r) { total += Math.max(1, num(r.performance_score)); });
    return { size: hist.length, enabled: hist.length >= C.MIN_SCORED_FOR_ELITES,
             parents: top.map(function (r) { return { row: r, share: Math.max(1, num(r.performance_score)) / total }; }) };
  }
  function selectionOdds(lib, dnaRows, videos) {
    var look = weightLookup(dnaRows), pool = elitePool(videos), pe = pool.enabled ? C.ELITE_RATE : 0;
    var wOf = function (d, v) { var w = look[d + '::' + v]; return Math.max(0.0001, w === undefined ? 1 : w); };
    var out = {};
    ['structure', 'palette', 'motion', 'lighting', 'format', 'hook', 'sound'].forEach(function (d) {
      var arr = lib[d] || [], n = arr.length;
      if (!n) return;
      var total = 0;
      arr.forEach(function (v) { total += wOf(d, v); });
      var rows = arr.map(function (v) {
        var U = 1 / n, W = wOf(d, v) / total, E = 0;
        pool.parents.forEach(function (p) {
          var pv = d === 'hook' ? String(p.row.caption || '').split('\n')[0].trim() : String(p.row[ODDS_FIELDS[d]] || '').trim();
          var inLib = arr.indexOf(pv) >= 0;
          E += p.share * ((inLib ? (1 - C.GENE_MUTATION) * (pv === v ? 1 : 0) + C.GENE_MUTATION * W : W));
        });
        var P = C.EXPLORATION_RATE * U + (1 - C.EXPLORATION_RATE) * ((1 - pe) * W + pe * E);
        return { value: v, p: P, uniform: U, weight: look[d + '::' + v] === undefined ? 1 : look[d + '::' + v],
                 known: look[d + '::' + v] !== undefined };
      }).sort(function (a, b) { return b.p - a.p; });
      out[d] = rows;
    });
    return { dims: out, pool: pool, modes: { explore: C.EXPLORATION_RATE, elite: (1 - C.EXPLORATION_RATE) * pe,
             weights: (1 - C.EXPLORATION_RATE) * (1 - pe) } };
  }

  // ------------------------------------------------------------------ release ve çalışma durumları
  function parseRelease(rel) {
    var m = /^(render|claimed|published) (.+)$/.exec(String(rel.name || ''));
    var at = parseTimeMs(rel.published_at || rel.created_at);
    return { id: rel.id, name: rel.name, tag: rel.tag_name, url: rel.html_url, state: m ? m[1] : 'other',
             videoId: m ? m[2] : '', atMs: at };
  }
  function runStatus(run) {
    if (!run) return 'none';
    if (run.status === 'completed') {
      if (run.conclusion === 'success') return 'ok';
      if (run.conclusion === 'cancelled' || run.conclusion === 'skipped' || run.conclusion === 'neutral') return 'cancel';
      return 'fail';
    }
    if (run.status === 'in_progress') return 'running';
    return 'queued';
  }
  function runDuration(run) {
    if (!run || run.status !== 'completed') return NaN;
    var a = parseTimeMs(run.run_started_at || run.created_at), b = parseTimeMs(run.updated_at);
    return finite(a) && finite(b) ? b - a : NaN;
  }

  // ------------------------------------------------------------------ sağlık denetimleri
  var LEVEL_RANK = { ok: 0, info: 1, unknown: 1, warn: 2, crit: 3 };
  function worst(checks) {
    var w = 'ok';
    checks.forEach(function (c) { if (LEVEL_RANK[c.level] > LEVEL_RANK[w]) w = c.level; });
    return w === 'info' || w === 'unknown' ? 'ok' : w;
  }

  var SCHED_HINTS = {
    publish: 'Paylaşım saati kayabilir. Saat sizin için kritikse publish’i istediğiniz saatte elle çalıştırın (Kontrol).',
    render: 'Render, yayından en az 60 dk önce başlar. Gecikme bu payı aşarsa yayın saatinde hazır render olmayabilir; gerekirse render’ı elle çalıştırın (Kontrol).',
    learn: 'Ölçümler geç gelir ama kaybolmaz: sonraki tur eksikleri tamamlar. Beklemek istemezseniz learn’ü elle çalıştırın (Kontrol).',
    token: 'Jeton ayın 1’i ve 15’inde yenilenir; bir tur kaçarsa sonraki tur tolerans içindedir. Kalan ömür 20 günün altına inerse elle çalıştırın.',
    report: 'Haftalık özet geç gelebilir; bir şey yapmanız gerekmez.'
  };

  /* ctx: { nowMs, stats, state: {config, workflows, runs{key:[]}, releases[], issues[], commits[]}, sources{key:{ok}} } */
  function healthChecks(ctx) {
    var now = ctx.nowMs, st = ctx.stats, S = ctx.state, out = [];
    var add = function (id, level, title, detail, hint, extra) {
      var o = { id: id, level: level, title: title, detail: detail, hint: hint || '' };
      if (extra) for (var k in extra) o[k] = extra[k];
      out.push(o);
    };
    var haveData = ctx.sources.videos && ctx.sources.videos.ok;

    // 1. Son paylaşım
    if (haveData) {
      if (!st.last) add('last-post', 'info', 'Henüz yayınlanmış video yok', 'videos.csv boş.', 'İlk yayın saatini bekleyin ya da publish’i elle çalıştırın.');
      else {
        var h = st.hoursSinceLast;
        var lvl = h > C.POST_STALE_H ? 'crit' : (h > 16 ? 'warn' : 'ok');
        add('last-post', lvl, 'Son paylaşım ' + fmtDur(h * HOUR) + ' önce',
            st.last.video_id + ' · ' + fmtIst(parseTimeMs(st.last.published_at)),
            lvl === 'ok' ? '' : 'Yayın ritmi günde iki. Akış sekmesinde publish ve render çalışmalarına ve zamanlayıcı gecikmesine bakın.');
      }
    }

    // 2. Haftalık tempo
    if (haveData && st.published.length) {
      var first = parseTimeMs(st.published[0].published_at), ageDays = (now - first) / DAY;
      if (ageDays < 7) add('pace', 'info', 'Son 7 günde ' + st.posted7 + ' video', 'Sistem 7 günden yeni; beklenen sayı ' + C.EXPECTED_POSTS_7D + ' haftalık tam hızda.', '');
      else add('pace', st.posted7 >= 12 ? 'ok' : (st.posted7 >= 7 ? 'warn' : 'crit'), 'Son 7 günde ' + st.posted7 + ' video',
               'Beklenen ' + C.EXPECTED_POSTS_7D + '.', st.posted7 >= 12 ? '' : 'Kaçan yayınların nedenini Akış sekmesinde (kaçırılan turlar, başarısız çalışmalar) arayın.');
    }

    // 3. Bekleyen ve geciken ölçümler
    if (haveData) {
      var late72 = st.pending72.length;
      var late24 = st.published.filter(function (r) {
        var m = measurement(r, now); return m.state24 === 'late' && !m.has72 && m.ageH < C.PENDING_72H_H;
      }).length;
      if (late72) add('pending', 'crit', late72 + ' videonun 72 saatlik ölçümü boş', '80 saatten eski videolar.', 'Öğrenme akışı ve Instagram jetonu kontrol edilmeli (Akış → learn).');
      else if (late24) add('pending', 'warn', late24 + ' videonun 24 saatlik ölçümü gecikti', '24. saatin üstünden 6 saatten fazla geçti.', 'learn her 3 saatte bir çalışır. Bir sonraki turu bekleyin; sürerse jetonu ve learn çalışmalarını kontrol edin.');
      else add('pending', 'ok', 'Ölçümler zamanında', 'Geciken 24 ya da 72 saatlik ölçüm yok.', '');
      if (st.noId) add('no-id', 'warn', st.noId + ' satırda instagram_id yok', 'Bu videolar ölçülemez.', 'videos.csv’de ilgili satırı kontrol edin.');
    }

    // 4. Jeton ve Actions dakikası
    var bill = S.billing;
    if (bill && bill.ok && finite(bill.remaining) && finite(bill.included)) {
      var tight = bill.included > 0 && bill.remaining < bill.included * 0.15;
      var billLevel = bill.remaining <= 0 ? 'crit' : (tight ? 'warn' : 'ok');
      add('actions-minutes', billLevel, 'Actions dakikası: ' + fmtNum(bill.remaining, 0) + ' kaldı',
          fmtNum(bill.used, 0) + ' / ' + fmtNum(bill.included, 0) + ' kullanıldı' + (bill.paid > 0 ? ' · ücretli ' + fmtNum(bill.paid, 0) : ''),
          billLevel === 'ok' ? '' : 'Ücretsiz dakika bitince render ve yayın durur. Daralınca sistem daha kısa videoya geçer; GitHub yine de harcanan dakikayı sayar.');
    }
    var cfg = S.config || {};
    if (ctx.sources.config && ctx.sources.config.ok) {
      var exp = parseTimeMs(cfg.ig_token_expires_at), upd = parseTimeMs(cfg.ig_token_updated_at);
      if (finite(exp)) {
        var left = (exp - now) / DAY;
        add('token', left < 10 ? 'crit' : (left < 20 ? 'warn' : 'ok'), 'Jeton ' + (left > 0 ? fmtNum(left, 0) + ' gün sonra doluyor' : 'doldu'),
            'Son yenileme ' + (finite(upd) ? fmtIst(upd) : '–') + ' · bitiş ' + fmtIst(exp),
            left < 20 ? 'Token workflow’unu çalıştırın (Kontrol). Yenilenemezse Meta’dan yeni jeton alıp IG_ACCESS_TOKEN gizli değerini güncelleyin.' : '');
      } else add('token', 'info', 'Jeton henüz yenilenmedi', 'İlk jeton kullanılıyor, bitiş tarihi kayıtlı değil (ilk jeton 60 gün geçerlidir).',
                 'Token workflow’u ayın 1’i ve 15’inde kendiliğinden çalışır. İlk jetondan 24 saat geçtikten sonra elle de çalıştırabilirsiniz.');
    }

    // 5. Workflow durumları
    WORKFLOWS.forEach(function (w) {
      var runs = (S.runs && S.runs[w.key]) || [], src = ctx.sources['runs:' + w.key];
      if (!src || !src.ok) return;
      var def = (S.workflows || []).filter(function (x) { return x.path === '.github/workflows/' + w.file; })[0];
      if (def && def.state !== 'active') {
        add('wf-' + w.key, 'crit', w.name + ' devre dışı', 'Durum: ' + def.state, 'GitHub → Actions sekmesinde workflow’u etkinleştirin. (GitHub, uzun süre etkinlik olmayan depolarda zamanlanmış çalışmaları kapatabilir.)', { workflow: w.key });
        return;
      }
      var lastDone = null;
      runs.forEach(function (r) {
        if (r.status !== 'completed' || offSeasonRun(r)) return;
        if (!lastDone || parseTimeMs(r.created_at) > parseTimeMs(lastDone.created_at)) lastDone = r;
      });
      if (!lastDone) { add('wf-' + w.key, 'info', w.name + ': henüz çalışma yok', 'İlk zamanlı çalışmayı bekliyor.', '', { workflow: w.key }); return; }
      var s = runStatus(lastDone);
      if (s === 'fail') add('wf-' + w.key, 'crit', w.name + ': son çalışma başarısız', fmtIst(parseTimeMs(lastDone.created_at)) + ' · ' + (lastDone.display_title || ''),
                            'Çalışma günlüğünde kırmızı adıma bakın. Kalıcı bir sorun varsa hata issue’su da açılmıştır.', { workflow: w.key, url: lastDone.html_url });
      else add('wf-' + w.key, 'ok', w.name + ': son çalışma başarılı', fmtRel(parseTimeMs(lastDone.created_at), now), '', { workflow: w.key });
    });

    // 6. Açık hata issue'ları
    if (ctx.sources.issues && ctx.sources.issues.ok) {
      var alerts = (S.issues || []).filter(function (i) { return /^\[FORMFORM\] Hata:/.test(i.title); });
      if (alerts.length) add('issues', 'crit', alerts.length + ' açık hata kaydı', alerts.map(function (i) { return i.title.replace('[FORMFORM] Hata: ', ''); }).join(', '),
                             'Kayıtlardaki “Ne yapmalı” ipucunu izleyin. Workflow düzelince kayıt kendiliğinden kapanır.', { issues: alerts });
      else add('issues', 'ok', 'Açık hata kaydı yok', '', '');
    }

    // 7. Release kuyruğu
    if (ctx.sources.releases && ctx.sources.releases.ok) {
      var rels = (S.releases || []).map(parseRelease);
      var stuck = rels.filter(function (r) { return r.state === 'claimed' && now - r.atMs > HOUR; });
      if (stuck.length) add('stuck', 'crit', stuck.length + ' release “claimed” durumunda takılı', stuck.map(function (r) { return r.videoId; }).join(', '),
                            'Yayın yarıda kalmış. Instagram’a bakın: paylaşıldıysa release adını “published <id>” yapın. Paylaşılmadıysa sebebi giderip adı “render <id>” yapın.', { releases: stuck });
      var ready = rels.filter(function (r) { return r.state === 'render'; });
      if (ready.length > 2) add('backlog', 'warn', ready.length + ' render yayını bekliyor', 'Yayın, render’ın gerisinde kalıyor.', 'publish çalışmalarını ve zamanlayıcı gecikmesini kontrol edin.');
      var nextPub = nextActive(WORKFLOWS[1], now, 1)[0];
      if (!ready.length && nextPub - now < 90 * MIN) add('no-render', 'warn', 'Sıradaki yayına ' + fmtDur(nextPub - now) + ' var, hazır render yok',
        'publish hazır bir render bulamazsa “Yayınlanacak render yok” hatasıyla durur.', 'render workflow’unu elle çalıştırın (Kontrol).');
      else if (ready.length) add('ready', 'ok', ready.length + ' render yayına hazır', ready.map(function (r) { return r.videoId; }).join(', '), '');
    }

    // 8. Zamanlayıcı
    WORKFLOWS.forEach(function (w) {
      var a = ctx.adherence && ctx.adherence[w.key];
      if (!a || a.total < 1) return;
      var bad = a.missed + a.verylate;
      if (!bad) return;
      var lv = (w.key === 'publish' || w.key === 'render') ? (a.missed ? 'crit' : 'warn') : 'warn';
      add('sched-' + w.key, lv, w.name + ': ' + a.total + ' turun ' + (a.missed ? a.missed + ' tanesi kaçırıldı' : '') + (a.missed && a.verylate ? ', ' : '') + (a.verylate ? a.verylate + ' tanesi 90 dakikadan fazla gecikti' : ''),
          'En büyük gecikme ' + fmtDur(a.maxDelay || 0) + '. Turları saat (Cloudflare Worker) başlatır; kaçırdığını nöbetçi (FORMFORM watch) günde iki kez başlatır.',
          SCHED_HINTS[w.key], { workflow: w.key });
    });
    return out;
  }

  // ------------------------------------------------------------------ bağlayıcı yanıtları (github MCP) → panel durumu
  function fileText(result) {
    var blocks = (result && result.content) || [], i, b, m;
    for (i = 0; i < blocks.length; i++) {
      b = blocks[i];
      if (b && b.type === 'resource' && b.resource) {
        if (typeof b.resource.text === 'string') return b.resource.text;
        if (typeof b.resource.blob === 'string' && typeof atob === 'function') {
          var bin = atob(b.resource.blob), bytes = new Uint8Array(bin.length);
          for (var k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
          return new TextDecoder('utf-8').decode(bytes);
        }
      }
    }
    for (i = 0; i < blocks.length; i++) {
      b = blocks[i];
      if (b && b.type === 'text' && typeof b.text === 'string') {
        m = /^\[Resource from github at [^\]]*\] ?([\s\S]*)$/.exec(b.text);
        if (m) return m[1];
      }
    }
    if (result && typeof result.payload === 'string' && !/^successfully downloaded/.test(result.payload)) return result.payload;
    return null;
  }
  function asObject(payload) {
    if (typeof payload === 'string') { try { return JSON.parse(payload); } catch (e) { return null; } }
    return payload;
  }
  function parseRuns(payload) {
    var p = asObject(payload);
    var list = p && (p.workflow_runs || (Array.isArray(p) ? p : null));
    if (!Array.isArray(list)) throw new Error('çalışma listesi okunamadı');
    return list.map(function (r) {
      return { id: r.id, name: r.name, display_title: r.display_title, workflow_id: r.workflow_id, run_number: r.run_number,
               event: r.event, status: r.status, conclusion: r.conclusion, head_branch: r.head_branch, html_url: r.html_url,
               created_at: r.created_at, run_started_at: r.run_started_at, updated_at: r.updated_at,
               actor: r.actor && r.actor.login };
    });
  }
  function parseWorkflows(payload) {
    var p = asObject(payload);
    if (!p || !Array.isArray(p.workflows)) throw new Error('workflow listesi okunamadı');
    return p.workflows.map(function (w) { return { id: w.id, name: w.name, path: w.path, state: w.state, html_url: w.html_url, created_at: w.created_at }; });
  }
  function parseReleases(payload) {
    var p = asObject(payload);
    if (!Array.isArray(p)) throw new Error('release listesi okunamadı');
    return p.map(function (r) { return { id: r.id, name: r.name, tag_name: r.tag_name, html_url: r.html_url, published_at: r.published_at, draft: r.draft }; });
  }
  function parseIssues(payload) {
    var p = asObject(payload);
    if (!p || !Array.isArray(p.issues)) throw new Error('issue listesi okunamadı');
    return p.issues.map(function (i) { return { number: i.number, title: i.title, state: i.state, created_at: i.created_at, updated_at: i.updated_at, url: i.html_url }; });
  }
  function parseCommits(payload) {
    var p = asObject(payload);
    if (!Array.isArray(p)) throw new Error('commit listesi okunamadı');
    return p.map(function (c) { return { sha: c.sha, url: c.html_url, message: c.commit && c.commit.message, at: c.commit && c.commit.committer && c.commit.committer.date }; });
  }
  function actionsQuota(payload) {
    // İki biçim: eski özette included/total/paid vardır. İnce ayarlı anahtarın okuduğu kullanım
    // raporunda yalnız bu ay harcanan dakika vardır; kalan hak o raporda yazmaz.
    if (!payload || payload.unavailable) return { ok: false, code: (payload && payload.code) || 'unavailable' };
    if (payload.source === 'usage' || (Array.isArray(payload.usageItems) && !finite(num(payload.included_minutes)))) {
      var usedMin = 0;
      (payload.usageItems || []).forEach(function (it) {
        if (!it) return;
        var sku = String(it.sku || '').toLowerCase(), product = String(it.product || '').toLowerCase();
        var unit = String(it.unitType || it.unit_type || '').toLowerCase();
        var actions = product === 'actions' || product === 'github actions' || sku.indexOf('actions') === 0;
        if (!actions || /storage|cache|image/.test(sku)) return;
        if (unit && unit !== 'minutes' && unit !== 'minute') return;
        var q = num(it.quantity != null ? it.quantity : it.grossQuantity);
        if (!finite(q) || q < 0) return;
        usedMin += q;
      });
      return { ok: true, included: null, used: usedMin, paid: 0, remaining: null };
    }
    var included = num(payload.included_minutes), used = num(payload.total_minutes_used), paid = num(payload.total_paid_minutes_used);
    if (!finite(included) || included < 0 || !finite(used) || used < 0) return { ok: false, code: 'parse' };
    if (!finite(paid) || paid < 0) paid = 0;
    var freeUsed = Math.max(0, used - paid);
    return { ok: true, included: included, used: freeUsed, paid: paid, remaining: Math.max(0, included - freeUsed) };
  }

  function parseConfig(text) {
    try { var o = JSON.parse(String(text || '{}')); return o && typeof o === 'object' && !Array.isArray(o) ? o : {}; } catch (e) { return {}; }
  }

  // ------------------------------------------------------------------ elle çalıştırma girdileri (Kontrol sekmesi)
  var VIDEO_ID_RE = /^[A-Za-z0-9_-]{3,64}$/;           // ops/cli.py _plan ile aynı
  function checkSpec(text, lib) {
    var t = String(text || '').trim();
    if (!t) return { ok: true, empty: true, problems: [], spec: null };
    var spec, problems = [];
    try { spec = JSON.parse(t); } catch (e) { return { ok: false, problems: ['JSON okunamadı: ' + e.message], spec: null }; }
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return { ok: false, problems: ['Tarif bir JSON nesnesi olmalı.'], spec: null };
    if (!VIDEO_ID_RE.test(String(spec.video_id || ''))) problems.push('video_id gerekli: harf, rakam, _ ve - (3–64 karakter).');
    var one = { structure: 'structure', palette: 'palette', motion_quality: 'motion', lighting: 'lighting' };
    Object.keys(one).forEach(function (k) {
      if (!spec[k]) problems.push(k + ' gerekli.');
      else if (lib[one[k]] && lib[one[k]].indexOf(spec[k]) < 0) problems.push(k + ' kütüphanede yok: “' + spec[k] + '”. Motor kararlı bir yedek seçer ve uyarı yazar.');
    });
    [['materials', 'material'], ['transformations', 'transformation']].forEach(function (p) {
      (Array.isArray(spec[p[0]]) ? spec[p[0]] : []).forEach(function (v) {
        if (lib[p[1]].indexOf(v) < 0) problems.push(p[0] + ' içinde kütüphanede olmayan ad: “' + v + '”.');
      });
    });
    if (Array.isArray(spec.transformations) && (spec.transformations.length < 2 || spec.transformations.length > 12)) problems.push('transformations 2–12 öğe olmalı.');
    return { ok: problems.length === 0, problems: problems, spec: spec, line: JSON.stringify(spec) };
  }

  // ------------------------------------------------------------------ örnek veri (gerçek değil, panelin dolu hâlini göstermek için)
  function mulberry32(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function makeDemo(nowMs, lib, seed) {
    var rnd = mulberry32(seed || 7), repo = 'ornek/ornek-depo';
    var pick = function (arr) { return arr[Math.floor(rnd() * arr.length)]; };
    var formats = [{ n: 'seamless_8s', d: 8, t: 4 }, { n: 'seamless_15s', d: 15, t: 7 }, { n: 'seamless_30s', d: 30, t: 10 }];
    var quality = {};
    ['structure', 'palette', 'motion', 'lighting'].forEach(function (d) { lib[d].forEach(function (v) { quality[d + v] = 0.65 + rnd() * 0.85; }); });
    var fq = { seamless_8s: 0.95, seamless_15s: 1.15, seamless_30s: 0.85 };
    var dayStart = localDayStart(nowMs);                                        // Berlin gün başı
    var slots = [];
    for (var d = 21; d >= 0; d--) { slots.push(dayStart - d * DAY + 8 * HOUR + 4 * MIN); slots.push(dayStart - d * DAY + 20 * HOUR + 3 * MIN); }
    slots = slots.filter(function (s) { return s < nowMs - 5 * MIN; });
    var videos = [], k = 0, growth;
    slots.forEach(function (pubMs, i) {
      var fmt = pick(formats), mode = i < 6 || rnd() < C.EXPLORATION_RATE ? 'exploration' : 'exploitation';
      var s = pick(lib.structure), p = pick(lib.palette), mo = pick(lib.motion), li = pick(lib.lighting), hk = pick(lib.hook);
      var mats = [], trs = [];
      for (var a = 0; a < fmt.t + 1; a++) mats.push(pick(lib.material));
      for (var b = 0; b < fmt.t; b++) trs.push(pick(lib.transformation));
      var q = (quality['structure' + s] + quality['palette' + p] + quality['motion' + mo] + quality['lighting' + li]) / 4 * fq[fmt.n];
      growth = 1 + i * 0.05;
      var views = Math.round(40 * growth * Math.exp((q - 0.9) * 3.2) * (0.6 + rnd() * 0.9));
      var eng = 0.03 + 0.07 * q * (0.6 + rnd() * 0.8);
      var created = pubMs - 35 * MIN, age = (nowMs - pubMs) / HOUR;
      var stamp = istParts(created), row = { video_id: 'FF_' + stamp.year + stamp.month + stamp.day + '_' + stamp.hour + stamp.minute + '00',
                  created_at: new Date(created).toISOString(), structure: s, palette: p, materials: mats.join(', '), transformations: trs.join(', '),
                  motion_quality: mo, lighting: li, loop_strategy: fmt.n, caption: hk + '\n\nform/form — continuous form study.\n\n#formform',
                  instagram_id: String(17900000000000000 + k++), published_at: new Date(pubMs).toISOString(),
                  views_24h: '', views_72h: '', likes: '', comments: '', shares: '', saves: '', quality_score: '', performance_score: '',
                  selection_mode: mode, structure_weight: 1, palette_weight: 1, motion_weight: 1, lighting_weight: 1, avg_watch_time: '' };
      if (age >= 24) row.views_24h = Math.round(views * 0.62);
      if (age >= 72) {
        row.views_72h = views;
        row.likes = Math.round(views * eng);
        row.comments = Math.round(views * eng * 0.06);
        row.shares = Math.round(views * eng * 0.18);
        row.saves = Math.round(views * eng * 0.22);
        row.avg_watch_time = Math.round(fmt.d * (0.35 + 0.4 * Math.min(1, q / 1.4)) * 100) / 100;
        row.performance_score = performanceScore(row);
      }
      videos.push(row);
    });
    var dna = dnaWeights(videos, new Date(nowMs).toISOString());
    var runs = {}, wfs = [], wid = 1000;
    var delayFor = function (key, i) {
      var r = rnd();
      if (key === 'publish' || key === 'render') return r < 0.55 ? Math.round(rnd() * 14 * MIN) : (r < 0.8 ? Math.round((25 + rnd() * 50) * MIN) : Math.round((2 + rnd() * 4) * HOUR));
      return r < 0.8 ? Math.round(rnd() * 12 * MIN) : Math.round((30 + rnd() * 40) * MIN);
    };
    WORKFLOWS.forEach(function (w, wi) {
      var list = [], created = nowMs - 14 * DAY, id = 9000000 + wi * 100000;
      expectedSlots(w, created, nowMs - 10 * MIN).forEach(function (slot, si) {
        if (rnd() < (w.key === 'learn' ? 0.1 : 0.04)) return;                         // kaçırılan tur
        var at = slot + delayFor(w.key, si), dur = (w.key === 'render' ? 8 + rnd() * 6 : w.key === 'publish' ? 1.2 + rnd() : 0.2 + rnd() * 0.3) * MIN * (w.key === 'render' ? 1 : 1);
        if (at + dur > nowMs) return;
        var bad = rnd() < 0.04;
        list.push({ id: id++, name: w.key + ' (zamanlı)', display_title: w.key + ' (zamanlı)', workflow_id: wid + wi, run_number: si + 1, event: 'schedule',
                    status: 'completed', conclusion: bad ? 'failure' : 'success', head_branch: 'main',
                    html_url: 'https://github.com/' + repo + '/actions/runs/' + id, created_at: new Date(at).toISOString(),
                    run_started_at: new Date(at).toISOString(), updated_at: new Date(at + dur).toISOString(), actor: 'ornek' });
      });
      list.sort(function (a, b) { return parseTimeMs(b.created_at) - parseTimeMs(a.created_at); });
      runs[w.key] = list;
      wfs.push({ id: wid + wi, name: w.name, path: '.github/workflows/' + w.file, state: 'active', html_url: 'https://github.com/' + repo, created_at: new Date(created).toISOString() });
    });
    var lastVid = videos[videos.length - 1];
    var releases = [];
    var nextRender = nextActive(WORKFLOWS[0], nowMs - 14 * HOUR, 1)[0];
    if (nextRender && nextRender < nowMs && nextActive(WORKFLOWS[1], nowMs - 14 * HOUR, 3).filter(function (t) { return t > nextRender && t < nowMs; }).length === 0)
      releases.push({ id: 1, name: 'render FF_DEMO_READY', tag_name: 'render-FF_DEMO_READY', html_url: 'https://github.com/' + repo + '/releases', published_at: new Date(nextRender + 12 * MIN).toISOString() });
    videos.slice(-8).reverse().forEach(function (v, i) {
      releases.push({ id: 10 + i, name: 'published ' + v.video_id, tag_name: 'render-' + v.video_id, html_url: 'https://github.com/' + repo + '/releases',
                      published_at: new Date(parseTimeMs(v.created_at) + 12 * MIN).toISOString() });
    });
    var config = { ig_token_updated_at: new Date(nowMs - 6 * DAY).toISOString().slice(0, 19) + 'Z',
                   ig_token_expires_at: new Date(nowMs + 54 * DAY).toISOString().slice(0, 19) + 'Z' };
    var billing = actionsQuota({ included_minutes: 2000, total_minutes_used: 640, total_paid_minutes_used: 0 });
    return { videos: videos, dna: dna, config: config, billing: billing, workflows: wfs, runs: runs, releases: releases,
             issues: [], commits: [{ sha: 'ornek', url: 'https://github.com/' + repo, message: 'Yayınlandı: ' + (lastVid ? lastVid.video_id : ''), at: lastVid ? lastVid.published_at : '' }] };
  }

  var api = {
    C: C, WORKFLOWS: WORKFLOWS, DIMENSIONS: DIMENSIONS, DIM_NAMES: DIM_NAMES, HOUR: HOUR, DAY: DAY, MIN: MIN, TZ: TZ,
    num: num, numOr0: numOr0, blank: blank, finite: finite, isNum: isNum, mean: mean, median: median, parseTimeMs: parseTimeMs,
    parseCSV: parseCSV, scoreParts: scoreParts, performanceScore: performanceScore, genesOf: genesOf,
    orderedScored: orderedScored, trendRelative: trendRelative, dnaWeights: dnaWeights, measurement: measurement, findDue: findDue,
    istHour: istHour, fmtIst: fmtIst, fmtIstShort: fmtIstShort, fmtClock: fmtClock, fmtDur: fmtDur, fmtRel: fmtRel, fmtNum: fmtNum,
    fmtCompact: fmtCompact,     parseCron: parseCron, slotsBetween: slotsBetween, nextRuns: nextRuns, describeCron: describeCron,
    offsetHours: offsetHours, expectedSlots: expectedSlots, nextActive: nextActive, localDayStart: localDayStart,
    localMidnights: localMidnights, clockMarks: clockMarks,
    adherence: adherence, adherenceSlots: adherenceSlots, offSeasonRun: offSeasonRun, buildStats: buildStats, enrichVideos: enrichVideos, elitePool: elitePool, selectionOdds: selectionOdds,
    weightLookup: weightLookup, parseRelease: parseRelease, runStatus: runStatus, runDuration: runDuration, healthChecks: healthChecks,
    worst: worst, fileText: fileText, parseRuns: parseRuns, parseWorkflows: parseWorkflows, parseReleases: parseReleases,
    parseIssues: parseIssues, parseCommits: parseCommits, parseConfig: parseConfig, actionsQuota: actionsQuota, checkSpec: checkSpec, makeDemo: makeDemo
  };
  return api;
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FF;
