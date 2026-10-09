"""Sabitler. Adresler test için ortam değişkeniyle değiştirilebilir."""
import os

REPO = os.environ.get('GITHUB_REPOSITORY') or 'tigerweirdo/formform'

# Instagram Business/Creator account ID (Graph API). Set IG_USER_ID in GitHub Actions / local env.
# Numeric placeholder for tests and forks; set IG_USER_ID in production.
IG_USER_ID = os.environ.get('IG_USER_ID') or '10000000000000000'
IG_VERSION = 'v23.0'

# Video barındırma: Instagram videoyu herkese açık bir adresten çeker
CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME') or 'YOUR_CLOUDINARY_CLOUD_NAME'

TIMEZONE = 'Europe/Berlin'

# Durum (videolar, öğrenilen ağırlıklar, jeton) bu dalda tutulur. Kod `main`de, veri burada.
DATA_BRANCH = 'veri'
MAIN_BRANCH = 'main'          # nöbetçinin başlattığı turlar hep bu daldaki workflow dosyasıyla çalışır

GITHUB_API = os.environ.get('FORMFORM_GITHUB_API') or 'https://api.github.com'
GITHUB_HOST = os.environ.get('FORMFORM_GITHUB_HOST') or 'https://github.com'
IG_API = os.environ.get('FORMFORM_IG_API') or 'https://graph.instagram.com'
CLOUDINARY_API = os.environ.get('FORMFORM_CLOUDINARY_API') or 'https://api.cloudinary.com/v1_1'
YT_API = os.environ.get('FORMFORM_YT_API') or 'https://www.googleapis.com'
TIKTOK_API = os.environ.get('FORMFORM_TIKTOK_API') or 'https://open.tiktokapis.com'
YT_TOKEN_URL = os.environ.get('FORMFORM_YT_TOKEN_URL') or 'https://oauth2.googleapis.com/token'

# Instagram videoyu işlerken beklenen en uzun süre ve yoklama aralığı
REEL_POLL_SECONDS = 20
REEL_MAX_WAIT_SECONDS = 15 * 60

# Instagram dışı platformlar: bundan eski render paylaşılmaz (kaçırılan yuvada günler önceki video çıkmasın)
PLATFORM_MAX_AGE_HOURS = 24

# Herkese açık site (panelin Worker adresi). TikTok'un izin dönüş adresi bununla tam eşleşmek zorundadır
# ve TikTok uygulama ayarında da aynen kayıtlı olmalıdır.
SITE_ORIGIN = os.environ.get('SITE_ORIGIN') or 'https://formform-placeholder.workers.dev'
TIKTOK_REDIRECT_URI = SITE_ORIGIN + '/auth/tiktok/callback'
TIKTOK_SCOPES = 'user.info.basic,video.upload,video.list'       # video.publish (Direct Post) bilerek yok

# TikTok'a tek parça yüklenebilen en büyük video (belgede doğrulanamadı, canlıda denenmeli). Bizimkiler ~7-30 MB.
TIKTOK_MAX_SINGLE_CHUNK = 64 * 1024 * 1024
TIKTOK_POLL_SECONDS = 10
# Yükleme başlatma ya da dosya gönderme "hiçbir şey oluşmadı" kanıtıyla (başlatma hatası, yükleme adresinde 404) yeni
# oturumla bu kadar denenir. Belirsiz hatalar (zaman aşımı, 5xx) ASLA yeniden denenmez: taslak oluşmuş olabilir.
TIKTOK_UPLOAD_ATTEMPTS = 3
TIKTOK_RETRY_WAITS = (5, 20)
TIKTOK_MAX_WAIT_SECONDS = 10 * 60
# Yükleme turu erişim jetonunu (24 saat geçerli) bitmesine bu kadar kala yeniler. Yükleme 12 saatte bir çalışır:
# 13 saat, her turda yenilenmesi demektir. Böylece jeton iki tur arasında hiç ölmez ve yenileme yapmayan öğrenme
# turu (üç saatte bir) onu hep geçerli bulur. Bir yükleme turu kaçsa bile jeton bir sonrakine kadar yaşar.
TIKTOK_UPLOAD_REFRESH_HOURS = 13
# Paylaşılan taslağı arama: hesabın en yeni bu kadar videosuna bakılır, bu kadar günden eski taslak aranmaz.
TIKTOK_SYNC_VIDEOS = 60
TIKTOK_DRAFT_WATCH_DAYS = 14
