/* ====================================================================== KONTROL: workflow'ları elle çalıştırma ve iptal
 * Panel yalnız iki yazma işlemi yapar: bir workflow'u çalıştırmak ve çalışan bir çalışmayı iptal etmek.
 * İkisi de iki adımlıdır (önce ne olacağı gösterilir, sonra onay). Gerçek paylaşım ayrıca yazılı onay ister.
 * Sonucu belirsiz kalan bir yazma (zaman aşımı, GitHub yanıtsız) asla kendiliğinden yeniden denenmez. */
var ACTIONS = {
  render: {
    risk: 'low', riskText: 'Düşük risk',
    effects: ['Evrim motoru yeni bir tarif üretir (ya da aşağıdaki tarifi kullanır).', 'Video çizilir (en çok 45 dk sürer) ve Reels uygunluğu doğrulanır.',
              '“render <video_id>” adlı bir release bırakılır. Yayın saatinde publish bunu alır.', 'GitHub Actions dakikası harcar (özel depoda ayda 2000 dk).',
              'Instagram’a hiçbir şey gönderilmez.']
  },
  publish: {
    risk: 'high', riskText: 'Gerçek paylaşım',
    effects: []
  },
  learn: {
    risk: 'low', riskText: 'Düşük risk',
    effects: ['Yayından 24 ve 72 saat geçen videoların Instagram ölçümleri çekilir.', 'Yeni 72 saatlik verilere performans skoru yazılır, DNA ağırlıkları yeniden hesaplanır.',
              'Sonuçlar veri dalına (videos.csv, dna_weights.csv) yazılır.', 'Normalde üç saatte bir kendiliğinden çalışır; beklememek için elle çalıştırılabilir.']
  },
  token: {
    risk: 'mid', riskText: 'Orta risk',
    effects: ['Instagram jetonu yenilenir, şifrelenip veri dalına yazılır.', 'Yanıt geçersizse eski jeton değişmez.',
              'Jeton en az 24 saatlik olmalı: ilk kurulumdan hemen sonra elle çalıştırmayın.']
  },
  report: {
    risk: 'low', riskText: 'Düşük risk',
    effects: []
  }
};
var CTRL_DEFAULTS = { render: { spec: '' }, publish: { dry_run: true }, learn: {}, token: {}, report: { bildirim_denemesi: true } };

function ctrlOf(key) {
  if (!S.ctrl[key]) S.ctrl[key] = { phase: 'idle', inputs: JSON.parse(JSON.stringify(CTRL_DEFAULTS[key])), word: '' };
  return S.ctrl[key];
}
function normWord(v) { return String(v || '').trim().toLocaleUpperCase('tr-TR').replace(/Ş/g, 'S'); }
function wordOk(c) { return normWord(c.word) === normWord(CONFIRM_WORD); }
function sampleSpec() {
  return JSON.stringify({ video_id: 'FF_MANUEL_' + Date.now().toString(36).toUpperCase(), structure: LIB.structure[0], palette: LIB.palette[0], motion_quality: LIB.motion[0],
    lighting: LIB.lighting[0], materials: LIB.material.slice(0, 5), transformations: LIB.transformation.slice(0, 4), duration: 6, fps: 30 }, null, 2);
}
function activeRun(V, w) {
  return (V.d.runs[w.key] || []).filter(function (r) { var s = FF.runStatus(r); return s === 'running' || s === 'queued'; })[0] || null;
}
function effectsFor(w, c) {
  if (w.key === 'publish') {
    return c.inputs.dry_run
      ? ['Instagram jetonu, hazır render’ın indirilmesi ve Cloudinary yüklemesi sınanır.', 'Paylaşım yapılmaz, release üstlenilmez, kayıt yazılmaz.', 'Her şey hazırsa “Her şey hazır” yazılır.']
      : ['En yeni hazır render üstlenilir (“claimed”) ve Cloudinary’ye yüklenir.', 'Instagram’da Reel olarak paylaşılır. Bu işlem geri alınamaz.',
         'videos.csv’ye satır yazılır, release “published” olur, öğrenme bu videodan başlar.', 'Hata olursa release “claimed” kalır ve otomatik denenmez.'];
  }
  if (w.key === 'report') {
    return c.inputs.bildirim_denemesi
      ? ['Kısa bir deneme issue’su açılır, size atanır ve kapatılır (e-posta gelmeli).', 'Özet hazırlanmaz. Bildirimlerin size ulaştığını sınar.']
      : ['Haftalık özet bir GitHub issue olarak açılır, size atanır ve kapatılır (e-posta gelir).', 'İçerik: özet, en iyi ve en kötü videolar, format ve saat karşılaştırması, öğrenilenler, sağlık.'];
  }
  return ACTIONS[w.key].effects;
}
function preflight(V, w, c) {
  var notes = [], ar = activeRun(V, w);
  if (ar) notes.push({ kind: 'crit', text: 'Bu workflow şu an ' + (FF.runStatus(ar) === 'running' ? 'çalışıyor' : 'sırada') + '. Bitmeden yenisini başlatmak aynı işi iki kez yaptırır.', url: ar.html_url });
  if (w.key === 'render' && V.ready.length >= 2) notes.push({ kind: 'warn', text: V.ready.length + ' render zaten yayını bekliyor. Yenisi birikimi artırır.' });
  if (w.key === 'publish') {
    if (!V.ready.length) notes.push({ kind: 'crit', text: 'Hazır render yok: publish “Yayınlanacak render yok” hatasıyla durur. Önce render’ı çalıştırın.' });
    else notes.push({ kind: 'info', text: 'Yayınlanacak olan: ' + V.ready[0].videoId + ' (render ' + FF.fmtRel(V.ready[0].atMs, V.now) + ').' });
    if (!c.inputs.dry_run && FF.finite(V.token.leftDays) && V.token.leftDays <= 0) notes.push({ kind: 'crit', text: 'Jetonun süresi dolmuş görünüyor; paylaşım başarısız olur.' });
  }
  if (w.key === 'token') {
    if (FF.finite(V.token.upd) && V.now - V.token.upd < 24 * HOUR) notes.push({ kind: 'warn', text: 'Jeton ' + FF.fmtRel(V.token.upd, V.now) + ' yenilendi. Instagram, 24 saatten genç jetonu yenilemeyi reddedebilir.' });
    else if (!FF.finite(V.token.upd)) notes.push({ kind: 'info', text: 'Jeton hiç yenilenmedi. İlk jetonunuz 24 saatten eskiyse çalıştırabilirsiniz.' });
  }
  return notes;
}
function buildInputs(w, c, specCheck) {
  if (w.key === 'render') return specCheck && specCheck.line ? { spec: specCheck.line } : null;      // boşsa evrim motoru üretir
  if (w.key === 'publish') return { dry_run: String(!!c.inputs.dry_run) };
  if (w.key === 'report') return { bildirim_denemesi: String(!!c.inputs.bildirim_denemesi) };
  return null;
}

function writeError(e) {
  var code = (e && e.code) || 'upstream_error';
  if (code === 'tool_error') return { kind: 'crit', title: 'GitHub isteği reddetti', text: String((e && e.message) || '').slice(0, 300) || 'Depo adını ve yazma iznini kontrol edin.' };
  if (code === 'server_unavailable' || code === 'upstream_error' || code === 'cancelled' || code === 'network')
    return { kind: 'warn', title: 'Sonuç belirsiz', text: 'GitHub yanıt vermedi; çalışma başlamış olabilir. Tekrar denemeden önce Akış sekmesinde yeni bir çalışma olup olmadığına bakın.' };
  var x = explainError(e);
  return { kind: 'crit', title: x.title, text: x.fix };
}

function dispatchWorkflow(w, specCheck) {
  var c = ctrlOf(w.key), inputs = buildInputs(w, c, specCheck);
  var args = { method: 'run_workflow', owner: OWNER, repo: REPO_NAME, workflow_id: w.file, ref: 'main' };
  if (inputs) args.inputs = inputs;
  c.phase = 'sending'; c.error = null; scheduleRender();
  S.mcp.callTool(SERVER, 'actions_run_trigger', args).then(function () {
    c.phase = 'sent'; c.sentAt = Date.now(); c.run = null; c.word = '';
    trackRun(w); scheduleRender();
  }, function (e) { c.phase = 'error'; c.error = writeError(e); scheduleRender(); });
}
function trackRun(w) {
  var c = ctrlOf(w.key), t0 = c.sentAt, n = 0;
  clearTimeout(S.timers[w.key]);
  (function poll() {
    if (S.mode !== 'live' || !S.mcp) return;
    S.mcp.invalidate(SERVER, 'actions_list').catch(function () { /* yoklama başarısızsa bir sonraki turda denenir */ });
    S.timers[w.key] = setTimeout(function () {
      var found = (S.live.runs[w.key] || []).filter(function (r) { return r.event === 'workflow_dispatch' && FF.parseTimeMs(r.created_at) >= t0 - 90000; })[0];
      if (found) { c.run = found; scheduleRender(); }
      n++;
      if (!(found && found.status === 'completed') && Date.now() - t0 < 25 * MIN) poll(); else scheduleRender();
    }, n < 3 ? 5000 : 15000);
  })();
}
function cancelRun(runId) {
  var cc = S.cancel;
  cc.phase = 'sending'; scheduleRender();
  S.mcp.callTool(SERVER, 'actions_run_trigger', { method: 'cancel_workflow_run', owner: OWNER, repo: REPO_NAME, run_id: runId }).then(function () {
    S.cancel = null; S.mcp.invalidate(SERVER, 'actions_list').catch(function () { /* sonraki yoklamada görünür */ }); scheduleRender();
  }, function (e) { cc.phase = 'error'; cc.error = writeError(e); scheduleRender(); });
}

function actionCard(V, w) {
  var c = ctrlOf(w.key), a = ACTIONS[w.key], ar = activeRun(V, w), live = liveOk(), notes = preflight(V, w, c);
  var specCheck = w.key === 'render' ? FF.checkSpec(c.inputs.spec, LIB) : null;
  var right = chip(a.risk === 'high' ? 'crit' : (a.risk === 'mid' ? 'warn' : 'ok'), a.riskText);
  var body = h('div', { class: 'act' }, h('p', { class: 'wf-d', text: w.does }),
    notes.length ? h('ul', { class: 'pre' }, notes.map(function (n) { return h('li', { class: 'pre-' + n.kind }, chip(n.kind === 'crit' ? 'crit' : (n.kind === 'warn' ? 'warn' : 'info'), n.kind === 'crit' ? 'Dikkat' : (n.kind === 'warn' ? 'Uyarı' : 'Bilgi')), h('span', { text: n.text + ' ' }), n.url ? link('Aç', n.url) : null); })) : null);

  if (c.phase === 'idle') {
    var why = !live ? (S.mode === 'demo' ? 'Örnek kipinde hiçbir şey çalıştırılmaz.' : 'GitHub bağlantısı yok.') : (ar ? 'Bu workflow zaten çalışıyor.' : '');
    body.appendChild(h('div', { class: 'btns' }, h('button', { class: 'btn btn-primary', type: 'button', disabled: !!why, onclick: function () { c.phase = 'confirm'; scheduleRender(); } }, 'Çalıştır…'), why ? h('span', { class: 'muted', text: why }) : null));
  } else if (c.phase === 'confirm') {
    body.appendChild(confirmPanel(V, w, c, specCheck, notes));
  } else if (c.phase === 'sending') {
    body.appendChild(h('p', { class: 'note', role: 'status', text: 'GitHub’a gönderiliyor…' }));
  } else if (c.phase === 'sent') {
    var r = c.run;
    body.appendChild(h('div', { class: 'sent', role: 'status' }, chip('ok', 'Tetiklendi'), r ? runChip(r) : chip('wait', 'Çalışma bekleniyor'),
      h('span', { class: 'muted', text: r ? (FF.finite(FF.runDuration(r)) ? 'Süre ' + FF.fmtDur(FF.runDuration(r)) : 'Başladı ' + FF.fmtRel(FF.parseTimeMs(r.created_at), V.now)) : 'GitHub çalışmayı sıraya alıyor…' }), r ? link('Çalışmayı aç', r.html_url) : null,
      h('button', { class: 'btn btn-ghost btn-xs', type: 'button', onclick: function () { clearTimeout(S.timers[w.key]); c.phase = 'idle'; c.run = null; scheduleRender(); } }, 'Tamam')));
  } else if (c.phase === 'error') {
    body.appendChild(h('div', { class: 'errbox pre-' + c.error.kind, role: 'alert' }, h('strong', { text: c.error.title }), h('p', { text: c.error.text }),
      h('div', { class: 'btns' }, h('button', { class: 'btn', type: 'button', onclick: function () { c.phase = 'idle'; c.error = null; scheduleRender(); } }, 'Kapat'))));
  }
  return h('section', { class: 'card act-card', id: 'act-' + w.key }, h('div', { class: 'card-h' }, h('div', null, h('h2', { text: w.name }), h('p', { class: 'sub', text: w.short + ' · ' + FF.describeCron(w.cron) })), right), body);
}

function confirmPanel(V, w, c, specCheck, notes) {
  var box = h('div', { class: 'confirm' }), real = w.key === 'publish' && !c.inputs.dry_run;
  function toggle(id, label, key, hint) {
    var cb = h('input', { type: 'checkbox', id: id, checked: !!c.inputs[key] });
    cb.addEventListener('change', function () { c.inputs[key] = cb.checked; if (cb.checked) c.word = ''; scheduleRender(); });
    return h('label', { class: 'tog' }, cb, h('span', null, h('strong', { text: label }), h('small', { text: hint })));
  }
  if (w.key === 'publish') box.appendChild(toggle('t-dry', 'Deneme (dry run)', 'dry_run', 'Açıkken hiçbir şey paylaşılmaz. Kapatmak gerçek paylaşım demektir.'));
  if (w.key === 'report') box.appendChild(toggle('t-ping', 'Yalnız bildirim denemesi', 'bildirim_denemesi', 'Açıkken özet hazırlanmaz, kısa bir deneme e-postası gelir.'));
  if (w.key === 'render') {
    var ta = h('textarea', { id: 'spec', rows: 8, spellcheck: 'false', placeholder: 'Boş bırakırsanız evrim motoru üretir. İsterseniz JSON tarifi yapıştırın.', 'aria-label': 'İsteğe bağlı elle tarif', value: c.inputs.spec });
    var out = h('div', { class: 'speccheck', 'aria-live': 'polite' });
    var paint = function (sc) {
      clear(out);
      if (sc.empty) out.appendChild(h('p', { class: 'note', text: 'Tarif boş: evrim motoru öğrenilmiş ağırlıklarla yeni bir tarif üretir.' }));
      else if (sc.ok) out.appendChild(h('p', { class: 'note', text: 'Tarif geçerli. Tek satıra çevrilip gönderilecek.' }));
      else out.appendChild(h('ul', { class: 'pre' }, sc.problems.map(function (p) { return h('li', { class: 'pre-warn', text: p }); })));
    };
    paint(specCheck);
    ta.addEventListener('input', function () { c.inputs.spec = ta.value; var sc = FF.checkSpec(ta.value, LIB); paint(sc); okBtn.disabled = !canGo(sc); });
    box.appendChild(h('label', { class: 'fld wide' }, h('span', { text: 'Elle tarif (isteğe bağlı)' }), ta));
    box.appendChild(h('div', { class: 'btns' }, h('button', { class: 'btn btn-ghost btn-xs', type: 'button', onclick: function () { c.inputs.spec = sampleSpec(); scheduleRender(); } }, 'Örnek tarif ekle'),
      h('button', { class: 'btn btn-ghost btn-xs', type: 'button', onclick: function () { c.inputs.spec = ''; scheduleRender(); } }, 'Temizle')));
    box.appendChild(out);
    box.appendChild(h('p', { class: 'note', text: 'Elle tarifte açıklama (caption) ve metadata olmadığı için publish onu seçmez: yalnız deneme içindir.' }));
  }
  box.appendChild(h('h4', { text: 'Bu işlem şunları yapar' }));
  box.appendChild(h('ul', { class: 'effects' }, effectsFor(w, c).map(function (e) { return h('li', { text: e }); })));
  if (real) {
    box.appendChild(h('div', { class: 'danger', role: 'alert' }, h('strong', { text: 'Gerçek Reel paylaşılır, geri alınamaz.' }),
      h('p', { text: 'Onaylamak için aşağıya ' + CONFIRM_WORD + ' yazın.' }),
      (function () { var i = h('input', { id: 'word', type: 'text', autocomplete: 'off', autocapitalize: 'characters', value: c.word, 'aria-label': 'Onay sözcüğü' });
        i.addEventListener('input', function () { c.word = i.value; okBtn.disabled = !canGo(specCheck); }); return i; })()));
  }
  function canGo(sc) {
    if (activeRun(V, w)) return false;
    if (w.key === 'render' && sc && !sc.ok && !sc.empty) return false;
    if (real && !wordOk(c)) return false;
    if (w.key === 'publish' && !V.ready.length) return false;      // dry run da hazır render arar: yoksa NotReady ile çöker
    return true;
  }
  var okBtn = h('button', { class: 'btn ' + (real ? 'btn-danger' : 'btn-primary'), type: 'button', disabled: !canGo(specCheck),
    onclick: function () { dispatchWorkflow(w, w.key === 'render' ? FF.checkSpec(c.inputs.spec, LIB) : null); } }, real ? 'Onayla ve paylaş' : (w.key === 'publish' ? 'Denemeyi çalıştır' : 'Onayla ve çalıştır'));
  box.appendChild(h('div', { class: 'btns' }, okBtn, h('button', { class: 'btn', type: 'button', onclick: function () { c.phase = 'idle'; c.word = ''; scheduleRender(); } }, 'Vazgeç')));
  return box;
}

function runningCard(V) {
  var active = V.allRuns.filter(function (it) { var s = FF.runStatus(it.run); return s === 'running' || s === 'queued'; });
  if (!active.length && !S.cancel) return null;
  var rows = active.map(function (it) {
    var r = it.run, cc = S.cancel && S.cancel.runId === r.id ? S.cancel : null, side;
    if (!cc) side = h('button', { class: 'btn btn-xs btn-danger', type: 'button', disabled: !liveOk(), onclick: function () { S.cancel = { runId: r.id, phase: 'confirm' }; scheduleRender(); } }, 'İptal et…');
    else if (cc.phase === 'confirm') side = h('span', { class: 'btns' }, h('span', { text: 'Çalışma iptal edilsin mi?' }), h('button', { class: 'btn btn-xs btn-danger', type: 'button', onclick: function () { cancelRun(r.id); } }, 'Evet, iptal et'), h('button', { class: 'btn btn-xs', type: 'button', onclick: function () { S.cancel = null; scheduleRender(); } }, 'Vazgeç'));
    else if (cc.phase === 'sending') side = h('span', { class: 'muted', text: 'İptal isteği gönderiliyor…' });
    else side = h('span', { class: 'pre-' + cc.error.kind }, h('strong', { text: cc.error.title + '. ' }), cc.error.text, h('button', { class: 'btn btn-xs', type: 'button', onclick: function () { S.cancel = null; scheduleRender(); } }, 'Kapat'));
    return h('li', { class: 'row' }, h('div', { class: 'row-main' }, h('span', { class: 'mono key', text: it.w.key }), runChip(r), h('span', { class: 'muted', text: FF.fmtRel(FF.parseTimeMs(r.created_at), V.now) }), link('Aç', r.html_url, 'lnk lnk-s')), h('div', { class: 'row-side' }, side));
  });
  return card('Şu an çalışanlar', 'Yarıda kesilen render ya da learn güvenlidir (sonraki turda yeniden denenir). publish’i yarıda kesmek release’i “claimed” bırakabilir.', h('ul', { class: 'rows' }, rows));
}

var RECOVERY = [
  ['Render başarısız', 'Akış’ta kırmızı çalışma, hata issue’su', 'Çalışma günlüğünde kırmızı adıma bakın. Düzeltince render’ı, bitince publish’i çalıştırın.', 'actions/workflows/render.yml'],
  ['Yayın saatinde hazır render yok', 'publish: “Yayınlanacak render yok”', 'render’ı çalıştırın; bitince publish’i çalıştırın. Sonraki yayın saati de en yeni hazır render’ı alır.', 'actions/workflows/render.yml'],
  ['Release “claimed” kaldı', 'publish kırmızı', 'Önce Instagram’a bakın. Paylaşıldıysa release adını “published <video_id>” yapın. Paylaşılmadıysa sebebi giderip adını “render <video_id>” yapın.', 'releases'],
  ['Paylaşım sonucu belirsiz', 'Hata metni: PublishUncertain', 'Önce Instagram’a bakın. Paylaşıldıysa release adını “published <id>” yapıp videos.csv’ye satırı ekleyin. Tekrar paylaşmayın.', 'releases'],
  ['Jeton yok ya da bozuk', 'publish: “Geçerli bir Instagram jetonu yok”', 'IG_ACCESS_TOKEN gizli değerini yeni bir jetonla güncelleyin. Hiçbir şey değişmemiştir.', 'settings/secrets/actions'],
  ['Veri dalına yazılamadı', 'Hata metni: “veri dalına yazılamadı”', 'Geçicidir. Çalışmayı yeniden başlatın.', 'actions']
];
function renderControl(V) {
  var root = h('div', { class: 'stack-y' }), live = liveOk(), who = STANDALONE ? 'GitHub erişim anahtarınızla' : 'GitHub bağlayıcınızla';
  root.appendChild(h('section', { class: 'card notice ' + (live ? 'notice-warn' : 'notice-info') },
    h('strong', { text: live ? 'Bu sekmedeki düğmeler gerçek sistemi çalıştırır.' : 'Bu sekme şu an yalnız okunur.' }),
    h('p', { text: live ? 'İşlemler ' + who + ', sizin yetkinizle yapılır. Her işlem önce ne yapacağını gösterir ve onayınızı ister. Gerçek paylaşım ayrıca yazılı onay ister.'
      : (S.mode === 'demo' ? 'Örnek veri kipinde hiçbir şey çalıştırılmaz. Canlı veriye geçmek için üstteki anahtarı kullanın.' : (STANDALONE ? 'Erişim anahtarı girilene kadar işlem yapılamaz.' : 'GitHub bağlayıcısı bağlanana kadar işlem yapılamaz.')) })));
  var running = runningCard(V);
  if (running) root.appendChild(running);
  root.appendChild(h('div', { class: 'wfgrid' }, W.map(function (w) { return actionCard(V, w); })));
  root.appendChild(card('Kurtarma rehberi', 'Sık görülen durumlar ve ne yapılacağı (README.md ile aynı)',
    h('div', { class: 'tablewrap' }, h('table', { class: 'tbl rec' }, h('thead', null, h('tr', null, ['Durum', 'Ne görürsünüz', 'Ne yapılır', ''].map(function (x) { return h('th', { text: x }); }))),
      h('tbody', null, RECOVERY.map(function (r) { return h('tr', null, h('th', { scope: 'row', text: r[0] }), h('td', { class: 'muted', text: r[1] }), h('td', { text: r[2] }), h('td', null, link('GitHub’da aç', GH + '/' + r[3], 'lnk lnk-s'))); }))))));
  root.appendChild(card('Bağlantılar', 'Doğrudan GitHub sayfaları', h('div', { class: 'links big' },
    link('Actions', GH + '/actions'), link('Release’ler', GH + '/releases'), link('Issue’lar', GH + '/issues'), link('veri dalı', GH + '/tree/veri'),
    link('Gizli değerler', GH + '/settings/secrets/actions'), link('Depo', GH))));
  return root;
}
