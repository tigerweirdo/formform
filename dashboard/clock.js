/* Saat. GitHub'ın schedule kuyruğu özel depoda saatlerce gecikiyor; bu Worker beş dakikada bir
 * uyanır, Berlin duvar saatine bakıp turu workflow_dispatch ile hemen başlatır. Dakika harcamaz.
 * GITHUB_DISPATCH_TOKEN yoksa hiçbir şey yapmaz; kaçan turu GitHub'daki nöbetçi (watch.yml) günde iki kez başlatır. (Bu ad Worker sırrıdır.
 * GitHub'da secret adı GITHUB_ ile başlayamaz: panel akışı onu GitHub'daki DISPATCH_TOKEN'dan Worker'a yazar.)
 * Anahtar yalnız api.github.com'a gider, günlüğe yazılmaz.
 *
 * TikTok bağlanma (Login Kit): /auth/tiktok/start ve /auth/tiktok/callback. Yalnız SAHİBİ bağlanabilir: başlatmak
 * için TIKTOK_CONNECT_KEY gerekir, yoksa herkes kendi TikTok hesabını sisteme bağlayıp taslakları kendi gelen
 * kutusuna yönlendirebilirdi. Dönüşte yetkilendirme kodu GitHub'daki tiktok workflow'una iletilir, jetona orada
 * çevrilir (TikTok gizli anahtarı Worker'a hiç girmez). Kod ekrana yazılmaz, günlüğe yazılmaz. */
const WINDOW_MIN = 20;

function githubRepo(env) {
  const owner = (env && env.GITHUB_OWNER) || 'tigerweirdo';
  const repo = (env && env.GITHUB_REPO) || 'formform';
  return owner + '/' + repo;
}

export const JOBS = [
  { key: 'render', file: 'render.yml', times: ['06:30', '18:30'] },
  { key: 'publish', file: 'publish.yml', times: ['07:45', '19:45'] },
  { key: 'tiktok', file: 'tiktok.yml', times: ['07:50', '19:50'] },
  { key: 'token', file: 'token.yml', times: ['05:07'], days: [1, 15] },
  { key: 'report', file: 'report.yml', times: ['09:17'], weekdays: [1] },
  { key: 'learn', file: 'learn.yml', everyHours: 3, minute: 5 }
];

function minutesOf(hhmm) {
  const parts = String(hhmm).split(':');
  return Number(parts[0]) * 60 + Number(parts[1]);
}

export function berlinParts(date) {
  const bag = {};
  new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Berlin', hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', weekday: 'short'
  }).formatToParts(date).forEach(function (p) { bag[p.type] = p.value; });
  const weekdays = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 };
  return {
    day: Number(bag.day), hour: Number(bag.hour), minute: Number(bag.minute),
    weekday: weekdays[bag.weekday] == null ? 0 : weekdays[bag.weekday]
  };
}

function inWindow(nowMin, target) {
  const delta = nowMin - minutesOf(target);
  return delta >= 0 && delta < WINDOW_MIN ? delta : null;
}

export function dueJobs(now) {
  const parts = berlinParts(now);
  const nowMin = parts.hour * 60 + parts.minute;
  const out = [];
  JOBS.forEach(function (job) {
    if (job.everyHours) {
      if (now.getUTCHours() % job.everyHours !== 0) return;
      const delta = now.getUTCMinutes() - job.minute;
      if (delta < 0 || delta >= WINDOW_MIN) return;
      out.push({ key: job.key, file: job.file, slotStart: now.getTime() - delta * 60000 });
      return;
    }
    if (job.days && job.days.indexOf(parts.day) < 0) return;
    if (job.weekdays && job.weekdays.indexOf(parts.weekday) < 0) return;
    let best = null;
    job.times.forEach(function (t) {
      const delta = inWindow(nowMin, t);
      if (delta != null && (best == null || delta < best)) best = delta;
    });
    if (best == null) return;
    out.push({ key: job.key, file: job.file, slotStart: now.getTime() - best * 60000 });
  });
  return out;
}

export function alreadyCovered(runs, slotStart) {
  return (runs || []).some(function (run) {
    const created = Date.parse(run.created_at || run.createdAt || '');
    if (!Number.isFinite(created) || created < slotStart - 2 * 60000) return false;
    const conclusion = run.conclusion || '';
    return conclusion !== 'cancelled' && conclusion !== 'failure' && conclusion !== 'timed_out' && conclusion !== 'startup_failure';
  });
}

async function github(env, method, path, body) {
  const res = await fetch('https://api.github.com' + path, {
    method: method,
    headers: {
      Authorization: 'Bearer ' + env.GITHUB_DISPATCH_TOKEN,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'formform-clock',
      'Content-Type': 'application/json'
    },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return res;
}

export async function tick(env, now, fetchRuns) {
  if (!env || !env.GITHUB_DISPATCH_TOKEN) return { dispatched: [], reason: 'token' };
  const due = dueJobs(now);
  const dispatched = [];
  for (const job of due) {
    let runs = [];
    try {
      runs = await fetchRuns(job.file);
    } catch (e) {
      continue;
    }
    if (alreadyCovered(runs, job.slotStart)) continue;
    const res = await github(env, 'POST', '/repos/' + githubRepo(env) + '/actions/workflows/' + job.file + '/dispatches',
      { ref: 'main', inputs: { clock: 'true' } });
    if (res.status === 204) dispatched.push(job.key);
  }
  return { dispatched: dispatched };
}

async function listRuns(env, file) {
  const res = await github(env, 'GET', '/repos/' + githubRepo(env) + '/actions/workflows/' + file + '/runs?per_page=15');
  if (!res.ok) throw new Error('runs ' + res.status);
  const body = await res.json();
  return body.workflow_runs || [];
}

export const TIKTOK_SCOPES = 'user.info.basic,video.upload,video.list';   // video.publish (Direct Post) bilerek yok
const TIKTOK_AUTH_URL = 'https://www.tiktok.com/v2/auth/authorize/';
const STATE_COOKIE = 'tt_state';
const COOKIE_PATH = '/auth/tiktok/';

function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

export function constantTimeEqual(a, b) {
  const x = String(a == null ? '' : a), y = String(b == null ? '' : b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x.charCodeAt(i) || 0) ^ (y.charCodeAt(i) || 0);
  return diff === 0;
}

function randomState() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
}

function readCookie(header, name) {
  const found = String(header || '').split(';').map(function (p) { return p.trim(); })
    .find(function (p) { return p.indexOf(name + '=') === 0; });
  return found ? found.slice(name.length + 1) : '';
}

function stateCookie(value, maxAge) {
  return STATE_COOKIE + '=' + value + '; Max-Age=' + maxAge + '; Path=' + COOKIE_PATH + '; HttpOnly; Secure; SameSite=Lax';
}

const PAGE_CSS = ':root{color-scheme:light dark;--bg:#fafafa;--fg:#1a1a1a;--mute:#5c5c5c;--line:#e2e2e2}' +
  '@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#ececec;--mute:#a0a0a0;--line:#2e2e2e}}' +
  'body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}' +
  'main{max-width:34rem;margin:0 auto;padding:2.5rem 1rem}h1{font-size:1.4rem;margin:0 0 .5rem}' +
  '.mute{color:var(--mute);font-size:.9rem}input,button{font:inherit;padding:.5rem .7rem;margin:.25rem 0}' +
  'input{width:100%;box-sizing:border-box}a{color:inherit}';

function securePage(status, title, body, headers) {
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escapeHtml(title) + ' · FORM/FORM</title>' +
    '<style>' + PAGE_CSS + '</style></head><body><main>' + body +
    '<p class="mute"><a href="/about.html">About</a> · <a href="/privacy.html">Privacy Policy</a> · ' +
    '<a href="/terms.html">Terms of Service</a></p></main></body></html>';
  return new Response(html, {
    status: status,
    headers: Object.assign({
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
      'Referrer-Policy': 'no-referrer',
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
      'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://www.tiktok.com; " +
        "base-uri 'none'; frame-ancestors 'none'"
    }, headers || {})
  });
}

export function tiktokConfigured(env) {
  return !!(env && env.TIKTOK_CLIENT_KEY && env.TIKTOK_CONNECT_KEY);
}

function keyPage(status, note) {
  return securePage(status, 'Connect TikTok',
    '<h1>Connect TikTok</h1>' +
    '<p>FORM/FORM is a personal tool connected to a single TikTok account, its owner\'s. Connecting is restricted to the owner. ' +
    'The app asks TikTok for these permissions: <code>user.info.basic</code>, <code>video.upload</code> (drafts only, ' +
    'the owner publishes manually) and <code>video.list</code>.</p>' +
    (note ? '<p><strong>' + escapeHtml(note) + '</strong></p>' : '') +
    '<form method="post" action="/auth/tiktok/start"><label for="key">Owner setup key</label>' +
    '<input id="key" name="key" type="password" autocomplete="off" required>' +
    '<button type="submit">Continue to TikTok</button></form>');
}

export async function tiktokStart(request, env) {
  if (!tiktokConfigured(env)) return securePage(404, 'Not available', '<h1>Not available</h1><p>TikTok connection is not set up.</p>');
  if (request.method === 'GET') return keyPage(200);
  if (request.method !== 'POST') return securePage(405, 'Not allowed', '<h1>Not allowed</h1>', { Allow: 'GET, POST' });
  let key = '';
  try { key = String((await request.formData()).get('key') || ''); } catch (e) { key = ''; }
  if (!constantTimeEqual(key, env.TIKTOK_CONNECT_KEY)) return keyPage(401, 'That key is not correct.');
  const origin = new URL(request.url).origin;
  const state = randomState();
  const params = new URLSearchParams({
    client_key: env.TIKTOK_CLIENT_KEY, scope: TIKTOK_SCOPES, response_type: 'code',
    redirect_uri: origin + '/auth/tiktok/callback', state: state,
    // İzin ekranı HER seferinde gösterilir (belgede 1 = her zaman). Açık oturumlu, daha önce yetki vermiş kullanıcıda
    // TikTok'un onu atlamaması gerekir: kullanıcı hangi yetkileri verdiğini her zaman görmeli (inceleme videosu da bunu ister).
    disable_auto_auth: '1'
  });
  return new Response(null, {
    status: 303,
    headers: { Location: TIKTOK_AUTH_URL + '?' + params.toString(), 'Set-Cookie': stateCookie(state, 600),
               'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' }
  });
}

export async function dispatchTiktokConnect(env, code) {
  if (!env || !env.GITHUB_DISPATCH_TOKEN) {
    const e = new Error('no dispatch token');
    e.reason = 'no-token';
    throw e;
  }
  const res = await github(env, 'POST', '/repos/' + githubRepo(env) + '/actions/workflows/tiktok.yml/dispatches',
    { ref: 'main', inputs: { action: 'connect', code: code } });
  if (res.status !== 204) {
    const e = new Error('github ' + res.status);
    e.reason = 'github';
    e.status = res.status;
    throw e;
  }
  return true;
}

export async function tiktokCallback(request, env, dispatch) {
  const url = new URL(request.url);
  const clear = { 'Set-Cookie': stateCookie('', 0) };
  const state = url.searchParams.get('state') || '';
  const cookie = readCookie(request.headers.get('Cookie'), STATE_COOKIE);
  // Yalnız bu tarayıcıda, sahibin anahtarıyla başlayan akış tamamlanabilir. Aksi halde hiçbir şey yapılmaz.
  if (!tiktokConfigured(env) || !cookie || !constantTimeEqual(state, cookie)) {
    return securePage(400, 'Not started here', '<h1>This sign-in was not started here</h1>' +
      '<p>Start again from the connect page.</p>', clear);
  }
  const error = url.searchParams.get('error');
  if (error) {
    return securePage(200, 'Not authorized', '<h1>TikTok did not authorize the app</h1><p>' +
      escapeHtml(url.searchParams.get('error_description') || error) + '</p>', clear);
  }
  const code = url.searchParams.get('code');
  if (!code) return securePage(400, 'No code', '<h1>No authorization code received</h1>', clear);
  let ok = false, failure = null;
  try { ok = await (dispatch || dispatchTiktokConnect)(env, code); } catch (e) { failure = e; }
  if (!ok) {
    // Neden söylenir (anahtar eksik mi, GitHub reddetti mi) ki kurulum hatası saatlerce aranmasın. Kod ve anahtar yazılmaz.
    if (failure && failure.reason === 'no-token') {
      return securePage(503, 'Setup incomplete', '<h1>Authorization received, but setup is incomplete</h1>' +
        '<p>This site has no key to start the connection job. The owner needs to finish setup, then connect again.</p>', clear);
    }
    const detail = failure && failure.status ? '<p>GitHub answered with status ' + Number(failure.status) + '.</p>' : '';
    return securePage(502, 'Could not finish', '<h1>Authorization received, but the connection could not be finished</h1>' +
      '<p>Try again in a minute.</p>' + detail, clear);
  }
  return securePage(200, 'Connected', '<h1>Authorization received</h1>' +
    '<p>FORM/FORM is now completing the connection with TikTok. This takes about a minute and needs no further action. ' +
    'You can close this tab.</p>', clear);
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === '/auth/tiktok/start') return tiktokStart(request, env);
    if (path === '/auth/tiktok/callback') return tiktokCallback(request, env);
    return env.ASSETS.fetch(request);
  },
  async scheduled(event, env, ctx) {
    const now = new Date(event && event.scheduledTime ? event.scheduledTime : Date.now());
    ctx.waitUntil(tick(env, now, function (file) { return listRuns(env, file); }));
  }
};
