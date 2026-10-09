"""Üretilen videonun Instagram Reels'e uygunluğunu doğrular (ffprobe).

  python check.py video.mp4
  python check.py video.mp4 --spec release.json     (tarifte `sound` varsa ses akışı ZORUNLU olur)

Hata varsa 1 ile çıkar. GitHub Actions'ta yayın öncesi son kapıdır.
"""
import argparse
import json
import re
import subprocess
import sys


def probe(path):
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=codec_name,profile,pix_fmt,width,height,avg_frame_rate,nb_read_frames,duration',
        '-show_entries', 'format=size,duration', '-of', 'json', path])
    return json.loads(out)


def probe_audio(path):
    """İlk ses akışı ya da None."""
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries',
        'stream=codec_name,sample_rate,channels,duration', '-of', 'json', path])
    streams = json.loads(out).get('streams') or []
    return streams[0] if streams else None


def mean_volume_db(path):
    """Sesin ortalama seviyesi (dB). Sessiz/bozuk ses dosyasını yakalar. Ölçülemezse None."""
    p = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', path, '-vn', '-af', 'volumedetect',
                        '-f', 'null', '-'], capture_output=True, text=True)
    m = re.search(r'mean_volume:\s*(-?[\d.]+) dB', p.stderr)
    return float(m.group(1)) if m else None


def problems(path, require_audio=False):
    info = probe(path)
    st = info['streams'][0]
    fmt = info['format']
    w, h = int(st['width']), int(st['height'])
    num, den = st['avg_frame_rate'].split('/')
    fps = float(num) / float(den)
    dur = float(st.get('duration') or fmt['duration'])
    size = int(fmt['size'])
    bad = []
    if st['codec_name'] != 'h264':
        bad.append(f"codec h264 olmalı, {st['codec_name']}")
    if st['pix_fmt'] != 'yuv420p':
        bad.append(f"pix_fmt yuv420p olmalı, {st['pix_fmt']}")
    if abs(w / h - 9 / 16) > 0.01:
        bad.append(f'oran 9:16 olmalı, {w}x{h}')
    if w < 720:
        bad.append(f'genişlik en az 720 olmalı, {w}')
    if not 23 <= fps <= 60:
        bad.append(f'fps 23-60 olmalı, {fps:.1f}')
    if not 3 <= dur <= 60:
        bad.append(f'süre 3-60 sn olmalı, {dur:.1f}')
    if int(st['nb_read_frames']) < dur * fps * 0.98:
        bad.append(f"kare sayısı eksik: {st['nb_read_frames']}")
    if size > 90 * 1024 * 1024:
        bad.append(f'dosya çok büyük: {size / 1e6:.1f} MB')
    if size < 50 * 1024:
        bad.append(f'dosya şüpheli derecede küçük: {size} bayt')

    summary = dict(width=w, height=h, fps=round(fps, 2), duration=round(dur, 2), size_mb=round(size / 1e6, 2))
    snd = probe_audio(path)
    if snd is None:
        if require_audio:
            bad.append('ses akışı yok (tarifte ses var)')
        summary['audio'] = None
    else:
        a_dur = float(snd.get('duration') or dur)
        if snd['codec_name'] != 'aac':
            bad.append(f"ses codec'i aac olmalı, {snd['codec_name']}")
        if int(snd['channels']) != 2:
            bad.append(f"ses stereo olmalı, {snd['channels']} kanal")
        if int(snd['sample_rate']) not in (44100, 48000):
            bad.append(f"ses örnekleme hızı 44100/48000 olmalı, {snd['sample_rate']}")
        if abs(a_dur - dur) > 0.25:
            bad.append(f'ses süresi ({a_dur:.2f} sn) video süresiyle ({dur:.2f} sn) uyuşmuyor')
        vol = mean_volume_db(path)
        if vol is not None and vol < -45.0:
            bad.append(f'ses neredeyse sessiz (ortalama {vol:.1f} dB)')
        summary['audio'] = dict(codec=snd['codec_name'], sample_rate=int(snd['sample_rate']),
                                channels=int(snd['channels']), mean_volume_db=vol)
    return bad, summary


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('--spec', help="tarif JSON'u: `sound` doluysa ses akışı zorunlu")
    a = ap.parse_args()
    need_audio = False
    if a.spec:
        with open(a.spec, encoding='utf-8') as f:
            need_audio = bool(json.load(f).get('sound'))
    bad, summary = problems(a.video, require_audio=need_audio)
    print(json.dumps(summary))
    if bad:
        print('\n'.join('HATA: ' + b for b in bad), file=sys.stderr)
        sys.exit(1)
