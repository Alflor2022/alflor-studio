"""Envia um padrão para a «OBS Virtual Camera» e lê-o de volta como faria o Zoom/Teams/OBS."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio.media import VirtualCam, find_ffmpeg  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vcam_readback.png")
cam = VirtualCam((1280, 720), 30)
print("dispositivo:", cam.device)
stop = False


def feed():
    i = 0
    while not stop:
        f = np.zeros((720, 1280, 3), np.uint8)
        f[:] = (40, 30, 20)
        cv2.putText(f, "ALFLOR STUDIO VCAM", (230, 380), cv2.FONT_HERSHEY_SIMPLEX, 3, (61, 138, 255), 8)
        cv2.circle(f, (100 + (i * 12) % 1080, 600), 40, (80, 220, 60), -1)
        cam.write(f)
        i += 1
        time.sleep(1 / 30)


t = threading.Thread(target=feed, daemon=True)
t.start()
time.sleep(1.5)
ff = find_ffmpeg()
r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "dshow", "-i", "video=OBS Virtual Camera",
                    "-frames:v", "1", "-ss", "1", str(out)], capture_output=True, text=True, timeout=30)
stop = True
t.join()
cam.close()
print("leitura:", "OK" if out.exists() else "FALHOU", r.stderr.strip()[-300:])
