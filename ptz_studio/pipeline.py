"""Processamento de imagem em tempo real e ferramentas de monitorização.

Ordem: recorte PTZ digital -> espelho -> modo noite -> redução de ruído temporal ->
chroma key / recorte IA -> nitidez -> look de cor -> desfoco de fundo. Tudo com operações
OpenCV rápidas para manter 1080p a 25–30 fps num portátil; o chroma corre numa thread
própria (1 frame de atraso) para não baixar os fps.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from .chroma import ChromaKeyer
from .grading import Grader


class AsyncStage:
    """Executa `fn(frame)` em `workers` threads (frames alternados) e entrega sempre o
    resultado mais recente já pronto (pipelining). O OpenCV liberta o GIL, por isso há
    paralelismo real. `fps` mede quantos resultados NOVOS saem por segundo (o que o
    espectador realmente vê), que pode ser inferior ao fps da câmara se o trabalho for pesado.
    """

    def __init__(self, fn, workers: int = 2) -> None:
        self.fn = fn
        self._cond = threading.Condition()
        self._pending: list = []          # [(seq, frame)] — no máximo `workers`, descarta os mais antigos
        self._result = None
        self._result_seq = -1
        self._seq = 0
        self._workers = workers
        self._running = True
        self.fps = 0.0
        self._count, self._t0 = 0, time.monotonic()
        for i in range(workers):
            threading.Thread(target=self._loop, name=f"chroma-stage-{i}", daemon=True).start()

    def exchange(self, x):
        with self._cond:
            first = self._result is None
            self._seq += 1
            self._pending.append((self._seq, x))
            if len(self._pending) > self._workers:
                self._pending.pop(0)
            self._cond.notify()
            res = self._result
        return self.fn(x) if first else res   # 1.º frame: síncrono (sem imagem em falta)

    def reset(self) -> None:
        with self._cond:
            self._pending.clear()
            self._result = None
            self._result_seq = -1

    def _loop(self) -> None:
        while self._running:
            with self._cond:
                while not self._pending and self._running:
                    self._cond.wait(0.5)
                if not self._pending:
                    continue
                seq, x = self._pending.pop()     # o mais recente primeiro (menos atraso)
            try:
                r = self.fn(x)
            except Exception:  # nunca matar a thread por um frame
                continue
            with self._cond:
                if seq > self._result_seq:       # nunca voltar atrás no tempo
                    self._result, self._result_seq = r, seq
                    self._count += 1
                now = time.monotonic()
                if now - self._t0 >= 1.0:
                    self.fps = self._count / (now - self._t0)
                    self._count, self._t0 = 0, now

LUMA = np.array([0.114, 0.587, 0.299], np.float32)  # pesos BGR


def _sat_matrix(s: float) -> np.ndarray:
    gray = np.tile(LUMA, (3, 1))
    return (1 - s) * gray + s * np.eye(3, dtype=np.float32)


def _curve_lut(contrast: float = 1.0, lift: float = 0.0) -> np.ndarray:
    x = np.arange(256, dtype=np.float32) / 255.0
    y = 0.5 + (x - 0.5) * contrast
    y = lift + y * (1 - lift)
    return np.clip(y * 255, 0, 255).astype(np.uint8)


def _scurve_lut(strength: float) -> np.ndarray:
    x = np.arange(256, dtype=np.float32) / 255.0
    s = x * x * (3 - 2 * x)
    y = x + (s - x) * strength
    return np.clip(y * 255, 0, 255).astype(np.uint8)


LOOKS: dict[str, tuple[np.ndarray | None, np.ndarray | None]] = {
    "Natural": (None, None),
    "Vivo": (_sat_matrix(1.3), _scurve_lut(0.35)),
    "Quente": (np.diag([0.90, 1.0, 1.10]).astype(np.float32), None),
    "Frio": (np.diag([1.10, 1.0, 0.92]).astype(np.float32), None),
    "Cinema": (np.array([[1.08, 0.04, -0.06], [0.0, 1.0, 0.0], [-0.06, 0.02, 1.06]], np.float32) @ _sat_matrix(0.9), _scurve_lut(0.6)),
    "Suave": (_sat_matrix(0.9), _curve_lut(0.85, 0.04)),
    "Preto e branco": (np.tile(LUMA, (3, 1)).astype(np.float32), _scurve_lut(0.4)),
    "Alto contraste": (None, _scurve_lut(1.0)),
}


class Pipeline:
    def __init__(self, settings: dict) -> None:
        self.settings = settings                 # referência a settings["pipeline"]
        self._prev: np.ndarray | None = None
        self._lock = threading.Lock()
        self._eptz = (0.5, 0.5, 1.0)             # centro x, centro y (normalizados) e escala
        self._eptz_smooth = [0.5, 0.5, 1.0]
        self.subject: tuple[float, float, float, float] | None = None  # caixa em coords "raw"
        self.subject_kind = "face"
        self._gain_cache: dict[float, np.ndarray] = {}
        self.frame_size = (1920, 1080)
        self.chroma = ChromaKeyer(settings.setdefault("chroma", {}))
        self.grader = Grader(settings.setdefault("grade", {}))   # ajuste automático / igualar referência
        self.alpha: np.ndarray | None = None      # alfa do chroma do último frame (ou None)
        self.ai_mask_raw: np.ndarray | None = None  # máscara de pessoa da IA (coords raw)
        self._chroma_stage = AsyncStage(self._chroma_pair)
        self._chroma_was_on = False

    def _chroma_pair(self, frame: np.ndarray):
        return self.chroma.process_pair(frame)   # thread-safe: o alfa vem com a imagem

    @property
    def chroma_fps(self) -> float:
        """Imagens recortadas novas por segundo (o que realmente sai)."""
        return self._chroma_stage.fps

    def _aligned_ai_mask(self) -> np.ndarray | None:
        """Máscara da IA (calculada no frame raw) recortada/espelhada como a imagem de saída."""
        m = self.ai_mask_raw
        if m is None:
            return None
        h, w = m.shape[:2]
        x0, y0, cw, ch = self.crop_rect()
        if cw < 0.999:
            m = m[int(y0 * h): int((y0 + ch) * h), int(x0 * w): int((x0 + cw) * w)]
        if self.settings["mirror"] and self.settings["flip"]:
            m = cv2.flip(m, -1)
        elif self.settings["mirror"]:
            m = cv2.flip(m, 1)
        elif self.settings["flip"]:
            m = cv2.flip(m, 0)
        return np.ascontiguousarray(m)

    # --- PTZ digital -----------------------------------------------------------
    def set_eptz(self, cx: float, cy: float, scale: float) -> None:
        with self._lock:
            self._eptz = (cx, cy, max(1.0, scale))

    def crop_rect(self) -> tuple[float, float, float, float]:
        """Retângulo de recorte atual (x0, y0, w, h) normalizado no frame original."""
        cx, cy, s = self._eptz_smooth
        w = h = 1.0 / s
        x0 = min(max(cx - w / 2, 0.0), 1.0 - w)
        y0 = min(max(cy - h / 2, 0.0), 1.0 - h)
        return x0, y0, w, h

    def raw_to_out(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, w, h = self.crop_rect()
        u, v = (x - x0) / w, (y - y0) / h
        if self.settings["mirror"]:
            u = 1 - u
        if self.settings["flip"]:
            v = 1 - v
        return u, v

    def out_to_raw(self, u: float, v: float) -> tuple[float, float]:
        if self.settings["mirror"]:
            u = 1 - u
        if self.settings["flip"]:
            v = 1 - v
        x0, y0, w, h = self.crop_rect()
        return x0 + u * w, y0 + v * h

    def box_raw_to_out(self, box):
        x, y, w, h = box
        (u0, v0), (u1, v1) = self.raw_to_out(x, y), self.raw_to_out(x + w, y + h)
        return min(u0, u1), min(v0, v1), abs(u1 - u0), abs(v1 - v0)

    # --- processamento ---------------------------------------------------------
    def process(self, frame: np.ndarray) -> np.ndarray:
        s = self.settings
        h, w = frame.shape[:2]
        self.frame_size = (w, h)
        frame = self._apply_eptz(frame)
        if s["mirror"] and s["flip"]:
            frame = cv2.flip(frame, -1)
        elif s["mirror"]:
            frame = cv2.flip(frame, 1)
        elif s["flip"]:
            frame = cv2.flip(frame, 0)
        if s["night"]:
            frame = cv2.LUT(frame, self._gain_lut(float(s["night_gain"])))
        level = int(s["denoise"]) + (1 if s["night"] else 0)
        gr = s.get("grade", {})
        grading = gr.get("mode", "off") != "off"
        if grading and gr.get("mode") == "auto" and gr.get("auto_denoise", True):
            level = max(level, self.grader.suggested_denoise)   # pouca luz → mais redução de ruído
        if level > 0:
            frame = self._temporal_denoise(frame, min(level, 3))
        else:
            self._prev = None
        if grading:
            self.grader.maybe_update(frame)   # analisa antes do chroma (a cena real, não o fundo novo)
        ck = s.get("chroma", {})
        if ck.get("enabled"):
            self.chroma.ai_mask = self._aligned_ai_mask()
            if not self._chroma_was_on:
                self._chroma_stage.reset()
            frame, self.alpha = self._chroma_stage.exchange(frame)
            self._chroma_was_on = True
        else:
            self.alpha = None
            self._chroma_was_on = False
        amt = float(s["sharpen"])
        if amt > 0:
            blur = cv2.GaussianBlur(frame, (0, 0), 1.3)
            frame = cv2.addWeighted(frame, 1 + amt, blur, -amt, 0)
        # gravação Log: guarda a imagem ANTES do ajuste e do look (neutra, para color grading)
        self.neutral = frame if getattr(self, "want_neutral", False) else None
        if grading:
            frame = self.grader.apply(frame)
        matrix, lut = LOOKS.get(s["look"], (None, None))
        if matrix is not None:
            frame = cv2.transform(frame, matrix)
        if lut is not None:
            frame = cv2.LUT(frame, lut)
        if s["bg_blur"] and self.subject is not None and self.alpha is None:
            frame = self._background_blur(frame)
        return frame

    def _apply_eptz(self, frame: np.ndarray) -> np.ndarray:
        with self._lock:
            target = self._eptz
        sm = self._eptz_smooth
        for i in range(3):  # suavização tipo "steadicam"
            sm[i] += (target[i] - sm[i]) * 0.18
            if abs(target[i] - sm[i]) < 1e-4:
                sm[i] = target[i]
        if sm[2] <= 1.0005:
            return frame
        h, w = frame.shape[:2]
        x0, y0, cw, ch = self.crop_rect()
        x, y = int(x0 * w), int(y0 * h)
        crop = frame[y:y + max(2, int(ch * h)), x:x + max(2, int(cw * w))]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

    def _gain_lut(self, gain: float) -> np.ndarray:
        key = round(gain, 2)
        if key not in self._gain_cache:
            x = np.arange(256, dtype=np.float32) / 255.0
            y = 1.0 - np.power(1.0 - x, gain)  # levanta sombras sem queimar altas luzes
            self._gain_cache[key] = np.clip(y * 255, 0, 255).astype(np.uint8)
        return self._gain_cache[key]

    def _temporal_denoise(self, frame: np.ndarray, level: int) -> np.ndarray:
        prev = self._prev
        if prev is None or prev.shape != frame.shape:
            self._prev = frame.copy()
            return frame
        alpha = (0.0, 0.5, 0.65, 0.8)[level]
        thr = (0, 10, 16, 24)[level]
        diff = cv2.cvtColor(cv2.absdiff(frame, prev), cv2.COLOR_BGR2GRAY)
        _, still = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY_INV)  # zonas sem movimento
        blended = cv2.addWeighted(prev, alpha, frame, 1 - alpha, 0)
        out = frame.copy()
        cv2.copyTo(blended, still, out)
        self._prev = out
        return out

    def _background_blur(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        sw, sh = w // 4, h // 4
        small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
        blurred = cv2.resize(cv2.GaussianBlur(small, (0, 0), 7), (w, h), interpolation=cv2.INTER_LINEAR)
        bx, by, bw, bh = self.box_raw_to_out(self.subject)
        mask = np.zeros((sh, sw), np.float32)
        if self.subject_kind == "face":   # do rosto estima-se o corpo
            cx, cy = (bx + bw / 2) * sw, (by + bh * 2.6) * sh
            axes = (bw * 1.9 * sw, bh * 3.9 * sh)
        else:
            cx, cy = (bx + bw / 2) * sw, (by + bh / 2) * sh
            axes = (bw * 0.62 * sw, bh * 0.62 * sh)
        cv2.ellipse(mask, (int(cx), int(cy)), (max(1, int(axes[0])), max(1, int(axes[1]))), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), max(1.0, sw * 0.035))
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
        return cv2.blendLinear(frame, blurred, mask, 1.0 - mask)


# --- monitorização (apenas na pré-visualização) ------------------------------------
_zebra_cache: dict[tuple[int, int], np.ndarray] = {}


def apply_zebra(img: np.ndarray, threshold: int = 245) -> None:
    h, w = img.shape[:2]
    stripes = _zebra_cache.get((w, h))
    if stripes is None:
        yy, xx = np.mgrid[0:h, 0:w]
        stripes = (((xx + yy) // 6) % 2 == 0).astype(np.uint8) * 255
        _zebra_cache[(w, h)] = stripes
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, hot = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_and(hot, stripes)
    img[mask > 0] = (255, 0, 255)


def apply_peaking(img: np.ndarray, threshold: int = 40) -> None:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap = cv2.convertScaleAbs(cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_16S, ksize=3))
    img[lap > threshold] = (0, 255, 80)


def luma_histogram(img: np.ndarray, bins: int = 64) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hist = cv2.calcHist([gray], [0], None, [bins], [0, 256]).ravel()
    return hist / max(1.0, hist.max())


def rgb_histogram(img: np.ndarray, bins: int = 64) -> dict:
    """Histograma por canal (B, G, R) + fração de píxeis cortados nas altas luzes e nas sombras."""
    if img.shape[1] > 480:
        img = cv2.resize(img, (480, max(1, int(img.shape[0] * 480 / img.shape[1]))), interpolation=cv2.INTER_NEAREST)
    hs = np.stack([cv2.calcHist([img], [c], None, [bins], [0, 256]).ravel() for c in range(3)])
    hs = np.minimum(hs / max(1.0, float(hs[:, 1:-1].max())), 1.0)   # os extremos não achatam o resto
    return {"rgb": hs, "clip_hi": float((img.max(axis=2) >= 254).mean()),
            "clip_lo": float((img.min(axis=2) <= 1).mean())}


def sharpness_score(img: np.ndarray) -> float:
    """Medida de foco (variância do Laplaciano) - útil para focar manualmente a 5D."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())
