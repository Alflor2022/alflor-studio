"""Testa os elementos no ecrã / gravação / foto (câmara real).

Uso: python tests\\hud_test.py [pasta de saída]
Grelha só no ecrã · timecode e claquete só na gravação · data/hora e logótipo na gravação e na foto.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ptz_studio import hud as hudmod  # noqa: E402
from ptz_studio.app import MainWindow  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
fails = []


def wait(s):
    loop = QEventLoop()
    QTimer.singleShot(int(s * 1000), loop.quit)
    loop.exec()


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


app = QApplication(sys.argv)
win = MainWindow(test_mode=True)
win.settings.update(output_dir=str(OUT), project="TesteHUD", scene="Elementos", take=7)
logo = np.zeros((120, 360, 4), np.uint8)
cv2.putText(logo, "ALFLOR", (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (61, 138, 255, 255), 8)
cv2.imencode(".png", logo)[1].tofile(str(OUT / "logo_teste.png"))
hs = win.settings["hud"]
for k in hs:
    if not k.startswith("_"):
        hs[k].update(screen=False, rec=False, photo=False)
win.settings["overlays"].update(grid=True, hist=False, zebra=False, peaking=False, safe=False)
hs["grid"]["screen"] = True
hs["timecode"]["rec"] = hs["slate"]["rec"] = True
hs["clock"]["rec"] = hs["clock"]["photo"] = True
hs["logo"]["rec"] = hs["logo"]["photo"] = True
hs["_logo_path"], hs["_logo_corner"] = str(OUT / "logo_teste.png"), "br"
win.show()
for _ in range(40):
    wait(0.5)
    if win.capture and win.capture.latest() is not None:
        break

# 1) sem nada marcado para um destino, a imagem sai intacta (sem custo)
frame = win.capture.latest()
check("grelha só no ecrã: não entra na gravação", not hudmod.on(win.settings, "grid", "rec"))
saved = {k: dict(v) for k, v in hs.items() if not k.startswith("_")}
for k in saved:
    hs[k].update(rec=False, photo=False)
check("nada marcado → imagem intacta", win.hud.burn(frame, {}, "rec") is frame)
for k, v in saved.items():
    hs[k].update(v)

# 2) custo por frame
win.hud.state.update(recording=True, rec_start=time.time() - 12.3, fps=30, slate="TesteHUD · Elementos · T07")
t = []
for _ in range(30):
    t0 = time.perf_counter(); win.hud.burn(frame, {}, "rec"); t.append((time.perf_counter() - t0) * 1000)
check(f"custo de queimar timecode+claquete+relógio+logótipo ({np.median(t):.1f} ms/frame)", np.median(t) < 12)

# 3) gravação real de 3 s e foto
win.toggle_record(True)
wait(3.0)
win.toggle_record(False)
wait(3.0)
vids = sorted(OUT.rglob("TesteHUD_*.mp4"))
check("vídeo gravado", bool(vids))
if vids:
    shot = OUT / "hud_video_frame.png"
    subprocess.run([win.ffmpeg, "-y", "-loglevel", "error", "-ss", "2", "-i", str(vids[-1]), "-frames:v", "1", str(shot)])
    vf = cv2.imread(str(shot))
    clean = win.capture.latest()
    diff = cv2.absdiff(cv2.resize(vf, (clean.shape[1], clean.shape[0])), clean)
    h, w = clean.shape[:2]
    zone = lambda y0, y1, x0, x1: float(diff[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].mean())
    check("timecode gravado (centro inferior)", zone(0.88, 0.99, 0.4, 0.6) > 12, f"{zone(0.88, 0.99, 0.4, 0.6):.1f}")
    p99 = lambda dd, y0, y1, x0, x1: float(np.percentile(dd[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)], 99))
    check("logótipo gravado (canto inferior direito)", p99(diff, 0.88, 0.99, 0.86, 0.99) > 60, f"{p99(diff, 0.88, 0.99, 0.86, 0.99):.0f}")
    check("data/hora gravada (canto superior direito)", zone(0.01, 0.06, 0.75, 0.99) > 12, f"{zone(0.01, 0.06, 0.75, 0.99):.1f}")
win.take_photo("single")
wait(2.5)
photos = sorted(OUT.rglob("TesteHUD_*.png"))
check("foto gravada", bool(photos))
if photos:
    pf = cv2.imdecode(np.fromfile(str(photos[-1]), np.uint8), cv2.IMREAD_COLOR)
    clean = win.capture.latest()
    d = cv2.absdiff(pf, clean)
    h, w = clean.shape[:2]
    z = lambda y0, y1, x0, x1: float(d[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].mean())
    zp = float(np.percentile(d[int(0.88 * h):int(0.99 * h), int(0.86 * w):int(0.99 * w)], 99))
    check("logótipo na foto", zp > 60, f"{zp:.0f}")
    check("timecode NÃO está na foto", z(0.88, 0.99, 0.4, 0.6) < 8, f"{z(0.88, 0.99, 0.4, 0.6):.1f}")
    sheet = np.hstack([cv2.resize(x, (640, 360)) for x in (vf if vids else pf, pf)])
    cv2.imwrite(str(OUT / "hud_comparacao.jpg"), sheet)
win.close()
print("RESULTADO:", "TUDO OK" if not fails else f"{len(fails)} FALHA(S): {fails}")
sys.exit(1 if fails else 0)
