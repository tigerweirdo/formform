"""GitHub API: release'ler (render çıktıları), issue'lar (hata/özet bildirimi) ve Actions çalışmaları (nöbetçi, bütçe)."""
import os

from . import config, net


def _headers(accept='application/vnd.github+json'):
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or ''
    net.add_secret(token)
    return {'Authorization': f'Bearer {token}', 'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'formform-ops'}


def _url(path):
    return f'{config.GITHUB_API}/repos/{config.REPO}{path}'


def list_releases(per_page=20):
    return net.request('GET', _url('/releases'), headers=_headers(), query={'per_page': per_page}, retries=2)


def rename_release(release_id, name):
    """Yan etkili: yeniden denenmez."""
    return net.request('PATCH', _url(f'/releases/{release_id}'), headers=_headers(), json_body={'name': name})


def download_asset(asset_api_url, dest):
    return net.download(asset_api_url, dest, headers=_headers('application/octet-stream'), retries=2)


def list_open_issues(per_page=50):
    items = net.request('GET', _url('/issues'), headers=_headers(),
                        query={'state': 'open', 'per_page': per_page}, retries=2)
    return [i for i in (items or []) if 'pull_request' not in i]


def create_issue(title, body, assignees=None):
    payload = {'title': title, 'body': body}
    if assignees:
        payload['assignees'] = assignees
    return net.request('POST', _url('/issues'), headers=_headers(), json_body=payload)


def comment_issue(number, body):
    return net.request('POST', _url(f'/issues/{number}/comments'), headers=_headers(), json_body={'body': body})


def close_issue(number):
    return net.request('PATCH', _url(f'/issues/{number}'), headers=_headers(),
                       json_body={'state': 'closed', 'state_reason': 'completed'})


def list_workflow_runs(workflow_file, per_page=20):
    """Bir workflow'un en yeni çalışmaları (yalnız okuma)."""
    res = net.request('GET', _url(f'/actions/workflows/{workflow_file}/runs'), headers=_headers(),
                      query={'per_page': per_page}, retries=2)
    return (res.get('workflow_runs') or []) if isinstance(res, dict) else []


def dispatch_workflow(workflow_file, inputs=None):
    """Bir workflow'u `main` dalında başlatır. Yan etkili: yeniden denenmez (çift tur açmasın)."""
    return net.request('POST', _url(f'/actions/workflows/{workflow_file}/dispatches'), headers=_headers(),
                       json_body={'ref': config.MAIN_BRANCH, 'inputs': dict(inputs or {})})


def runs_created(date_range, max_pages=10):
    """Verilen tarih aralığında (ör. '2026-10-01..2026-10-10', UTC) açılmış bütün çalışmalar (yalnız okuma).

    GitHub süzülmüş listeyi 1000 sonuçta keser: çağıran, ayı bunu aşmayacak dilimlere böler."""
    out = []
    for page in range(1, max_pages + 1):
        res = net.request('GET', _url('/actions/runs'), headers=_headers(),
                          query={'created': date_range, 'per_page': 100, 'page': page}, retries=2)
        runs = (res.get('workflow_runs') or []) if isinstance(res, dict) else []
        out.extend(runs)
        if len(runs) < 100:
            break
    return out
