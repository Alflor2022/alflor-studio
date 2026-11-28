"""Mede quantos frames 1080p/s o gravador aguenta (sem câmara).

Uso: python tests\\recorder_bench.py [codec] [imagem.png]   (BENCH_SECS=15 para teste longo)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio.media import Recorder, find_ffmpeg  # noqa: E402

codec = sys.argv[1] if len(sys.argv) > 1 else "auto"
secs = float(os.environ.get("BENCH_SECS", "5"))
out = Path(tempfile.gettempdir()) / f"ptz_bench_{codec}.mp4"
if len(sys.argv) > 2:  # imagem real (mais representativa do que ruído)
    import cv2
    base = cv2.resize(cv2.imread(sys.argv[2]), (1920, 1080))
else:
    base = np.random.default_rng(0).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
rec = Recorder(find_ffmpeg(), out, (1920, 1080), 30, codec, 20, "")
t0 = time.monotonic()
n = 0
while time.monotonic() - t0 < secs:
    rec.write(np.roll(base, n * 7, axis=1))
    n += 1
    time.sleep(max(0.0, t0 + n / 30 - time.monotonic()))
err = rec.stop()
print(f"{codec} -> {rec.codec}: enviados {n}, escritos {rec.frames}, descartados {rec.dropped}, "
      f"{rec.path.stat().st_size // 1024} KB {rec.path.name} {err}")
out.unlink(missing_ok=True)
