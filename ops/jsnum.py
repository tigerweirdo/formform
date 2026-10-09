"""JavaScript'le aynı sonucu veren küçük sayı yardımcıları (eski n8n koduyla birebir aynı çıktı için)."""
import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal


def num(value):
    """Number(x): boş metin 0, geçersiz metin NaN."""
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == '':
        return 0.0
    try:
        return float(text)
    except ValueError:
        return math.nan


def num_or0(value):
    """Number(x) || 0"""
    n = num(value)
    return 0.0 if (math.isnan(n) or n == 0) else n


def finite(x):
    return not (math.isnan(x) or math.isinf(x))


def blank(value):
    return value is None or str(value).strip() == ''


def fold_sum(values):
    """JS reduce((s, v) => s + v, 0): düz soldan sağa toplama.
    Python 3.12 sum() ondalıklı sayıları farklı (daha hassas) topladığı için sürüme bağlı olmasın diye kullanılmaz."""
    total = 0
    for v in values:
        total += v
    return total


def js_round(x):
    """Math.round: .5 yukarı yuvarlanır (Python'un round'u çifte yuvarlar)."""
    return math.floor(x + 0.5)


def tidy(x):
    """Tam sayı değerli ondalığı tam sayı yapar (JS 5 yazar, Python 5.0 yazardı)."""
    if isinstance(x, float) and finite(x) and x == int(x) and abs(x) < 1e15:
        return int(x)
    return x


def to_fixed(x, digits):
    """Number.prototype.toFixed: tam eşitlikte yukarı (sıfırdan uzağa) yuvarlar."""
    if not finite(x):
        return 'NaN' if math.isnan(x) else ('Infinity' if x > 0 else '-Infinity')
    q = Decimal(1).scaleb(-digits)
    d = Decimal(abs(x)).quantize(q, rounding=ROUND_HALF_UP)
    text = format(d, 'f')
    return ('-' if x < 0 else '') + text      # JS: sıfıra yuvarlanan negatif de "-0.0" yazılır


def parse_time_ms(text):
    """Date.parse (ISO biçimleri): milisaniye ya da NaN."""
    s = str(text or '').strip()
    if not s:
        return math.nan
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return math.nan
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0
