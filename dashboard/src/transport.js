/* GitHub REST taşıma katmanı (bağımsız sürüm).
 *
 * Artifact'taki "github" bağlayıcısının yerine geçer: sayfa aynı araç adlarıyla ve aynı girdilerle çağırır, bu katman isteği
 * GitHub REST API'sine çevirir ve yanıtı sayfanın zaten okuduğu biçime sokar. Böylece arayüz ve hesaplar iki sürümde de aynıdır.
 * DOM yok; Node'da sahte bir fetch ile sınanır (ops/tests/test_dashboard.py).
 *
 * Güvenlik:
 *  - Anahtar (token) yalnız Authorization başlığına yazılır ve yalnız BASE adresine gider. URL'ye, günlüğe ya da hata metnine girmez.
 *  - Yalnız izin verilen dosyalar okunur, yalnız bilinen beş workflow `main` dalında çalıştırılır, yalnız sayısal kimlikli çalışma iptal edilir.
 *  - Özel başlık ve `cache` seçeneği kullanılmaz: ikisi de tarayıcıda ek CORS ön kontrolü doğurabilir. Tazelik için sorguya zaman damgası eklenir. */
var RestTransport = (function () {
  'use strict';

  var BASE = 'https://api.github.com';
  var ALLOWED_FILES = ['videos.csv', 'dna_weights.csv', 'config.json'];
  var ALLOWED_WORKFLOWS = ['render.yml', 'publish.yml', 'learn.yml', 'token.yml', 'report.yml'];
  var MIN_INTERVAL = 30000;

  function fail(code, message, extra) {
    var e = { code: code, message: String(message) };
    if (extra) for (var k in extra) e[k] = extra[k];
    return e;
  }
  function qs(obj) {
    return Object.keys(obj).filter(function (k) { return obj[k] != null && obj[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(obj[k]); }).join('&');
  }

  function create(opts) {
    var fetchFn = opts.fetch, getToken = opts.getToken, owner = opts.owner, repo = opts.repo, base = opts.base || BASE;
    var minInterval = opts.minIntervalMs == null ? MIN_INTERVAL : opts.minIntervalMs;
    var watchers = [];

    function repoPath(p) { return '/repos/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + p; }
    function scrub(text) {                          // hata metnine anahtar sızmasın
      var t = getToken && getToken(), s = String(text == null ? '' : text);
      return t ? s.split(t).join('***') : s;
    }

    async function http(method, path, o) {
      o = o || {};
      var token = getToken();
      if (!token) throw fail('token_missing', 'GitHub erişim anahtarı girilmemiş');
      var query = Object.assign({}, o.query || {});
      if (method === 'GET' && !o.nostamp) query._ = Date.now();   // önbelleği başlık eklemeden aş
      var url = base + path + (Object.keys(query).length ? '?' + qs(query) : '');
      var headers = { Authorization: 'Bearer ' + token, Accept: o.raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json' };
      var init = { method: method, headers: headers, credentials: 'omit', referrerPolicy: 'no-referrer' };
      if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(o.body); }
      var res;
      try { res = await fetchFn(url, init); }
      catch (e) { throw fail('network', 'GitHub’a ulaşılamadı (ağ ya da tarayıcı engeli)', { retryable: true }); }
      if (res.ok) {
        if (res.status === 204) return null;
        return o.raw ? res.text() : res.json();
      }
      var message = '';
      try { var body = JSON.parse(await res.text()); message = body && body.message ? body.message : ''; } catch (e) { /* gövde okunamadı */ }
      message = scrub(message).slice(0, 300);
      var remaining = res.headers && res.headers.get ? res.headers.get('x-ratelimit-remaining') : null;
      if (res.status === 401) throw fail('needs_reauth', message || 'Anahtar geçersiz ya da süresi dolmuş');
      if (res.status === 429 || (res.status === 403 && (remaining === '0' || /rate limit/i.test(message)))) throw fail('rate_limited', message || 'İstek sınırı', { retryable: true, retryAfterMs: 60000 });
      if (res.status === 403) throw fail('forbidden', message || 'Anahtarın bu işlem için yetkisi yok');
      if (res.status >= 500) throw fail('server_unavailable', message || 'GitHub hatası (' + res.status + ')', { retryable: true });
      throw fail('tool_error', (message || 'İstek reddedildi') + ' (' + res.status + ')');
    }

    function json(obj) { return { content: [{ type: 'text', text: JSON.stringify(obj) }], payload: obj }; }
    function bad(msg) { return fail('bad_request', msg); }

    // Sayfanın kullandığı araçlar. Girdi biçimi github MCP araçlarıyla aynıdır.
    var TOOLS = {
      get_file_contents: async function (i) {
        var p = String(i.path || '');
        if (ALLOWED_FILES.indexOf(p) < 0) throw bad('Bu dosya okunamaz: ' + scrub(p));
        var ref = String(i.ref || '').replace(/^refs\/heads\//, '');
        var text = await http('GET', repoPath('/contents/' + encodeURIComponent(p)), { query: { ref: ref }, raw: true });
        return { content: [{ type: 'resource', resource: { uri: 'repo://' + owner + '/' + repo + '/' + p, text: text } }], payload: 'ok' };
      },
      actions_list: async function (i) {
        if (i.method === 'list_workflows') return json(await http('GET', repoPath('/actions/workflows'), { query: { per_page: 100 } }));
        if (i.method === 'list_workflow_runs') {
          if (ALLOWED_WORKFLOWS.indexOf(String(i.resource_id)) < 0) throw bad('Bilinmeyen workflow');
          return json(await http('GET', repoPath('/actions/workflows/' + i.resource_id + '/runs'), { query: { per_page: i.perPage || 30 } }));
        }
        throw bad('Desteklenmeyen liste yöntemi');
      },
      list_releases: async function (i) { return json(await http('GET', repoPath('/releases'), { query: { per_page: i.perPage || 30 } })); },
      list_issues: async function (i) {
        var items = await http('GET', repoPath('/issues'), { query: { state: String(i.state || 'OPEN').toLowerCase(), per_page: i.perPage || 30 } });
        var issues = (items || []).filter(function (x) { return !x.pull_request; });
        return json({ issues: issues, totalCount: issues.length });
      },
      list_commits: async function (i) { return json(await http('GET', repoPath('/commits'), { query: { sha: i.sha, per_page: i.perPage || 5 } })); },
      actions_billing: async function () {
        // Eski /billing/actions özeti klasik anahtar ister; ince ayarlı anahtar Plan izniyle
        // /billing/usage okur. İzin yoksa diğer kaynaklar bozulmasın.
        var root = '/users/' + encodeURIComponent(owner) + '/settings/billing/';
        function denied(e) {
          return e && (e.code === 'forbidden' || e.code === 'needs_reauth' || (e.code === 'tool_error' && /\((401|403|404)\)/.test(String(e.message || ''))));
        }
        try {
          var classic = await http('GET', root + 'actions', { nostamp: true });
          if (classic && isFinite(+classic.included_minutes) && +classic.included_minutes >= 0) return json(classic);
        } catch (e) { /* kullanım raporuna düş */ }
        try {
          var parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Europe/Berlin', year: 'numeric', month: 'numeric' }).formatToParts(new Date());
          var year, month;
          parts.forEach(function (p) { if (p.type === 'year') year = p.value; if (p.type === 'month') month = p.value; });
          var usage = await http('GET', root + 'usage', { nostamp: true, query: { year: year, month: month } });
          return json(Object.assign({ source: 'usage' }, usage || {}));
        } catch (e) {
          if (denied(e)) return json({ unavailable: true, code: 'forbidden' });
          throw e;
        }
      },
      actions_run_trigger: async function (i) {
        if (i.method === 'run_workflow') {
          if (ALLOWED_WORKFLOWS.indexOf(String(i.workflow_id)) < 0) throw bad('Bu workflow çalıştırılamaz');
          if (i.ref !== 'main') throw bad('Workflow yalnız main dalında çalıştırılır');
          var body = { ref: 'main' };
          if (i.inputs) body.inputs = i.inputs;
          await http('POST', repoPath('/actions/workflows/' + i.workflow_id + '/dispatches'), { body: body });
          return { content: [], payload: null };
        }
        if (i.method === 'cancel_workflow_run') {
          if (typeof i.run_id !== 'number' || !isFinite(i.run_id) || Math.floor(i.run_id) !== i.run_id || i.run_id <= 0) throw bad('Geçersiz çalışma kimliği');
          await http('POST', repoPath('/actions/runs/' + i.run_id + '/cancel'));
          return { content: [], payload: null };
        }
        throw bad('Desteklenmeyen yöntem');
      }
    };

    function call(server, tool, input) {
      if (server !== 'github') return Promise.reject(bad('Bilinmeyen sunucu'));
      var fn = TOOLS[tool];
      if (!fn) return Promise.reject(bad('Bilinmeyen araç: ' + tool));
      return fn(input || {});
    }

    function runWatcher(w) {
      if (w.stopped || w.busy) return;
      w.busy = true;
      call(w.server, w.tool, w.input).then(
        function (result) { if (!w.stopped) w.handler({ type: 'data', result: result }); },
        function (error) { if (!w.stopped) w.handler({ type: 'error', error: error }); }
      ).then(function () { w.busy = false; schedule(w); });
    }
    function schedule(w) {
      if (w.stopped || !w.interval) return;
      clearTimeout(w.timer);
      w.timer = setTimeout(function () {
        if (typeof document !== 'undefined' && document.hidden) schedule(w); else runWatcher(w);   // gizli sekmede yoklama yapılmaz
      }, w.interval);
    }

    return {
      callTool: function (server, tool, input) {
        return call(server, tool, input).then(function (r) { return r; });
      },
      watchTool: function (server, tool, input, handler, options) {
        var w = { server: server, tool: tool, input: input, handler: handler, stopped: false, busy: false, timer: null,
                  interval: options && options.refetchInterval ? Math.max(minInterval, options.refetchInterval) : 0 };
        watchers.push(w);
        setTimeout(function () { runWatcher(w); }, 0);
        return function () { w.stopped = true; clearTimeout(w.timer); watchers = watchers.filter(function (x) { return x !== w; }); };
      },
      invalidate: function (server, tool) {
        watchers.filter(function (w) { return !tool || w.tool === tool; }).forEach(function (w) { runWatcher(w); });
        return Promise.resolve();
      },
      // Kurulumda anahtarı sınamak için: depoya erişilebiliyor mu?
      check: async function () {
        var r = await http('GET', repoPath(''));
        return { name: r && r.full_name ? r.full_name : owner + '/' + repo };
      }
    };
  }

  return { create: create, BASE: BASE, ALLOWED_FILES: ALLOWED_FILES, ALLOWED_WORKFLOWS: ALLOWED_WORKFLOWS };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = RestTransport;
