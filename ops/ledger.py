"""Platform başına yayın durumu (Instagram dışındaki platformlar için).

Instagram'ın durumu release adındadır ('render' → 'claimed' → 'published'). Release adı tek ve ortak olduğu için
platform başına olamaz: biri 'claimed' yapınca ötekiler videoyu göremezdi. Diğer platformların durumu kendi
kaydındadır (`<platform>/videos.csv`, `status` sütunu) ve aynı güvenceyi verir:

  1. ÜSTLEN: videonun satırı `status=claimed` ile yazılır. Paylaşımdan ÖNCE ve atlanamaz.
  2. paylaş
  3. `settle`: satır `published` olur, platformun video kimliği ve zamanı yazılır.

Üstlenme atomiktir: kayıt `Store.transaction` ile veri dalına itilir, iki akış aynı anda üstlenmeye kalkarsa
yalnız biri itmeyi kazanır, öteki yeni veriyle baştan dener ve satırı görüp vazgeçer. Paylaşım hata verirse satır
'claimed' kalır ve otomatik denenmez. Yeniden denemek için o satırı veri dalındaki `<platform>/videos.csv`'den silin (`unclaim` bunu güvenle yapar,
yayınlanmış satıra dokunmaz; komut ve workflow düğmesi YouTube’un workflow’uyla birlikte bağlanacak).

`claimed` ve `drafted` satırı öğrenmeye girmez: yayın zamanı boştur (`scoring.find_due` ve
`ready_to_score` bu satırları atlar). TikTok'un `drafted` satırını, kullanıcı taslağı paylaştıktan sonra
`ops/tiktok_sync.py` bulur ve `published` yapar.
"""
import uuid
from datetime import timezone

from . import platforms
from .errors import OpsError


def stamp(now):
    return now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def taken_ids(data, platform_name):
    """Bu platformda üstlenilmiş ya da yayınlanmış (herhangi bir durumda) video kimlikleri."""
    return {r.get('video_id') for r in data.ledger(platform_name).videos if r.get('video_id')}


def claim(store, platform_name, row, message=None):
    """Videoyu bu platform için üstlenir. True: üstlendik, paylaşabiliriz. False: başkası zaten üstlenmiş.

    Kayıt yazılamazsa StoreError fırlar: paylaşım yapılmamış olur (güvenli taraf)."""
    platform = platforms.get(platform_name)
    video_id = row['video_id']
    outcome = {}

    def apply(data):
        rows = data.ledger(platform.name).videos
        if any(r.get('video_id') == video_id for r in rows):
            outcome['claimed'] = False
            return
        # Üstlenen satır öğrenmeye girmemeli: kimlik ve yayın zamanı çağıranın verdiği ne olursa olsun boş başlar
        rows.append(dict(row, **{platform.id_field: '', 'published_at': '', 'status': 'claimed'}))
        outcome['claimed'] = True

    # Commit mesajı HER üstlenme çağrısına özgüdür. Aynı saniyede aynı satırı yazan iki yarışçının commit'leri (aynı
    # ebeveyn, içerik, yazar, zaman) bit bit aynı olurdu: git aynı commit'in ikinci kez itilmesini "zaten güncel" diye
    # başarılı sayar ve iki taraf da "ben üstlendim" derdi. Özgü mesajla commit'ler farklıdır, ikinci itme reddedilir.
    store.transaction(apply, message or f'Üstlenildi ({platform.name}): {video_id} [{uuid.uuid4().hex[:12]}]')
    return outcome['claimed']


def settle(store, platform_name, video_id, platform_id, now, message=None, status='published'):
    """Paylaşım yapıldıktan sonra satırı tamamlar ve platformun kimliğini yazar.

    status='published': yayın zamanı da yazılır, öğrenme bu satırı ölçer. status='drafted' (TikTok taslak modu):
    video gelen kutusuna taslak olarak düştü ama paylaşan kullanıcıdır. Gerçek yayın zamanı bilinmediği için
    `published_at` boş kalır ve satır öğrenmeye girmez (yayın zamanı sonradan TikTok'un video listesinden gelir,
    `ops/tiktok_sync.py`)."""
    platform = platforms.get(platform_name)

    def apply(data):
        rows = data.ledger(platform.name).videos
        row = next((r for r in rows if r.get('video_id') == video_id), None)
        if row is None:                                    # üstlenme satırı kaybolmuşsa yine de kaydı bırak
            row = {'video_id': video_id}
            rows.append(row)
        row.update({platform.id_field: platform_id, 'status': status})
        if status == 'published':
            row['published_at'] = stamp(now)

    store.transaction(apply, message or f'{"Yayınlandı" if status == "published" else "Taslak yüklendi"} ({platform.name}): {video_id}')


def unclaim(store, platform_name, video_id, message=None):
    """Yarım kalmış üstlenmeyi kaldırır, böylece video bu platform için yeniden denenebilir. True: kaldırıldı.

    Yalnız hâlâ 'claimed' olan ve platform kimliği ile yayın zamanı BOŞ olan satıra dokunur: yayınlanmış bir videonun
    satırı bu yolla silinemez (yeniden paylaşım olmasın). Paylaşımın gerçekten yapılmadığından emin olunca kullanın.
    Instagram'ın durumu release adındadır, buradan kaldırılmaz."""
    platform = platforms.get(platform_name)
    if platform.name == platforms.DEFAULT:
        raise OpsError('Instagram\'ın durumu release adındadır. Releases sayfasında release adını "render <video_id>" yapın.')
    outcome = {}

    def apply(data):
        rows = data.ledger(platform.name).videos
        row = next((r for r in rows if r.get('video_id') == video_id), None)
        if row is None or row.get('status') != 'claimed' or row.get(platform.id_field) or row.get('published_at'):
            outcome['removed'] = False
            return
        rows.remove(row)
        outcome['removed'] = True

    store.transaction(apply, message or f'Üstlenme kaldırıldı ({platform.name}): {video_id}')
    return outcome['removed']
