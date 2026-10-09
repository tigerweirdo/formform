# FORM/FORM işletim katmanı (`ops/`)

Zamanlama dışındaki her şey: planlama (evrim motoru), yayın, öğrenme, jeton yenileme, haftalık özet, hata bildirimi.
GitHub Actions bunları `python -m ops <komut>` ile çağırır (`.github/workflows/`). Yalnız standart kütüphane kullanır
(render hariç, o `render/` altında). Genel mimari ve kurulum: `../README.md`.

## Komutlar

| Komut | Ne yapar | Workflow |
| --- | --- | --- |
| `plan --out DIR [--spec-env AD]` | Evrim motoru yeni tarif üretir (`DIR/release.json`), `RENDER_ID` verir. Aylık dakika bütçesine göre (bütün workflow’ların harcaması GitHub’dan okunur) format ve çözünürlüğü kısıtlar | `render.yml` |
| `usage --since T0 [--spec F]` | Render çalışmasının dakikasını ve bütün workflow’ların ay içi toplamını aylık bütçeye yazar (hata olsa da) | `render.yml` |
| `publish [--platform P] [--dry-run]` | En yeni hazır render’ı bir platforma paylaşır (`instagram` varsayılan, `youtube` kod olarak hazır ama workflow’a bağlı değil, `tiktok` taslak modunda kurulu: video gelen kutusuna taslak düşer) | `publish.yml` |
| `tiktok-connect [--replace]` | Siteden gelen TikTok yetkilendirme kodunu (ortam değişkeni `TIKTOK_CODE`) jetona çevirir, şifreli saklar | `tiktok.yml` |
| `tiktok-stats [--limit N]` | Bağlı TikTok hesabının kendi videolarının sayılarını okur (yalnız okuma) | `tiktok.yml` |
| `tiktok-sync [--refresh] [--dry-run]` | Paylaşılan TikTok taslaklarını bulur (`drafted` → `published`), 24/72 saatlik sayıları okur, skorlar. `--refresh` yoksa jetonu yenilemez, jeton yoksa ya da dolmuşsa hata vermeden atlar | `learn.yml` (üç saatte bir), `tiktok.yml` (elle, `--refresh`) |
| `learn` | 24/72 saatlik ölçümler, performans skoru, DNA ağırlıkları | `learn.yml` |
| `watch [--after-render]` | Nöbetçi: saatin kaçırdığı render, yayın, TikTok ve öğrenme turlarını başlatır, saat hiç başlatmadıysa hata verir. `--after-render`: render turunun sonunda yalnız kaçmış yayın ve TikTok’u başlatır | `watch.yml` (günde iki kez), `render.yml` (nöbetçi turunda) |
| `token` | Instagram jetonunu yeniler, şifreleyip saklar | `token.yml` |
| `report` | Haftalık özeti issue olarak gönderir | `report.yml` |
| `ping` | Bildirim denemesi | `report.yml` (elle) |
| `alert --workflow AD` / `resolve --workflow AD` | Hata issue’su aç / düzelince kapat | her workflow’un son adımı |

Ortam: `GITHUB_TOKEN` (Actions verir), `IG_ACCESS_TOKEN`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`.
İsteğe bağlı: `CLOUDINARY_CLOUD_NAME`, `IG_USER_ID`.
YouTube yayını için: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` (isteğe bağlı `YT_PRIVACY`).
TikTok için: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` (`tiktok.yml` ve kayıtlı jetonu açmak için `learn.yml`), `TIKTOK_CONNECT_KEY` (yalnız Worker, `panel.yml` yazar).

## Modüller

| Dosya | Görev |
| --- | --- |
| `evolve.py` | Evrim motoru: %30 rastgele, %35 elit melezleme, %35 ağırlıklı seçim |
| `scoring.py` | Hangi video ölçülecek, ölçüm ayrıştırma, performans skoru, DNA ağırlıkları |
| `learn.py`, `publish.py`, `report.py` | Akışların orkestrasyonu |
| `schedule.py`, `watch.py` | Takvim (her işin Berlin saati) ve nöbetçi: render, yayın, TikTok ve öğrenmenin GitHub’da kendi zamanlayıcısı yoktur, saat başlatır, kaçanı nöbetçi başlatır |
| `tiktok_sync.py` | TikTok öğrenmesi: paylaşılan taslağı hesabın videolarıyla eşleştirir, ölçer, skorlar (yalnız okuma) |
| `platforms.py`, `ledger.py` | Platform tanımları (her platformun kaydı ayrı) ve Instagram dışı platformların atomik üstlenme/tamamlama durumu |
| `store.py` | Durum deposu: `veri` dalında CSV/JSON, çakışmada yeni veriyle baştan uygular |
| `tokens.py`, `seal.py` | Jeton yenileme ve şifreli saklama (anahtar: ilk jeton) |
| `github.py`, `instagram.py`, `youtube.py`, `tiktok.py`, `cloudinary.py`, `net.py` | Dış servisler. Yan etkili çağrılar asla yeniden denenmez |
| `alerts.py`, `errors.py` | Hata bildirimi: issue + “ne yapmalı” ipucu |
| `budget.py` | Ücretsiz dakika bütçesi: bütün workflow’ların ay içi harcamasını sayar, diğer işlerin payını ayırır, kalanı render’lara böler: izinli formatları ve tam (1080p) ya da ekonomik (720p, 8 sn) seviyeyi seçer |
| `caption.py` | Caption’dan öğrenilen parçalar: açılış cümlesi ve hashtag’ler |
| `jsnum.py` | Eski n8n koduyla birebir aynı sayı davranışı (yuvarlama, `Number()`, `toFixed`, düz soldan sağa toplama) |

## Test

```
python -m unittest discover -s ops/tests -t .          # depo kökünden
python -m unittest discover -s render/tests -t render
```

- `fakes.py` yerel sahte GitHub, Instagram ve Cloudinary sunucusudur: akışlar gerçek ağ olmadan uçtan uca sınanır, çağrı sırası ve sayısı doğrulanır.
- `test_docs.py` belgelerdeki her iddiayı gerçek dosyalarla karşılaştırır.
