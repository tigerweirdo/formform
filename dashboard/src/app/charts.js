/* ====================================================================== grafikler ve küçük görseller */
var MODE_COLOR = { exploration: 'var(--s2)', exploitation: 'var(--s1)' };
var MODE_NAME = { exploration: 'Keşif (rastgele)', exploitation: 'Öğrenilmiş seçim', random: 'Rastgele (eski)' };
var FORMAT_COLOR = { seamless_8s: 'var(--s1)', seamless_15s: 'var(--s2)', seamless_30s: 'var(--s3)' };
var KIND = {
  ontime: { name: 'Zamanında', color: 'var(--good)' }, late: { name: 'Gecikmeli', color: 'var(--warn)' },
  verylate: { name: 'Çok gecikmeli (90 dk+)', color: 'var(--serious)' }, missed: { name: 'Kaçırıldı', color: 'var(--crit)' }
};
function dShort(ms) { return FF.fmtIst(ms).slice(0, 5); }

function segmented(options, current, onChange, label) {
  var box = h('div', { class: 'seg', role: 'group', 'aria-label': label });
  options.forEach(function (o) {
    box.appendChild(h('button', { type: 'button', class: o.value === current ? 'on' : '', 'aria-pressed': o.value === current ? 'true' : 'false',
      onclick: function () { onChange(o.value); } }, o.label));
  });
  return box;
}

function sparkline(values, w, hgt) {
  var vs = values.filter(FF.finite);
  if (vs.length < 2) return h('span', { class: 'spark-empty', text: '–' });
  var lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs), pad = 4, span = hi - lo || 1;
  var pts = vs.map(function (v, i) { return [pad + i * (w - 2 * pad) / (vs.length - 1), hgt - pad - (v - lo) / span * (hgt - 2 * pad)]; });
  var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' ');
  var last = pts[pts.length - 1];
  return sv('svg', { viewBox: '0 0 ' + w + ' ' + hgt, width: w, height: hgt, class: 'spark', role: 'img', 'aria-label': 'Eğilim çizgisi' },
    sv('path', { d: d, fill: 'none', stroke: 'var(--muted)', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
    sv('circle', { cx: last[0], cy: last[1], r: 4, fill: 'var(--accent)', stroke: 'var(--surface)', 'stroke-width': 2 }));
}

function meter(value, max, kind, label) {
  var pct = clamp(value / max * 100, 0, 100), el = h('div', { class: 'meter meter-' + (kind || 'accent'), role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': max, 'aria-valuenow': nz(value, 0), 'aria-label': label || null });
  var fill = h('div', { class: 'meter-f' });
  fill.style.width = pct + '%';
  el.appendChild(fill);
  return el;
}

function stackedBar(parts) {
  var total = parts.reduce(function (s, p) { return s + Math.max(0, p.value); }, 0) || 1;
  var bar = h('div', { class: 'stack' });
  parts.forEach(function (p) {
    if (!(p.value > 0)) return;
    var seg = h('div', { class: 'stack-s', title: p.label + ': ' + p.text });
    seg.style.flexGrow = String(p.value / total * 1000);
    seg.style.background = p.color;
    bar.appendChild(seg);
  });
  var legend = h('div', { class: 'legend' });
  parts.forEach(function (p) {
    legend.appendChild(h('span', { class: 'lg' }, h('i', { class: 'sw', style: 'background:' + p.color }), h('span', { text: p.label + ' ' + p.text })));
  });
  return h('div', null, bar, legend);
}

// ------------------------------------------------------------------ yatay çubuklar (karşılaştırmalar)
/* rows: [{label, value, color, valueText, note, tip, faint}]. Çubuk en çok 14 px, sıfır tabandan büyür, uç 4 px yuvarlak. */
function hbars(rows, max) {
  var top = max || Math.max.apply(null, rows.map(function (r) { return nz(r.value, 0); }).concat([1]));
  var box = h('div', { class: 'hb' });
  rows.forEach(function (r) {
    var fill = h('div', { class: 'hb-f' + (r.faint ? ' faint' : '') });
    fill.style.width = clamp(nz(r.value, 0) / top * 100, 0, 100) + '%';
    fill.style.background = r.color || 'var(--s1)';
    var row = h('div', { class: 'hb-r', tabindex: r.tip ? '0' : null },
      h('div', { class: 'hb-l' }, h('span', { text: r.label }), r.note ? h('small', { text: r.note }) : null),
      h('div', { class: 'hb-t' }, fill),
      h('div', { class: 'hb-v', text: r.valueText }));
    if (r.tip) tipOn(row, function () { return r.tip; });
    box.appendChild(row);
  });
  return box;
}

// ------------------------------------------------------------------ DNA ağırlıkları: 1,0 nötr, aşağı kırmızı yukarı mavi
function weightsChart(rows) {
  var lo = C.MIN_WEIGHT, hi = C.MAX_WEIGHT, pos = function (w) { return clamp((w - lo) / (hi - lo), 0, 1) * 100; };
  var box = h('div', { class: 'wt' });
  box.appendChild(h('div', { class: 'wt-axis' },
    h('span', { text: String(lo).replace('.', ',') + ' (en zayıf)' }), h('span', { text: '1,0 nötr' }), h('span', { text: String(hi).replace('.', ',') + ' (en güçlü)' })));
  rows.forEach(function (r) {
    var mid = pos(1), at = pos(r.weight), left = Math.min(mid, at), width = Math.abs(at - mid);
    var fill = h('div', { class: 'wt-f' + (r.n < C.MIN_SAMPLES_LEARNED ? ' faint' : '') + (r.weight >= 1 ? ' up' : ' down') });
    fill.style.left = left + '%'; fill.style.width = Math.max(width, r.known ? 0.8 : 0) + '%';
    var midLine = h('i', { class: 'wt-mid' });
    midLine.style.left = mid + '%';
    var track = h('div', { class: 'wt-t' }, midLine, fill);
    var row = h('div', { class: 'wt-r', tabindex: '0' },
      h('div', { class: 'wt-l', text: r.value }), track,
      h('div', { class: 'wt-v' }, h('b', { text: r.known ? FF.fmtNum(r.weight, 2) : '1,00' }), h('small', { text: r.known ? 'n=' + r.n : 'denenmedi' })));
    tipOn(row, function () {
      return [r.value, [r.known ? 'Ağırlık' : 'Ağırlık (varsayılan)', FF.fmtNum(r.weight, 3)], r.known ? ['Örnek sayısı', String(r.n)] : ['Örnek sayısı', '0'],
              r.known ? ['Ortalama skor', FF.fmtNum(r.avg, 1)] : ['Ortalama skor', '–'],
              r.known && r.n < C.MIN_SAMPLES_LEARNED ? 'Örnek az: ağırlık henüz güvenilir değil' : ''];
    });
    box.appendChild(row);
  });
  return box;
}

// ------------------------------------------------------------------ skor akışı: video başına sütun
var METRICS = {
  score: { label: 'Performans skoru', unit: '', fn: function (v) { return v.scored ? v.score : null; }, fmt: function (n) { return FF.fmtNum(n, 1); }, fixedMax: 100 },
  views: { label: '72 saatlik izlenme', unit: '', fn: function (v) { return FF.finite(v.views72) ? v.views72 : null; }, fmt: FF.fmtCompact },
  eng: { label: 'Etkileşim puanı', unit: '', fn: function (v) { return v.parts ? v.parts.adjusted : null; }, fmt: function (n) { return FF.fmtNum(n, 1); }, fixedMax: 100 },
  watch: { label: 'İzlenen oran', unit: '%', fn: function (v) { return FF.finite(v.watch) && v.duration ? v.watch / v.duration * 100 : null; }, fmt: function (n) { return FF.fmtNum(n, 0) + '%'; }, fixedMax: 100 }
};
function metricSeries(V, key, limit) {
  var m = METRICS[key];
  return V.vids.filter(function (v) { return FF.finite(v.pubMs); }).slice(0, limit || 40).reverse().map(function (v) { return { v: v, y: m.fn(v) }; });
}
function pendingText(v, now) {
  if (v.m.state72 === 'ok') return '';
  return v.m.state72 === 'wait' ? '72 saatlik ölçüm ' + FF.fmtRel(v.m.due72, now) : '72 saatlik ölçüm gecikti';
}
function videoTip(it, m, now) {
  var v = it.v, lines = [v.id, ['Yayın', FF.fmtIst(v.pubMs)]];
  if (it.y == null) lines.push(pendingText(v, now) || (m.label + ' verisi yok'));
  else lines.push([m.label, m.fmt(it.y)]);
  lines.push(['Mod', MODE_NAME[v.row.selection_mode] || v.row.selection_mode || '–']);
  lines.push(['Yapı · palet', (v.row.structure || '–') + ' · ' + (v.row.palette || '–')]);
  lines.push(['Format', v.row.loop_strategy || '–']);
  return lines;
}

function scoreChart(V) {
  var key = S.metric, m = METRICS[key], items = metricSeries(V, key, 40), now = V.now;
  var vals = items.map(function (i) { return i.y; }).filter(function (y) { return y != null; });
  var useTable = S.view.score === 'table';
  var controls = h('div', { class: 'ctl' },
    segmented([{ value: 'score', label: 'Skor' }, { value: 'views', label: 'İzlenme' }, { value: 'eng', label: 'Etkileşim' }, { value: 'watch', label: 'İzlenen oran' }], key,
      function (v) { S.metric = v; store.set('metric', v); scheduleRender(); }, 'Metrik'),
    segmented([{ value: 'chart', label: 'Grafik' }, { value: 'table', label: 'Tablo' }], useTable ? 'table' : 'chart',
      function (v) { S.view.score = v; scheduleRender(); }, 'Görünüm'));
  var legend = h('div', { class: 'legend' },
    h('span', { class: 'lg' }, h('i', { class: 'sw', style: 'background:var(--s1)' }), 'Öğrenilmiş seçim'),
    h('span', { class: 'lg' }, h('i', { class: 'sw', style: 'background:var(--s2)' }), 'Keşif (rastgele)'),
    key === 'score' ? h('span', { class: 'lg' }, h('i', { class: 'sw line' }), '5’li hareketli ortalama') : null,
    h('span', { class: 'lg' }, h('i', { class: 'sw ghost' }), 'Ölçüm bekliyor'));
  var body;
  if (!items.length) body = empty('Henüz yayınlanmış video yok', 'İlk video yayınlanınca burada görünür.');
  else if (useTable) {
    var rows = items.slice().reverse().map(function (it) {
      return h('tr', null, h('td', { class: 'mono', text: it.v.id }), h('td', { text: FF.fmtIst(it.v.pubMs) }),
        h('td', { text: MODE_NAME[it.v.row.selection_mode] || it.v.row.selection_mode || '–' }),
        h('td', { class: 'num', text: it.y == null ? (pendingText(it.v, now) || '–') : m.fmt(it.y) }));
    });
    body = h('div', { class: 'tablewrap' }, h('table', { class: 'tbl' }, h('thead', null, h('tr', null, h('th', { text: 'Video' }), h('th', { text: 'Yayın' }), h('th', { text: 'Mod' }), h('th', { class: 'num', text: m.label }))), h('tbody', null, rows)));
  } else body = fitSvg(function (W0) { return drawColumns(W0, items, m, key === 'score', now); }, 250);
  var subtitle = vals.length ? 'Son ' + items.length + ' yayın, eskiden yeniye. ' + vals.length + ' tanesinin ' + m.label.toLowerCase() + ' verisi var.'
    : 'Ölçümler yayından 24 ve 72 saat sonra gelir. Gri çizgiler ölçümü bekleyen videolardır.';
  return card(m.label + ' · video başına', subtitle, h('div', null, legend, body, srcNote(['videos'])), { right: controls, id: 'score-chart' });
}

function drawColumns(W0, items, m, withAvg, now) {
  var H = 250, ml = 38, mr = 8, mt = 18, mb = 26, pw = W0 - ml - mr, ph = H - mt - mb, n = items.length;
  var vals = items.map(function (i) { return i.y; }).filter(function (y) { return y != null; });
  var top = m.fixedMax || niceMax(Math.max.apply(null, vals.concat([1])) * 1.08);
  var Y = function (y) { return mt + ph - clamp(y / top, 0, 1) * ph; };
  var slot = pw / n, bw = Math.min(24, Math.max(3, slot - 2));
  var svg = sv('svg', { viewBox: '0 0 ' + W0 + ' ' + H, width: W0, height: H, class: 'chart', role: 'img', 'aria-label': m.label + ' sütun grafiği' });
  for (var t = 0; t <= 4; t++) {
    var gv = top * t / 4, gy = Y(gv);
    svg.appendChild(sv('line', { x1: ml, x2: W0 - mr, y1: gy, y2: gy, stroke: t === 0 ? 'var(--line-2)' : 'var(--line)', 'stroke-width': 1 }));
    svg.appendChild(sv('text', { x: ml - 6, y: gy + 4, 'text-anchor': 'end', class: 'ax', text: m.unit === '%' ? FF.fmtNum(gv, 0) : FF.fmtCompact(gv) }));
  }
  var maxI = -1, maxV = -Infinity, lastI = -1;
  items.forEach(function (it, i) { if (it.y != null) { lastI = i; if (it.y > maxV) { maxV = it.y; maxI = i; } } });
  var avgPts = [], seq = [];
  items.forEach(function (it, i) {
    var cx = ml + slot * (i + 0.5), x = cx - bw / 2, y0 = Y(0);
    if (it.y == null) {
      svg.appendChild(sv('rect', { x: x, y: y0 - 4, width: bw, height: 4, rx: 1, fill: 'var(--line-2)' }));
    } else {
      var y = Y(it.y), hh = Math.max(2, y0 - y), r = Math.min(4, hh, bw / 2), yy = y0 - hh;
      svg.appendChild(sv('path', { d: 'M' + x + ' ' + y0 + 'V' + (yy + r) + 'Q' + x + ' ' + yy + ' ' + (x + r) + ' ' + yy + 'H' + (x + bw - r) + 'Q' + (x + bw) + ' ' + yy + ' ' + (x + bw) + ' ' + (yy + r) + 'V' + y0 + 'Z',
        fill: MODE_COLOR[it.v.row.selection_mode] || 'var(--muted)' }));
      if (i === maxI || i === lastI) svg.appendChild(sv('text', { x: cx, y: yy - 5, 'text-anchor': 'middle', class: 'dl', text: m.fmt(it.y) }));
      seq.push(it.y);
      if (withAvg) avgPts.push([cx, Y(FF.mean(seq.slice(-5)))]);
    }
  });
  if (avgPts.length > 1) svg.appendChild(sv('path', { d: avgPts.map(function (p, i) { return (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join(' '),
    fill: 'none', stroke: 'var(--ink-2)', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  var every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(pw / 64))));
  items.forEach(function (it, i) {
    if (i % every === 0 || i === n - 1) svg.appendChild(sv('text', { x: ml + slot * (i + 0.5), y: H - 8, 'text-anchor': i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle'), class: 'ax', text: dShort(it.v.pubMs) }));
  });
  items.forEach(function (it, i) {            // isabet alanı çubuktan geniş: tüm sütun genişliği ve yüksekliği
    var hit = sv('rect', { x: ml + slot * i, y: mt, width: slot, height: ph + 6, fill: 'transparent', tabindex: n <= 24 ? '0' : null, 'aria-label': it.v.id });
    tipOn(hit, function () { return videoTip(it, m, now); });
    hit.addEventListener('click', function () { openVideo(it.v.id); });
    svg.appendChild(hit);
  });
  return svg;
}

// ------------------------------------------------------------------ zamanlayıcı güvenilirliği: planlanan tur → gerçek çalışma
function markShape(kind, x, y, color) {
  switch (kind) {
    case 'ontime': return sv('circle', { cx: x, cy: y, r: 4.5, fill: color, stroke: 'var(--surface)', 'stroke-width': 2 });
    case 'late': return sv('path', { d: 'M' + x + ' ' + (y - 6) + 'L' + (x + 6) + ' ' + y + 'L' + x + ' ' + (y + 6) + 'L' + (x - 6) + ' ' + y + 'Z', fill: color, stroke: 'var(--surface)', 'stroke-width': 2 });
    case 'verylate': return sv('path', { d: 'M' + x + ' ' + (y - 6) + 'L' + (x + 6) + ' ' + (y + 5) + 'L' + (x - 6) + ' ' + (y + 5) + 'Z', fill: color, stroke: 'var(--surface)', 'stroke-width': 2 });
    default: return sv('path', { d: 'M' + (x - 5) + ' ' + (y - 5) + 'L' + (x + 5) + ' ' + (y + 5) + 'M' + (x + 5) + ' ' + (y - 5) + 'L' + (x - 5) + ' ' + (y + 5), stroke: color, 'stroke-width': 2.5, 'stroke-linecap': 'round', fill: 'none' });
  }
}
function stripChart(V, days) {
  var now = V.now, from = now - days * DAY, rows = W, earliest = Infinity;
  W.forEach(function (w) { V.adh[w.key].items.forEach(function (it) { if (it.slot < earliest) earliest = it.slot; }); });
  if (isFinite(earliest)) from = Math.min(Math.max(from, earliest - 3 * HOUR), now - 12 * HOUR);   // sistem gençse boş alan çizme
  return fitSvg(function (W0) {
    var ml = 84, mr = 12, mt = 8, rowH = 34, mb = 24, H = mt + rows.length * rowH + mb, pw = W0 - ml - mr;
    var X = function (t) { return ml + clamp((t - from) / (now - from), 0, 1) * pw; };
    var svg = sv('svg', { viewBox: '0 0 ' + W0 + ' ' + H, width: W0, height: H, class: 'chart', role: 'img', 'aria-label': 'Planlanan ve gerçekleşen çalışma zamanları' });
    var midnights = FF.localMidnights(from, now);                        // Berlin gün başları
    midnights.forEach(function (t) {
      svg.appendChild(sv('line', { x1: X(t), x2: X(t), y1: mt, y2: H - mb, stroke: 'var(--line)', 'stroke-width': 1 }));
      svg.appendChild(sv('text', { x: X(t) + 3, y: H - 8, class: 'ax', text: dShort(t + HOUR) }));
    });
    rows.forEach(function (w, ri) {
      var cy = mt + ri * rowH + rowH / 2, a = V.adh[w.key];
      svg.appendChild(sv('line', { x1: ml, x2: W0 - mr, y1: cy, y2: cy, stroke: 'var(--line)', 'stroke-width': 1 }));
      svg.appendChild(sv('text', { x: ml - 10, y: cy + 4, 'text-anchor': 'end', class: 'ax strong', text: w.key }));
      a.items.filter(function (it) { return it.slot >= from; }).forEach(function (it) {
        var k = KIND[it.kind], x = X(it.slot);
        if (it.runAt != null && it.delay > 0) svg.appendChild(sv('line', { x1: x, x2: X(it.runAt), y1: cy, y2: cy, stroke: k.color, 'stroke-width': 2, opacity: 0.55 }));
        svg.appendChild(markShape(it.kind, x, cy, k.color));
        var hit = sv('rect', { x: x - 10, y: cy - 14, width: 20, height: 28, fill: 'transparent' });
        tipOn(hit, function () {
          return [w.name, ['Planlanan', FF.fmtIstShort(it.slot)], ['Gerçekleşen', it.runAt == null ? 'çalışmadı' : FF.fmtIstShort(it.runAt)],
                  ['Gecikme', it.delay == null ? '–' : FF.fmtDur(it.delay)], k.name];
        });
        svg.appendChild(hit);
      });
    });
    svg.appendChild(sv('line', { x1: X(now), x2: X(now), y1: mt, y2: H - mb, stroke: 'var(--accent)', 'stroke-width': 1.5 }));
    svg.appendChild(sv('text', { x: X(now) - 4, y: H - 8, 'text-anchor': 'end', class: 'ax', text: 'şimdi' }));
    return svg;
  }, 60 + rows.length * 34);
}
function kindLegend() {
  var legend = h('div', { class: 'legend' });
  ['ontime', 'late', 'verylate', 'missed'].forEach(function (k) {
    var s = sv('svg', { viewBox: '-8 -8 16 16', width: 14, height: 14, 'aria-hidden': 'true' });
    s.appendChild(markShape(k, 0, 0, KIND[k].color));
    legend.appendChild(h('span', { class: 'lg' }, s, KIND[k].name));
  });
  return legend;
}

// ------------------------------------------------------------------ günün hattı: Berlin saatiyle 24 saat
function dayClock(V) {
  var rows = [W[0], W[1], W[2]];
  return fitSvg(function (W0) {
    var ml = 62, mr = 12, mt = 20, rowH = 30, mb = 22, H = mt + rows.length * rowH + mb, pw = W0 - ml - mr;
    var X = function (hr) { return ml + (hr / 24) * pw; };
    var svg = sv('svg', { viewBox: '0 0 ' + W0 + ' ' + H, width: W0, height: H, class: 'chart', role: 'img', 'aria-label': 'Günlük çalışma saatleri (Berlin)' });
    for (var hr = 0; hr <= 24; hr += 3) {
      svg.appendChild(sv('line', { x1: X(hr), x2: X(hr), y1: mt - 4, y2: H - mb, stroke: 'var(--line)', 'stroke-width': 1 }));
      svg.appendChild(sv('text', { x: X(hr), y: H - 7, 'text-anchor': hr === 0 ? 'start' : (hr === 24 ? 'end' : 'middle'), class: 'ax', text: ((hr % 24) < 10 ? '0' : '') + (hr % 24) + ':00' }));
    }
    [8, 20].forEach(function (target) {                    // kalıcı gereksinim: paylaşım 08:00 ve 20:00
      svg.appendChild(sv('line', { x1: X(target), x2: X(target), y1: mt - 4, y2: H - mb, stroke: 'var(--accent)', 'stroke-width': 1.5 }));
      svg.appendChild(sv('text', { x: X(target), y: 10, 'text-anchor': 'middle', class: 'ax accent', text: 'hedef ' + (target < 10 ? '0' : '') + target + ':00' }));
    });
    rows.forEach(function (w, ri) {
      var cy = mt + ri * rowH + rowH / 2, a = V.adh[w.key], med = a && a.total >= 2 && FF.finite(a.medianDelay) ? a.medianDelay : 0;
      svg.appendChild(sv('text', { x: ml - 8, y: cy + 4, 'text-anchor': 'end', class: 'ax strong', text: w.key }));
      FF.clockMarks(w, V.now).forEach(function (mark) {
        var x = X(mark.hour);
        if (med > C.DELAY_OK_MS) svg.appendChild(sv('rect', { x: x, y: cy - 6, width: Math.max(2, med / HOUR / 24 * pw), height: 12, rx: 3, fill: 'var(--warn)', opacity: 0.35 }));
        var dot = sv('circle', { cx: x, cy: cy, r: 5, fill: 'var(--ink-2)', stroke: 'var(--surface)', 'stroke-width': 2, tabindex: '0' });
        tipOn(dot, function () {
          var l = [w.name, ['Plan', mark.label + ' (Berlin)']];
          if (med > C.DELAY_OK_MS) l.push(['Tipik gecikme', FF.fmtDur(med)]);
          return l;
        });
        svg.appendChild(dot);
      });
    });
    return svg;
  }, 60 + rows.length * 30);
}
