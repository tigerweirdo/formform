"""Yayın platformları. Her platformun kendi kaydı (videos + dna_weights) ve kendi yayın durumu vardır.

Bir render tek kez çizilir, her platform onu kendi saatinde yayınlar. Ölçüm, skor ve öğrenilen ağırlıklar
platforma özeldir: bir platformdaki başarı ötekinin verisine karışmaz.

Instagram'ın kayıtları depo kökünde (`videos.csv`, `dna_weights.csv`) kalır: canlı sistem ve panel onlara bağlı.
Diğer platformlar kendi klasöründe tutulur (`youtube/videos.csv`, `youtube/dna_weights.csv`).
"""
import posixpath
from collections import namedtuple

VIDEO_COLUMNS = [
    'video_id', 'created_at', 'structure', 'palette', 'materials', 'transformations', 'motion_quality',
    'lighting', 'loop_strategy', 'caption', 'instagram_id', 'published_at', 'views_24h', 'views_72h',
    'likes', 'comments', 'shares', 'saves', 'quality_score', 'performance_score', 'selection_mode',
    'structure_weight', 'palette_weight', 'motion_weight', 'lighting_weight', 'avg_watch_time', 'sound',
]
DNA_COLUMNS = ['key', 'dimension', 'value', 'avg_score', 'sample_count', 'weight', 'updated_at']

# directory: kayıtların klasörü ('' = depo kökü). id_field: platformun video kimliği sütunu.
Platform = namedtuple('Platform', 'name label directory id_field columns')


def _columns(name):
    """Instagram'ın sütunları eskisi gibi kalır. Diğer platformlar kendi kimlik sütununu ve bir `status`
    sütununu alır: 'claimed' (üstlenildi, paylaşım sürüyor ya da yarım kaldı) ya da 'published'."""
    if name == 'instagram':
        return list(VIDEO_COLUMNS)
    return [f'{name}_id' if c == 'instagram_id' else c for c in VIDEO_COLUMNS] + ['status']


def _platform(name, label):
    return Platform(name, label, '' if name == 'instagram' else name, f'{name}_id', _columns(name))


PLATFORMS = {p.name: p for p in (
    _platform('instagram', 'Instagram'),
    _platform('youtube', 'YouTube'),
    _platform('tiktok', 'TikTok'),
)}
DEFAULT = 'instagram'


def get(name):
    try:
        return PLATFORMS[name]
    except KeyError:
        raise ValueError(f"bilinmeyen platform: {name!r} (var olanlar: {', '.join(PLATFORMS)})") from None


def videos_file(platform):
    return posixpath.join(platform.directory, 'videos.csv')


def dna_file(platform):
    return posixpath.join(platform.directory, 'dna_weights.csv')
