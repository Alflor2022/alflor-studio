"""Teste ao vivo (câmara real): fps com chroma/IA e mesa com duas câmaras.

Uso: python tests\\live_test.py [câmara principal] [segunda câmara] [pasta de saída]
Não move a câmara. Mede fps de captura em cada cenário e guarda uma folha de contacto.
"""
import copy
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ptz_studio.ai import AIWorker  # noqa: E402
from ptz_studio.bridge import Bridge  # noqa: E402
from ptz_studio.capture import CaptureThread  # noqa: E402
from ptz_studio.chroma import auto_key  # noqa: E402
from ptz_studio.config import DEFAULTS  # noqa: E402
from ptz_studio.pipeline import Pipeline  # noqa: E402
from ptz_studio.switcher import CallableSource, CameraSource, SwitcherEngine  # noqa: E402
from ptz_studio.tracking import SubjectTracker  # noqa: E402

MAIN = sys.argv[1] if len(sys.argv) > 1 else "UVC Camera"
SECOND = sys.argv[2] if len(sys.argv) > 2 else "Integrated Webcam"
OUT = Path(sys.argv[3] if len(sys.argv) > 3 else ".")
OUT.mkdir(parents=True, exist_ok=True)
res: dict = {}


def wait(s: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(s * 1000), loop.quit)
    loop.exec()


def measure(cap, secs=6.0) -> float:
    wait(1.5)
    samples = []
    t_end = time.monotonic() + secs
    while time.monotonic() < t_end:
        wait(1.0)
        samples.append(cap.fps)
    return round(float(np.median(samples)), 1)


app = QApplication(sys.argv)
b = Bridge()
devs = b.list_devices()["video"]
b.shutdown()
settings = copy.deepcopy(DEFAULTS)
pipe = Pipeline(settings["pipeline"])
ai = AIWorker()
tracker = SubjectTracker(settings["tracking"], ai)
cap = CaptureThread(devs.index(MAIN), settings, pipe, tracker)
cap.start()
wait(3)
res["base_fps"] = measure(cap)

ck = settings["pipeline"]["chroma"]
raw = cap.latest(processed=False)
found = auto_key(raw, "green")
res["auto_key"] = found
if found:
    ck["color"], ck["similarity"] = found
ck.update(enabled=True, bg="color", bg_color=[120, 60, 20], light_wrap=0.25, gpu=True)
res["chroma_gpu_fps"] = measure(cap)
res["chroma_ms"] = round(pipe.chroma.last_ms, 1)
comp = cap.latest(processed=True)
alpha = cap.latest_alpha()

ck["gpu"] = False
res["chroma_cpu_fps"] = measure(cap)
ck["gpu"] = True

settings["tracking"]["gestures"] = True          # IA: mãos + pose em paralelo
ai.want["segment"] = True
ck["mode"] = "hybrid"
res["chroma_hibrido_mais_gestos_fps"] = measure(cap)
res["ia_ms"] = {k: round(v, 1) for k, v in ai.results.timings.items()}
res["ia_erros"] = ai.errors
hyb = cap.latest(processed=True)
settings["tracking"]["gestures"] = False
ai.want["segment"] = False
ck["enabled"] = False

# mesa: principal + segunda câmara
eng = SwitcherEngine()
eng.set_source(0, CallableSource("Principal", cap.latest_with_alpha))
try:
    s2 = copy.deepcopy(DEFAULTS)
    s2["resolution"] = "1920x1080"
    cam2 = CameraSource(SECOND, devs.index(SECOND), s2)
    eng.set_source(1, cam2)
    wait(4)
    mv = []
    eng.on_multiview = mv.append
    eng.cut()
    wait(3)
    res["mesa"] = {"fps_mesa": round(eng.stats["fps"], 1), "ms_frame": round(eng.stats["ms"], 1),
                   "fps_principal": round(cap.fps, 1), "fps_segunda": round(cam2.capture.fps, 1),
                   "segunda_ptz": cam2.ptz is not None}
    if mv:
        cv2.imwrite(str(OUT / "live_multiview.jpg"), mv[-1])
except Exception as exc:
    res["mesa"] = f"erro: {exc}"
eng.stop()
cap.stop()
ai.stop()

if comp is not None:
    a3 = cv2.cvtColor(alpha, cv2.COLOR_GRAY2BGR) if alpha is not None else np.zeros_like(comp)
    tiles = [cv2.resize(x, (640, 360)) for x in (raw, a3, comp, hyb if hyb is not None else comp)]
    cv2.imwrite(str(OUT / "live_chroma.jpg"), np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))
print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
