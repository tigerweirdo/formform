/* ====================================================================== bağımsız sürüm: erişim anahtarı (token) kurulumu
 * Bu dosya yalnız bağımsız sürümde bulunur. Anahtar yalnız bu tarayıcıda (localStorage) saklanır, yalnız api.github.com'a gönderilir
 * (RestTransport) ve sayfada hiçbir yerde görüntülenmez. Yalnız ince ayarlı (fine-grained) anahtar kabul edilir: tek depoya, süreli. */
var TOKEN_URL = 'https://github.com/settings/personal-access-tokens/new';

function startStandalone() {
  S.token = store.get('token', '');
  S.mcp = RestTransport.create({ fetch: function (u, i) { return window.fetch(u, i); }, getToken: function () { return S.token; }, owner: OWNER, repo: REPO_NAME });
  $('keyBtn').addEventListener('click', function () { S.setup = true; render(true); });
  if (!S.token) { S.setup = true; render(true); return; }
  connect();
}
function stopWatching() {
  (S.unsubs || []).forEach(function (u) { try { u(); } catch (e) { /* zaten durmuş */ } });
  S.unsubs = []; S.src = {}; S.have = {}; S.live = emptyData(); S.ver++;
}
function ensureBillingSource() {
  if (SOURCES.some(function (s) { return s.key === 'billing'; })) return;
  SOURCES.push({
    key: 'billing', tool: 'actions_billing', label: 'Actions dakika kotası', input: {},
    parse: function (r) { return FF.actionsQuota(r.payload); },
    apply: function (d, v) { d.billing = v; }, clear: function (d) { d.billing = null; }
  });
}
function connect() {
  stopWatching();
  S.setup = false; S.mode = 'live'; S.firstCallAt = Date.now();
  ensureBillingSource();
  SOURCES.forEach(watchSource);
  render(true);
}
function forgetToken() {
  stopWatching();
  S.token = ''; store.set('token', '');
  S.setup = true; render(true);
}
function setupError(e) {
  switch (e && e.code) {
    case 'needs_reauth': return 'GitHub bu anahtarı reddetti (geçersiz ya da süresi dolmuş). Anahtarı eksiksiz kopyaladığınızdan emin olun.';
    case 'forbidden': case 'tool_error': return 'Anahtar ' + OWNER + '/' + REPO_NAME + ' deposuna erişemiyor. Oluştururken “Only select repositories” altında bu depoyu seçtiniz mi?';
    case 'network': return 'Tarayıcı api.github.com adresine ulaşamadı. İnternetinizi, reklam engelleyicinizi ya da VPN’inizi kontrol edin. Sürerse tarayıcı konsolunda bir CORS hatası olup olmadığına bakın.';
    case 'rate_limited': return 'GitHub istek sınırına ulaşıldı. Birkaç dakika sonra deneyin.';
    default: return 'Bağlanılamadı: ' + ((e && (e.message || e.code)) || 'bilinmeyen hata');
  }
}

function renderSetup(V) {
  var input = h('input', { id: 'token', type: 'password', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', placeholder: 'github_pat_…', 'aria-label': 'GitHub erişim anahtarı' });
  var msg = h('p', { class: 'note', id: 'tokmsg', role: 'status' });
  var save = h('button', { class: 'btn btn-primary', type: 'button' }, 'Kaydet ve bağlan');
  function say(kind, text) { msg.textContent = text; msg.className = 'note' + (kind === 'crit' ? ' note-warn' : ''); }
  function submit() {
    var tok = String(input.value || '').trim();
    if (!/^github_pat_[A-Za-z0-9_]{20,}$/.test(tok)) { say('crit', 'Bu, ince ayarlı (fine-grained) bir anahtara benzemiyor. Doğru anahtar “github_pat_” ile başlar.'); return; }
    var prev = S.token;
    S.token = tok; save.disabled = true; say('info', 'GitHub’a bağlanılıyor…');
    S.mcp.check().then(function () {
      store.set('token', tok);
      var kept = store.get('token', '') === tok;
      connect();
      if (!kept) S.stickyNote = 'Tarayıcı anahtarı saklayamıyor: sayfayı kapatınca yeniden girmeniz gerekir.';
    }, function (e) { S.token = prev; save.disabled = false; say('crit', setupError(e)); });
  }
  save.addEventListener('click', submit);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  var perms = h('dl', { class: 'perms' },
    kv('Token name', 'FORMFORM panel'), kv('Expiration', '30 gün (ya da 90)'), kv('Repository access', 'Only select repositories → ' + REPO_NAME),
    kv('Actions', 'Read and write'), kv('Contents', 'Read-only'), kv('Issues', 'Read-only'), kv('Metadata', 'Read-only (kendiliğinden gelir)'),
    kv('Account permissions', 'Plan → Read-only (kalan Actions dakikası için)'));
  var steps = h('ol', { class: 'setup-steps' },
    h('li', null, 'GitHub’da şu sayfayı açın: ', link('Yeni anahtar oluştur', TOKEN_URL), '.'),
    h('li', null, 'Şunları doldurun:', perms),
    h('li', { text: 'Sayfanın altındaki “Generate token” düğmesine basın ve çıkan github_pat_… metnini kopyalayın.' }),
    h('li', { text: 'Aşağıya yapıştırıp “Kaydet ve bağlan”a basın.' }));
  var extra = h('div', { class: 'btns' },
    S.token ? h('button', { class: 'btn', type: 'button', onclick: function () { S.setup = false; render(true); } }, 'Vazgeç') : null,
    S.token ? h('button', { class: 'btn btn-danger', type: 'button', onclick: forgetToken }, 'Kayıtlı anahtarı sil') : null,
    h('button', { class: 'btn btn-ghost', type: 'button', onclick: function () { enterDemo('Anahtar girmeden örnek veriyle geziliyor'); } }, 'Anahtarsız, örnek veriyle gez'));
  return h('div', { class: 'stack-y' },
    card('GitHub erişim anahtarı', 'Panelin depodaki verileri okuyabilmesi ve onayınızla workflow çalıştırabilmesi için bir kez anahtar girin.',
      h('div', { class: 'setup' }, steps,
        h('div', { class: 'tokrow' }, input, save), msg, extra,
        h('p', { class: 'note', text: 'Anahtar yalnız bu tarayıcıda saklanır ve yalnız api.github.com adresine gönderilir. Kimseyle paylaşmayın. Bu sayfanın kendisinde anahtar ya da veri bulunmaz; herkese açık bir adreste durması sorun değildir.' }),
        S.stickyNote ? h('p', { class: 'note note-warn', text: S.stickyNote }) : null)),
    card('Bu anahtar ne yapabilir?', 'İzinler en dar hâliyle verilir',
      h('ul', { class: 'effects' },
        h('li', { text: 'Okuma: veri dalındaki videos.csv, dna_weights.csv, config.json; Actions çalışmaları, release’ler, açık issue’lar; hesabın Actions dakika kotası.' }),
        h('li', { text: 'Yazma: yalnız bilinen beş workflow’u main dalında çalıştırmak ve çalışan bir çalışmayı iptal etmek. Panel her işlemden önce sizden onay ister.' }),
        h('li', { text: 'Yapamaz: depoya dosya yazmak, gizli değerleri (secrets) okumak, başka depolara erişmek.' }),
        h('li', { text: 'Süre dolunca ya da anahtarı GitHub’dan silince panel “anahtar geçersiz” der; yenisini girersiniz.' }))));
}
