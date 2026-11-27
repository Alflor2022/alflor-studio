"""Testa o chroma key: precisão numa cena sintética (resposta conhecida), desempenho a
1080p e resultado numa foto real do estúdio.

Uso: python tests\\test_chroma.py [foto_real.png] [pasta_saida]
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio.chroma import DEFAULTS, ChromaKeyer, auto_key  # noqa: E402

out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
out_dir.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(1)
W, H = 1920, 1080

# --- cena sintética: ecrã verde com sombra e ruído + "pessoa" com spill verde ---------------
bg = np.zeros((H, W, 3), np.float32)
bg[:] = (60, 185, 70)
shade = np.linspace(0.65, 1.1, W, dtype=np.float32)[None, :, None]     # luz desigual no ecrã
bg = bg * shade + rng.normal(0, 6, (H, W, 3))
truth = np.zeros((H, W), np.uint8)
cv2.ellipse(truth, (960, 380), (130, 170), 0, 0, 360, 255, -1)           # cabeça
cv2.rectangle(truth, (760, 520), (1160, 1080), 255, -1)                  # tronco
fg = np.zeros((H, W, 3), np.float32)
fg[:] = (150, 170, 215)                                                    # pele
fg[520:, :] = (140, 60, 40)                                                # camisola azul-escura
fg += rng.normal(0, 5, (H, W, 3))
edge = cv2.GaussianBlur(truth, (0, 0), 3).astype(np.float32) / 255
spill = (np.clip(1 - np.abs(edge - 0.5) * 2, 0, 1))[..., None] * np.array([0, 45, 0], np.float32)
img = np.where(truth[..., None] > 0, fg + spill, bg)
img = np.clip(img, 0, 255).astype(np.uint8)

s = dict(DEFAULTS, enabled=True, bg="color", bg_color=[30, 30, 30], light_wrap=0.0)
found = auto_key(img, "green")
assert found, "auto_key não encontrou o ecrã"
s["color"], s["similarity"] = found
k = ChromaKeyer(s)
out = k.process(img)
a = k.alpha > 127
t = truth > 127
iou = (a & t).sum() / max(1, (a | t).sum())
bg_leak = a[~t].mean()                    # fundo que ficou (devia ser 0)
fg_holes = (~a[t]).mean()                 # sujeito que desapareceu (devia ser 0)
green_after = out[t][..., 1].astype(int) - np.maximum(out[t][..., 0], out[t][..., 2]).astype(int)
times = []
for _ in range(15):
    t0 = time.perf_counter(); k.process(img); times.append((time.perf_counter() - t0) * 1000)
print(f"sintético: cor={s['color']} semelhança={s['similarity']:.3f}  IoU={iou:.4f}  "
      f"fundo restante={bg_leak*100:.2f}%  buracos={fg_holes*100:.2f}%  "
      f"spill médio no sujeito (G-max(R,B))={green_after.mean():.1f}")
print(f"desempenho 1080p: {np.median(times):.1f} ms/frame (mediana de 15)")
for view in ("matte", "original"):
    s["view"] = view; k.process(img)
s["view"] = "composite"
s["bg"] = "transparent"; k.process(img)
assert k.alpha is not None and k.alpha.shape == (H, W)
# a orla com spill sintético forte fica semitransparente (correto); tolera 2 % nessa orla
ok = iou > 0.98 and bg_leak < 0.005 and fg_holes < 0.02 and np.median(times) < 30
print("RESULTADO SINTÉTICO:", "OK" if ok else "FALHOU")

# --- foto real do estúdio --------------------------------------------------------------------------
if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
    real = cv2.imread(sys.argv[1])
    s2 = dict(DEFAULTS, enabled=True, bg="color", bg_color=[120, 60, 20], light_wrap=0.25)
    found = auto_key(real, "green")
    print("real: auto_key ->", found)
    if found:
        s2["color"], s2["similarity"] = found
    k2 = ChromaKeyer(s2)
    comp = k2.process(real)
    matte = cv2.cvtColor(k2.alpha, cv2.COLOR_GRAY2BGR)
    sheet = np.hstack([cv2.resize(x, (640, 360)) for x in (real, matte, comp)])
    cv2.imwrite(str(out_dir / "chroma_real.jpg"), sheet)
    print("real: folha de contacto ->", out_dir / "chroma_real.jpg", f"({k2.last_ms:.1f} ms)")
sys.exit(0 if ok else 1)
