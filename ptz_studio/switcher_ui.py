"""Janela da mesa de realização (superfície de controlo inspirada nas mesas ATEM Mini)."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider,
    QToolButton, QVBoxLayout, QWidget,
)

from .config import DEFAULTS
from .media import Recorder, VirtualCam, output_path
from .switcher import (
    MAX_INPUTS, OUT_H, OUT_W, PIP_CORNERS, TRANSITIONS, CallableSource, CameraSource, ColorSource, ImageSource,
    SwitcherEngine, VideoSource,
)
from .widgets import JoystickWidget

SURFACE_QSS = """
QPushButton[bus="pgm"] { background:#2a1517; border:1px solid #5a2a30; color:#e8b4b8; min-width:54px; min-height:40px; font-weight:700; }
QPushButton[bus="pgm"]:checked { background:#d22b3a; border-color:#ff5b68; color:white; }
QPushButton[bus="pvw"] { background:#14261b; border:1px solid #2a5a3c; color:#b4e8c4; min-width:54px; min-height:40px; font-weight:700; }
QPushButton[bus="pvw"]:checked { background:#1f9e4f; border-color:#3ddc84; color:white; }
QPushButton#cut, QPushButton#auto { min-height:52px; min-width:76px; font-size:12pt; font-weight:800; }
QPushButton#auto:checked { background:#d22b3a; color:white; }
QPushButton#ftb:checked { background:#d22b3a; color:white; }
"""


def _qimage_bgra(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return arr[:, : img.width()].copy()   # ARGB32 em little-endian = B, G, R, A


class SwitcherWindow(QMainWindow):
    multiview_ready = Signal(object)
    tally_changed = Signal(int, int)

    def __init__(self, main) -> None:
        super().__init__()
        self.main = main
        self.settings = main.settings
        self.cfg = self.settings["switcher"]
        self.setWindowTitle("Mesa de realização · Alflor Studio")
        self.resize(1500, 900)
        self.setStyleSheet(main.styleSheet() + SURFACE_QSS)
        self.engine = SwitcherEngine()
        self.engine.st.transition = self.cfg.get("transition", "mix")
        self.engine.st.duration = float(self.cfg.get("duration", 1.0))
        self.engine.st.pip_corner = self.cfg.get("pip_corner", "tr")
        self.engine.st.pip_size = float(self.cfg.get("pip_size", 0.3))
        self.engine.on_multiview = lambda img: self.multiview_ready.emit(img)
        self.engine.on_tally = lambda p, v: self.tally_changed.emit(p, v)
        self.recorder: Recorder | None = None
        self.vcam: VirtualCam | None = None
        self._build()
        self.multiview_ready.connect(self._show_multiview)
        self.tally_changed.connect(self._update_tally)
        self.engine.set_source(0, CallableSource("Alflor (principal)", self._main_frame, ptz=main.ptz))
        self._restore_inputs()
        self._refresh_labels()
        self._update_tally(self.engine.st.program, self.engine.st.preview)
        self.timer = QTimer(self, interval=250, timeout=self._tick)
        self.timer.start()

    # --- fontes ------------------------------------------------------------------------
    def _main_frame(self):
        cap = self.main.capture
        return cap.latest_with_alpha() if cap else (None, None)

    def _restore_inputs(self) -> None:
        for d in self.cfg.get("inputs", []):
            slot = d.get("slot")
            if slot is None or not 1 <= slot < MAX_INPUTS:
                continue
            try:
                self.engine.set_source(slot, self._make_source(d))
            except Exception as exc:
                self.statusBar().showMessage(f"Entrada {slot + 1} não restaurada: {exc}", 6000)

    def _make_source(self, d: dict):
        kind = d["kind"]
        if kind == "camera":
            devices = self.main.devices.get("video", [])
            if d["device"] not in devices:
                raise ValueError(f"câmara «{d['device']}» não encontrada")
            if d["device"] == self.main.device:
                raise ValueError("essa câmara já é a principal")
            s = copy.deepcopy(DEFAULTS)
            s["resolution"] = "1920x1080"
            return CameraSource(d["device"], devices.index(d["device"]), s)
        if kind == "image":
            return ImageSource(d.get("name") or Path(d["path"]).stem, d["path"])
        if kind == "video":
            return VideoSource(d.get("name") or Path(d["path"]).stem, d["path"])
        if kind == "bars":
            return ColorSource("Barras", bars=True)
        return ColorSource(d.get("name", "Preto"), tuple(d.get("color", (0, 0, 0))))

    def _save_inputs(self) -> None:
        out = []
        for i, src in enumerate(self.engine.sources):
            if i == 0 or src is None:
                continue
            d = {"slot": i, "kind": src.kind, "name": src.name}
            if src.kind == "camera":
                d["device"] = src.device
            elif src.kind in ("image", "video"):
                d["path"] = src.path
            elif src.kind == "color":
                d["kind"] = "bars" if src.name == "Barras" else "color"
                d["color"] = [int(c) for c in src._img[0, -1]]
            out.append(d)
        self.cfg["inputs"] = out
        self.settings.save()

    def add_input(self, kind: str, device: str = "") -> None:
        slot = self.engine.free_slot()
        if slot is None:
            QMessageBox.information(self, "Mesa", "As 8 entradas estão ocupadas. Remova uma primeiro.")
            return
        d = {"slot": slot, "kind": kind}
        if kind == "camera":
            d["device"] = device
        elif kind in ("image", "video"):
            filt = "Imagens (*.png *.jpg *.jpeg *.bmp *.webp)" if kind == "image" else "Vídeos (*.mp4 *.mov *.mkv *.avi *.webm)"
            path, _ = QFileDialog.getOpenFileName(self, "Escolher ficheiro", str(Path.home()), filt)
            if not path:
                return
            d["path"] = path
        elif kind == "color":
            d["name"], d["color"] = "Preto", (0, 0, 0)
        try:
            self.engine.set_source(slot, self._make_source(d))
        except Exception as exc:
            QMessageBox.warning(self, "Mesa", f"Não foi possível adicionar: {exc}")
            return
        self._save_inputs()
        self._refresh_labels()

    def remove_input(self, slot: int) -> None:
        if slot == 0:
            return
        self.engine.set_source(slot, None)
        self._save_inputs()
        self._refresh_labels()

    def _inputs_menu(self) -> None:
        menu = QMenu(self)
        cams = [d for d in self.main.devices.get("video", []) if d != self.main.device
                and not any(getattr(s, "device", None) == d for s in self.engine.sources if s)]
        sub = menu.addMenu("Adicionar câmara")
        for d in cams:
            sub.addAction(d, lambda d=d: self.add_input("camera", d))
        if not cams:
            sub.addAction("(nenhuma câmara livre)").setEnabled(False)
        menu.addAction("Adicionar imagem / logótipo / cenário…", lambda: self.add_input("image"))
        menu.addAction("Adicionar vídeo em ciclo…", lambda: self.add_input("video"))
        menu.addAction("Adicionar barras de cor", lambda: self.add_input("bars"))
        menu.addAction("Adicionar preto", lambda: self.add_input("color"))
        rm = menu.addMenu("Remover entrada")
        for i, s in enumerate(self.engine.sources):
            if i and s:
                rm.addAction(self.engine.label(i), lambda i=i: self.remove_input(i))
        menu.exec(self.btn_inputs.mapToGlobal(self.btn_inputs.rect().bottomLeft()))

    # --- interface ----------------------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        self.mv = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.mv.setMinimumSize(640, 360)
        self.mv.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mv.setStyleSheet("background:#05070a;")
        root.addWidget(self.mv, 1)
        surface = QHBoxLayout()

        # barramentos
        g_bus = QGroupBox("Barramentos  (1–8 pré-visualização · Ctrl+1–8 programa)")
        gb = QGridLayout(g_bus)
        gb.addWidget(QLabel("PROGRAMA"), 0, 0)
        gb.addWidget(QLabel("PREVIEW"), 1, 0)
        self.pgm_buttons, self.pvw_buttons = [], []
        self.pgm_group, self.pvw_group = QButtonGroup(self), QButtonGroup(self)
        for i in range(MAX_INPUTS):
            for row, bus, group, lst, fn in ((0, "pgm", self.pgm_group, self.pgm_buttons, self.engine.set_program),
                                             (1, "pvw", self.pvw_group, self.pvw_buttons, self.engine.set_preview)):
                b = QPushButton(str(i + 1), checkable=True)
                b.setProperty("bus", bus)
                b.clicked.connect(lambda _=False, i=i, fn=fn: fn(i))
                group.addButton(b)
                gb.addWidget(b, row, i + 1)
                lst.append(b)
        self.btn_inputs = QPushButton("Entradas…")
        self.btn_inputs.clicked.connect(self._inputs_menu)
        gb.addWidget(self.btn_inputs, 2, 1, 1, 3)
        surface.addWidget(g_bus, 3)

        # transição
        g_tr = QGroupBox("Transição")
        gt = QGridLayout(g_tr)
        self.tr_group = QButtonGroup(self)
        for j, (key, label) in enumerate(TRANSITIONS.items()):
            b = QPushButton(label, checkable=True)
            b.setChecked(key == self.engine.st.transition)
            b.clicked.connect(lambda _=False, k=key: self._set_transition(k))
            self.tr_group.addButton(b)
            gt.addWidget(b, j // 3, j % 3)
        self.spn_rate = QDoubleSpinBox(minimum=0.1, maximum=10, value=self.engine.st.duration, suffix=" s", singleStep=0.25)
        self.spn_rate.valueChanged.connect(self._set_rate)
        gt.addWidget(QLabel("Duração"), 2, 0)
        gt.addWidget(self.spn_rate, 2, 1, 1, 2)
        cut = QPushButton("CUT", objectName="cut")
        cut.clicked.connect(self.engine.cut)
        self.btn_auto = QPushButton("AUTO", objectName="auto", checkable=True)
        self.btn_auto.clicked.connect(lambda: (self.engine.auto(), self.btn_auto.setChecked(True)))
        self.btn_ftb = QPushButton("FTB", objectName="ftb", checkable=True)
        self.btn_ftb.clicked.connect(lambda: self.engine.toggle_ftb())
        gt.addWidget(cut, 3, 0)
        gt.addWidget(self.btn_auto, 3, 1)
        gt.addWidget(self.btn_ftb, 3, 2)
        self.tbar = QSlider(Qt.Orientation.Vertical, minimum=0, maximum=1000)
        self.tbar.setMinimumHeight(140)
        self.tbar.valueChanged.connect(self._tbar_moved)
        gt.addWidget(QLabel("T-bar"), 0, 3)
        gt.addWidget(self.tbar, 1, 3, 3, 1)
        surface.addWidget(g_tr, 2)

        # chaves
        g_key = QGroupBox("Chaves e grafismo")
        gk = QGridLayout(g_key)
        self.chk_pip = QCheckBox("PiP")
        self.chk_pip.toggled.connect(lambda v: setattr(self.engine.st, "pip_on", v))
        self.cmb_pip = QComboBox()
        self.cmb_pip.currentIndexChanged.connect(lambda i: setattr(self.engine.st, "pip_source", max(0, i)))
        self.cmb_corner = QComboBox()
        for k, v in PIP_CORNERS.items():
            self.cmb_corner.addItem(v, k)
        self.cmb_corner.setCurrentIndex(max(0, self.cmb_corner.findData(self.engine.st.pip_corner)))
        self.cmb_corner.currentIndexChanged.connect(lambda _i: self._set_cfg("pip_corner", self.cmb_corner.currentData(), "pip_corner"))
        sld_size = QSlider(Qt.Orientation.Horizontal, minimum=15, maximum=50, value=int(self.engine.st.pip_size * 100))
        sld_size.valueChanged.connect(lambda v: self._set_cfg("pip_size", v / 100, "pip_size"))
        gk.addWidget(self.chk_pip, 0, 0)
        gk.addWidget(self.cmb_pip, 0, 1)
        gk.addWidget(self.cmb_corner, 0, 2)
        gk.addWidget(sld_size, 0, 3)
        self.chk_key = QCheckBox("Cenário virtual")
        self.chk_key.setToolTip("A câmara principal com chroma transparente por cima de outra entrada")
        self.chk_key.toggled.connect(lambda v: setattr(self.engine.st, "key_on", v))
        self.cmb_keybg = QComboBox()
        self.cmb_keybg.currentIndexChanged.connect(lambda i: setattr(self.engine.st, "key_bg", max(0, i)))
        gk.addWidget(self.chk_key, 1, 0)
        gk.addWidget(QLabel("fundo:"), 1, 1)
        gk.addWidget(self.cmb_keybg, 1, 2, 1, 2)
        self.chk_logo = QCheckBox("Logótipo")
        self.chk_logo.toggled.connect(self._toggle_logo)
        b_logo = QToolButton(text="…")
        b_logo.clicked.connect(self._choose_logo)
        gk.addWidget(self.chk_logo, 2, 0)
        gk.addWidget(b_logo, 2, 1)
        self.chk_lower = QCheckBox("Oráculo")
        self.chk_lower.toggled.connect(self._toggle_lower)
        self.ed_title = QLineEdit(self.cfg.get("lower_title", ""), placeholderText="Nome")
        self.ed_sub = QLineEdit(self.cfg.get("lower_sub", ""), placeholderText="Função / tema")
        for e in (self.ed_title, self.ed_sub):
            e.editingFinished.connect(self._update_lower)
        gk.addWidget(self.chk_lower, 3, 0)
        gk.addWidget(self.ed_title, 3, 1, 1, 2)
        gk.addWidget(self.ed_sub, 4, 1, 1, 2)
        surface.addWidget(g_key, 3)

        # controlo de câmara
        g_cam = QGroupBox("Controlo de câmara (entrada em PREVIEW)")
        gc = QGridLayout(g_cam)
        self.lbl_cam = QLabel("—")
        gc.addWidget(self.lbl_cam, 0, 0, 1, 3)
        joy = JoystickWidget()
        joy.setFixedSize(130, 130)
        joy.moved.connect(lambda x, y: self._cam_ptz() and self._cam_ptz().set_velocity(pan=x, tilt=y))
        joy.released.connect(lambda: self._cam_ptz() and self._cam_ptz().set_velocity(pan=0, tilt=0))
        gc.addWidget(joy, 1, 0, 3, 1)
        for r, (txt, z) in enumerate((("Zoom ＋", 0.6), ("Zoom －", -0.6))):
            b = QPushButton(txt)
            b.pressed.connect(lambda z=z: self._cam_ptz() and self._cam_ptz().set_velocity(zoom=z))
            b.released.connect(lambda: self._cam_ptz() and self._cam_ptz().set_velocity(zoom=0))
            gc.addWidget(b, 1 + r, 1, 1, 2)
        home = QPushButton("⌂ Início")
        home.clicked.connect(lambda: self._cam_ptz() and self._cam_ptz().home())
        gc.addWidget(home, 3, 1, 1, 2)
        for n in range(1, 7):
            b = QPushButton(str(n), toolTip="Clique = ir · Ctrl+clique = guardar")
            b.clicked.connect(lambda _=False, n=n: self._cam_preset(n))
            gc.addWidget(b, 4 + (n - 1) // 3, (n - 1) % 3)
        surface.addWidget(g_cam, 2)

        # saídas
        g_out = QGroupBox("Saída de programa")
        go = QVBoxLayout(g_out)
        self.btn_rec = QPushButton("● Gravar programa", objectName="rec", checkable=True)
        self.btn_rec.clicked.connect(self._toggle_record)
        go.addWidget(self.btn_rec)
        self.chk_vcam = QCheckBox("Programa → câmara virtual")
        self.chk_vcam.toggled.connect(self._toggle_vcam)
        go.addWidget(self.chk_vcam)
        self.lbl_out = QLabel("", objectName="hint", wordWrap=True)
        go.addWidget(self.lbl_out)
        go.addStretch(1)
        surface.addWidget(g_out, 2)
        root.addLayout(surface)
        self.setCentralWidget(central)

    def _set_cfg(self, key, value, attr=None) -> None:
        self.cfg[key] = value
        if attr:
            setattr(self.engine.st, attr, value)

    def _set_transition(self, k: str) -> None:
        self._set_cfg("transition", k, "transition")

    def _set_rate(self, v: float) -> None:
        self._set_cfg("duration", float(v), "duration")

    def _tbar_moved(self, v: int) -> None:
        self.engine.set_tbar(v / 1000)
        if v >= 999:  # transição completa: a T-bar volta ao início (como numa mesa real)
            self.tbar.blockSignals(True)
            self.tbar.setValue(0)
            self.tbar.blockSignals(False)

    # --- grafismo -----------------------------------------------------------------------
    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Logótipo (PNG com transparência)", str(Path.home()), "Imagens (*.png *.jpg *.webp)")
        if path:
            self.cfg["logo_path"] = path
            self.settings.save()
            self._toggle_logo(True)
            self.chk_logo.setChecked(True)

    def _toggle_logo(self, on: bool) -> None:
        path = self.cfg.get("logo_path", "")
        if on and path and Path(path).exists():
            img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
            if img is not None:
                if img.ndim == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
                elif img.shape[2] == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
                lw = int(OUT_W * 0.12)
                img = cv2.resize(img, (lw, max(1, int(img.shape[0] * lw / img.shape[1]))), interpolation=cv2.INTER_AREA)
                canvas = np.zeros((OUT_H, OUT_W, 4), np.uint8)
                m = int(OUT_W * 0.03)
                x, y = OUT_W - lw - m, m
                h = min(img.shape[0], OUT_H - y)
                canvas[y:y + h, x:x + lw] = img[:h]
                self.engine.st.dsk_image = canvas
        elif on:
            self.statusBar().showMessage("Escolha primeiro um ficheiro de logótipo («…»)", 4000)
        self.engine.st.dsk_on = on and self.engine.st.dsk_image is not None

    def _update_lower(self) -> None:
        self.cfg["lower_title"], self.cfg["lower_sub"] = self.ed_title.text(), self.ed_sub.text()
        img = QImage(OUT_W, OUT_H, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y, w, h = 110, OUT_H - 250, 820, 150
        p.fillRect(QRectF(x, y, w, h), QColor(14, 17, 22, 215))
        p.fillRect(QRectF(x, y, 10, h), QColor("#ff8a3d"))
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 34, QFont.Weight.Bold))
        p.drawText(QRectF(x + 40, y + 14, w - 60, 70), Qt.AlignmentFlag.AlignVCenter, self.ed_title.text())
        p.setPen(QColor(220, 225, 235))
        p.setFont(QFont("Segoe UI", 22))
        p.drawText(QRectF(x + 40, y + 82, w - 60, 52), Qt.AlignmentFlag.AlignVCenter, self.ed_sub.text())
        p.end()
        self.engine.st.lower_image = _qimage_bgra(img)

    def _toggle_lower(self, on: bool) -> None:
        if on:
            self._update_lower()
        self.engine.st.lower_on = on

    # --- câmara em preview -------------------------------------------------------------
    def _cam_source(self):
        return self.engine.sources[self.engine.st.preview]

    def _cam_ptz(self):
        src = self._cam_source()
        return getattr(src, "ptz", None) if src else None

    def _cam_preset(self, n: int) -> None:
        src = self._cam_source()
        if src is None:
            return
        from PySide6.QtWidgets import QApplication
        save = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        if src.kind == "main":  # a câmara principal usa os presets da janela principal
            self.main.save_preset(n) if save else self.main.recall_preset(n)
            return
        ptz = getattr(src, "ptz", None)
        if ptz is None:
            self.statusBar().showMessage("Esta entrada não tem PTZ", 3000)
            return
        presets = self.settings.device_presets(src.device)
        if save:
            st = ptz.state()
            presets[str(n)] = {"p": st.get("pan", 0), "t": st.get("tilt", 0), "z": st.get("zoom", 0), "name": f"Preset {n}"}
            self.settings.save()
            self.statusBar().showMessage(f"{src.name}: preset {n} guardado", 3000)
        elif str(n) in presets:
            p = presets[str(n)]
            ptz.move_to(p["p"], p["t"], p["z"], duration=2.0)

    # --- saídas -------------------------------------------------------------------------------
    def _toggle_record(self, on: bool) -> None:
        if on and self.recorder is None:
            if not self.main.ffmpeg:
                self.btn_rec.setChecked(False)
                return
            rc = self.settings["record"]
            snap = dict(self.settings)
            snap["scene"] = f"{self.settings['scene']}_PGM"
            path = output_path(snap, "video", rc["container"])
            codec = rc["codec"] if rc["codec"] != "prores4444" else "auto"
            self.recorder = Recorder(self.main.ffmpeg, path, (OUT_W, OUT_H), 30, codec, rc["quality"], rc["audio"])
            self.engine.sinks.append(self.recorder)
            self.btn_rec.setText("■ Parar gravação")
            self.lbl_out.setText(f"A gravar: {path.name}")
        elif not on and self.recorder is not None:
            rec, self.recorder = self.recorder, None
            if rec in self.engine.sinks:
                self.engine.sinks.remove(rec)
            err = rec.stop()
            self.btn_rec.setText("● Gravar programa")
            self.lbl_out.setText(f"Guardado: {rec.path}" if rec.path.exists() else f"Falha: {err[-200:]}")

    def _toggle_vcam(self, on: bool) -> None:
        if on and self.vcam is None:
            if getattr(self.main, "vcam", None):
                QMessageBox.information(self, "Mesa", "A câmara virtual já está a ser usada pela janela principal. Desligue-a lá primeiro.")
                self.chk_vcam.setChecked(False)
                return
            try:
                self.vcam = VirtualCam((OUT_W, OUT_H), 30)
                self.engine.sinks.append(self.vcam)
            except Exception as exc:
                self.chk_vcam.setChecked(False)
                QMessageBox.warning(self, "Mesa", f"Câmara virtual indisponível: {exc}")
        elif not on and self.vcam is not None:
            if self.vcam in self.engine.sinks:
                self.engine.sinks.remove(self.vcam)
            self.vcam.close()
            self.vcam = None

    def program_jpeg(self) -> bytes:
        img = self.engine.program_frame
        if img is None:
            return b""
        ok, buf = cv2.imencode(".jpg", cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else b""

    def state(self) -> dict:
        st = self.engine.st
        return {"program": st.program, "preview": st.preview, "ftb": st.ftb_on,
                "labels": [self.engine.label(i) for i in range(MAX_INPUTS)]}

    # --- atualização -------------------------------------------------------------------------
    def _show_multiview(self, img: np.ndarray) -> None:
        q = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format.Format_BGR888)
        self.mv.setPixmap(QPixmap.fromImage(q).scaled(self.mv.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))

    def _update_tally(self, program: int, preview: int) -> None:
        for i, b in enumerate(self.pgm_buttons):
            b.setChecked(i == program)
        for i, b in enumerate(self.pvw_buttons):
            b.setChecked(i == preview)
        self.btn_auto.setChecked(self.engine.st.running)
        src = self.engine.sources[preview]
        ptz = getattr(src, "ptz", None) if src else None
        self.lbl_cam.setText(f"{self.engine.label(preview)}" + ("  · PTZ" if ptz else "  · sem PTZ"))

    def _refresh_labels(self) -> None:
        for combo in (self.cmb_pip, self.cmb_keybg):
            cur = combo.currentIndex()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([self.engine.label(i) for i in range(MAX_INPUTS)])
            combo.setCurrentIndex(cur if cur >= 0 else 1)
            combo.blockSignals(False)
        for i in range(MAX_INPUTS):
            name = self.engine.label(i)
            for b in (self.pgm_buttons[i], self.pvw_buttons[i]):
                b.setToolTip(name)
                short = name.split(" ", 1)[1][:7] if " " in name else ""
                b.setText(f"{i + 1}\n{short}")
        self._update_tally(self.engine.st.program, self.engine.st.preview)

    def _tick(self) -> None:
        st = self.engine.st
        self.btn_auto.setChecked(st.running)
        self.btn_ftb.setChecked(st.ftb_on)
        rec = f" · REC {int(time.time() - self.recorder.started.timestamp())} s" if self.recorder else ""
        self.statusBar().showMessage(f"Mesa {self.engine.stats['fps']:.0f} fps · {self.engine.stats['ms']:.0f} ms/frame{rec}")

    # --- teclado (estilo ATEM) -----------------------------------------------------------
    def keyPressEvent(self, ev) -> None:
        key, mods = ev.key(), ev.modifiers()
        if isinstance(self.focusWidget(), QLineEdit):
            return super().keyPressEvent(ev)
        d = key - Qt.Key.Key_1
        if 0 <= d < MAX_INPUTS:
            (self.engine.set_program if mods & Qt.KeyboardModifier.ControlModifier else self.engine.set_preview)(d)
        elif key == Qt.Key.Key_Space:
            self.engine.cut()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.engine.auto()
        elif key == Qt.Key.Key_F:
            self.engine.toggle_ftb()
        elif key == Qt.Key.Key_P:
            self.chk_pip.toggle()
        else:
            return super().keyPressEvent(ev)

    def closeEvent(self, ev) -> None:
        if self.recorder:
            self._toggle_record(False)
        if self.vcam:
            self._toggle_vcam(False)
        self._save_inputs()
        self.engine.stop()
        self.main.on_switcher_closed()
        super().closeEvent(ev)
