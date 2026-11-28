"""Elementos sobrepostos (HUD): escolher, um a um, se aparecem no ECRÃ, na GRAVAÇÃO e na FOTO.

Ex.: a grelha e as zebras só ajudam o operador (ecrã), o timecode e a claquete ficam gravados
para a edição, e o logótipo vai como marca de água para a gravação e as fotos.

A pré-visualização desenha os elementos marcados para «ecrã» (widgets.PreviewWidget).
Este módulo "queima" na imagem os marcados para «gravação» (vai também para a câmara
virtual/OBS) e para «foto». Os textos são desenhados com o Qt (acentos e símbolos corretos)
em pequenas imagens guardadas em cache, só redesenhadas quando o texto muda.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from .pipeline import apply_peaking, apply_zebra, luma_histogram

# chave, rótulo, (ecrã, gravação, foto) por omissão
ITEMS: list[tuple[str, str, tuple[bool, bool, bool]]] = [
    ("grid", "Grelha de terços", (True, False, False)),
    ("safe", "Áreas seguras", (False, False, False)),
    ("center", "Cruz central", (True, False, False)),
    ("hist", "Histograma", (True, False, False)),
    ("zebra", "Zebras (sobre-exposição)", (False, False, False)),
    ("peaking", "Focus peaking", (False, False, False)),
    ("track", "Caixa do tracking", (True, False, False)),
    ("hands", "Mãos e gestos (IA)", (True, False, False)),
    ("info", "Câmara · zoom · posição", (True, False, False)),
    ("status", "Ícones de estado (seguir, gestos, chroma…)", (True, False, False)),
    ("rec", "Indicador REC e tempo", (True, False, False)),
    ("countdown", "Contagem do temporizador", (True, False, False)),
    ("timecode", "Timecode da gravação", (False, False, False)),
    ("clock", "Data e hora", (False, False, False)),
    ("slate", "Claquete (projeto · cena · take)", (False, False, False)),
    ("logo", "Logótipo / marca de água", (False, False, False)),
]
LABELS = {k: lbl for k, lbl, _ in ITEMS}
TARGETS = ("screen", "rec", "photo")
# estes 5 já existiam como "overlays" (atalhos G/Z/P): o ecrã continua a usar settings["overlays"]
LEGACY_SCREEN = ("grid", "safe", "zebra", "peaking", "hist")


def ensure_settings(settings: dict) -> dict:
    """Garante settings["hud"] completo (migrando o ecrã dos overlays antigos)."""
    hud = settings.setdefault("hud", {})
    ov = settings.get("overlays", {})
    for key, _lbl, (scr, rec, ph) in ITEMS:
        cur = hud.setdefault(key, {"screen": scr, "rec": rec, "photo": ph})
        for t, v in zip(TARGETS, (scr, rec, ph)):
            cur.setdefault(t, v)
        if key in LEGACY_SCREEN and key in ov:
            cur["screen"] = bool(ov[key])
    hud.setdefault("_logo_path", "")
    hud.setdefault("_logo_corner", "br")
    return hud


def on(settings: dict, key: str, target: str) -> bool:
    if target == "screen" and key in LEGACY_SCREEN:
        return bool(settings.get("overlays", {}).get(key, False))
    return bool(settings.get("hud", {}).get(key, {}).get(target, False))


def any_on(settings: dict, target: str) -> bool:
    return any(on(settings, k, target) for k, _l, _d in ITEMS)


def _text_tile(text: str, px: int, color: QColor, bg: QColor | None = QColor(0, 0, 0, 150), bold: bool = True) -> np.ndarray:
    """Pequena imagem BGRA com o texto (fundo semitransparente)."""
    font = QFont("Segoe UI", 1)
    font.setPixelSize(px)
    font.setBold(bold)
    tmp = QImage(1, 1, QImage.Format.Format_ARGB32)
    p = QPainter(tmp)
    p.setFont(font)
    w = int(p.fontMetrics().horizontalAdvance(text)) + px
    h = int(px * 1.55)
    p.end()
    img = QImage(max(2, w), h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if bg is not None:
        p.setBrush(bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), px * 0.3, px * 0.3)
    p.setFont(font)
    p.setPen(color)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    arr = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine() // 4, 4)
    return arr[:, :w].copy()


def _blend(dst: np.ndarray, tile: np.ndarray, x: int, y: int) -> None:
    """Mistura um tile BGRA em dst (BGR) na posição (x, y), recortando nas margens."""
    h, w = tile.shape[:2]
    H, W = dst.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    t = tile[y0 - y:y1 - y, x0 - x:x1 - x]
    a = np.ascontiguousarray(t[..., 3])
    a3 = cv2.merge((a, a, a))
    roi = dst[y0:y1, x0:x1]
    # mistura em 8 bits (SIMD): ~10× mais rápida do que em float
    roi[:] = cv2.add(cv2.multiply(np.ascontiguousarray(t[..., :3]), a3, scale=1 / 255.0),
                     cv2.multiply(roi, cv2.bitwise_not(a3), scale=1 / 255.0))


class HudBurner:
    """Queima os elementos escolhidos numa imagem de saída (gravação ou foto)."""

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        ensure_settings(settings)
        self.state: dict = {}         # atualizado pela interface: rec_start, device, info, status, countdown…
        self._tiles: dict[str, tuple[str, np.ndarray]] = {}
        self._logo: tuple | None = None
        self._hist: np.ndarray | None = None
        self._frame_i = 0

    def _tile(self, key: str, text: str, px: int, color, bg=QColor(0, 0, 0, 150)) -> np.ndarray:
        cached = self._tiles.get(key)
        if cached is None or cached[0] != (text, px):
            cached = ((text, px), _text_tile(text, px, QColor(color), bg))
            self._tiles[key] = cached
        return cached[1]

    def _logo_tile(self, w: int) -> np.ndarray | None:
        path = self.settings["hud"].get("_logo_path", "")
        if not path or not Path(path).exists():
            return None
        key = (path, w)
        if self._logo is None or self._logo[0] != key:
            img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                return None
            if img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            lw = int(w * 0.12)
            img = cv2.resize(img, (lw, max(1, int(img.shape[0] * lw / img.shape[1]))), interpolation=cv2.INTER_AREA)
            self._logo = (key, img)
        return self._logo[1]

    def burn(self, frame: np.ndarray, meta: dict, target: str) -> np.ndarray:
        """Devolve uma cópia da imagem com os elementos marcados para `target` ("rec" ou "photo")."""
        s = self.settings
        if not any_on(s, target):
            return frame
        img = frame.copy()
        h, w = img.shape[:2]
        k = h / 1080.0                       # escala dos elementos (1080p = 1)
        thin = max(1, int(round(1.5 * k)))
        st = self.state
        en = lambda key: on(s, key, target)
        self._frame_i += 1
        if en("zebra"):
            apply_zebra(img)
        if en("peaking"):
            apply_peaking(img)
        if en("grid"):
            for i in (1, 2):
                cv2.line(img, (w * i // 3, 0), (w * i // 3, h), (235, 235, 235), thin, cv2.LINE_AA)
                cv2.line(img, (0, h * i // 3), (w, h * i // 3), (235, 235, 235), thin, cv2.LINE_AA)
        if en("safe"):
            for frac, col in ((0.9, (230, 230, 230)), (0.8, (0, 200, 255))):
                mx, my = int(w * (1 - frac) / 2), int(h * (1 - frac) / 2)
                cv2.rectangle(img, (mx, my), (w - mx, h - my), col, thin, cv2.LINE_AA)
        if en("center"):
            c, L = (w // 2, h // 2), int(18 * k)
            cv2.line(img, (c[0] - L, c[1]), (c[0] + L, c[1]), (240, 240, 240), thin, cv2.LINE_AA)
            cv2.line(img, (c[0], c[1] - L), (c[0], c[1] + L), (240, 240, 240), thin, cv2.LINE_AA)
        box = meta.get("box")
        if en("track") and box:
            x, y, bw, bh = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
            cv2.rectangle(img, (x, y), (x + bw, y + bh), (61, 138, 255), max(2, int(3 * k)), cv2.LINE_AA)
        if en("hands"):
            for (hx, hy, hw_, hh), gesture in meta.get("hands") or []:
                x, y = int(hx * w), int(hy * h)
                cv2.rectangle(img, (x, y), (x + int(hw_ * w), y + int(hh * h)),
                              (132, 220, 61) if gesture else (200, 200, 200), thin, cv2.LINE_AA)
                if gesture:
                    from .gestures import GESTURES
                    _blend(img, self._tile(f"g{gesture}", GESTURES.get(gesture, gesture), int(22 * k), "#3ddc84"), x, y - int(36 * k))
        if en("hist"):
            if self._frame_i % 5 == 1 or self._hist is None:
                self._hist = luma_histogram(cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA))
            hw_, hh = int(200 * k), int(80 * k)
            x0, y0 = w - hw_ - int(20 * k), h - hh - int(20 * k)
            roi = img[y0:y0 + hh, x0:x0 + hw_]
            roi[:] = (roi.astype(np.float32) * 0.45).astype(np.uint8)
            pts = [(x0 + int(hw_ * i / (len(self._hist) - 1)), y0 + hh - int(float(v) * (hh - 4))) for i, v in enumerate(self._hist)]
            cv2.polylines(img, [np.array([(x0, y0 + hh)] + pts + [(x0 + hw_, y0 + hh)], np.int32)], False, (230, 230, 230), thin, cv2.LINE_AA)
        pad = int(22 * k)
        fs = int(26 * k)
        y_top = pad
        if en("rec") and st.get("recording"):
            secs = int(time.time() - st.get("rec_start", time.time()))
            t = self._tile("rec", f"● REC  {secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}", fs, "#ff3b4a")
            _blend(img, t, pad, y_top)
            y_top += t.shape[0] + int(8 * k)
        if en("status") and st.get("status"):
            t = self._tile("status", st["status"], int(22 * k), "#ffb27a")
            _blend(img, t, pad, y_top)
            y_top += t.shape[0] + int(8 * k)
        if en("timecode") and st.get("recording"):
            el = max(0.0, time.time() - st.get("rec_start", time.time()))
            fps = max(1, int(round(st.get("fps", 30))))
            tc = f"{int(el // 3600):02d}:{int(el % 3600 // 60):02d}:{int(el % 60):02d}:{int((el % 1) * fps):02d}"
            t = _text_tile(tc, fs, QColor("white"), QColor(0, 0, 0, 170))   # muda a cada frame: sem cache
            _blend(img, t, (w - t.shape[1]) // 2, h - t.shape[0] - pad)
        if en("clock"):
            t = self._tile("clock", dt.datetime.now().strftime("%d/%m/%Y  %H:%M:%S"), int(24 * k), "white")
            _blend(img, t, w - t.shape[1] - pad, pad)
        if en("slate"):
            text = st.get("slate", "")
            if text:
                t = self._tile("slate", text, int(24 * k), "white")
                _blend(img, t, w - t.shape[1] - pad, pad + int(50 * k))
        if en("info") and st.get("info"):
            t = self._tile("info", st["info"], int(20 * k), "white")
            _blend(img, t, pad, h - t.shape[0] - pad)
        if en("countdown") and st.get("countdown"):
            t = self._tile("cd", f"{st.get('countdown_label', '')} {st['countdown']}".strip(), int(90 * k), "white", QColor(0, 0, 0, 120))
            _blend(img, t, (w - t.shape[1]) // 2, (h - t.shape[0]) // 2)
        if en("logo"):
            logo = self._logo_tile(w)
            if logo is not None:
                corner = s["hud"].get("_logo_corner", "br")
                lx = w - logo.shape[1] - pad if corner in ("tr", "br") else pad
                ly = pad if corner in ("tr", "tl") else h - logo.shape[0] - pad
                _blend(img, logo, lx, ly)
        return img
