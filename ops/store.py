"""Durum deposu: videolar, öğrenilen ağırlıklar ve jeton bilgisi `veri` dalında dosya olarak tutulur.

Google Tablosu'nun yerini alır. İki akış aynı anda yazarsa (ör. yayın ile öğrenme) çakışma çözümü:
`transaction` en güncel veriyi çeker, değişikliği ona uygular, iter. Çakışmada işlemi yeni veri üzerinde
baştan uygular. Bu yüzden `fn` aynı veride tekrar çalıştırılabilir (idempotent) olmalıdır.
"""
import csv
import io
import json
import os
import subprocess
import tempfile
import time

from . import config, platforms
from .platforms import DNA_COLUMNS, VIDEO_COLUMNS  # noqa: F401  (başka modüller buradan alır)
from .errors import StoreError
from .net import scrub

README = """FORM/FORM durum dalı. Bu dalı elle düzenlemeyin, sistem yazar.

- videos.csv      Instagram'da yayınlanan videolar ve ölçümleri (sistemin öğrendiği her şeyin kaynağı)
- dna_weights.csv Instagram'dan öğrenilen ağırlıklar (hangi yapı/palet/hareket iyi çalışıyor)
- youtube/, tiktok/ aynı iki dosya, her platform için ayrı (platformların verisi birbirine karışmaz)
- config.json     jeton yenileme tarihleri (jetonun kendisi değil)
- ig_token.sealed şifreli Instagram jetonu (anahtarı GitHub gizli değeri olan IG_ACCESS_TOKEN)
- tiktok_token.sealed şifreli TikTok jetonları (anahtarı GitHub gizli değeri olan TIKTOK_CLIENT_SECRET)

Kod `main` dalındadır.
"""


def _csv_read(text):
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def _csv_write(rows, columns):
    extras = []
    for row in rows:
        for key in row:
            if key not in columns and key not in extras:
                extras.append(key)
    cols = list(columns) + extras
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator='\n', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({c: ('' if row.get(c) is None else row.get(c)) for c in cols})
    return buf.getvalue()


class Ledger:
    """Bir platformun kaydı: yayınlanan videolar ve o platformdan öğrenilen ağırlıklar."""

    def __init__(self, platform, videos, dna):
        self.platform, self.videos, self.dna = platform, videos, dna


class Data:
    """Bir işlemin çalışma kopyası. fn içinde değiştirilir.

    `videos` ve `dna` Instagram'ın kaydıdır (eskiden beri böyle). Diğer platformlar `ledger(ad)` ile açılır ve
    kendi klasöründe durur. Yalnız okunan bir platform kaydı değişmiş sayılmaz, boş bir kayıt için dosya açılmaz."""

    def __init__(self, directory):
        self._dir = directory
        self.videos = _csv_read(self._text('videos.csv'))
        self.dna = _csv_read(self._text('dna_weights.csv'))
        try:
            self.config = json.loads(self._text('config.json') or '{}')
        except ValueError:
            self.config = {}
        self.sealed_token = self._text('ig_token.sealed').strip()
        self.sealed_tiktok = self._text('tiktok_token.sealed').strip()
        self._ledgers = {platforms.DEFAULT: Ledger(platforms.get(platforms.DEFAULT), self.videos, self.dna)}
        self._before = self._snapshot()

    def ledger(self, name):
        """Platformun kaydı. İlk açılışta diskten okunur, sonra aynı nesne döner."""
        if name not in self._ledgers:
            platform = platforms.get(name)
            self._ledgers[name] = Ledger(platform, _csv_read(self._text(platforms.videos_file(platform))),
                                         _csv_read(self._text(platforms.dna_file(platform))))
            self._before.update(self._ledger_snapshot(self._ledgers[name]))
        return self._ledgers[name]

    def _text(self, name):
        path = os.path.join(self._dir, name)
        if not os.path.exists(path):
            return ''
        with open(path, encoding='utf-8') as f:
            return f.read()

    def _ledger_snapshot(self, ledger):
        platform = ledger.platform
        out = {}
        for name, rows, columns in ((platforms.videos_file(platform), ledger.videos, platform.columns),
                                    (platforms.dna_file(platform), ledger.dna, DNA_COLUMNS)):
            if rows or platform.name == platforms.DEFAULT or os.path.exists(os.path.join(self._dir, name)):
                out[name] = _csv_write(rows, columns)
        return out

    def _snapshot(self):
        out = {
            'config.json': json.dumps(self.config, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            'ig_token.sealed': self.sealed_token + '\n' if self.sealed_token else '',
            'tiktok_token.sealed': self.sealed_tiktok + '\n' if self.sealed_tiktok else '',
        }
        for ledger in self._ledgers.values():
            out.update(self._ledger_snapshot(ledger))
        return out

    def changed(self):
        return self._snapshot() != self._before

    def write(self):
        for name, text in self._snapshot().items():
            path = os.path.join(self._dir, name)
            if text == '' and not os.path.exists(path):
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)


class Store:
    def __init__(self, remote, directory=None, branch=None, attempts=6):
        self.remote = remote
        self.branch = branch or config.DATA_BRANCH
        self.dir = directory or tempfile.mkdtemp(prefix='ff-veri-')
        self.attempts = attempts
        self._ready = False

    @classmethod
    def from_env(cls, directory=None):
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN') or ''
        remote = os.environ.get('FORMFORM_STORE_REMOTE')
        if not remote:
            if not token:
                raise StoreError('GITHUB_TOKEN yok, veri dalına erişilemez')
            from .net import add_secret
            add_secret(token)
            host = config.GITHUB_HOST.replace('https://', '')
            remote = f'https://x-access-token:{token}@{host}/{config.REPO}.git'
        return cls(remote, directory)

    # -- git ----------------------------------------------------------------
    def _git(self, *args, check=True):
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', LC_ALL='C')   # _sync İngilizce hata metni arar
        cmd = ['git', '-c', 'user.name=formform-bot', '-c', 'user.email=formform-bot@users.noreply.github.com',
               '-c', 'commit.gpgsign=false', *args]
        p = subprocess.run(cmd, cwd=self.dir, env=env, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise StoreError(scrub(f'git {args[0]} başarısız: {p.stderr.strip() or p.stdout.strip()}'))
        return p

    def open(self):
        if self._ready:
            return self
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.isdir(os.path.join(self.dir, '.git')):
            self._git('init', '-q')
            self._git('remote', 'add', 'origin', self.remote)
        self._ready = True
        self._sync()
        return self

    def _sync(self):
        """Uzaktaki en güncel durumu alır. Dal yoksa yerelde boş bir başlangıç hazırlar."""
        p = self._git('fetch', '-q', 'origin', self.branch, check=False)
        if p.returncode == 0:
            self._git('checkout', '-q', '-f', '-B', self.branch, 'FETCH_HEAD')
            self._git('clean', '-q', '-fd')
            return True
        err = p.stderr.lower()
        if "couldn't find remote ref" in err or 'not found' in err or 'unknown revision' in err:
            self._git('checkout', '-q', '--orphan', self.branch, check=False)
            self._git('rm', '-rq', '--cached', '.', check=False)
            for name in os.listdir(self.dir):
                if name != '.git':
                    path = os.path.join(self.dir, name)
                    if os.path.isfile(path):
                        os.remove(path)
            with open(os.path.join(self.dir, 'README.md'), 'w', encoding='utf-8') as f:
                f.write(README)
            return False
        raise StoreError(scrub(f'veri dalı alınamadı: {p.stderr.strip()}'))

    # -- okuma / yazma ---------------------------------------------------------
    def read(self):
        """En güncel veriyi (salt okunur kullanım) döndürür."""
        self.open()
        self._sync()
        return Data(self.dir)

    def transaction(self, fn, message):
        """fn(Data) çalıştırır, değişiklik varsa işler ve gönderir. Çakışmada yeni veriyle baştan dener."""
        self.open()
        last = None
        for attempt in range(self.attempts):
            self._sync()
            data = Data(self.dir)
            result = fn(data)
            if not data.changed() and self._has_commit():
                return result
            data.write()
            self._git('add', '-A')
            self._git('commit', '-q', '--allow-empty', '-m', message)
            p = self._git('push', '-q', 'origin', f'HEAD:refs/heads/{self.branch}', check=False)
            if p.returncode == 0:
                return result
            last = scrub(p.stderr.strip())
            time.sleep(min(2 ** attempt, 8) * 0.5)
        raise StoreError(f'veri dalına yazılamadı ({self.attempts} deneme): {last}')

    def _has_commit(self):
        return self._git('rev-parse', '-q', '--verify', 'HEAD', check=False).returncode == 0
