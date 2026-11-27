"""Mesa de realização multi-câmara (inspirada nas mesas ATEM / switchers de estúdio).

Fontes: a câmara principal do Alflor Studio (já com tracking/chroma/looks), outras câmaras
(USB, telemóvel, captura HDMI da Canon), imagens, vídeos em ciclo, cor e barras.

Barramentos PROGRAMA (no ar) e PRÉ-VISUALIZAÇÃO (próxima), CUT, AUTO com transições
(Mistura, Dip a preto/branco, Wipe ▶ / ▼, Push), T-bar manual, Fade to Black,
PiP (imagem na imagem), chave de alfa (a câmara principal com chroma por cima de outra
fonte — cenário virtual), logótipo (DSK) e oráculo (lower third).

O motor corre numa thread a 30 fps e produz: o programa (para gravar, câmara virtual,
telemóvel/VLC) e o multiview (PVW + PGM + todas as entradas com tally vermelho/verde).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

OUT_W, OUT_H = 1920, 1080
TRANSITIONS = {"mix": "MIX", "dip": "DIP", "wipe_lr": "WIPE ▶", "wipe_tb": "WIPE ▼", "push": "PUSH"}
PIP_CORNERS = {"tr": "Canto sup. direito", "tl": "Canto sup. esquerdo", "br": "Canto inf. direito", "bl": "Canto inf. esquerdo"}
MAX_INPUTS = 8


def _fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Encaixa a imagem em w×h (preenche cortando o excesso, sem deformar)."""
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):
        return img
    s = max(w / iw, h / ih)
    r = cv2.resize(img, (max(1, int(iw * s + 0.5)), max(1, int(ih * s + 0.5))),
                   interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    y0, x0 = (r.shape[0] - h) // 2, (r.shape[1] - w) // 2
    return r[y0:y0 + h, x0:x0 + w]


def color_bars(w: int = OUT_W, h: int = OUT_H) -> np.ndarray:
    cols = [(192, 192, 192), (0, 192, 192), (192, 192, 0), (0, 192, 0), (192, 0, 192), (0, 0, 192), (192, 0, 0)]
    img = np.zeros((h, w, 3), np.uint8)
    bw = w // len(cols)
    for i, c in enumerate(cols):
        img[: int(h * 0.75), i * bw:(i + 1) * bw] = c
    img[int(h * 0.75):] = (20, 20, 20)
    return img


# --- fontes -----------------------------------------------------------------------------
class Source:
    kind = "base"

    def __init__(self, name: str) -> None:
        self.name = name
        self.ptz = None            # PTZController se a fonte tiver PTZ

    def frame(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """(imagem BGR, alfa uint8 ou None)."""
        return None, None

    def close(self) -> None:
        pass


class ColorSource(Source):
    kind = "color"

    def __init__(self, name: str, color=(0, 0, 0), bars: bool = False) -> None:
        super().__init__(name)
        self._img = color_bars() if bars else np.full((OUT_H, OUT_W, 3), color, np.uint8)

    def frame(self):
        return self._img, None


class ImageSource(Source):
    kind = "image"

    def __init__(self, name: str, path: str) -> None:
        super().__init__(name)
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"não foi possível abrir {path}")
        self._alpha = None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:  # PNG com transparência mantém o alfa
            self._alpha = _fit(img[..., 3], OUT_W, OUT_H).copy()
            img = img[..., :3]
        self._img = _fit(img, OUT_W, OUT_H).copy()
        self.path = path

    def frame(self):
        return self._img, self._alpha


class VideoSource(Source):
    """Vídeo em ciclo, ao ritmo do próprio ficheiro (thread própria)."""
    kind = "video"

    def __init__(self, name: str, path: str) -> None:
        super().__init__(name)
        self.path = path
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise ValueError(f"não foi possível abrir {path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30
        self._img = None
        self._running = True
        threading.Thread(target=self._loop, name=f"video:{name}", daemon=True).start()

    def _loop(self) -> None:
        period = 1 / max(1.0, self._fps)
        nxt = time.monotonic()
        while self._running:
            ok, img = self._cap.read()
            if not ok:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self._img = _fit(img, OUT_W, OUT_H)
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 1:
                nxt = time.monotonic()

    def frame(self):
        return self._img, None

    def close(self) -> None:
        self._running = False
        time.sleep(0.05)
        self._cap.release()


class CallableSource(Source):
    """Fonte alimentada por uma função (ex.: a câmara principal já processada)."""
    kind = "main"

    def __init__(self, name: str, getter, ptz=None) -> None:
        super().__init__(name)
        self._getter = getter
        self.ptz = ptz

    def frame(self):
        return self._getter()


class CameraSource(Source):
    """Outra câmara (USB, telemóvel, captura HDMI) com captura própria e PTZ se tiver motores."""
    kind = "camera"

    def __init__(self, name: str, index: int, settings: dict) -> None:
        super().__init__(name)
        from .capture import CaptureThread
        from .pipeline import Pipeline
        self.device = name
        self.settings = settings
        self.pipeline = Pipeline(settings["pipeline"])
        self.capture = CaptureThread(index, settings, self.pipeline, None, emit_preview=False)
        self.capture.start()
        self.bridge = None
        try:  # PTZ desta câmara (ponte DirectShow própria, independente da principal)
            from .bridge import Bridge
            from .ptz import PhysicalBackend, PTZController
            b = Bridge()
            info = b.open(index)
            motors = {k: v for k, v in info.get("cam", {}).items() if k in ("pan", "tilt", "zoom")}
            if motors:
                self.bridge = b
                self.ptz = PTZController(settings)
                self.ptz.attach(PhysicalBackend(b, motors), motors)
            else:
                b.shutdown()
        except Exception:
            self.bridge = None

    def frame(self):
        return self.capture.latest(processed=True), None

    def close(self) -> None:
        self.capture.stop()
        if self.bridge:
            self.bridge.shutdown()


# --- estado da mesa ----------------------------------------------------------------------
@dataclass
class SwitcherState:
    program: int = 0
    preview: int = 1
    transition: str = "mix"
    duration: float = 1.0
    dip_color: tuple = (0, 0, 0)
    tbar: float = 0.0              # 0..1 (manual)
    progress: float = 0.0          # 0..1 da transição em curso
    running: bool = False
    t0: float = 0.0
    ftb: float = 0.0               # nível atual de preto (0..1)
    ftb_on: bool = False
    pip_on: bool = False
    pip_source: int = 1
    pip_corner: str = "tr"
    pip_size: float = 0.3
    key_on: bool = False           # câmara principal com alfa por cima de outra fonte
    key_fill: int = 0              # fonte com alfa (normalmente a principal)
    key_bg: int = 2                # fundo do cenário virtual
    dsk_on: bool = False
    dsk_image: np.ndarray | None = None     # BGRA já à resolução de saída
    lower_on: bool = False
    lower_image: np.ndarray | None = None   # BGRA à resolução de saída
    labels: list = field(default_factory=list)


class SwitcherEngine:
    """Mistura as fontes a 30 fps. Thread-safe para comandos vindos da UI/remoto."""

    def __init__(self, fps: float = 30.0) -> None:
        self.sources: list[Source | None] = [None] * MAX_INPUTS
        self.st = SwitcherState()
        self.fps = fps
        self.sinks: list = []                 # objetos com .write(frame BGR)
        self.on_multiview = None              # callback(np.ndarray BGR) ~15 fps
        self.on_tally = None                  # callback(program, preview)
        self.program_frame: np.ndarray | None = None
        self.stats = {"fps": 0.0, "ms": 0.0}
        self._lock = threading.RLock()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="switcher", daemon=True)
        self._thread.start()

    # --- fontes ---------------------------------------------------------------------------
    def set_source(self, idx: int, src: Source | None) -> None:
        with self._lock:
            old = self.sources[idx]
            self.sources[idx] = src
        if old is not None and old is not src:
            old.close()

    def free_slot(self) -> int | None:
        return next((i for i, s in enumerate(self.sources) if s is None), None)

    def label(self, idx: int) -> str:
        s = self.sources[idx]
        return f"{idx + 1} {s.name}" if s else f"{idx + 1} —"

    # --- comandos (estilo ATEM) ---------------------------------------------------------
    def set_preview(self, idx: int) -> None:
        with self._lock:
            if not self.st.running:
                self.st.preview = idx
        self._tally()

    def set_program(self, idx: int) -> None:
        """Corte direto de uma fonte para o programa (barramento de programa)."""
        with self._lock:
            self.st.running = False
            self.st.progress = 0.0
            if idx == self.st.preview:
                self.st.preview = self.st.program
            self.st.program = idx
        self._tally()

    def cut(self) -> None:
        with self._lock:
            self.st.running = False
            self.st.progress = 0.0
            self.st.program, self.st.preview = self.st.preview, self.st.program
        self._tally()

    def auto(self) -> None:
        with self._lock:
            if self.st.running:
                return
            self.st.running = True
            self.st.t0 = time.monotonic()
            self.st.progress = 0.0

    def set_tbar(self, v: float) -> None:
        """T-bar manual: 0→1 leva a pré-visualização ao ar; ao chegar a 1 troca os barramentos."""
        with self._lock:
            self.st.running = False
            self.st.progress = max(0.0, min(1.0, v))
            if self.st.progress >= 0.999:
                self.st.program, self.st.preview = self.st.preview, self.st.program
                self.st.progress = 0.0
                done = True
            else:
                done = False
        if done:
            self._tally()

    def toggle_ftb(self) -> None:
        with self._lock:
            self.st.ftb_on = not self.st.ftb_on

    # --- composição -----------------------------------------------------------------------
    def _render(self, idx: int) -> np.ndarray:
        src = self.sources[idx] if 0 <= idx < MAX_INPUTS else None
        img = None
        alpha = None
        if src is not None:
            try:
                img, alpha = src.frame()
            except Exception:
                img = None
        if img is None:
            return np.zeros((OUT_H, OUT_W, 3), np.uint8)
        img = _fit(img, OUT_W, OUT_H)
        st = self.st
        if st.key_on and idx == st.key_fill and alpha is not None and st.key_bg != idx:
            bg = self._render(st.key_bg)
            a = _fit(alpha, OUT_W, OUT_H)
            img = _blend_alpha(img, bg, a)
        return img

    def _transition(self, a: np.ndarray, b: np.ndarray, p: float) -> np.ndarray:
        kind = self.st.transition
        if p <= 0:
            return a
        if p >= 1:
            return b
        if kind == "dip":
            c = np.full_like(a, self.st.dip_color)
            return cv2.addWeighted(a, 1 - p * 2, c, p * 2, 0) if p < 0.5 else cv2.addWeighted(c, 2 - p * 2, b, p * 2 - 1, 0)
        if kind in ("wipe_lr", "wipe_tb"):
            out = a.copy()
            if kind == "wipe_lr":
                x = int(OUT_W * p)
                out[:, :x] = b[:, :x]
                e = slice(max(0, x - 6), min(OUT_W, x + 6))
                out[:, e] = cv2.addWeighted(a[:, e], 0.5, b[:, e], 0.5, 0)
            else:
                y = int(OUT_H * p)
                out[:y] = b[:y]
            return out
        if kind == "push":
            x = int(OUT_W * p)
            out = np.empty_like(a)
            out[:, : OUT_W - x] = a[:, x:]
            out[:, OUT_W - x:] = b[:, : x]
            return out
        e = p * p * (3 - 2 * p)  # mistura com curva suave
        return cv2.addWeighted(a, 1 - e, b, e, 0)

    def _compose(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        st = self.st
        now = time.monotonic()
        if st.running:
            st.progress = min(1.0, (now - st.t0) / max(0.05, st.duration))
            if st.progress >= 1.0:
                st.running = False
                st.progress = 0.0
                st.program, st.preview = st.preview, st.program
                self._tally()
        pgm = self._render(st.program)
        pvw = self._render(st.preview)
        out = self._transition(pgm, pvw, st.progress) if st.progress > 0 else pgm.copy()
        if st.pip_on and st.pip_source not in (st.program,):
            pip = self._render(st.pip_source)
            out = _overlay_pip(out, pip, st.pip_corner, st.pip_size)
        if st.lower_on and st.lower_image is not None:
            out = _overlay_bgra(out, st.lower_image)
        if st.dsk_on and st.dsk_image is not None:
            out = _overlay_bgra(out, st.dsk_image)
        target = 1.0 if st.ftb_on else 0.0
        st.ftb += (target - st.ftb) * min(1.0, 1 / (self.fps * 0.25))  # ~1 s de fade
        if abs(st.ftb - target) < 0.01:
            st.ftb = target
        if st.ftb > 0:
            out = cv2.convertScaleAbs(out, alpha=1 - st.ftb)
        return out, pgm, pvw

    def _multiview(self, program: np.ndarray, pvw: np.ndarray) -> np.ndarray:
        W, H = 1280, 720
        mv = np.zeros((H, W, 3), np.uint8)
        mv[:360, :640] = cv2.resize(pvw, (640, 360), interpolation=cv2.INTER_AREA)
        mv[:360, 640:] = cv2.resize(program, (640, 360), interpolation=cv2.INTER_AREA)
        cv2.rectangle(mv, (1, 1), (638, 358), (60, 200, 60), 3)
        cv2.rectangle(mv, (641, 1), (1278, 358), (50, 50, 230), 3)
        _label(mv, "PREVIEW", 10, 340)
        _label(mv, "PROGRAMA", 650, 340)
        for i in range(MAX_INPUTS):
            x, y = (i % 4) * 320, 360 + (i // 4) * 180
            src = self.sources[i]
            if src is not None:
                try:
                    img, _ = src.frame()
                except Exception:
                    img = None
                if img is not None:
                    mv[y:y + 180, x:x + 320] = cv2.resize(_fit(img, 1920, 1080), (320, 180), interpolation=cv2.INTER_AREA)
            color = (50, 50, 230) if i == self.st.program else ((60, 200, 60) if i == self.st.preview else (70, 70, 70))
            cv2.rectangle(mv, (x + 1, y + 1), (x + 318, y + 178), color, 3 if color != (70, 70, 70) else 1)
            _label(mv, self.label(i), x + 8, y + 168)
        return mv

    def _loop(self) -> None:
        period = 1 / self.fps
        nxt = time.monotonic()
        frames, t_fps, mv_i = 0, time.monotonic(), 0
        while self._running:
            t0 = time.perf_counter()
            try:
                with self._lock:
                    out, pgm, pvw = self._compose()
                self.program_frame = out
                for sink in list(self.sinks):
                    try:
                        sink.write(out)
                    except Exception:
                        self.sinks.remove(sink)
                mv_i += 1
                if self.on_multiview and mv_i % 2 == 0:
                    self.on_multiview(self._multiview(out, pvw))
            except Exception:
                pass
            self.stats["ms"] = (time.perf_counter() - t0) * 1000
            frames += 1
            now = time.monotonic()
            if now - t_fps >= 1:
                self.stats["fps"] = frames / (now - t_fps)
                frames, t_fps = 0, now
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 0.5:
                nxt = time.monotonic()

    def _tally(self) -> None:
        if self.on_tally:
            try:
                self.on_tally(self.st.program, self.st.preview)
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        self._thread.join(2)
        for i in range(MAX_INPUTS):
            self.set_source(i, None)


# --- ajudas de composição ----------------------------------------------------------------
def _blend_alpha(fg: np.ndarray, bg: np.ndarray, a8: np.ndarray) -> np.ndarray:
    a3 = cv2.merge((a8, a8, a8))
    return cv2.add(cv2.multiply(fg, a3, scale=1 / 255.0), cv2.multiply(bg, cv2.bitwise_not(a3), scale=1 / 255.0))


def _overlay_bgra(dst: np.ndarray, bgra: np.ndarray) -> np.ndarray:
    return _blend_alpha(bgra[..., :3], dst, bgra[..., 3].copy())


def _overlay_pip(dst: np.ndarray, pip: np.ndarray, corner: str, size: float) -> np.ndarray:
    pw, ph = int(OUT_W * size), int(OUT_H * size)
    small = cv2.resize(pip, (pw, ph), interpolation=cv2.INTER_AREA)
    m = int(OUT_W * 0.03)
    x = OUT_W - pw - m if corner in ("tr", "br") else m
    y = m if corner in ("tr", "tl") else OUT_H - ph - m
    out = dst.copy()
    cv2.rectangle(out, (x - 3, y - 3), (x + pw + 2, y + ph + 2), (255, 255, 255), 3)
    out[y:y + ph, x:x + pw] = small
    return out


def _label(img: np.ndarray, text: str, x: int, y: int) -> None:
    import unicodedata  # a fonte do OpenCV só tem ASCII: "Câmara" → "Camara"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode() or "-"
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
