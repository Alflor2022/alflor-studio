"""Chroma key profissional + recorte de pessoa por IA, acelerado pela GPU (OpenCL).

Modos
- "chroma":  recorte pela cor (verde/azul/personalizada) na distância de crominância CbCr,
             como os keyers de estúdio — robusto a sombras e variações de luz no fundo.
- "ai":      recorte da pessoa por IA (MediaPipe), sem ecrã verde.
- "hybrid":  chroma para os contornos finos (cabelo) × máscara de IA para remover tudo o
             que não é pessoa (cadeiras, tripés à frente do ecrã verde).

Afinação: semelhança, suavidade, supressão de spill (reflexo verde na pele/roupa),
contração (choke) e esbatimento (feather) do contorno, garbage matte (corta zonas fora
do ecrã), light wrap (a luz do fundo "envolve" os contornos), calibração automática a
partir da imagem, e vistas Composto / Matte / Original.

Fundo: transparente (alfa para PNG / ProRes 4444 / mesa de realização), cor, imagem,
vídeo em ciclo ou a própria imagem desfocada.

Desempenho: só funções OpenCV, que correm na GPU Intel via OpenCL (UMat) quando existe —
medido na UHD 630: despill + mistura 16,6 ms (GPU) contra 26,8 ms (CPU), resultado idêntico.
A máscara é calculada a meia resolução.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
import numpy as np

DEFAULTS = {
    "enabled": False, "mode": "chroma", "key": "green", "color": [60, 180, 60],
    "similarity": 0.10, "smoothness": 0.06, "spill": 0.7, "choke": 1, "feather": 1.5,
    "garbage": [0.0, 0.0, 0.0, 0.0],        # esquerda, topo, direita, base (fração cortada)
    "bg": "transparent", "bg_color": [40, 30, 20], "bg_path": "", "bg_blur": 0,
    "light_wrap": 0.0, "view": "composite", "gpu": True,
}
KEY_COLORS = {"green": [60, 180, 60], "blue": [200, 80, 30]}   # BGR típicos
BACKGROUNDS = {"transparent": "Transparente (alfa)", "color": "Cor sólida", "image": "Imagem",
               "video": "Vídeo em ciclo", "blur": "Imagem original desfocada"}
MODES = {"chroma": "Chroma key (ecrã verde/azul)", "ai": "Recorte por IA (sem ecrã)",
         "hybrid": "Híbrido (chroma + IA)"}
VIEWS = {"composite": "Composto", "matte": "Matte (alfa)", "original": "Original"}

OPENCL = bool(cv2.ocl.haveOpenCL())
if OPENCL:
    cv2.ocl.setUseOpenCL(True)


def key_crcb(bgr) -> tuple[float, float]:
    px = np.uint8([[list(bgr)]])
    ycc = cv2.cvtColor(px, cv2.COLOR_BGR2YCrCb)[0, 0].astype(np.float32) / 255.0
    return float(ycc[1]), float(ycc[2])


def auto_key(frame: np.ndarray, key: str = "green") -> tuple[list[int], float] | None:
    """Deteta a cor do ecrã (mediana dos píxeis verdes/azuis saturados) e sugere a semelhança.

    Devolve (cor BGR, semelhança recomendada) ou None se não houver ecrã visível.
    """
    small = cv2.resize(frame, (480, int(frame.shape[0] * 480 / frame.shape[1])), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    lo, hi = ((35, 90, 40), (90, 255, 255)) if key != "blue" else ((95, 90, 40), (130, 255, 255))
    m = cv2.inRange(hsv, lo, hi)
    if cv2.countNonZero(m) < 0.05 * m.size:
        return None
    color = [int(np.median(small[..., c][m > 0])) for c in range(3)]
    ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb).astype(np.float32) / 255.0
    kcr, kcb = key_crcb(color)
    d = np.sqrt((ycc[..., 1] - kcr) ** 2 + (ycc[..., 2] - kcb) ** 2)[m > 0]
    # 92 % do ecrã abaixo da semelhança (as zonas mais escuras/claras ficam para a suavidade)…
    sim = float(np.percentile(d, 92) * 1.1 + 0.005)
    # …mas nunca tão larga que apanhe brancos, cinzentos e pele (cor neutra = CrCb 0,5/0,5)
    neutral = float(np.hypot(kcr - 0.5, kcb - 0.5))
    sim = float(np.clip(sim, 0.03, 0.55 * neutral))
    return color, sim


def _get(x):
    return x.get() if isinstance(x, cv2.UMat) else x


def _mix(fg, bg, a8):
    """fg·a + bg·(1−a) em aritmética de 8 bits; funciona em numpy (CPU) ou UMat (GPU)."""
    a3 = cv2.merge((a8, a8, a8))
    return cv2.add(cv2.multiply(fg, a3, scale=1 / 255.0), cv2.multiply(bg, cv2.bitwise_not(a3), scale=1 / 255.0))


class ChromaKeyer:
    def __init__(self, settings: dict) -> None:
        self.s = settings
        for k, v in DEFAULTS.items():
            self.s.setdefault(k, v)
        self.alpha: np.ndarray | None = None       # último alfa (uint8, resolução de saída)
        self.ai_mask: np.ndarray | None = None     # máscara de pessoa da IA (float32, qualquer resolução)
        self._bg_cache: tuple | None = None
        self._garbage_cache: tuple | None = None
        self._lw_cache: tuple | None = None
        self._bg_lock = threading.Lock()
        self._video: cv2.VideoCapture | None = None
        self._video_path = ""
        self.last_ms = 0.0
        self.backend = "GPU (OpenCL)" if OPENCL else "CPU"

    @property
    def transparent(self) -> bool:
        return self.s["enabled"] and self.s["bg"] == "transparent"

    def _gpu(self) -> bool:
        return OPENCL and bool(self.s.get("gpu", True))

    # --- máscara -------------------------------------------------------------------------
    def _matte(self, x, w: int, h: int):
        """Alfa uint8 (0 = fundo, 255 = sujeito) à resolução de saída; x pode ser UMat."""
        s = self.s
        sw, sh = w // 2, h // 2
        small = cv2.resize(x, (sw, sh), interpolation=cv2.INTER_AREA)
        mode = s["mode"]
        a = None
        if mode in ("chroma", "hybrid"):
            # a cor efetiva está sempre em s["color"] (a UI põe lá o verde/azul típico ou a cor medida)
            kcr, kcb = key_crcb(s.get("color") or KEY_COLORS.get(s["key"], KEY_COLORS["green"]))
            _, cr, cb = cv2.split(cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb))
            crf = cv2.subtract(cr, (kcr * 255.0, 0, 0, 0), dtype=cv2.CV_32F)
            cbf = cv2.subtract(cb, (kcb * 255.0, 0, 0, 0), dtype=cv2.CV_32F)
            dist = cv2.magnitude(crf, cbf)                          # 0..~180 (escala 0–255)
            sim, smooth = float(s["similarity"]) * 255, max(0.5, float(s["smoothness"]) * 255)
            k = 255.0 / smooth
            a = cv2.convertScaleAbs(dist, alpha=k, beta=-sim * k)   # |(d−sim)/smooth|·255, saturado
            a = cv2.bitwise_and(a, cv2.compare(dist, sim, cv2.CMP_GT))  # abaixo da semelhança = 0
        if mode in ("ai", "hybrid"):
            m = self.ai_mask
            if m is not None:
                m8 = cv2.convertScaleAbs(cv2.resize(m, (sw, sh), interpolation=cv2.INTER_LINEAR), alpha=255.0)
                ai = cv2.convertScaleAbs(m8, alpha=2.0, beta=-64)   # (m−0,25)/0,5, saturado 0..255
                ai = cv2.bitwise_and(ai, cv2.compare(m8, 64, cv2.CMP_GT))
                if mode == "hybrid":
                    # a IA decide ONDE está a pessoa (dilatada para não cortar cabelo); o chroma faz o contorno
                    k = max(3, sw // 60) | 1
                    ai = cv2.dilate(ai, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
                    ai = cv2.GaussianBlur(ai, (0, 0), k / 2)
                    if isinstance(small, cv2.UMat):
                        ai = cv2.UMat(ai)
                    a = cv2.multiply(a, ai, scale=1 / 255.0) if a is not None else ai
                else:
                    a = ai
            elif a is None:
                a = np.full((sh, sw), 255, np.uint8)  # IA ainda sem resultado: mostra tudo
        choke = int(s["choke"])
        if choke > 0:
            a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * choke + 1, 2 * choke + 1)))
        feather = float(s["feather"])
        if feather > 0:
            a = cv2.GaussianBlur(a, (0, 0), feather)
        g = tuple(float(v) for v in s["garbage"])
        if any(g):  # garbage matte: tudo fora do retângulo é fundo
            if self._garbage_cache is None or self._garbage_cache[0] != (g, sw, sh):
                gm = np.zeros((sh, sw), np.uint8)
                gm[int(g[1] * sh): sh - int(g[3] * sh), int(g[0] * sw): sw - int(g[2] * sw)] = 255
                self._garbage_cache = ((g, sw, sh), gm)
            gm = self._garbage_cache[1]
            a = cv2.bitwise_and(a, cv2.UMat(gm) if isinstance(a, cv2.UMat) else gm)
        return cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)

    def matte(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        return _get(self._matte(cv2.UMat(frame) if self._gpu() else frame, w, h))

    # --- spill ---------------------------------------------------------------------------
    def _despill(self, x):
        amt = float(self.s["spill"])
        if amt <= 0 or self.s["mode"] == "ai":
            return x
        b, g, r = cv2.split(x)
        color = self.s.get("color") or KEY_COLORS.get(self.s["key"], KEY_COLORS["green"])
        dominant = int(np.argmax(color))  # 0=B (azul), 1=G (verde), 2=R
        if dominant == 1:
            g = cv2.subtract(g, cv2.convertScaleAbs(cv2.subtract(g, cv2.max(r, b)), alpha=amt))
        elif dominant == 0:
            b = cv2.subtract(b, cv2.convertScaleAbs(cv2.subtract(b, cv2.max(r, g)), alpha=amt))
        else:
            r = cv2.subtract(r, cv2.convertScaleAbs(cv2.subtract(r, cv2.max(g, b)), alpha=amt))
        return cv2.merge((b, g, r))

    def despill(self, frame: np.ndarray) -> np.ndarray:
        return _get(self._despill(cv2.UMat(frame) if self._gpu() else frame))

    # --- fundo ---------------------------------------------------------------------------
    def background(self, frame: np.ndarray) -> np.ndarray:
        """Fundo à resolução do frame (numpy). Imagens e cores ficam em cache."""
        with self._bg_lock:   # o leitor de vídeo e as caches não são seguros entre threads
            return self._background(frame)

    def _background(self, frame: np.ndarray) -> np.ndarray:
        s = self.s
        h, w = frame.shape[:2]
        kind = s["bg"]
        if kind == "blur":
            small = cv2.resize(frame, (w // 8, h // 8), interpolation=cv2.INTER_AREA)
            return cv2.resize(cv2.GaussianBlur(small, (0, 0), 4), (w, h), interpolation=cv2.INTER_LINEAR)
        if kind == "video" and s["bg_path"]:
            if self._video is None or self._video_path != s["bg_path"]:
                if self._video is not None:
                    self._video.release()
                self._video = cv2.VideoCapture(s["bg_path"])
                self._video_path = s["bg_path"]
            ok, img = self._video.read()
            if not ok:
                self._video.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, img = self._video.read()
            if ok:
                img = _cover(img, w, h)
                if int(s["bg_blur"]):
                    img = cv2.GaussianBlur(img, (0, 0), int(s["bg_blur"]))
                return img
        key = (kind, s["bg_path"] if kind == "image" else "", tuple(s["bg_color"]), w, h, int(s["bg_blur"]))
        if self._bg_cache is None or self._bg_cache[0] != key:
            img = None
            if kind == "image" and s["bg_path"]:
                img = cv2.imdecode(np.fromfile(s["bg_path"], np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    img = _cover(img, w, h)
                    if int(s["bg_blur"]):
                        img = cv2.GaussianBlur(img, (0, 0), int(s["bg_blur"]))
            if img is None:
                color = (0, 0, 0) if kind == "transparent" else tuple(int(c) for c in s["bg_color"])
                img = np.full((h, w, 3), color, np.uint8)
            self._bg_cache = (key, img, cv2.UMat(img) if OPENCL else None)
        return self._bg_cache[1]

    # --- composição -------------------------------------------------------------------------
    def process(self, frame: np.ndarray) -> np.ndarray:
        """Devolve o frame composto (numpy BGR); o alfa fica em `self.alpha`."""
        return self.process_pair(frame)[0]

    def process_pair(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(frame composto, alfa) — seguro para várias threads em paralelo."""
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        gpu = self._gpu()
        x = cv2.UMat(frame) if gpu else frame
        a8 = self._matte(x, w, h)
        view = self.s["view"]
        if view == "matte":
            out = cv2.cvtColor(a8, cv2.COLOR_GRAY2BGR)
        elif view == "original":
            out = x
        else:
            fg = self._despill(x)
            bg = self.background(frame)
            if gpu:
                bg = self._bg_cache[2] if self._bg_cache and self._bg_cache[1] is bg and self._bg_cache[2] is not None else cv2.UMat(bg)
            out = _mix(fg, bg, a8)
            lw = float(self.s["light_wrap"])
            if lw > 0 and self.s["bg"] != "transparent":
                # light wrap: a luz do fundo entra ligeiramente nos contornos do sujeito
                qw, qh = w // 4, h // 4
                sa = cv2.resize(a8, (qw, qh), interpolation=cv2.INTER_AREA)
                inv_blur = cv2.bitwise_not(cv2.GaussianBlur(sa, (0, 0), 3))
                edge = cv2.multiply(inv_blur, sa, scale=2.0 * lw / 255.0)   # máximo junto ao contorno
                edge = cv2.min(edge, 150)
                e8 = cv2.resize(edge, (w, h), interpolation=cv2.INTER_LINEAR)
                static = self.s["bg"] in ("color", "image")
                if static and self._lw_cache is not None and self._lw_cache[0] == self._bg_cache[0]:
                    bgb = self._lw_cache[1]           # fundo fixo: o desfocado fica em cache
                else:
                    bgb = cv2.resize(cv2.GaussianBlur(cv2.resize(bg, (qw, qh)), (0, 0), 3), (w, h))
                    if static:
                        self._lw_cache = (self._bg_cache[0], bgb)
                out = _mix(bgb, out, e8)
        alpha = _get(a8)
        out = _get(out)
        self.alpha = alpha
        self.last_ms = (time.perf_counter() - t0) * 1000
        return out, alpha

    def close(self) -> None:
        if self._video is not None:
            self._video.release()
            self._video = None


def _cover(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Redimensiona a imagem para cobrir w×h sem deformar (recorta o excesso)."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    r = cv2.resize(img, (int(iw * s + 0.5), int(ih * s + 0.5)), interpolation=cv2.INTER_AREA)
    y0, x0 = (r.shape[0] - h) // 2, (r.shape[1] - w) // 2
    return np.ascontiguousarray(r[y0:y0 + h, x0:x0 + w])


def checkerboard(w: int, h: int, cell: int = 16) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((xx // cell) + (yy // cell)) % 2).astype(np.uint8)
    img = np.where(c[..., None] == 1, np.uint8(170), np.uint8(110))
    return np.repeat(img, 3, axis=2)


def path_ok(p: str) -> bool:
    return bool(p) and Path(p).exists()
