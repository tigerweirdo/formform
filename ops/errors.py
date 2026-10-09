"""Hata türleri. Her birinin `hint` alanı, e-posta/issue bildiriminde "ne yapmalı" olarak gösterilir."""


class OpsError(Exception):
    hint = "Actions sekmesinde başarısız çalıştırmayı açıp kırmızı adıma bakın."

    def __init__(self, message, hint=None):
        super().__init__(message)
        if hint:
            self.hint = hint


class NotReady(OpsError):
    hint = ("Yayınlanacak render yok. Actions → 'FORMFORM render' çalışmasına bakın (kırmızı mı, hâlâ çalışıyor mu). "
            "Düzelince 'FORMFORM publish'i elle çalıştırın.")


class TokenMissing(OpsError):
    hint = ("Instagram jetonu bulunamadı ya da bozuk. Depo → Settings → Secrets and variables → Actions → "
            "IG_ACCESS_TOKEN değerini kontrol edin (yalnız IGAA ile başlayan metin, başında 'Bearer' olmadan).")


class TokenRefreshFailed(OpsError):
    hint = ("Instagram jetonu yenilenemedi. Jetonun süresi dolmuşsa Meta for Developers'tan yeni jeton alıp "
            "IG_ACCESS_TOKEN gizli değerini değiştirin. Eski jeton hâlâ geçerliyse sonraki denemede yeniden denenir.")


class ReelFailed(OpsError):
    hint = ("Instagram videoyu işleyemedi. Release 'claimed' kaldı ve otomatik denenmez. Sebebi giderince Releases "
            "sayfasında release adını 'render <video_id>' yapıp 'FORMFORM publish'i yeniden çalıştırın.")


class PublishUncertain(OpsError):
    hint = ("Instagram paylaşımı sonucu belirsiz. Önce Instagram'da videonun paylaşılıp paylaşılmadığına bakın, sonra "
            "Releases'te adı buna göre düzeltin ('published <id>' ya da 'render <id>').")


class StoreError(OpsError):
    hint = "Veri dalına ('veri') yazılamadı. Birkaç dakika sonra çalıştırmayı yeniden deneyin."


class CredentialsMissing(OpsError):
    hint = "Kimlik bilgisi eksik. Depo → Settings → Secrets and variables → Actions sayfasındaki gizli değerleri kontrol edin."


class YoutubeAuthFailed(OpsError):
    hint = ("Google erişim jetonu alınamadı. Çoğunlukla yenileme jetonu geçersizdir: OAuth onay ekranı 'Testing' "
            "durumundaysa jeton 7 günde ölür ('In production' olmalı), ya da erişim geri alınmıştır. Yeniden "
            "yetkilendirip YT_REFRESH_TOKEN gizli değerini güncelleyin. Hiçbir şey paylaşılmadı.")


class YoutubeFailed(OpsError):
    hint = ("YouTube videoyu almadı, hiçbir şey paylaşılmadı. YouTube kaydı 'claimed' kaldı ve otomatik denenmez. "
            "Sebebi giderince (ör. günlük kota, kimlik) üstlenmeyi kaldırın: veri dalındaki youtube/videos.csv "
            "dosyasından o satırı silin (GitHub'da dal listesinden 'veri' dalını seçin, dosyayı açıp kalem simgesiyle "
            "düzenleyin). Sonra publish'i çalıştırın.")


class YoutubeUncertain(OpsError):
    hint = ("YouTube yüklemesi sonucu belirsiz. Önce YouTube Studio'ya bakın. Video yüklendiyse hiçbir şey yapmayın, "
            "tekrar paylaşılmaz (satır 'claimed' kalır). Öğrenmeye katmak isterseniz youtube/videos.csv'de o satıra "
            "video kimliğini ve yayın zamanını yazıp status'u 'published' yapın. Yüklenmediyse o satırı youtube/videos.csv'den "
            "silin, böylece yeniden denenir.")


class TiktokNotConnected(OpsError):
    hint = ("TikTok hesabı bağlı değil. Siteden 'Connect TikTok' ile bağlanın (README.md / dashboard/README.md). "
            "Hiçbir şey paylaşılmadı.")


class TiktokAuthFailed(OpsError):
    hint = ("TikTok yetkilendirmesi başarısız. Yenileme jetonu 365 gün geçerlidir ve her yenilemede değişebilir. "
            "Jeton ölmüş ya da erişim geri alınmışsa siteden yeniden bağlanın. Hiçbir şey paylaşılmadı.")


class TiktokFailed(OpsError):
    hint = ("TikTok videoyu almadı ya da işleyemedi, gelen kutusuna taslak düşmedi. TikTok kaydı 'claimed' kaldı ve "
            "otomatik denenmez. Sebebi giderince veri dalındaki tiktok/videos.csv dosyasından o satırı silin "
            "(GitHub'da dal listesinden 'veri' dalını seçin), sonra yayını yeniden çalıştırın.")


class TiktokUncertain(OpsError):
    hint = ("TikTok yüklemesi sonucu belirsiz. TikTok uygulamasında gelen kutusuna (Inbox) bakın. Taslak geldiyse "
            "hiçbir şey yapmayın, tekrar yüklenmez (satır 'claimed' kalır). Gelmediyse o satırı "
            "tiktok/videos.csv'den silin, böylece yeniden denenir.")


class ClockMissed(OpsError):
    hint = ("Saat (Cloudflare Worker) bu turları başlatmadı, nöbetçi başlattı: video gecikmeli çıkar. Bir kez olduysa "
            "geçicidir, kayıt sonraki temiz nöbette kendiliğinden kapanır. Sürüyorsa saat çalışmıyor demektir: "
            "DISPATCH_TOKEN gizli değerinin süresi dolmuş olabilir. Yenisini oluşturup Actions → FORMFORM panel → "
            "Run workflow ile Worker'a yazın (dashboard/README.md). Saat düzelene kadar turları günde iki kez nöbetçi başlatır.")
