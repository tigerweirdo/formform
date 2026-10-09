"""Yönetim panelini kaynak dosyalardan birleştirir. İki çıktı üretir:

    dashboard/index.html              Claude'da artifact olarak yayımlanan parça (GitHub bağlayıcısıyla çalışır)
    dashboard/standalone/index.html   Ayrı bir web sitesi olarak barındırılan tam sayfa (erişim anahtarıyla çalışır)
    dashboard/standalone/_headers     Netlify ve Cloudflare Pages için güvenlik başlıkları

    python dashboard/build.py                  ikisini de yazar
    python dashboard/build.py --check          dosyalar güncel değilse 1 ile çıkar
    python dashboard/build.py --wrapped YOL    artifact parçasını yerelde açmak için tam HTML belgesi olarak yazar

Kaynaklar `dashboard/src/` altındadır. Yapı, palet, malzeme gibi adlar `render/formform/library.py` ve
`ops/evolve.py` içindeki tek kaynaktan okunup sayfaya gömülür, böylece sayfadaki listeler eskiyemez
(`ops/tests/test_dashboard.py` bunu da sınar).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ops import evolve  # noqa: E402  (render/ yolunu da ekler)

TITLE = 'FORM/FORM Kontrol Paneli'
APP_PARTS = ('app/core.js', 'app/charts.js', 'app/tabs.js', 'app/control.js', 'app/boot.js')
STANDALONE_PART = 'app/standalone.js'          # boot.js'ten önce gelir: boot() dosyanın sonunda çağrılır
SOURCES = ('style.css', 'body.html', 'logic.js')
STANDALONE_DIR = os.path.join(HERE, 'standalone')

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
         '&family=Instrument+Sans:wght@400;500;600&display=swap">')

# Bağımsız sayfa yalnız kendi betiğini çalıştırabilir, yalnız api.github.com'a bağlanabilir (anahtar başka yere gidemez).
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; connect-src https://api.github.com; img-src data:; base-uri 'none'; form-action 'none'")
HEADERS = (
    '/*\n'
    f"  Content-Security-Policy: {CSP}; frame-ancestors 'none'\n"
    '  Referrer-Policy: no-referrer\n'
    '  X-Content-Type-Options: nosniff\n'
    '  X-Frame-Options: DENY\n'
    '  Cache-Control: no-cache\n'
)
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
           "%3Cellipse cx='16' cy='16' rx='13' ry='6' fill='none' stroke='%231a1916' stroke-width='2' transform='rotate(-28 16 16)'/%3E"
           "%3Cellipse cx='16' cy='16' rx='13' ry='6' fill='none' stroke='%231a1916' stroke-width='2' opacity='.35' transform='rotate(32 16 16)'/%3E%3C/svg%3E")
RESET = ('<style>:root{color-scheme:light;padding-block:env(safe-area-inset-top,0px) env(safe-area-inset-bottom,0px)}'
         'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>')


def library():
    return {
        'structure': list(evolve.STRUCTURES),
        'palette': list(evolve.PALETTES),
        'motion': list(evolve.MOTION_QUALITIES),
        'lighting': list(evolve.LIGHTING_STYLES),
        'format': list(evolve.FORMAT_NAMES),
        'material': list(evolve.MATERIALS),
        'transformation': list(evolve.TRANSFORMATIONS),
        'hook': list(evolve.CAPTION_OPENINGS),
        'sound': list(evolve.SOUND_NAMES),
    }


def formats():
    return [dict(f) for f in evolve.FORMATS]


def _read(name):
    with open(os.path.join(HERE, 'src', name), encoding='utf-8') as f:
        return f.read().rstrip('\n')


def app_source(standalone=False):
    """Sayfa kodunun parçaları tek kapsamda (IIFE) birleşir; yalnız FF, LIB, FORMATS, STANDALONE (ve bağımsız sürümde
    RestTransport) dışarıdan gelir."""
    parts = list(APP_PARTS)
    if standalone:
        parts.insert(parts.index('app/boot.js'), STANDALONE_PART)
    return '(function () {\n\'use strict\';\n' + '\n'.join(_read(n) for n in parts) + '\n})();'


def _script_safe(text):
    # Satır içi script'te "</script" dizisi sayfayı bozar
    return text.replace('</script', '<\\/script')


def _script(standalone):
    lib = _script_safe(json.dumps(library(), ensure_ascii=False))
    fmts = _script_safe(json.dumps(formats(), ensure_ascii=False))
    parts = [
        '/* Aşağıdaki satırlar build.py tarafından library.py ve evolve.py kaynaklarından yazılır. */',
        f'var LIB = {lib};',
        f'var FORMATS = {fmts};',
        f'var STANDALONE = {"true" if standalone else "false"};',
        _script_safe(_read('logic.js')),
    ]
    if standalone:
        parts.append(_script_safe(_read('transport.js')))
    parts.append(_script_safe(app_source(standalone)))
    return '\n'.join(parts)


def build():
    """Artifact parçası: belge iskeletini yayın aracı ekler."""
    css, body = _read('style.css'), _read('body.html')
    return '\n'.join([f'<title>{TITLE}</title>', FONTS, '<style>', css, '</style>', body, '<script>', _script(False), '</script>', ''])


def build_standalone():
    """Ayrı bir sitede barındırılan tam belge."""
    css, body = _read('style.css'), _read('body.html')
    return '\n'.join([
        '<!doctype html>', '<html lang="tr">', '<head>', '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        '<meta name="referrer" content="no-referrer">', '<meta name="robots" content="noindex,nofollow">',
        f'<link rel="icon" href="{FAVICON}">', f'<title>{TITLE}</title>', FONTS, RESET, '<style>', css, '</style>',
        '</head>', '<body>', body, '<script>', _script(True), '</script>', '</body>', '</html>', '',
    ])


def wrapped(fragment):
    """Yerelde açmak için: Artifact aracının yayın sırasında eklediği belge iskeletinin benzeri."""
    return ('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<style>:root{color-scheme:light;padding-block:env(safe-area-inset-top,0px) env(safe-area-inset-bottom,0px)}'
            'body{margin:0;font:14px system-ui,sans-serif}[hidden]{display:none!important}</style></head><body>'
            + fragment + '</body></html>')


def outputs():
    return {
        os.path.join(HERE, 'index.html'): build(),
        os.path.join(STANDALONE_DIR, 'index.html'): build_standalone(),
        os.path.join(STANDALONE_DIR, '_headers'): HEADERS,
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--wrapped' in argv:
        path = argv[argv.index('--wrapped') + 1]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(wrapped(build()))
        print(f'yazıldı: {path}')
        return 0
    stale = []
    for path, text in outputs().items():
        if '--check' in argv:
            try:
                with open(path, encoding='utf-8') as f:
                    current = f.read()
            except OSError:
                current = ''
            if current != text:
                stale.append(os.path.relpath(path, ROOT))
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'yazıldı: {os.path.relpath(path, ROOT)} ({len(text) // 1024} KB)')
    if stale:
        print('güncel değil: ' + ', '.join(stale) + ' — python dashboard/build.py çalıştırın', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
