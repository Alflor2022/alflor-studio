"""Tracking de sujeito: rosto, pessoa ou objeto escolhido pelo utilizador.

Corre na thread de captura sobre uma cópia reduzida do frame. Devolve a caixa do
sujeito em coordenadas normalizadas do frame original ("raw"). O enquadramento
(físico ou digital) é feito pelo AutoFramer na thread da interface.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .config import LOCAL_DATA

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_PATH = LOCAL_DATA / "models" / "face_detection_yunet_2023mar.onnx"

# Tamanho do rosto (fração da altura do frame) por tipo de plano
SHOT_FACE = {
    "Grande plano": 0.42,
    "Primeiro plano": 0.27,
    "Plano médio": 0.15,
    "Plano americano": 0.10,
    "Plano geral": 0.06,
}
# Tamanho do corpo/objeto (fração da altura) por tipo de plano
SHOT_BODY = {
    "Grande plano": 1.6,
    "Primeiro plano": 1.25,
    "Plano médio": 0.95,
    "Plano americano": 0.75,
    "Plano geral": 0.45,
}
TARGETS = {  # ponto de composição (x, y) para o centro do sujeito
    "center": (0.5, 0.5),
    "left_third": (1 / 3, 0.5),
    "right_third": (2 / 3, 0.5),
}


def download_yunet() -> Path:
    YUNET_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = YUNET_PATH.with_suffix(".part")
    urllib.request.urlretrieve(YUNET_URL, tmp)
    tmp.replace(YUNET_PATH)
    return YUNET_PATH


def _make_tracker():
    for name in ("TrackerCSRT_create", "TrackerKCF_create", "TrackerMIL_create"):
        factory = getattr(cv2, name, None)
        if factory:
            return factory()
    raise RuntimeError("Nenhum tracker OpenCV disponível")


class SubjectTracker:
    WORK_W = 640

    def __init__(self) -> None:
        self.enabled = False
        self.mode = "face"                 # "face" | "person" | "subject"
        self.box: tuple[float, float, float, float] | None = None
        self.last_seen = 0.0
        self._lock = threading.Lock()
        self._frame_i = 0
        self._tracker = None
        self._pending_select: tuple[float, float, float, float] | None = None
        cascades = Path(cv2.data.haarcascades)
        self._haar = cv2.CascadeClassifier(str(cascades / "haarcascade_frontalface_default.xml"))
        self._haar_profile = cv2.CascadeClassifier(str(cascades / "haarcascade_profileface.xml"))
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._yunet = None
        self._yunet_size = (0, 0)
        self.load_yunet()

    @property
    def detector_name(self) -> str:
        if self.mode == "face":
            return "IA YuNet" if self._yunet is not None else "Haar (clássico)"
        return {"person": "HOG + CSRT", "subject": "CSRT"}.get(self.mode, "-")

    def load_yunet(self) -> bool:
        if YUNET_PATH.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (320, 240), 0.75, 0.3, 50)
                return True
            except cv2.error:
                self._yunet = None
        return False

    # --- controlo (thread da UI) -----------------------------------------------------
    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self._tracker = None
            self.box = None

    def select(self, box: tuple[float, float, float, float]) -> None:
        """Seleção manual (coordenadas raw normalizadas) -> segue esse objeto."""
        with self._lock:
            self.mode = "subject"
            self._pending_select = box
            self.enabled = True

    def reset(self) -> None:
        with self._lock:
            self._tracker = None
            self.box = None

    # --- processamento (thread de captura) ------------------------------------------
    def update(self, frame: np.ndarray) -> tuple[float, float, float, float] | None:
        if not self.enabled:
            return None
        h, w = frame.shape[:2]
        scale = self.WORK_W / w
        small = cv2.resize(frame, (self.WORK_W, int(h * scale)), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        self._frame_i += 1
        with self._lock:
            mode = self.mode
            pending, self._pending_select = self._pending_select, None
        found = None
        if mode == "subject":
            if pending:
                x, y, bw, bh = pending
                self._tracker = _make_tracker()
                self._tracker.init(small, (int(x * sw), int(y * sh), max(4, int(bw * sw)), max(4, int(bh * sh))))
            if self._tracker is not None:
                ok, r = self._tracker.update(small)
                if ok:
                    found = (r[0] / sw, r[1] / sh, r[2] / sw, r[3] / sh)
        elif mode == "face":
            if self._frame_i % 2 == 0 or self.box is None:
                found = self._detect_face(small)
            else:
                found = self.box  # mantém entre deteções
        elif mode == "person":
            if self._frame_i % 6 == 0 or self._tracker is None:
                det = self._detect_person(small)
                if det:
                    found = det
                    self._tracker = _make_tracker()
                    self._tracker.init(small, tuple(int(v) for v in (det[0] * sw, det[1] * sh, det[2] * sw, det[3] * sh)))
            if found is None and self._tracker is not None:
                ok, r = self._tracker.update(small)
                if ok:
                    found = (r[0] / sw, r[1] / sh, r[2] / sw, r[3] / sh)
        if found:
            self.last_seen = time.monotonic()
            if self.box is not None:
                a = 0.45  # suavização exponencial da caixa
                found = tuple(p + (n - p) * a for p, n in zip(self.box, found))
            self.box = found
        elif time.monotonic() - self.last_seen > 1.0:
            self.box = None
        return self.box

    def _pick(self, boxes: list[tuple[float, float, float, float]]):
        if not boxes:
            return None
        if self.box is not None:  # continuidade: o mais próximo do sujeito anterior
            px, py = self.box[0] + self.box[2] / 2, self.box[1] + self.box[3] / 2
            return min(boxes, key=lambda b: (b[0] + b[2] / 2 - px) ** 2 + (b[1] + b[3] / 2 - py) ** 2)
        return max(boxes, key=lambda b: b[2] * b[3])

    def _detect_face(self, small: np.ndarray):
        sh, sw = small.shape[:2]
        if self._yunet is not None:
            if self._yunet_size != (sw, sh):
                self._yunet.setInputSize((sw, sh))
                self._yunet_size = (sw, sh)
            _, faces = self._yunet.detect(small)
            boxes = [] if faces is None else [(f[0] / sw, f[1] / sh, f[2] / sw, f[3] / sh) for f in faces]
            return self._pick(boxes)
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        rects = list(self._haar.detectMultiScale(gray, 1.15, 5, minSize=(22, 22)))
        if not rects:
            rects = list(self._haar_profile.detectMultiScale(gray, 1.15, 5, minSize=(22, 22)))
        return self._pick([(x / sw, y / sh, w / sw, h / sh) for x, y, w, h in rects])

    def _detect_person(self, small: np.ndarray):
        sh, sw = small.shape[:2]
        rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.06)
        boxes = [(x / sw, y / sh, w / sw, h / sh) for (x, y, w, h), wt in zip(rects, np.ravel(weights)) if wt > 0.4]
        return self._pick(boxes)


class AutoFramer:
    """Converte a posição do sujeito em movimentos PTZ (físicos ou digitais)."""

    def __init__(self, settings: dict, ptz, pipeline) -> None:
        self.settings = settings  # settings["tracking"]
        self.ptz = ptz
        self.pipeline = pipeline
        self.calibration = {"pan_fw": 1100.0, "tilt_fh": 620.0, "zoom_max_mag": 3.0}
        self._last_cmd = 0.0
        self._last_zoom = 0.0
        self._lost_since = 0.0
        self._settle_until = 0.0  # PTZ físico: a imagem chega atrasada face ao motor

    def magnification(self) -> float:
        if not self.ptz.has("zoom"):
            return 1.0
        z = self.ptz.axes["zoom"]
        frac = (z.value - z.min) / max(1e-6, z.span)
        return 1.0 + frac * (self.calibration["zoom_max_mag"] - 1.0)

    def target_point(self, mode: str) -> tuple[float, float]:
        tx, ty = TARGETS.get(self.settings["target"], (0.5, 0.5))
        if mode == "face":
            ty = 0.36  # espaço acima da cabeça (headroom) correto
        return tx, ty

    def step(self, box, mode: str) -> None:
        now = time.monotonic()
        if box is None:
            if self.settings.get("lost_home") and self._lost_since and now - self._lost_since > 4:
                self.ptz.home()
                self._lost_since = 0
            elif not self._lost_since:
                self._lost_since = now
            return
        self._lost_since = 0
        sens = float(self.settings["sensitivity"])
        interval = 0.22 - 0.12 * sens  # mais sensível = corrige mais vezes
        if now - self._last_cmd < interval or now < self._settle_until:
            return
        self._last_cmd = now
        x, y, w, h = box
        cx, cy = x + w / 2, y + h / 2
        if self.ptz.is_physical:
            # o sujeito está em coordenadas raw; o PTZ físico atua no frame raw
            ox, oy = cx, cy
        else:
            ox, oy = self.pipeline.raw_to_out(cx, cy)
        tx, ty = self.target_point(mode)
        ex, ey = ox - tx, oy - ty
        dead = 0.07 - 0.04 * sens
        gain = 0.25 + 0.35 * sens
        mag = self.magnification()
        if self.ptz.is_physical:
            # Malha aberta com atraso: corrige uma fração do erro, no máximo 12% do ecrã por passo,
            # e espera o motor chegar + a imagem estabilizar antes de medir outra vez.
            # (Sem isto as correções acumulam-se, a câmara passa do ponto e o tracker escorrega.)
            gain = 0.35 + 0.25 * sens
            pfw = self.calibration["pan_fw"] / mag
            tfh = self.calibration["tilt_fh"] / mag
            ex = max(-0.12, min(0.12, ex)) if abs(ex) > dead else 0.0
            ey = max(-0.12, min(0.12, ey)) if abs(ey) > dead else 0.0
            # calibração com sinal: pan_fw = unidades por largura de frame (sentido incluído)
            dpan = ex * pfw * gain
            dtilt = -ey * tfh * gain
            if dpan or dtilt:
                self._settle_until = now + 0.45 + max(abs(dpan) / 500.0, abs(dtilt) / 300.0)
        else:
            scale = self.pipeline.crop_rect()[2]
            free = max(1e-3, 0.5 - 0.5 * scale)
            dpan = ex * scale / free * 1000 * gain if abs(ex) > dead else 0.0
            dtilt = ey * scale / free * 1000 * gain if abs(ey) > dead else 0.0
        if dpan or dtilt:
            self.ptz.nudge(dpan=dpan, dtilt=dtilt)
        if self.settings.get("auto_zoom") and self.ptz.has("zoom") and now - self._last_zoom > 0.6:
            table = SHOT_FACE if mode == "face" else SHOT_BODY
            want = table.get(self.settings["shot"], 0.15)
            have = h if self.ptz.is_physical else h / max(1e-3, self.pipeline.crop_rect()[3])
            ratio = want / max(1e-3, have)
            if abs(ratio - 1) > 0.18:
                self._last_zoom = now
                z = self.ptz.axes["zoom"]
                step = z.span * 0.03 * (1 if ratio > 1 else -1)
                self.ptz.nudge(dzoom=step)
