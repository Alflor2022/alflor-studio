"""Thread de captura: lê a câmara, processa, alimenta pré-visualização, gravação,
câmara virtual, telemóvel e tracking."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .pipeline import Pipeline, apply_peaking, apply_zebra, luma_histogram

RES_CANDIDATES = [(3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (640, 480)]


class CaptureThread(QThread):
    frame_ready = Signal(QImage, object)       # imagem de pré-visualização + metadados
    status = Signal(str)
    opened = Signal(int, int, float)
    failed = Signal(str)

    PREVIEW_W = 1280

    def __init__(self, index: int, settings, pipeline: Pipeline, tracker, parent=None, emit_preview: bool = True) -> None:
        super().__init__(parent)
        self.index = index
        self.settings = settings
        self.pipeline = pipeline
        self.tracker = tracker
        self.emit_preview = emit_preview         # False nas câmaras extra da mesa (poupa CPU)
        self.sinks: list = []                   # objetos com .write(frame) (ou .wants_alpha → BGRA)
        self._running = True
        self._lock = threading.Lock()
        self._latest_raw: np.ndarray | None = None
        self._latest_out: np.ndarray | None = None
        self._latest_alpha: np.ndarray | None = None
        self._checker: np.ndarray | None = None
        self._grab_requests: list[tuple[int, list, threading.Event]] = []
        self._jpeg_cache: tuple[float, bytes] = (0.0, b"")
        self.fps = 0.0
        self.size = (0, 0)

    def stop(self) -> None:
        self._running = False
        self.wait(4000)

    # --- acesso seguro a partir de outras threads -----------------------------------
    def latest(self, processed: bool = True) -> np.ndarray | None:
        with self._lock:
            img = self._latest_out if processed else self._latest_raw
            return None if img is None else img.copy()

    def latest_alpha(self) -> np.ndarray | None:
        """Alfa do chroma do último frame (uint8) ou None se o chroma estiver desligado."""
        with self._lock:
            return None if self._latest_alpha is None else self._latest_alpha.copy()

    def latest_with_alpha(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        with self._lock:
            img, a = self._latest_out, self._latest_alpha
            return (None if img is None else img.copy()), (None if a is None else a.copy())

    def grab(self, count: int, processed: bool = False, timeout: float = 10.0) -> list[np.ndarray]:
        """Recolhe os próximos N frames consecutivos (foto por empilhamento)."""
        frames: list = []
        ev = threading.Event()
        with self._lock:
            self._grab_requests.append((count, frames, ev, processed))
        ev.wait(timeout)
        return frames

    def jpeg(self, max_w: int = 960, quality: int = 70, max_age: float = 0.08) -> bytes:
        now = time.monotonic()
        if now - self._jpeg_cache[0] < max_age:
            return self._jpeg_cache[1]
        img = self.latest()
        if img is None:
            return b""
        h, w = img.shape[:2]
        if w > max_w:
            img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        data = buf.tobytes() if ok else b""
        self._jpeg_cache = (now, data)
        return data

    # --- ciclo principal ------------------------------------------------------------
    def _open(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        res = self.settings["resolution"]
        fps = int(self.settings["fps"])
        wanted = RES_CANDIDATES if res == "max" else [tuple(int(v) for v in res.split("x"))]
        for w, h in wanted:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw, ah = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if (aw, ah) == (w, h):
                break
        cap.set(cv2.CAP_PROP_FPS, fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def run(self) -> None:
        cap = self._open()
        if cap is None:
            self.failed.emit("Não foi possível abrir a câmara (pode estar a ser usada por outra aplicação).")
            return
        ok, frame = cap.read()
        if not ok:
            self.failed.emit("A câmara abriu mas não envia imagem (em uso por outra aplicação?).")
            cap.release()
            return
        self.size = (frame.shape[1], frame.shape[0])
        self.opened.emit(frame.shape[1], frame.shape[0], cap.get(cv2.CAP_PROP_FPS))
        t_last = time.monotonic()
        frames = 0
        failures = 0
        hist = None
        while self._running:
            ok, frame = cap.read()
            if not ok or frame is None:
                failures += 1
                if failures > 30:
                    self.status.emit("Sinal perdido - a tentar religar a câmara…")
                    cap.release()
                    time.sleep(1.0)
                    cap = self._open()
                    failures = 0
                    if cap is None:
                        self.failed.emit("A câmara foi desligada.")
                        return
                continue
            failures = 0
            ai = getattr(self.tracker, "ai", None) if self.tracker else None
            if ai is not None:
                ai.submit(frame)                  # a IA corre em paralelo; usa o último resultado
                self.pipeline.ai_mask_raw = ai.results.mask if ai.want.get("segment") else None
            box = self.tracker.update(frame) if self.tracker else None
            self.pipeline.subject = box
            self.pipeline.subject_kind = self.tracker.mode if self.tracker else "face"
            out = self.pipeline.process(frame)
            alpha = self.pipeline.alpha
            if alpha is not None and alpha.shape[:2] != out.shape[:2]:
                alpha = cv2.resize(alpha, (out.shape[1], out.shape[0]))
            with self._lock:
                self._latest_raw = frame
                self._latest_out = out
                self._latest_alpha = alpha
                pending = []
                for req in self._grab_requests:
                    count, bucket, ev, processed = req
                    bucket.append((out if processed else frame).copy())
                    if len(bucket) >= count:
                        ev.set()
                    else:
                        pending.append(req)
                self._grab_requests = pending
            bgra = None
            for sink in list(self.sinks):
                try:
                    if getattr(sink, "wants_alpha", False):   # ex.: ProRes 4444 com transparência
                        if bgra is None:
                            a = alpha if alpha is not None else np.full(out.shape[:2], 255, np.uint8)
                            bgra = cv2.merge((*cv2.split(out), a))
                        sink.write(bgra)
                    else:
                        sink.write(out)
                except Exception as exc:  # um destino com erro não pode parar a captura
                    self.status.emit(f"Erro em {type(sink).__name__}: {exc}")
                    self.sinks.remove(sink)
            if not self.emit_preview:
                frames += 1
                now = time.monotonic()
                if now - t_last >= 1.0:
                    self.fps = frames / (now - t_last)
                    frames, t_last = 0, now
                continue
            # pré-visualização
            ph = int(out.shape[0] * self.PREVIEW_W / out.shape[1])
            prev = cv2.resize(out, (self.PREVIEW_W, ph), interpolation=cv2.INTER_AREA)
            ck = self.settings["pipeline"].get("chroma", {})
            if alpha is not None and ck.get("bg") == "transparent" and ck.get("view") == "composite":
                # transparência visível ao operador: xadrez por trás do sujeito
                if self._checker is None or self._checker.shape[:2] != prev.shape[:2]:
                    from .chroma import checkerboard
                    self._checker = checkerboard(prev.shape[1], prev.shape[0])
                a_small = cv2.resize(alpha, (prev.shape[1], prev.shape[0]), interpolation=cv2.INTER_AREA)
                a3 = cv2.merge((a_small, a_small, a_small))
                prev = cv2.add(cv2.multiply(prev, a3, scale=1 / 255.0),
                               cv2.multiply(self._checker, cv2.bitwise_not(a3), scale=1 / 255.0))
            ov = self.settings["overlays"]
            if ov["peaking"]:
                apply_peaking(prev)
            if ov["zebra"]:
                apply_zebra(prev)
            if ov["hist"] and frames % 5 == 0:
                hist = luma_histogram(prev)
            img = QImage(prev.data, prev.shape[1], prev.shape[0], prev.strides[0], QImage.Format.Format_BGR888).copy()
            frames += 1
            now = time.monotonic()
            if now - t_last >= 1.0:
                self.fps = frames / (now - t_last)
                frames = 0
                t_last = now
            out_box = self.pipeline.box_raw_to_out(box) if box else None
            meta = {"box": out_box, "raw_box": box, "hist": hist, "fps": self.fps}
            if self.tracker is not None:
                meta["head"] = self.tracker.head
                meta["state"] = self.tracker.state
                if self.tracker.hands:   # mãos e gestos (coords de saída) para desenhar
                    meta["hands"] = [(self.pipeline.box_raw_to_out(h.box), h.gesture) for h in self.tracker.hands]
            self.frame_ready.emit(img, meta)
        cap.release()
        with self._lock:
            for _, _, ev, _ in self._grab_requests:
                ev.set()
