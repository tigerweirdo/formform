# FORM/FORM render motoru

Soyut morph videolarını **ücretsiz** üretir. API anahtarı, kredi ya da harici servis yoktur. `.github/workflows/render.yml` bu motoru GitHub Actions’ta çalıştırır (evrim motoru tarifi üretir, motor çizer). Aynı motor yerelde de çalışır.

Genel mimari ve kurulum için `../README.md`.

## Kullanım

```
pip install -r requirements.txt          # ayrıca ffmpeg (libx264) gerekir
python render.py --spec ornek-spec.json --out video.mp4
python preview.py --structure "spiral form" --palette violet-pink --out onizleme.png
python check.py video.mp4                # Reels uygunluk doğrulaması
python -m unittest discover -s tests -t .     # render/ dizininden
```

## Tarif (spec)

| Alan | Açıklama |
| --- | --- |
| `video_id` | Zorunlu. **Tohum = crc32(video_id)**. Aynı kimlik, aynı video. |
| `structure`, `palette`, `motion_quality`, `lighting` | Zorunlu. `formform/library.py` içindeki anahtarlardan biri. |
| `materials` | Aşama başına bir malzeme (dönüşüm sayısı + 1). Eksikse verilenler döngüyle tekrarlanır. |
| `transformations` | 2–12 dönüşüm, sırayla uygulanır. Aşama sayısı bununla belirlenir. Fazlası kırpılır. |
| `loop` | `seamless` (varsayılan): son aşamadan başa yumuşak dönüş, son kare ilk kareyle aynı, video kusursuz döner. `forward`: tek yönlü morph, döngüyü dışarıda (Cloudinary boomerang) yap. |
| `seed` | İsteğe bağlı. `video_id` tohumunu geçersiz kılar. |
| `duration`, `fps`, `width`, `height` | Varsayılan 6 sn (en çok 32), 30, 720, 1280. Evrim motoru 1080×1920 verir. |
| `sound` | İsteğe bağlı. `library.SOUNDS` içindeki stillerden biri: videoya o stilde ambient ses eklenir (AAC stereo). Yoksa video sessiz kalır. |
| `start` | `peak` (varsayılan): kusursuz döngü en dolu ve parlak kareden başlar. `base`: döngü başlangıç formundan başlar. `forward` kipinde etkisizdir. |

Tarifte motorun bilmediği ek alanlar (ör. `caption`, `metadata`) yok sayılır. İşletim katmanı (`ops/`) bunları release gövdesinde taşır.

Bilinmeyen bir isim render’ı durdurmaz. Kararlı bir yedek seçilir ve çıktıda `UYARI` yazılır. Testler evrim motorunun ürettiği hiçbir tarifte bunun olmamasını zorunlu kılar.

## Nasıl çalışır

- **Geometri** (`formform/geometry.py`): bir form, 29 sürekli düğmeyle (`KNOBS`) tarif edilen şerit yapraklarıdır (yarıçap, genişlik, yaprak sayısı, burulma, dönüş sayısı, katlanma, sıvılaşma, kristalleşme, parçalanma…). Yüzey P(u, v) nokta bulutu olarak örneklenir, normaller ve alan elemanları sonlu farklardan çıkar.
- **Zaman çizelgesi** (`formform/timeline.py`): aşama 1 yapının başlangıç vektörüdür, sonraki her aşama bir dönüşümün uygulanmasıdır. Her aşamada düğmeler tabana doğru %18 gevşer ve tabandan toplam sapma `CAP` (2,0) ile sınırlanır: uzun zincirler uç değerlere kaçmaz, form kimliğini korur. Aşamalar arası geçiş düğmelerin interpolasyonudur. Hareket niteliği aşamalarda beklemeyi, hızlanmayı ve kısa hızlı değişim anlarını (~9 sn’de bir) belirler. Video başına tohumdan küçük rastgelelik gelir (oranlar, eğim, ışık yönü, fazlar).
- **Kusursuz loop**: zamana bağlı her şey (harmonik fazlar, sıvılaşma gürültüsü, duman, form salınımı) tam sayı çevrimle yazılır, yani τ = 1 anındaki form τ = 0 ile aynıdır. `seamless` kipinde son aşama başa döner, kareler τ = i/n ile üretilir (τ = 1 kare tekrar etmez), analiz yumuşatması sarmaldır, dikiş görünmez.
- **Rasterleştirme** (`formform/raster.py`): alan ağırlıklı bilineer sıçratma, toplamsal birleştirme. Yarı saydam katmanlar parlar, arka plan saf siyah kalır. `opaque` malzemelerde ön yüzey ayrıca derinlik sıralı çizilir. Palet, ışık ve malzeme gölgelemeyi belirler. Bloom ve renk korumalı ton eşleme.
- **Motor** (`formform/engine.py`): iki geçiş. Önce yarı çözünürlükte her 3 karede bir ölçek, merkez ve pozlama çıkarılır, zamanda yumuşatılır (form kadrajda sabit kütlede ve ortada kalır, titreme olmaz). Sonra kareler çekirdeklere dağıtılıp çizilir ve ffmpeg’e sırayla yazılır. Her kare bağımsızdır, aynı tarif bayt bayt aynı kareyi üretir. Karede NaN çıkarsa temizlenir ve çıktıdaki `nan_frames` sayısında raporlanır.

- **Kompozisyon** (`formform/raster.py`): form dikeyde `STRETCH_Y` kadar uzatılır ve `CENTER_Y` ile hafif yukarı alınır, 9:16 kare dolar ve Instagram arayüzünün kapattığı alt kenardan uzak durur. Parlama yarıçapları çözünürlükle orantılıdır (`Renderer.res`): 1080p’de de 720p’deki görünüm korunur.
- **Açılış anı** (`engine.py`): analiz geçişi her kare için küçük bir parlaklık histogramı saklar (`raster.luma_hist`), doluluk (`hist_interest`) kazanç yumuşatıldıktan sonra gerçek kazançla hesaplanır. Kusursuz döngü en dolu kareden başlatılır, ilk kare kapak olur. Ses de aynı noktadan başlar.
- **Ses** (`formform/audio.py`): prosedürel ambient, telifsiz. Her salınım videoya tam sayı çevrim sığar, gürültü ve yankı FFT ile yapılır: ses de dikişsiz döner. Ses görüntünün kendisinden üretilir: zaman çizelgesinden ve bitmiş karelerin ölçülerinden (aşağıda). Yankı iki kanalda ortaktır (mono çalmada kaybolmaz). Çıkış -14 LUFS, gerçek tepe -1 dBTP altında.
- **Kalite kapısı** (`engine.quality_report`): kapkara, boş ya da donuk çıktıda render hata verir. `check.py` ses akışını, süresini ve seviyesini denetler.

## Ses görüntüyü nasıl izler

Render bittiğinde her karenin ölçüsü alınır (`audio.frame_features`), ses bu eğrilerle sentezlenir. Kaynak tektir: ekranda ne görünüyorsa ses onu izler, ikisi de aynı zaman çizelgesinden çıkar.

| Görüntü | Nereden | Ses |
| --- | --- | --- |
| Hareket (ardışık kareler arası değişim) | kareler | Genel seviye: form durulunca ses çekilir, hızlanınca kabarır. Nefes gürültüsü ve üst sesler de açılır. |
| Parlama (beyaza kaçan alan) | kareler | Tiz cam tınısı ve parlak kısmi sesler parlamayla açılıp kapanır. |
| Işığın sağ-sol dengesi | kareler | Stereo denge: ışık hangi yandaysa ses o yana yaslanır. |
| Doluluk (aydınlık alan) | kareler | Gövde: alt kısmi sesler ve alt bas. |
| Her geçişin en hızlı anı | plan + kareler | Cam/çan notası ve alt kabarma, tam o karede. |
| Karmaşıklık (tabandan sapma) | plan | Üst kısmi sesler açılır (tını). |
| Harmonik lobların akışı (`rig.cycles`, `rig.phases`) | plan | Kısmi seslerin tremolosu aynı çevrim ve fazla. |
| Malzeme (tel, duman) | plan | Tel: gövde incelir. Duman: nefes gürültüsü artar. |

Görüntüye bağlı seviyeler yankıdan sonra uygulanır: yankı kuyruğu sesi görüntünün gerisine düşürmez. Sesin kendi yavaş dalgalanması (detune vuruşları) düzlenir, seviyeyi görüntü yönetir.

Ayar sayıları `formform/audio.py` başındadır:

| Sayı | Ne yapar |
| --- | --- |
| `FOLLOW_MOTION` | Seviyenin hareketi izleme derinliği (0 = sabit seviye, 0,55 ≈ 7 dB). İlk oynanacak sayı budur. |
| `GLINT_LEVEL`, `GLINT_FOLLOW` | Cam tınısının seviyesi ve parlak seslerin parlamayı izleme derinliği. |
| `PAN_DEPTH` | Stereo dengenin ışığı izleme derinliği. |
| `BELL_LEVEL`, `SWELL_LEVEL`, `SWELL_MIN` | Değişim anındaki nota ve alt kabarmanın seviyesi, olay üreten en zayıf hareket. |
| `LEVEL_HOLD` | Sesin kendi dalgalanmasının düzlenme oranı (düşürülürse eski, kendi kendine dalgalanan karakter geri gelir). |
| `TARGET_LUFS`, `TRUE_PEAK_DB` | Çıkış ses yüksekliği ve tepe sınırı. |

Stil başına `library.SOUNDS` içinde isteğe bağlı `glint` (cam tınısı çarpanı, varsayılan 1) verilebilir.

## Yeni yapı, malzeme, dönüşüm ya da ses stili eklemek

1. `formform/library.py` içine ekle (ses stilleri `SOUNDS`). Evrim motoru (`../ops/evolve.py`) listeleri oradan okuduğu için başka yerde değişiklik gerekmez.
2. Testleri çalıştır. Malzeme ve dönüşüm adları `, ` içeremez, her ad render’da uyarısız çözülmelidir.

## Başarım

8 sn, 240 kare, 4 işçi (ölçüldü): 720p yaklaşık 84 sn, **1080p yaklaşık 200 sn** (2,4 kat), sesli 8 sn 1080p dosya yaklaşık 7 MB. GitHub’ın 2 çekirdekli runner’ında 8 sn/720p çalışması kurulumla 4,5 dk sürdü (yerelden 2,7 kat yavaş), buradan 1080p için tahmin: 8 sn ≈ 10, 15 sn ≈ 18, 30 sn ≈ 29 dk. Aylık dakika bütçesi `../ops/budget.py` ile korunur. Eski ölçüm: 6 sn, 720×1280, 180 kare: 4 işçide yaklaşık 100 sn, 2 işçide yaklaşık 3,5 dk. En ağır durum (30 sn, 24 fps, 10 dönüşüm, 720 kare): 4 işçide 598 sn (0,83 sn/kare). Süre kareyle doğrusal artar: 30 sn × 24 fps = 720 kare. Süreç başına en çok ~400 MB bellek. GitHub’ın 2 çekirdekli runner’ında gerçek süre henüz görülmedi. Nokta sayısı kare başına en çok 700 bin ile sınırlıdır (`geometry.MAX_POINTS`).
