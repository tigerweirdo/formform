"""FORM/FORM işletim katmanı: zamanlama dışındaki her şey (planlama, yayın, öğrenme, jeton, rapor).

Eskiden n8n'de çalışan akışların Python karşılığı. GitHub Actions'ta çalışır, yalnız standart
kütüphane kullanır (render hariç). Render motoru `render/formform` altındadır.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Render kütüphanesi (yapı, palet, malzeme adları) tek kaynaktan okunur.
_RENDER = os.path.join(ROOT, 'render')
if _RENDER not in sys.path:
    sys.path.insert(0, _RENDER)
