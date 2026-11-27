"""Teste de hardware de ponta a ponta (precisa da câmara ligada).

Uso:  .venv\\Scripts\\python.exe tests\\hardware_test.py [nome da câmara] [pasta de saída]
Mede fps, calibra o PTZ, tira fotos, grava vídeo, testa o servidor remoto e o tracking.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ptz_studio.bridge import Bridge  # noqa: E402
from ptz_studio.capture import CaptureThread  # noqa: E402
from ptz_studio.config import Settings  # noqa: E402
from ptz_studio.media import Recorder, best_sharpness, find_ffmpeg, output_path, save_image, stack_frames  # noqa: E402
from ptz_studio.pipeline import Pipeline  # noqa: E402
from ptz_studio.ptz import PhysicalBackend, PTZController  # noqa: E402
from ptz_studio.remote import RemoteServer  # noqa: E402
from ptz_studio.tracking import AutoFramer, SubjectTracker  # noqa: E402

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "UVC Camera"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else Path.home() / "Videos" / "PTZ Studio" / "_teste")
OUT.mkdir(parents=True, exist_ok=True)
results: dict = {}


def wait(seconds: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def gray(img):
    return np.float32(cv2.cvtColor(cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))


def main() -> None:
    app = QApplication(sys.argv)
    settings = Settings.load()
    settings["output_dir"] = str(OUT)
    bridge = Bridge()
    devs = bridge.list_devices()["video"]
    idx = devs.index(DEVICE)
    info = bridge.open(idx)
    motors = {k: v for k, v in info["cam"].items() if k in ("pan", "tilt", "zoom")}
    results["motores"] = list(motors)
    pipeline = Pipeline(settings["pipeline"])
    for k, v in {"denoise": 0, "night": False, "sharpen": 0.0, "look": "Natural", "bg_blur": False, "mirror": False, "flip": False}.items():
        settings["pipeline"][k] = v
    tracker = SubjectTracker()
    ptz = PTZController(settings)
    ptz.attach(PhysicalBackend(bridge, motors), motors)
    cap = CaptureThread(idx, settings, pipeline, tracker)
    boxes: list = []
    cap.frame_ready.connect(lambda _img, meta: boxes.append(meta.get("raw_box")))
    cap.start()
    wait(4)
    ptz.home(0)
    ptz.resync()
    wait(3)

    # 1) fps
    wait(5)
    results["fps_base"] = round(cap.fps, 1)
    settings["pipeline"].update({"denoise": 2, "sharpen": 0.4, "look": "Cinema"})
    wait(5)
    results["fps_processado"] = round(cap.fps, 1)
    settings["pipeline"].update({"denoise": 0, "sharpen": 0.0, "look": "Natural"})

    # 2) calibração (zoom mínimo)
    win = cv2.createHanningWindow((640, 360), cv2.CV_32F)
    cal = {}
    a = gray(cap.latest(False)); ptz.nudge(dpan=120); wait(2.5)
    (dx, _), resp = cv2.phaseCorrelate(a, gray(cap.latest(False)), win)
    cal["pan_dx_px"], cal["pan_resp"] = round(dx, 1), round(resp, 3)
    if abs(dx) > 2:
        cal["pan_fw"] = round(-120 / (dx / 640), 1)
    ptz.nudge(dpan=-120); wait(2.5)
    a = gray(cap.latest(False)); ptz.nudge(dtilt=80); wait(2.5)
    (_, dy), resp = cv2.phaseCorrelate(a, gray(cap.latest(False)), win)
    cal["tilt_dy_px"], cal["tilt_resp"] = round(dy, 1), round(resp, 3)
    if abs(dy) > 2:
        cal["tilt_fh"] = round(80 / (dy / 360), 1)
    ptz.nudge(dtilt=-80); wait(2.5)
    z0 = cap.latest(False)
    ptz.move_to(zoom=motors["zoom"]["max"]); wait(6)
    z1 = cap.latest(False)
    cv2.imwrite(str(OUT / "zoom_max.jpg"), z1)
    from ptz_studio.app import MainWindow
    mag = MainWindow._estimate_scale(z0, z1)
    cal["zoom_max_mag"] = round(mag, 2) if mag else None
    ptz.move_to(zoom=0); wait(5)
    results["calibracao"] = cal
    keep = {k: cal[k] for k in ("pan_fw", "tilt_fh", "zoom_max_mag") if cal.get(k)}
    if keep:
        saved = Settings.load()
        saved["calibration"].setdefault(DEVICE, {}).update(keep)
        saved.save()

    # 3) fotos
    snap = {"output_dir": str(OUT), "project": "Teste", "scene": "Hardware", "take": 1}
    p1 = save_image(cap.latest(True), output_path(snap, "photo", "png"), "png")
    p2 = save_image(stack_frames(cap.grab(8, False)), output_path(snap, "photo", "jpg"), "jpg")
    p3 = save_image(best_sharpness(cap.grab(5, True)), output_path(snap, "photo", "jpg"), "jpg")
    results["fotos"] = [f"{p.name} ({p.stat().st_size // 1024} KB)" for p in (p1, p2, p3)]

    # 4) gravação 6 s
    ff = find_ffmpeg()
    rec = Recorder(ff, output_path(snap, "video", "mp4"), cap.size, 30, "auto", 20, "")
    cap.sinks.append(rec)
    ptz.motion("push_in", 4.0, 0.3)
    wait(6)
    cap.sinks.remove(rec)
    err = rec.stop()
    probe = subprocess.run([ff.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height,r_frame_rate",
                            "-of", "json", str(rec.path)], capture_output=True, text=True)
    results["video"] = {"ficheiro": rec.path.name, "codec": rec.codec, "frames": rec.frames, "descartados": rec.dropped,
                        "erro": err[-200:], "probe": json.loads(probe.stdout or "{}")}
    ptz.move_to(zoom=0); wait(4)

    # 5) servidor remoto
    srv = RemoteServer(8799, "teste", lambda: cap.jpeg(), lambda: {"ok": True, "fps": cap.fps})
    base = "http://127.0.0.1:8799"
    state = urllib.request.urlopen(f"{base}/api/state?k=teste", timeout=3).read()
    jpg = urllib.request.urlopen(f"{base}/snapshot.jpg?k=teste", timeout=3).read()
    page = urllib.request.urlopen(f"{base}/?k=teste", timeout=3).read()
    try:
        urllib.request.urlopen(f"{base}/api/state?k=errado", timeout=3)
        denied = False
    except urllib.error.HTTPError as e:
        denied = e.code == 403
    req = urllib.request.Request(f"{base}/api/cmd?k=teste", data=b'{"a":"home"}', headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=3).read()
    results["remoto"] = {"estado": json.loads(state), "snapshot_kb": len(jpg) // 1024, "pagina_kb": len(page) // 1024,
                         "token_errado_recusado": denied, "comando_na_fila": srv.commands.get_nowait()}
    srv.stop()

    # 6) tracking de objeto: seleciona a almofada vermelha, desloca a câmara e deixa o tracking recentrar
    frame = cap.latest(False)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, (0, 120, 70), (10, 255, 255)) | cv2.inRange(hsv, (170, 120, 70), (180, 255, 255))
    cnts, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    track = {}
    if cnts:
        x, y, w, h = cv2.boundingRect(max(cnts, key=cv2.contourArea))
        H, W = frame.shape[:2]
        sel = (x / W, y / H, w / W, h / H)
        track["selecao"] = [round(v, 3) for v in sel]
        cal_now = Settings.load().device_calibration(DEVICE)
        framer = AutoFramer({"target": "center", "shot": "Plano médio", "auto_zoom": False, "sensitivity": 0.6, "lost_home": False}, ptz, pipeline)
        framer.calibration = cal_now
        tracker.select(sel)
        wait(1.5)
        b0 = tracker.box
        track["inicio_centro"] = [round(b0[0] + b0[2] / 2, 3), round(b0[1] + b0[3] / 2, 3)] if b0 else None
        cv2.imwrite(str(OUT / "tracking_inicio.jpg"), cap.latest(False))
        t_end = time.monotonic() + 14
        while time.monotonic() < t_end:
            framer.step(tracker.box, "subject")
            wait(0.05)
        b1 = tracker.box
        track["fim_centro"] = [round(b1[0] + b1[2] / 2, 3), round(b1[1] + b1[3] / 2, 3)] if b1 else None
        track["ptz_final"] = {k: round(v, 1) for k, v in ptz.state().items() if isinstance(v, float)}
        cv2.imwrite(str(OUT / "tracking_fim.jpg"), cap.latest(False))
        tracker.enabled = False
    results["tracking"] = track

    ptz.home(0); wait(4)
    cap.stop()
    bridge.shutdown()
    print(json.dumps(results, indent=2, ensure_ascii=False))
    app.quit()


if __name__ == "__main__":
    main()
