# FORM/FORM yönetim paneli

Sistemi tek ekrandan izleyip yönetmek için bir web sayfası. İki sürümü vardır, ikisi de aynı kod ve aynı hesaplarla çalışır:

| Sürüm | Nerede açılır | GitHub'a nasıl bağlanır |
| --- | --- | --- |
| **Artifact** (`index.html`) | Claude içinde, özel bağlantıyla | Claude'daki **GitHub bağlayıcınız** üzerinden. Anahtar girmeniz gerekmez. |
| **Bağımsız** (`standalone/`) | Kendi adresinizde ya da dosya olarak, herhangi bir tarayıcıda | Sizin oluşturduğunuz, tek depoya yetkili, süreli bir **erişim anahtarıyla** (`api.github.com`). |

Genel kurallar: `../README.md` ve bu dosya.

**Panel (canlı):** Kendi Cloudflare Worker adresiniz (`SITE_ORIGIN` / `ops/config.py`). `main` dalında `dashboard/` değişince GitHub Actions `panel.yml` ile Wrangler’a yükler (`CLOUDFLARE_API_TOKEN` gizli değeri gerekir).

## Sekmeler

| Sekme | Ne gösterir |
| --- | --- |
| Genel | Sistem durumu, göstergeler (yayın, skor, son paylaşım, sıradaki yayın, jeton), sıradaki işler, son çalışmalar, skor grafiği |
| Videolar | Süzülebilir ve sıralanabilir tablo. Satıra tıklayınca tarif, ölçüm takvimi, skor ayrıntısı, seçim bilgisi |
| Öğrenme | Eşiklere ilerleme, keşif–öğrenilmiş seçim kıyası, sonraki videoda ne seçileceği (olasılıklar), DNA ağırlıkları, elit havuzu, format ve saat kıyası, skor hesaplayıcı, sabit kurallar |
| Akış | Günün hattı (Berlin saati), workflow kartları, **zamanlayıcı güvenilirliği**, release kuyruğu, çalışma geçmişi |
| Sağlık | Denetimler (son paylaşım, geciken ölçüm, jeton, başarısız çalışma, hata issue’su, takılı release, zamanlayıcı) ve her biri için “ne yapmalı” |
| Kontrol | render, publish, learn, token, report’u elle çalıştırma; çalışanı iptal etme; kurtarma rehberi |

## Güvenlik

- Okuma: `get_file_contents` (yalnız `veri` dalındaki üç dosya), `actions_list`, `list_releases`, `list_issues`, `list_commits`.
- Yazma yalnız `actions_run_trigger` ile ve yalnız `run_workflow` (dal `main`) ile `cancel_workflow_run`. Depoya dosya yazılmaz.
- Her yazma iki adımlıdır. Gerçek paylaşım ayrıca `PAYLAŞ` yazılmasını ister. `dry_run` varsayılan olarak açıktır.
- Sayfaya jeton girilmez. Erişim `manifest.json` içindeki araçlarla sınırlıdır ve ilk açılışta sizden izin ister.
- Depodan ya da GitHub’dan gelen metin yalnız `textContent` ile yazılır (testle zorunlu).

## Bağımsız sürüm: kendi adresinizde açmak

Claude dışında, normal bir internet adresinde (telefon dahil) açmak için. İlk açılışta sayfa adım adım anlatan bir kurulum ekranı gösterir.

**1. Erişim anahtarı (token) oluşturun** (kurulum ekranındaki bağlantıdan): GitHub → Settings → Developer settings → Fine-grained tokens.

| Alan | Değer |
| --- | --- |
| Token name | FORMFORM panel |
| Expiration | 30 gün (ya da 90). Süre dolunca panel “anahtar geçersiz” der, yenisini girersiniz. |
| Repository access | Only select repositories → `formform` (veya kendi kopyanız) |
| Actions | Read and write (yalnız workflow çalıştırma ve iptal için) |
| Contents | Read-only (veri dalı, release’ler, geçmiş) |
| Issues | Read-only (hata kayıtları) |
| Metadata | Read-only (kendiliğinden gelir) |

Anahtar yalnız o tarayıcıda saklanır, yalnız `api.github.com` adresine gider. Sayfanın ve adresinin kendisinde anahtar ya da veri yoktur.

**2. Sayfayı barındırın.** `dashboard/standalone/` klasöründe iki dosya var: `index.html` (sayfa) ve `_headers` (güvenlik başlıkları, bırakmayın).

- **Dosya olarak:** `index.html` dosyasını çift tıklayıp açın. Hesap gerekmez. (Anahtar o dosyayı açtığınız tarayıcıda saklanır.)
- **Netlify:** ücretsiz hesapla app.netlify.com/drop sayfasına klasörü (ya da zip’i) sürükleyin. Verdiği adres sizindir.
- **Cloudflare Pages:** Workers & Pages → Create → Pages → Upload assets. Klasörü yükleyin.
- **GitHub Pages:** özel depolarda genellikle ücretli plan gerekir.

Adresi başkasına vermeyin: sayfa herkese açık adreste güvenle durur ama yalnız sizin anahtarınızla çalışır. Anahtarı yanlış ellere geçmesin diye başka kimseyle paylaşmayın.

**Güvenlik önlemleri:** sayfa bir içerik güvenlik politikasıyla (CSP) yalnız `api.github.com` adresine bağlanabilir, dış betik yükleyemez; anahtar URL’ye, günlüğe ya da ekrana yazılmaz; yalnız bilinen üç veri dosyası okunabilir (`ig_token.sealed` dahil değildir); yalnız bilinen beş workflow `main` dalında çalıştırılabilir. Hepsi testle zorunludur.

**Doğrulanamayan:** tarayıcıdan doğrudan GitHub API’sine erişim (CORS) GitHub’ın belgelerine göre mümkündür ama geliştirme ortamından sınanamadı. Sayfa, tarayıcı isteği engellerse kurulum sırasında bunu açıkça söyler.

## Dosyalar

- `src/logic.js` — saf mantık (DOM yok): skor, DNA ağırlıkları, istatistik, cron, zamanlayıcı uyumu, sağlık denetimleri, bağlayıcı yanıt ayrıştırıcıları, örnek veri. `ops/` ile aynı sonucu verir.
- `src/app/*.js` — arayüz: çekirdek ve veri katmanı, grafikler, sekmeler, kontrol, açılış. `standalone.js` yalnız bağımsız sürümde: anahtar kurulum ekranı.
- `src/transport.js` — yalnız bağımsız sürümde: bağlayıcı çağrılarını GitHub REST API’sine çevirir (izin listeleri, hata kodları, anahtar güvenliği).
- `src/style.css`, `src/body.html` — görünüm.
- `build.py` — hepsini `index.html` (artifact) ve `standalone/index.html` + `standalone/_headers` (bağımsız) dosyalarına birleştirir ve yapı, palet, malzeme adlarını `render/formform/library.py` ile `ops/evolve.py` kaynaklarından gömer.
- `manifest.json` — sayfanın bağlayıcı izinleri (yayımlarken `capabilities` olarak verilir).

## Değiştirmek ve yayımlamak

```
python dashboard/build.py                           # iki sürümü yeniler
python -m unittest ops.tests.test_dashboard -v      # Python ile eşitlik, güvenlik kuralları, güncellik
```

Canlı adres, `main`’e gelen `dashboard/` değişikliğiyle yüklenir. Yükleme anahtarı depoda durmaz; GitHub’daki `CLOUDFLARE_API_TOKEN` gizli değeridir. Claude kopyası ayrıdır: oturumda `dashboard/index.html` dosyasını `manifest.json` izinleriyle yayımlayarak güncellenir.
Yeni bir yapı, palet ya da malzeme eklenince `build.py` çalıştırılmalıdır, yoksa test kırılır.

## Sınırlar

- Artifact sürümü yalnız Claude içinde, bağlayıcı sağlandığında canlı çalışır; dışarıda açılırsa **Örnek veri** kipine geçer. Bağımsız sürüm her yerde çalışır ama anahtar ister.
- GitHub’a her yenilemede on üç kadar okuma çağrısı yapılır (iki dakikada bir).
- Elle çalıştırılan render’ın sonucu (video) panelde izlenmez, yalnız çalışma durumu izlenir.
