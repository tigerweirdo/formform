"""Hata ve haftalık özet bildirimi: GitHub issue açılır, depo sahibine atanır (GitHub e-posta gönderir).

Eski n8n ALERT workflow'unun karşılığı. Aynı workflow için 6 saatte en çok bir bildirim
(saatlik öğrenme hata verirse posta kutusu dolmasın). Workflow düzelince açık issue kendiliğinden kapanır.
"""
import json
import os
import tempfile
from datetime import datetime, timezone

from . import config, github, net
from .jsnum import parse_time_ms

COOLDOWN_MS = 6 * 60 * 60 * 1000
TITLE = '[FORMFORM] Hata: {workflow}'


def error_file():
    return os.path.join(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir(), 'formform_error.json')


def remember_error(command, message, hint):
    with open(error_file(), 'w', encoding='utf-8') as f:
        json.dump({'command': command, 'message': net.scrub(message), 'hint': hint}, f, ensure_ascii=False)


def _owner():
    return os.environ.get('GITHUB_REPOSITORY_OWNER') or config.REPO.split('/')[0]


def _create(title, body):
    try:
        return github.create_issue(title, body, assignees=[_owner()])
    except net.HttpError:
        return github.create_issue(title, body)       # atanamazsa atamasız aç


def report_failure(workflow, run_url='', now=None):
    now = now or datetime.now(timezone.utc)
    info = {}
    try:
        with open(error_file(), encoding='utf-8') as f:
            info = json.load(f)
    except (OSError, ValueError):
        pass
    message = info.get('message') or 'Hata metni alınamadı. Çalıştırma günlüğünde kırmızı adıma bakın.'
    hint = info.get('hint') or 'Actions sekmesinde başarısız çalıştırmayı açıp kırmızı adıma bakın.'
    when = now.astimezone(__import__('zoneinfo').ZoneInfo(config.TIMEZONE)).strftime('%d.%m.%Y %H:%M')
    title = TITLE.format(workflow=workflow)

    existing = next((i for i in github.list_open_issues() if i.get('title') == title), None)
    if existing and now.timestamp() * 1000 - parse_time_ms(existing.get('updated_at')) < COOLDOWN_MS:
        return 'cooldown'

    body = '\n'.join([
        f'@{_owner()}',
        '',
        f'**Workflow:** {workflow}',
        f'**Hata:** {message}',
        f'**Zaman:** {when} (Berlin)',
        '',
        f'**Ne yapmalı:** {hint}',
        '',
        f'**Çalıştırma:** {run_url or "bağlantı yok"}',
        '',
        'Bu bildirim aynı workflow için 6 saatte en çok bir kez gönderilir. '
        'Workflow düzelince bu kayıt kendiliğinden kapanır.',
    ])
    if existing:
        github.comment_issue(existing['number'], body)
        return 'commented'
    _create(title, body)
    return 'created'


def resolve(workflow):
    """Başarılı çalışmadan sonra açık hata kaydını kapatır."""
    title = TITLE.format(workflow=workflow)
    closed = 0
    for issue in github.list_open_issues():
        if issue.get('title') == title:
            github.comment_issue(issue['number'], 'Düzeldi: son çalıştırma başarılı oldu. Kayıt kapatılıyor.')
            github.close_issue(issue['number'])
            closed += 1
    return closed


def post_report(subject, message, now=None):
    """Haftalık özet: issue olarak açılır (e-posta gider), sonra kapatılır ki liste kalabalıklaşmasın."""
    body = f'@{_owner()}\n\n```text\n{message}\n```\n'
    issue = _create(subject, body)
    try:
        github.close_issue(issue['number'])
    except net.HttpError:
        pass
    return issue


def post_note(subject, intro, text):
    """Kopyalanabilir bir metin içeren bildirim: issue olarak açılıp depo sahibine atanır (e-posta ve GitHub
    uygulaması bildirimi gider), sonra kapatılır. Metin kendi kutusundadır (telefonda uzun basıp kopyalanır)."""
    body = f'@{_owner()}\n\n{intro}\n\n```text\n{text}\n```\n'
    issue = _create(subject, body)
    try:
        github.close_issue(issue['number'])
    except net.HttpError:
        pass
    return issue


def ping():
    """Bildirim denemesi: bir issue açılıp depo sahibine atanır (e-posta gelmeli), sonra kapatılır."""
    body = (f'@{_owner()}\n\nBu bir bildirim denemesidir. Bu e-postayı aldıysanız hata bildirimleri ve haftalık '
            'özet size ulaşıyor demektir. Kayıt kendiliğinden kapatılıyor, bir şey yapmanız gerekmez.')
    issue = _create('[FORMFORM] Bildirim denemesi', body)
    try:
        github.close_issue(issue['number'])
    except net.HttpError:
        pass
    return issue
