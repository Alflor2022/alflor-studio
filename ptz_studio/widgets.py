"""Widgets: pré-visualização com ferramentas de enquadramento e joystick virtual."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

ACCENT = QColor("#ff8a3d")


class PreviewWidget(QWidget):
    """Monitor: clique = centrar, arrastar = mover, roda = zoom, botão direito = escolher sujeito."""

    clicked = Signal(float, float)          # coordenadas normalizadas da imagem mostrada
    drag_velocity = Signal(float, float)
    drag_end = Signal()
    wheel_zoom = Signal(int)
    subject_selected = Signal(float, float, float, float)
    click_track = Signal(float, float)
    double_clicked = Signal()

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.image: QImage | None = None
        self.meta: dict = {}
        self.message = "Sem imagem"
        self.info = ""
        self.countdown = 0
        self.recording = False
        self.rec_time = ""
        self.composition = (0.5, 0.5)
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._press: QPointF | None = None
        self._dragging = False
        self._select_start: QPointF | None = None
        self._select_rect: QRectF | None = None

    def set_frame(self, img: QImage, meta: dict) -> None:
        self.image = img
        self.meta = meta
        self.update()

    # --- geometria -------------------------------------------------------------------
    def image_rect(self) -> QRectF:
        if not self.image or self.image.isNull():
            return QRectF(self.rect())
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _norm(self, p: QPointF) -> tuple[float, float]:
        r = self.image_rect()
        return (min(1, max(0, (p.x() - r.x()) / r.width())), min(1, max(0, (p.y() - r.y()) / r.height())))

    # --- desenho --------------------------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#07090c"))
        r = self.image_rect()
        if not self.image:
            p.setPen(QColor("#8b95a5"))
            p.setFont(QFont("Segoe UI", 13))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        p.drawImage(r, self.image)
        ov = self.settings["overlays"]
        thin = QPen(QColor(255, 255, 255, 90), 1)
        if ov["grid"]:
            p.setPen(thin)
            for i in (1, 2):
                x = r.x() + r.width() * i / 3
                y = r.y() + r.height() * i / 3
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if ov["safe"]:
            for frac, col in ((0.9, QColor(255, 255, 255, 110)), (0.8, QColor(255, 200, 0, 110))):
                p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
                p.drawRect(QRectF(r.x() + r.width() * (1 - frac) / 2, r.y() + r.height() * (1 - frac) / 2, r.width() * frac, r.height() * frac))
        # centro
        p.setPen(QPen(QColor(255, 255, 255, 140), 1))
        c = r.center()
        p.drawLine(QPointF(c.x() - 10, c.y()), QPointF(c.x() + 10, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 10), QPointF(c.x(), c.y() + 10))
        # sujeito em tracking
        box = self.meta.get("box")
        if box:
            bx = QRectF(r.x() + box[0] * r.width(), r.y() + box[1] * r.height(), box[2] * r.width(), box[3] * r.height())
            p.setPen(QPen(ACCENT, 2))
            L = min(bx.width(), bx.height()) * 0.25
            for (x, y, dx, dy) in ((bx.left(), bx.top(), 1, 1), (bx.right(), bx.top(), -1, 1), (bx.left(), bx.bottom(), 1, -1), (bx.right(), bx.bottom(), -1, -1)):
                p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))
            tx, ty = self.composition
            p.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 150), 1, Qt.PenStyle.DotLine))
            p.drawEllipse(QPointF(r.x() + tx * r.width(), r.y() + ty * r.height()), 6, 6)
        if self._select_rect:
            p.setPen(QPen(QColor("#3ddc84"), 2, Qt.PenStyle.DashLine))
            p.drawRect(self._select_rect)
        # histograma
        hist = self.meta.get("hist")
        if ov["hist"] and hist is not None:
            hw, hh = 180, 70
            hx, hy = r.right() - hw - 12, r.bottom() - hh - 12
            p.fillRect(QRectF(hx, hy, hw, hh), QColor(0, 0, 0, 140))
            path = QPainterPath(QPointF(hx, hy + hh))
            n = len(hist)
            for i, v in enumerate(hist):
                path.lineTo(QPointF(hx + hw * i / (n - 1), hy + hh - float(v) * (hh - 4)))
            path.lineTo(QPointF(hx + hw, hy + hh))
            p.fillPath(path, QColor(255, 255, 255, 120))
        # textos
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QRectF(r.x() + 12, r.bottom() - 28, r.width() - 220, 20), Qt.AlignmentFlag.AlignLeft, self.info)
        if self.recording:
            p.setBrush(QBrush(QColor("#ff3b4a")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(r.x() + 22, r.y() + 22), 7, 7)
            p.setPen(QColor("white"))
            p.drawText(QPointF(r.x() + 36, r.y() + 27), f"REC  {self.rec_time}")
        if self.countdown:
            p.setFont(QFont("Segoe UI", 96, QFont.Weight.Bold))
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.countdown))

    # --- rato -----------------------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if ev.button() == Qt.MouseButton.LeftButton and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.click_track.emit(*self._norm(ev.position()))  # Ctrl + clique = seguir este objeto
            return
        # escolher sujeito: botão direito arrastado OU Shift + botão esquerdo (touchpads)
        if ev.button() == Qt.MouseButton.RightButton or (ev.button() == Qt.MouseButton.LeftButton and shift):
            self._select_button = ev.button()
            self._select_start = ev.position()
            self._select_rect = QRectF(ev.position(), ev.position())
        elif ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position()
            self._dragging = False

    def mouseMoveEvent(self, ev) -> None:
        if self._press is not None:
            d = ev.position() - self._press
            if self._dragging or math.hypot(d.x(), d.y()) > 8:
                self._dragging = True
                r = self.image_rect()
                vx = max(-1.0, min(1.0, d.x() / (r.width() * 0.3)))
                vy = max(-1.0, min(1.0, -d.y() / (r.height() * 0.3)))
                self.drag_velocity.emit(vx, vy)
        if self._select_start is not None:
            self._select_rect = QRectF(self._select_start, ev.position()).normalized()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if self._dragging:
                self.drag_end.emit()
            else:
                self.clicked.emit(*self._norm(ev.position()))
            self._press = None
            self._dragging = False
        elif ev.button() == getattr(self, "_select_button", None) and self._select_rect is not None:
            rect = self._select_rect
            self._select_start = None
            self._select_rect = None
            if rect.width() > 10 and rect.height() > 10:
                x0, y0 = self._norm(rect.topLeft())
                x1, y1 = self._norm(rect.bottomRight())
                self.subject_selected.emit(x0, y0, x1 - x0, y1 - y0)
            self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        self.double_clicked.emit()

    def wheelEvent(self, ev) -> None:
        notches = ev.angleDelta().y() / 120  # cada "clique" da roda conta (antes só contava 1 por evento)
        if notches:
            self.wheel_zoom.emit(int(round(notches)) or (1 if notches > 0 else -1))


class JoystickWidget(QWidget):
    """Joystick virtual: arrastar o botão central; volta ao centro ao largar."""

    moved = Signal(float, float)
    released = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(170, 170)
        self._pos = QPointF(0, 0)
        self._active = False

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = self.width() / 2 - 4
        p.setPen(QPen(QColor("#2a313c"), 1))
        p.setBrush(QColor("#151a21"))
        p.drawEllipse(c, R, R)
        p.setPen(QPen(QColor("#2a313c"), 1, Qt.PenStyle.DotLine))
        p.drawEllipse(c, R * 0.5, R * 0.5)
        k = c + self._pos * (R - 26)
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(QColor("#2d3541"))
        p.drawEllipse(k, 24, 24)

    def _update(self, pos: QPointF) -> None:
        c = QPointF(self.width() / 2, self.height() / 2)
        R = self.width() / 2 - 30
        d = (pos - c) / R
        m = math.hypot(d.x(), d.y())
        if m > 1:
            d /= m
        self._pos = d
        self.moved.emit(d.x(), -d.y())
        self.update()

    def mousePressEvent(self, ev) -> None:
        self._active = True
        self._update(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        if self._active:
            self._update(ev.position())

    def mouseReleaseEvent(self, _ev) -> None:
        self._active = False
        self._pos = QPointF(0, 0)
        self.released.emit()
        self.update()
