"""Motor de IA em segundo plano (MediaPipe): mãos/gestos, corpo, rostos e recorte de pessoa.

Corre numa thread própria sobre o frame mais recente (descarta os intermédios), para que
a captura continue a 25–30 fps mesmo com vários modelos ligados. Medido neste portátil:
mãos ~14 ms, corpo ~20 ms, rostos ~2 ms, recorte ~8 ms (imagem de 640 px).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from . import models  # noqa: E402
from .gestures import classify  # noqa: E402

WORK_W = 640

try:  # o MediaPipe é opcional: sem ele a app usa os detetores clássicos
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    AVAILABLE = True
except Exception:  # pragma: no cover - depende da instalação
    mp = None
    vision = None
    AVAILABLE = False


@dataclass
class Hand:
    landmarks: np.ndarray                  # (21, 3) normalizado ao frame
    gesture: str | None
    box: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2] / 2, self.box[1] + self.box[3] / 2)


@dataclass
class Person:
    box: tuple[float, float, float, float]          # corpo visível
    head: tuple[float, float, float, float] | None  # cabeça (para enquadrar com headroom)
    score: float


@dataclass
class AIResults:
    ts: float = 0.0
    hands: list[Hand] = field(default_factory=list)
    persons: list[Person] = field(default_factory=list)
    faces: list[tuple[float, float, float, float]] = field(default_factory=list)
    mask: np.ndarray | None = None          # confiança de pessoa (float32, resolução de trabalho)
    timings: dict[str, float] = field(default_factory=dict)


def _box_from_points(pts: np.ndarray, pad: float = 0.08) -> tuple[float, float, float, float] | None:
    if len(pts) < 2:
        return None
    x0, y0 = pts[:, 0].min(), pts[:, 1].min()
    x1, y1 = pts[:, 0].max(), pts[:, 1].max()
    w, h = x1 - x0, y1 - y0
    x0, y0 = max(0.0, x0 - w * pad), max(0.0, y0 - h * pad)
    x1, y1 = min(1.0, x1 + w * pad), min(1.0, y1 + h * pad)
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return float(x0), float(y0), float(x1 - x0), float(y1 - y0)


class AIWorker(threading.Thread):
    """Recebe frames com `submit()`; publica `results` (sempre o último)."""

    def __init__(self) -> None:
        super().__init__(name="ai-worker", daemon=True)
        self.want = {"hands": False, "pose": False, "face": False, "segment": False}
        self.results = AIResults()
        self.errors: dict[str, str] = {}
        self._frame: np.ndarray | None = None
        self._cond = threading.Condition()
        self._running = True
        self._tasks: dict[str, object] = {}
        self._ts_ms = 0
        if AVAILABLE:
            self.start()

    @property
    def available(self) -> bool:
        return AVAILABLE

    def active(self) -> bool:
        return AVAILABLE and any(self.want.values())

    def submit(self, frame: np.ndarray) -> None:
        if not self.active():
            return
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WORK_W, int(h * WORK_W / w)), interpolation=cv2.INTER_AREA)
        with self._cond:
            self._frame = small
            self._cond.notify()

    def stop(self) -> None:
        self._running = False
        with self._cond:
            self._cond.notify()

    # --- criação preguiçosa dos modelos --------------------------------------------
    def _task(self, key: str):
        if key in self._tasks:
            return self._tasks[key]
        model_key = {"hands": "hand", "pose": "pose", "face": "face", "segment": "segment"}[key]
        p = models.path(model_key)
        if p is None:
            self.errors[key] = f"modelo «{models.MODELS[model_key]['file']}» em falta"
            self._tasks[key] = None
            return None
        base = BaseOptions(model_asset_buffer=p.read_bytes())
        rm = vision.RunningMode.VIDEO
        try:
            if key == "hands":
                t = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                    base_options=base, running_mode=rm, num_hands=4,
                    min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5))
            elif key == "pose":
                t = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                    base_options=base, running_mode=rm, num_poses=4, min_pose_detection_confidence=0.5))
            elif key == "face":
                t = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
                    base_options=base, running_mode=rm, min_detection_confidence=0.5))
            else:
                t = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                    base_options=base, running_mode=rm, output_confidence_masks=True))
            self.errors.pop(key, None)
        except Exception as exc:
            self.errors[key] = str(exc)
            t = None
        self._tasks[key] = t
        return t

    # --- ciclo ------------------------------------------------------------------------
    def run(self) -> None:
        while self._running:
            with self._cond:
                while self._running and self._frame is None:
                    self._cond.wait(0.5)
                frame, self._frame = self._frame, None
            if frame is None or not self._running:
                continue
            try:
                self._process(frame)
            except Exception as exc:  # um erro num modelo não pode matar a thread
                self.errors["worker"] = str(exc)

    def _process(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        self._ts_ms = max(self._ts_ms + 1, int(time.monotonic() * 1000))
        res = AIResults(ts=time.monotonic())
        prev = self.results
        if self.want["hands"] and (t := self._task("hands")):
            t0 = time.perf_counter()
            r = t.detect_for_video(image, self._ts_ms)
            for lms in r.hand_landmarks:
                arr = np.array([[p.x, p.y, p.z] for p in lms], np.float32)
                box = _box_from_points(arr[:, :2], 0.1)
                if box:
                    res.hands.append(Hand(arr, classify(arr), box))
            res.timings["hands"] = (time.perf_counter() - t0) * 1000
        if self.want["pose"] and (t := self._task("pose")):
            t0 = time.perf_counter()
            r = t.detect_for_video(image, self._ts_ms)
            for lms in r.pose_landmarks:
                arr = np.array([[p.x, p.y, getattr(p, "visibility", 1.0) or 0.0] for p in lms], np.float32)
                vis = arr[arr[:, 2] > 0.5][:, :2]
                vis = vis[(vis[:, 0] > -0.05) & (vis[:, 0] < 1.05) & (vis[:, 1] > -0.05) & (vis[:, 1] < 1.05)]
                box = _box_from_points(np.clip(vis, 0, 1), 0.06)
                if box is None:
                    continue
                head_pts = arr[:11][arr[:11, 2] > 0.5][:, :2]  # nariz, olhos, orelhas, boca
                head = None
                if len(head_pts) >= 3:
                    hb = _box_from_points(np.clip(head_pts, 0, 1), 0.0)
                    if hb:  # os pontos da cara são pequenos: alarga para a cabeça inteira
                        cx, cy = hb[0] + hb[2] / 2, hb[1] + hb[3] / 2
                        hw = max(hb[2] * 1.5, 0.02)
                        hh = hw * (w / h) * 1.3  # caixa ~quadrada em píxeis (x e y normalizados diferem)
                        head = (max(0.0, cx - hw / 2), max(0.0, cy - hh * 0.55), hw, hh)
                res.persons.append(Person(box, head, float(np.mean(arr[:, 2]))))
            res.timings["pose"] = (time.perf_counter() - t0) * 1000
        if self.want["face"] and (t := self._task("face")):
            t0 = time.perf_counter()
            r = t.detect_for_video(image, self._ts_ms)
            for d in r.detections:
                bb = d.bounding_box
                res.faces.append((bb.origin_x / w, bb.origin_y / h, bb.width / w, bb.height / h))
            res.timings["face"] = (time.perf_counter() - t0) * 1000
        if self.want["segment"] and (t := self._task("segment")):
            t0 = time.perf_counter()
            r = t.segment_for_video(image, self._ts_ms)
            m = r.confidence_masks[0].numpy_view()
            # CÓPIA obrigatória: numpy_view() aponta para memória do MediaPipe que é libertada com `r`;
            # sem cópia o chroma híbrido lia memória já libertada (crash 0xC0000005 no live_test)
            res.mask = np.array(m.reshape(m.shape[0], m.shape[1]), dtype=np.float32, copy=True)
            res.timings["segment"] = (time.perf_counter() - t0) * 1000
        elif prev.mask is not None and not self.want["segment"]:
            res.mask = None
        self.results = res
