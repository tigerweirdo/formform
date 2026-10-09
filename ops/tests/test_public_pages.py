"""Herkese açık sayfalar (TikTok başvurusu için): about, privacy, terms.

Sayfalar panelle aynı adreste yayınlanır ve panelin sıkı güvenlik başlığı altında çalışmalıdır. İçlerindeki iddialar
(jeton şifreli, otomatik paylaşım yok, istenen yetkiler) koddan ve belgelerden kopmamalı."""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(ROOT, 'dashboard', 'standalone')
PAGES = ('about.html', 'privacy.html', 'terms.html')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def page(name):
    return read('dashboard', 'standalone', name)


def ops_source():
    out = ''
    for path in glob.glob(os.path.join(ROOT, 'ops', '*.py')):
        with open(path, encoding='utf-8') as f:
            out += f.read()
    return out


class PublicPages(unittest.TestCase):
    def test_every_page_links_to_the_other_two_without_a_menu(self):
        for name in PAGES:
            html = page(name)
            self.assertIn('<html lang="en">', html, name)
            self.assertRegex(html, r'<title>[^<]+ · FORM/FORM</title>', name)
            nav = re.search(r'<nav>(.*?)</nav>', html, re.S).group(1)
            footer = re.search(r'<footer>(.*?)</footer>', html, re.S).group(1)
            for part in (nav, footer):                            # TikTok: bağlantılar menü açmadan görünür olmalı
                for target in PAGES:
                    self.assertIn(f'href="{target}"', part, f'{name}: {target} bağlantısı yok')

    def test_pages_obey_the_strict_security_headers_of_the_panel(self):
        headers = read('dashboard', 'standalone', '_headers')
        self.assertTrue(headers.startswith('/*'), 'başlıklar tüm yolları kapsamalı')
        self.assertIn("default-src 'none'", headers)
        for name in PAGES:
            html = page(name)
            self.assertNotIn('<script', html, name)                  # script-src yalnız satır içi, hiç betik gerekmez
            self.assertNotRegex(html, r'(?i)(src=|rel="stylesheet"|@import|url\()', f'{name}: dış kaynak')
            for href in re.findall(r'href="([^"]+)"', html):
                self.assertTrue(href in PAGES or href.startswith('mailto:') or href == '/auth/tiktok/start',
                                f'{name}: beklenmeyen bağlantı {href}')

    def test_the_contact_address_is_the_same_everywhere(self):
        found = set()
        for name in PAGES:
            found |= set(re.findall(r'mailto:([^"]+)"', page(name)))
        self.assertEqual(len(found), 1, found)
        self.assertRegex(next(iter(found)), r'^[^@\s]+@[^@\s]+\.[a-z]+$')
        self.assertIn(next(iter(found)), page('privacy.html'))

    def test_what_the_pages_promise_is_true_of_the_code(self):
        text = ' '.join(page(n) for n in PAGES)
        code = ops_source()
        # "never posts automatically": TikTok'un Direct Post uç noktası kodda olmamalı (yalnız taslak/Upload)
        self.assertIn('never posts', text)
        self.assertNotIn('publish/video/init', code)
        # "tokens are stored encrypted": şifreli saklama gerçekten var
        self.assertIn('stored encrypted', page('privacy.html'))
        self.assertIn('def seal(', read('ops', 'seal.py'))
        self.assertIn('seal(_key()', read('ops', 'tiktok.py'))              # TikTok jetonları gerçekten şifreli yazılır
        # "no cookies, no analytics": sayfalarda çerez ya da izleyici yok
        self.assertNotRegex(text, r'(?i)(document\.cookie|google-analytics|gtag|fbq\()')

    def test_the_about_page_explains_the_flow_and_links_the_owner_only_connect_page(self):
        html = page('about.html')
        self.assertIn('href="/auth/tiktok/start"', html)
        for phrase in ('How it works', 'Connect TikTok (owner only)', '<strong>draft</strong>', 'publishes it manually'):
            self.assertIn(phrase, html, phrase)
        self.assertIn('Login Kit', html)

    def test_the_tiktok_verification_file_matches_its_name(self):
        """TikTok sahipliği bir dosyayla doğrular: dosya adındaki kod, içindeki metinle aynı olmalı ve yerinde kalmalı."""
        files = glob.glob(os.path.join(DIR, 'tiktok*.txt'))
        if not files:
            self.skipTest('TikTok verification file omitted from public mirror')
        self.assertTrue(files)
        for path in files:
            code = os.path.basename(path)[len('tiktok'):-len('.txt')]
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read().strip(), f'tiktok-developers-site-verification={code}')


if __name__ == '__main__':
    unittest.main()
