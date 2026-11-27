"""Fotos, gravação de vídeo (FFmpeg com aceleração Intel QSV) e câmara virtual."""
from __future__ import annotations

import datetime as dt
import glob
import os
import queue
import re
import shutil
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np

NO_WINDOW = 0x08000000


def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "ffmpeg.exe")
    hits = glob.glob(pattern, recursive=True)
    return hits[0] if hits else None


def _safe(text: str) -> str:
    return re.sub(r"[^\w\-]+", "-", text.strip(), flags=re.UNICODE).strip("-") or "x"


def output_path(settings, kind: str, ext: str) -> Path:
    """<pasta>/<AAAA-MM-DD>/<Fotos|Videos>/<projeto>_<cena>_T01_<hhmmss>.<ext>

    Estrutura previsível para importar no Premiere, CapCut, DaVinci ou sincronizar na cloud.
    """
    now = dt.datetime.now()
    folder = Path(settings["output_dir"]) / now.strftime("%Y-%m-%d") / ("Fotos" if kind == "photo" else "Videos")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{_safe(settings['project'])}_{_safe(settings['scene'])}_T{int(settings['take']):02d}_{now.strftime('%H%M%S')}"
    path = folder / f"{name}.{ext}"
    n = 2
    while path.exists():
        path = folder / f"{name}_{n}.{ext}"
        n += 1
    return path


def save_image(img: np.ndarray, path: Path, fmt: str) -> Path:
    if fmt == "png":
        cv2.imwrite(str(path), img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    elif fmt == "tiff":
        cv2.imwrite(str(path), img)
    else:
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 100, cv2.IMWRITE_JPEG_OPTIMIZE, 1])
    return path


def stack_frames(frames: list[np.ndarray]) -> np.ndarray:
    """Empilhamento por média (reduz ruído ~sqrt(N)); para fotos noturnas com câmara parada."""
    acc = np.zeros(frames[0].shape, np.float32)
    for f in frames:
        acc += f
    acc /= len(frames)
    return np.clip(acc + 0.5, 0, 255).astype(np.uint8)


def best_sharpness(frames: list[np.ndarray]) -> np.ndarray:
    """Do burst, escolhe o frame mais nítido (menos tremido/desfocado)."""
    def score(f):
        g = cv2.cvtColor(cv2.resize(f, (640, 360)), cv2.COLOR_BGR2GRAY)
        return cv2.Laplacian(g, cv2.CV_64F).var()
    return max(frames, key=score)


_encoder_cache: dict[str, bool] = {}


def encoder_available(ffmpeg: str, codec: str) -> bool:
    if codec in _encoder_cache:
        return _encoder_cache[codec]
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=0.2",
            "-frames:v", "3", "-c:v", codec, "-f", "null", "-"]
    try:
        ok = subprocess.run(args, capture_output=True, timeout=20, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _encoder_cache[codec] = ok
    return ok


CODECS = {
    "auto": "Automático (H.264, recomendado)",
    "h264_qsv": "H.264 - Intel QSV (poupa CPU)",
    "hevc_qsv": "H.265 - Intel QSV (ficheiros menores)",
    "libx264": "H.264 - CPU (compatível)",
    "mjpeg": "MJPEG intra (edição pesada sem perdas visíveis)",
}


class Recorder:
    """Recebe frames processados e envia-os ao FFmpeg numa thread própria."""

    def __init__(self, ffmpeg: str, path: Path, size: tuple[int, int], fps: float, codec: str, quality: int, audio_device: str = "") -> None:
        self.path = path
        self.size = size
        self._q: queue.Queue = queue.Queue(maxsize=24)
        self.dropped = 0
        self.frames = 0
        if codec == "auto":
            # x264 "veryfast" mantém 30 fps sem perdas; o QSV deste iGPU perde ~6% a 1080p30
            codec = "libx264" if encoder_available(ffmpeg, "libx264") else "h264_qsv"
        self.codec = codec
        w, h = size
        # Envia YUV 4:2:0 já convertido (OpenCV SIMD): metade dos bytes e o FFmpeg não converte cor
        self._yuv = codec != "mjpeg" and w % 2 == 0 and h % 2 == 0
        pix_in = "yuv420p" if self._yuv else "bgr24"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-thread_queue_size", "64", "-use_wallclock_as_timestamps", "1",
               "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{w}x{h}", "-color_range", "tv", "-i", "-"]
        if audio_device:
            cmd += ["-thread_queue_size", "1024", "-f", "dshow", "-audio_buffer_size", "50", "-i", f"audio={audio_device}"]
        cmd += ["-map", "0:v"] + (["-map", "1:a"] if audio_device else [])
        cmd += ["-fps_mode", "cfr", "-r", str(int(round(fps)) or 30)]
        q = max(1, min(51, int(quality)))
        if codec in ("h264_qsv", "hevc_qsv"):
            cmd += ["-c:v", codec, "-preset", "medium", "-global_quality", str(q), "-pix_fmt", "nv12"]
        elif codec == "mjpeg":
            cmd += ["-c:v", "mjpeg", "-q:v", "2", "-pix_fmt", "yuvj422p"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q), "-pix_fmt", "yuv420p"]
        if audio_device:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        if path.suffix.lower() == ".mp4":
            cmd += ["-movflags", "+faststart"]
        cmd += [str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self._thread = threading.Thread(target=self._pump, name="recorder", daemon=True)
        self._thread.start()
        self.started = dt.datetime.now()

    def write(self, frame: np.ndarray) -> None:
        if (frame.shape[1], frame.shape[0]) != self.size:
            frame = cv2.resize(frame, self.size)
        try:
            self._q.put_nowait(frame)
        except queue.Full:
            self.dropped += 1  # o FFmpeg compensa com carimbo de tempo real

    def _pump(self) -> None:
        assert self.proc.stdin
        while True:
            frame = self._q.get()
            if frame is None:
                break
            try:
                if self._yuv:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
                self.proc.stdin.write(memoryview(frame).cast("B"))
                self.frames += 1
            except OSError:
                break

    def stop(self) -> str:
        self._q.put(None)
        self._thread.join(5)
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            _, err = self.proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            err = b"timeout"
        return (err or b"").decode("utf-8", "replace").strip()


class VirtualCam:
    """Saída para a câmara virtual do OBS (aparece no Zoom, Teams, Meet, browsers…)."""

    def __init__(self, size: tuple[int, int], fps: float) -> None:
        import pyvirtualcam
        self._cam = pyvirtualcam.Camera(width=size[0], height=size[1], fps=fps or 30,
                                        fmt=pyvirtualcam.PixelFormat.BGR, backend="obs")
        self.device = self._cam.device
        self.size = size

    def write(self, frame: np.ndarray) -> None:
        if (frame.shape[1], frame.shape[0]) != self.size:
            frame = cv2.resize(frame, self.size)
        self._cam.send(frame)

    def close(self) -> None:
        self._cam.close()
