"""Janela principal do Alflor Studio."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTabWidget,
    QToolButton, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .bridge import Bridge, BridgeError
from .capture import CaptureThread
from .config import APP_DATA, Settings
from .integrations import Gamepad, OBSLink, apply_update, check_update, restart_app
from .media import CODECS, Recorder, VirtualCam, best_sharpness, find_ffmpeg, output_path, save_image, stack_frames
from .pipeline import LOOKS, Pipeline
from .ptz import EASINGS, DigitalBackend, Keyframe, PhysicalBackend, PTZController
from .remote import RemoteServer
from .tracking import SHOT_FACE, TARGETS, YUNET_PATH, AutoFramer, SubjectTracker, download_yunet
from .widgets import JoystickWidget, PreviewWidget

AMP_LABELS = {
    "brightness": "Brilho", "contrast": "Contraste", "hue": "Tonalidade", "saturation": "Saturação",
    "sharpness": "Nitidez", "gamma": "Gama", "color": "Cor", "whitebalance": "Balanço de brancos (K)",
    "backlight": "Contraluz", "gain": "Ganho",
}
CAM_EXTRA = {"focus": "Foco", "exposure": "Exposição", "iris": "Íris"}
TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QPlainTextEdit)
SHORTCUTS = """Setas / WASD ...... mover (Shift = rápido, Ctrl = preciso)
+ / E / PgUp ...... zoom in      − / Q / PgDn ...... zoom out
Home / H .......... posição inicial   Esc ...... parar tudo
1 – 9 ............. ir para preset     Ctrl+1 – 9 ...... guardar preset
Espaço ............ foto     R ...... gravar     T ...... tracking
G grelha · Z zebras · P peaking · N noite · B desfoque · M espelho
F11 / duplo clique . ecrã inteiro     Ctrl+P ...... reproduzir travelling
Rato: clique = centrar ali · arrastar = mover · roda = zoom
       Ctrl + clique = seguir esse objeto (contorno automático)
       botão direito arrastado (ou Shift + arrastar) = escolher sujeito
Comando Xbox: stick = mover · gatilhos = zoom · A foto · B gravar
       X tracking · Y início · LB/RB presets · Start parar"""

QSS = """
* { font-family: 'Segoe UI'; font-size: 10pt; color: #e8ecf2; }
QMainWindow, QWidget { background: #0e1116; }
QTabWidget::pane { border: 1px solid #2a313c; border-radius: 6px; top: -1px; }
QTabBar::tab { background: #151a21; padding: 7px 12px; border: 1px solid #2a313c; border-bottom: none;
               border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; color: #8b95a5; }
QTabBar::tab:selected { background: #1d232c; color: #ff8a3d; }
QGroupBox { border: 1px solid #2a313c; border-radius: 6px; margin-top: 14px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #8b95a5; }
QPushButton, QToolButton { background: #1b2129; border: 1px solid #2a313c; border-radius: 6px; padding: 6px 10px; }
QPushButton:hover, QToolButton:hover { border-color: #ff8a3d; }
QPushButton:checked, QToolButton:checked { background: #3a2617; border-color: #ff8a3d; color: #ffb27a; }
QPushButton#rec { color: #ff5b68; font-weight: 700; }
QPushButton#rec:checked { background: #ff3b4a; color: white; }
QPushButton#big { font-size: 11pt; font-weight: 600; padding: 8px 14px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {
    background: #151a21; border: 1px solid #2a313c; border-radius: 5px; padding: 4px 6px; }
QSlider::groove:horizontal { height: 4px; background: #2a313c; border-radius: 2px; }
QSlider::handle:horizontal { background: #ff8a3d; width: 14px; margin: -6px 0; border-radius: 7px; }
QStatusBar { color: #8b95a5; }
QScrollArea { border: none; }
QLabel#hint { color: #8b95a5; }
"""


class MainWindow(QMainWindow):
    notify = Signal(str)
    photo_saved = Signal(str)
    update_found = Signal(dict)

    def __init__(self, test_mode: bool = False) -> None:
        super().__init__()
        self.test_mode = test_mode  # teste automático: sem teclado e sem gravar definições
        self.settings = Settings.load()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 900)
        self.pipeline = Pipeline(self.settings["pipeline"])
        self.tracker = SubjectTracker()
        self.tracker.mode = self.settings["tracking"]["mode"] if self.settings["tracking"]["mode"] != "subject" else "face"
        self.ptz = PTZController(self.settings, self)
        self.framer = AutoFramer(self.settings["tracking"], self.ptz, self.pipeline)
        self.obs = OBSLink()
        self.gamepad = Gamepad()
        self.capture: CaptureThread | None = None
        self.recorder: Recorder | None = None
        self.vcam: VirtualCam | None = None
        self.remote: RemoteServer | None = None
        self.ffmpeg = find_ffmpeg()
        self.device = ""
        self.devices: dict = {"video": [], "audio": []}
        self.cam_ranges: dict = {}
        self._held_keys: set[int] = set()
        self._keyframes: list[Keyframe] = []
        self._calib_state: dict | None = None
        self._gp_last_vel = (0.0, 0.0, 0.0)
        try:
            self.bridge: Bridge | None = Bridge()
        except Exception as exc:
            self.bridge = None
            QTimer.singleShot(500, lambda: QMessageBox.warning(self, APP_NAME, f"Controlo DirectShow indisponível; só PTZ digital.\n\n{exc}"))

        self._build_ui()
        self.notify.connect(self.statusBar().showMessage)
        self.photo_saved.connect(self._on_photo_saved)
        self.update_found.connect(self._on_update_found)
        self.ptz.changed.connect(self._on_ptz_changed)
        QApplication.instance().installEventFilter(self)

        self.ui_timer = QTimer(self, interval=33, timeout=self._ui_tick)
        self.ui_timer.start()
        self.refresh_devices(select=self.settings["device"])
        if self.settings["remote"]["enabled"]:
            QTimer.singleShot(800, lambda: self.chk_remote.setChecked(True))
        if self.settings["obs"]["auto_connect"]:
            QTimer.singleShot(1500, self.obs_connect)
        if self.settings["updates"]["check"] and time.time() - self.settings["updates"].get("last_check", 0) > 6 * 3600:
            threading.Thread(target=self._check_update_bg, daemon=True).start()

    # ======================================================================= UI
    def _build_ui(self) -> None:
        self.setStyleSheet(QSS)
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        # barra superior
        top = QHBoxLayout()
        self.cmb_device = QComboBox(minimumWidth=260)
        self.cmb_device.activated.connect(lambda _i: self.open_device(self.cmb_device.currentText()))
        btn_refresh = QToolButton(text="⟳", toolTip="Procurar câmaras")
        btn_refresh.clicked.connect(lambda: self.refresh_devices(select=self.device))
        self.lbl_mode = QLabel("", objectName="hint")
        top.addWidget(QLabel("Câmara:"))
        top.addWidget(self.cmb_device)
        top.addWidget(btn_refresh)
        top.addWidget(self.lbl_mode)
        top.addStretch(1)
        self.btn_photo = QPushButton("📷  Foto", objectName="big", toolTip="Espaço")
        self.btn_photo.clicked.connect(lambda: self.take_photo("single"))
        self.btn_rec = QPushButton("●  Gravar", objectName="rec", checkable=True, toolTip="R")
        self.btn_rec.setMinimumWidth(130)
        self.btn_rec.clicked.connect(self.toggle_record)
        self.btn_track = QPushButton("◎  Seguir", objectName="big", checkable=True, toolTip="T")
        self.btn_track.toggled.connect(self.set_tracking)
        btn_home = QPushButton("⌂  Início", objectName="big", toolTip="Home")
        btn_home.clicked.connect(lambda: self.ptz.home())
        for b in (self.btn_photo, self.btn_rec, self.btn_track, btn_home):
            top.addWidget(b)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.preview = PreviewWidget(self.settings)
        self.preview.clicked.connect(self.click_to_center)
        self.preview.drag_velocity.connect(lambda vx, vy: self.ptz.set_velocity(pan=vx, tilt=vy))
        self.preview.drag_end.connect(lambda: self.ptz.set_velocity(pan=0, tilt=0))
        self.preview.wheel_zoom.connect(self._wheel_zoom)
        self.preview.subject_selected.connect(self.select_subject)
        self.preview.click_track.connect(self.track_at)
        self.preview.double_clicked.connect(self.toggle_fullscreen)
        split.addWidget(self.preview)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(400)
        self.tabs.setMaximumWidth(520)
        for title, builder in (("PTZ", self._tab_ptz), ("Imagem", self._tab_image), ("Tracking", self._tab_tracking),
                               ("Captura", self._tab_capture), ("Travelling", self._tab_travel),
                               ("Ligações", self._tab_links), ("Sistema", self._tab_system)):
            scroll = QScrollArea(widgetResizable=True)
            scroll.setWidget(builder())
            self.tabs.addTab(scroll, title)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.lbl_stats = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_stats)

    def _slider(self, lo: int, hi: int, value: int, on_change, scale: float = 1.0) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal, minimum=lo, maximum=hi, value=value)
        s.valueChanged.connect(lambda v: on_change(v / scale if scale != 1.0 else v))
        return s

    # --- PTZ ------------------------------------------------------------------------
    def _tab_ptz(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Movimento")
        gl = QHBoxLayout(g)
        self.joy = JoystickWidget()
        self.joy.moved.connect(lambda x, y: self.ptz.set_velocity(pan=x, tilt=y))
        self.joy.released.connect(lambda: self.ptz.set_velocity(pan=0, tilt=0))
        gl.addWidget(self.joy)
        zl = QVBoxLayout()
        zin = QPushButton("Zoom ＋", autoRepeat=False)
        zout = QPushButton("Zoom －", autoRepeat=False)
        zin.pressed.connect(lambda: self.ptz.set_velocity(zoom=0.6))
        zin.released.connect(lambda: self.ptz.set_velocity(zoom=0))
        zout.pressed.connect(lambda: self.ptz.set_velocity(zoom=-0.6))
        zout.released.connect(lambda: self.ptz.set_velocity(zoom=0))
        zl.addWidget(zin)
        zl.addWidget(zout)
        self.lbl_pos = QLabel("P 0 · T 0 · Z 0", objectName="hint")
        zl.addWidget(self.lbl_pos)
        zl.addStretch(1)
        gl.addLayout(zl)
        lay.addWidget(g)

        g2 = QGroupBox("Controlo")
        f = QFormLayout(g2)
        self.sld_speed = self._slider(5, 100, int(self.settings["ptz"]["speed"] * 100),
                                      lambda v: self.settings["ptz"].__setitem__("speed", v), 100.0)
        f.addRow("Velocidade", self.sld_speed)
        self.sld_zoom = self._slider(0, 1000, 0, self._zoom_abs)
        f.addRow("Zoom", self.sld_zoom)
        self.spn_transition = QDoubleSpinBox(minimum=0, maximum=30, value=2.0, suffix=" s", singleStep=0.5)
        f.addRow("Transição de preset", self.spn_transition)
        self.cmb_easing = QComboBox()
        self.cmb_easing.addItems(list(EASINGS))
        self.cmb_easing.setCurrentText("Cinema")
        f.addRow("Curva", self.cmb_easing)
        lay.addWidget(g2)

        g3 = QGroupBox("Presets  (clique = ir · botão direito = guardar/renomear)")
        grid = QGridLayout(g3)
        self.preset_buttons: dict[int, QToolButton] = {}
        for n in range(1, 10):
            b = QToolButton(text=str(n))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(112, 63))
            b.setMinimumSize(124, 92)
            b.clicked.connect(lambda _=False, n=n: self.recall_preset(n))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, n=n: self._preset_menu(n))
            grid.addWidget(b, (n - 1) // 3, (n - 1) % 3)
            self.preset_buttons[n] = b
        lay.addWidget(g3)

        g4 = QGroupBox("Movimentos de realizador")
        mg = QGridLayout(g4)
        self.spn_motion = QDoubleSpinBox(minimum=0.5, maximum=60, value=5.0, suffix=" s")
        motions = [("Push-in", "push_in"), ("Pull-out", "pull_out"), ("Panorâmica", "pan_sweep"),
                   ("Revelar (tilt)", "tilt_reveal"), ("Arco", "orbit_feel"), ("Chicote", "whip_pan"),
                   ("Crash zoom", "crash_zoom")]
        for i, (label, kind) in enumerate(motions):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=kind: self.ptz.motion(k, self.spn_motion.value()))
            mg.addWidget(b, i // 3, i % 3)
        stop = QPushButton("■ Parar")
        stop.clicked.connect(self.stop_all)
        mg.addWidget(stop, 2, 1)
        mg.addWidget(QLabel("Duração"), 3, 0)
        mg.addWidget(self.spn_motion, 3, 1)
        lay.addWidget(g4)
        lay.addStretch(1)
        return w

    # --- Imagem -----------------------------------------------------------------------
    def _tab_image(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.grp_hw = QGroupBox("Câmara (hardware)")
        self.hw_form = QFormLayout(self.grp_hw)
        lay.addWidget(self.grp_hw)

        g = QGroupBox("Processamento (software, em tempo real)")
        f = QFormLayout(g)
        p = self.settings["pipeline"]
        cmb_dn = QComboBox()
        cmb_dn.addItems(["Desligada", "Leve", "Média", "Forte"])
        cmb_dn.setCurrentIndex(int(p["denoise"]))
        cmb_dn.currentIndexChanged.connect(lambda i: p.__setitem__("denoise", i))
        f.addRow("Redução de ruído", cmb_dn)
        self.chk_night = QCheckBox("Modo noite (levanta sombras + ruído)")
        self.chk_night.setChecked(p["night"])
        self.chk_night.toggled.connect(lambda v: p.__setitem__("night", v))
        f.addRow(self.chk_night)
        f.addRow("Ganho noturno", self._slider(100, 400, int(p["night_gain"] * 100), lambda v: p.__setitem__("night_gain", v), 100.0))
        f.addRow("Nitidez extra", self._slider(0, 150, int(p["sharpen"] * 100), lambda v: p.__setitem__("sharpen", v), 100.0))
        self.cmb_look = QComboBox()
        self.cmb_look.addItems(list(LOOKS))
        self.cmb_look.setCurrentText(p["look"])
        self.cmb_look.currentTextChanged.connect(lambda t: p.__setitem__("look", t))
        f.addRow("Look de cor", self.cmb_look)
        self.chk_blur = QCheckBox("Desfoque de fundo (segue o sujeito)")
        self.chk_blur.setChecked(p["bg_blur"])
        self.chk_blur.toggled.connect(self._toggle_bg_blur)
        f.addRow(self.chk_blur)
        self.chk_mirror = QCheckBox("Espelhar")
        self.chk_mirror.setChecked(p["mirror"])
        self.chk_mirror.toggled.connect(lambda v: p.__setitem__("mirror", v))
        chk_flip = QCheckBox("Inverter (câmara de teto)")
        chk_flip.setChecked(p["flip"])
        chk_flip.toggled.connect(lambda v: p.__setitem__("flip", v))
        row = QHBoxLayout()
        row.addWidget(self.chk_mirror)
        row.addWidget(chk_flip)
        f.addRow(row)
        lay.addWidget(g)

        g2 = QGroupBox("Monitorização (só no ecrã)")
        gl = QGridLayout(g2)
        ov = self.settings["overlays"]
        self.overlay_checks = {}
        for i, (key, label) in enumerate((("grid", "Grelha terços (G)"), ("safe", "Áreas seguras"), ("zebra", "Zebras (Z)"),
                                          ("peaking", "Focus peaking (P)"), ("hist", "Histograma"))):
            c = QCheckBox(label)
            c.setChecked(ov[key])
            c.toggled.connect(lambda v, k=key: ov.__setitem__(k, v))
            gl.addWidget(c, i // 2, i % 2)
            self.overlay_checks[key] = c
        lay.addWidget(g2)
        hint = QLabel("Nota: esta PTZ não expõe foco, exposição nem íris por USB. Se uma câmara os tiver "
                      "(ex.: outra PTZ ou webcam), aparecem automaticamente em «Câmara (hardware)».", objectName="hint", wordWrap=True)
        lay.addWidget(hint)
        lay.addStretch(1)
        return w

    def _rebuild_hw_controls(self, info: dict) -> None:
        while self.hw_form.rowCount():
            self.hw_form.removeRow(0)
        if not info:
            self.hw_form.addRow(QLabel("Sem controlos de hardware para esta câmara.", objectName="hint"))
            return
        for group, labels, setter in (("amp", AMP_LABELS, "set_amp"), ("cam", CAM_EXTRA, "set_cam")):
            for name, label in labels.items():
                r = info.get(group, {}).get(name)
                if not r:
                    continue
                row = QHBoxLayout()
                s = QSlider(Qt.Orientation.Horizontal, minimum=r["min"], maximum=r["max"], value=r["cur"])
                val = QLabel(str(r["cur"]), minimumWidth=44)
                def changed(v, n=name, st=setter, lab=val):
                    lab.setText(str(v))
                    if self.bridge:
                        getattr(self.bridge, st)(n, v)
                s.valueChanged.connect(changed)
                row.addWidget(s, 1)
                row.addWidget(val)
                if r["caps"] & 1:
                    auto = QCheckBox("Auto")
                    auto.setChecked(bool(r["flags"] & 1))
                    s.setEnabled(not auto.isChecked())
                    def auto_changed(on, n=name, st=setter, sl=s):
                        sl.setEnabled(not on)
                        if self.bridge:
                            getattr(self.bridge, st)(n, sl.value(), auto=on)
                    auto.toggled.connect(auto_changed)
                    row.addWidget(auto)
                reset = QToolButton(text="↺", toolTip=f"Repor ({r['def']})")
                reset.clicked.connect(lambda _=False, sl=s, d=r["def"]: sl.setValue(d))
                row.addWidget(reset)
                self.hw_form.addRow(label, row)

    # --- Tracking ------------------------------------------------------------------------
    def _tab_tracking(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        t = self.settings["tracking"]
        g = QGroupBox("Seguir sujeito")
        f = QFormLayout(g)
        self.chk_track = QCheckBox("Ativo (T)")
        self.chk_track.toggled.connect(lambda v: self.btn_track.setChecked(v))
        f.addRow(self.chk_track)
        self.cmb_track_mode = QComboBox()
        for label, key in (("Rosto", "face"), ("Pessoa (corpo)", "person"), ("Objeto escolhido (botão direito)", "subject")):
            self.cmb_track_mode.addItem(label, key)
        self.cmb_track_mode.setCurrentIndex(max(0, self.cmb_track_mode.findData(self.tracker.mode)))
        self.cmb_track_mode.currentIndexChanged.connect(self._track_mode_changed)
        f.addRow("Sujeito", self.cmb_track_mode)
        cmb_target = QComboBox()
        for label, key in (("Centro", "center"), ("Terço esquerdo", "left_third"), ("Terço direito", "right_third")):
            cmb_target.addItem(label, key)
        cmb_target.setCurrentIndex(max(0, cmb_target.findData(t["target"])))
        cmb_target.currentIndexChanged.connect(lambda _i: t.__setitem__("target", cmb_target.currentData()))
        f.addRow("Composição", cmb_target)
        cmb_shot = QComboBox()
        cmb_shot.addItems(list(SHOT_FACE))
        cmb_shot.setCurrentText(t["shot"])
        cmb_shot.currentTextChanged.connect(lambda s: t.__setitem__("shot", s))
        f.addRow("Plano", cmb_shot)
        chk_az = QCheckBox("Zoom automático para manter o plano")
        chk_az.setChecked(t["auto_zoom"])
        chk_az.toggled.connect(lambda v: t.__setitem__("auto_zoom", v))
        f.addRow(chk_az)
        f.addRow("Reatividade", self._slider(0, 100, int(t["sensitivity"] * 100), lambda v: t.__setitem__("sensitivity", v), 100.0))
        chk_lost = QCheckBox("Voltar ao início se perder o sujeito")
        chk_lost.setChecked(t["lost_home"])
        chk_lost.toggled.connect(lambda v: t.__setitem__("lost_home", v))
        f.addRow(chk_lost)
        self.lbl_detector = QLabel("", objectName="hint")
        f.addRow("Detetor", self.lbl_detector)
        lay.addWidget(g)

        g2 = QGroupBox("Precisão")
        v = QVBoxLayout(g2)
        b_cal = QPushButton("Calibrar câmara (mede campo de visão e zoom)")
        b_cal.clicked.connect(self.start_calibration)
        v.addWidget(b_cal)
        self.lbl_calib = QLabel("", objectName="hint", wordWrap=True)
        v.addWidget(self.lbl_calib)
        self.btn_yunet = QPushButton("Ativar detetor de rostos por IA (YuNet, ~230 KB)")
        self.btn_yunet.clicked.connect(self.enable_yunet)
        self.btn_yunet.setVisible(not YUNET_PATH.exists())
        v.addWidget(self.btn_yunet)
        lay.addWidget(g2)
        lay.addWidget(QLabel("Dica: arraste com o botão direito (ou Shift + botão esquerdo) um retângulo sobre qualquer objeto ou pessoa "
                             "para a câmara o seguir. Funciona também em telemóveis e na Canon (PTZ digital).",
                             objectName="hint", wordWrap=True))
        lay.addStretch(1)
        self._update_detector_label()
        return w

    # --- Captura ------------------------------------------------------------------------
    def _tab_capture(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        s = self.settings
        g = QGroupBox("Organização (pronto para Premiere, CapCut, DaVinci)")
        f = QFormLayout(g)
        for key, label in (("project", "Projeto"), ("scene", "Cena")):
            e = QLineEdit(s[key])
            e.textChanged.connect(lambda txt, k=key: s.__setitem__(k, txt))
            f.addRow(label, e)
        take_row = QHBoxLayout()
        self.spn_take = QSpinBox(minimum=1, maximum=999, value=int(s["take"]))
        self.spn_take.valueChanged.connect(lambda v: s.__setitem__("take", v))
        nxt = QPushButton("Próximo take")
        nxt.clicked.connect(lambda: self.spn_take.setValue(self.spn_take.value() + 1))
        take_row.addWidget(self.spn_take)
        take_row.addWidget(nxt)
        f.addRow("Take", take_row)
        out_row = QHBoxLayout()
        self.lbl_out = QLineEdit(s["output_dir"], readOnly=True)
        b_choose = QToolButton(text="…")
        b_choose.clicked.connect(self.choose_output)
        b_open = QToolButton(text="Abrir")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(s["output_dir"])))
        out_row.addWidget(self.lbl_out, 1)
        out_row.addWidget(b_choose)
        out_row.addWidget(b_open)
        f.addRow("Pasta", out_row)
        onedrive = os.environ.get("OneDrive")
        if onedrive:
            b_cloud = QPushButton("Guardar no OneDrive (sincroniza na cloud)")
            b_cloud.clicked.connect(lambda: self._set_output(str(Path(onedrive) / "Alflor Studio")))
            f.addRow(b_cloud)
        lay.addWidget(g)

        g2 = QGroupBox("Fotografia")
        f2 = QFormLayout(g2)
        ph = s["photo"]
        cmb_fmt = QComboBox()
        for label, key in (("PNG (sem perdas)", "png"), ("JPEG qualidade máxima", "jpg"), ("TIFF (edição)", "tiff")):
            cmb_fmt.addItem(label, key)
        cmb_fmt.setCurrentIndex(max(0, cmb_fmt.findData(ph["format"])))
        cmb_fmt.currentIndexChanged.connect(lambda _i: ph.__setitem__("format", cmb_fmt.currentData()))
        f2.addRow("Formato", cmb_fmt)
        chk_look = QCheckBox("Aplicar ajustes e look à foto")
        chk_look.setChecked(ph["apply_look"])
        chk_look.toggled.connect(lambda v: ph.__setitem__("apply_look", v))
        f2.addRow(chk_look)
        grid = QGridLayout()
        for i, (label, mode) in enumerate((("Foto", "single"), ("Temporizador 3 s", "timer3"), ("Temporizador 10 s", "timer10"),
                                           ("Burst: melhor de N", "burst"), ("Noite: empilhar N", "stack"))):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, m=mode: self.take_photo(m))
            grid.addWidget(b, i // 2, i % 2)
        f2.addRow(grid)
        nrow = QHBoxLayout()
        sb = QSpinBox(minimum=2, maximum=30, value=ph["burst"], prefix="burst ")
        sb.valueChanged.connect(lambda v: ph.__setitem__("burst", v))
        ss = QSpinBox(minimum=2, maximum=60, value=ph["stack"], prefix="empilhar ")
        ss.valueChanged.connect(lambda v: ph.__setitem__("stack", v))
        nrow.addWidget(sb)
        nrow.addWidget(ss)
        f2.addRow("N", nrow)
        lay.addWidget(g2)

        g3 = QGroupBox("Vídeo")
        f3 = QFormLayout(g3)
        rc = s["record"]
        cmb_codec = QComboBox()
        for key, label in CODECS.items():
            cmb_codec.addItem(label, key)
        cmb_codec.setCurrentIndex(max(0, cmb_codec.findData(rc["codec"])))
        cmb_codec.currentIndexChanged.connect(lambda _i: rc.__setitem__("codec", cmb_codec.currentData()))
        f3.addRow("Codec", cmb_codec)
        sq = QSpinBox(minimum=12, maximum=35, value=rc["quality"], toolTip="Menor = mais qualidade (18–22 recomendado)")
        sq.valueChanged.connect(lambda v: rc.__setitem__("quality", v))
        f3.addRow("Qualidade (CRF/QP)", sq)
        cmb_cont = QComboBox()
        cmb_cont.addItems(["mp4", "mov", "mkv"])
        cmb_cont.setCurrentText(rc["container"])
        cmb_cont.currentTextChanged.connect(lambda t: rc.__setitem__("container", t))
        f3.addRow("Contentor", cmb_cont)
        self.cmb_audio = QComboBox()
        self.cmb_audio.currentIndexChanged.connect(lambda _i: rc.__setitem__("audio", self.cmb_audio.currentData() or ""))
        f3.addRow("Áudio", self.cmb_audio)
        f3.addRow(QLabel("A PTZ não tem microfone. Use um microfone USB, de lapela sem fios (recetor USB) "
                         "ou o microfone do telemóvel via Iriun/iVCam.", objectName="hint", wordWrap=True))
        if not self.ffmpeg:
            f3.addRow(QLabel("⚠ FFmpeg não encontrado: instale com  winget install Gyan.FFmpeg", wordWrap=True))
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    # --- Travelling -----------------------------------------------------------------------
    def _tab_travel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Crie trajetórias de câmara ponto a ponto: posicione a câmara, defina a duração "
                             "e a curva, e adicione. Reproduza uma vez ou em ciclo.", objectName="hint", wordWrap=True))
        self.lst_keys = QListWidget()
        lay.addWidget(self.lst_keys, 1)
        f = QFormLayout()
        self.spn_kf_dur = QDoubleSpinBox(minimum=0, maximum=120, value=4.0, suffix=" s")
        self.spn_kf_hold = QDoubleSpinBox(minimum=0, maximum=120, value=0.0, suffix=" s")
        self.cmb_kf_ease = QComboBox()
        self.cmb_kf_ease.addItems(list(EASINGS))
        self.cmb_kf_ease.setCurrentText("Cinema")
        f.addRow("Duração até ao ponto", self.spn_kf_dur)
        f.addRow("Pausa no ponto", self.spn_kf_hold)
        f.addRow("Curva", self.cmb_kf_ease)
        lay.addLayout(f)
        grid = QGridLayout()
        b_add = QPushButton("＋ Posição atual")
        b_add.clicked.connect(self.add_keyframe)
        b_preset = QPushButton("＋ De um preset…")
        b_preset.clicked.connect(self.add_keyframe_from_preset)
        b_del = QPushButton("− Remover")
        b_del.clicked.connect(self.remove_keyframe)
        b_clear = QPushButton("Limpar")
        b_clear.clicked.connect(lambda: (self._keyframes.clear(), self._refresh_keys()))
        self.chk_loop = QCheckBox("Ciclo contínuo")
        b_play = QPushButton("▶ Reproduzir (Ctrl+P)")
        b_play.clicked.connect(self.play_travel)
        b_stop = QPushButton("■ Parar")
        b_stop.clicked.connect(self.stop_all)
        b_save = QPushButton("Guardar…")
        b_save.clicked.connect(self.save_sequence)
        b_load = QPushButton("Abrir…")
        b_load.clicked.connect(self.load_sequence)
        for i, wdg in enumerate((b_add, b_preset, b_del, b_clear, self.chk_loop, b_play, b_stop, b_save, b_load)):
            grid.addWidget(wdg, i // 2, i % 2)
        lay.addLayout(grid)
        return w

    # --- Ligações ---------------------------------------------------------------------------
    def _tab_links(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("OBS Studio")
        f = QFormLayout(g)
        self.lbl_obs = QLabel("desligado", objectName="hint", wordWrap=True)
        b_obs = QPushButton("Ligar ao OBS")
        b_obs.clicked.connect(self.obs_connect)
        f.addRow(self.lbl_obs, b_obs)
        srow = QHBoxLayout()
        self.cmb_scene = QComboBox()
        b_scene = QPushButton("Mudar cena")
        b_scene.clicked.connect(lambda: self._obs_do(lambda: self.obs.set_scene(self.cmb_scene.currentText())))
        srow.addWidget(self.cmb_scene, 1)
        srow.addWidget(b_scene)
        f.addRow("Cena", srow)
        orow = QHBoxLayout()
        b_orec = QPushButton("Gravar no OBS")
        b_orec.clicked.connect(lambda: self._obs_do(lambda: self.obs.stop_record() if self.obs.record_status() else self.obs.start_record()))
        b_ostream = QPushButton("Live on/off")
        b_ostream.clicked.connect(lambda: self._obs_do(self.obs.toggle_stream))
        orow.addWidget(b_orec)
        orow.addWidget(b_ostream)
        f.addRow(orow)
        chk_sync = QCheckBox("Ao gravar aqui, gravar também no OBS")
        chk_sync.setChecked(self.settings["obs"]["sync_record"])
        chk_sync.toggled.connect(lambda v: self.settings["obs"].__setitem__("sync_record", v))
        f.addRow(chk_sync)
        f.addRow(QLabel("No OBS: painel «PTZ Controls» (plugin obs-ptz) e filtro «Face Tracker» já instalados. "
                        "Para enviar a imagem processada daqui, ative a câmara virtual abaixo e no OBS adicione "
                        "«Dispositivo de captura de vídeo → OBS Virtual Camera» — ou use a fonte UVC no OBS e controle o PTZ daqui.",
                        objectName="hint", wordWrap=True))
        lay.addWidget(g)

        g2 = QGroupBox("Câmara virtual")
        v2 = QVBoxLayout(g2)
        self.chk_vcam = QCheckBox("Enviar imagem processada para «OBS Virtual Camera»")
        self.chk_vcam.toggled.connect(self.toggle_vcam)
        v2.addWidget(self.chk_vcam)
        v2.addWidget(QLabel("Aparece no Zoom, Teams, Meet, browsers, CapCut (gravação de ecrã/câmara) e no próprio OBS.",
                            objectName="hint", wordWrap=True))
        lay.addWidget(g2)

        g3 = QGroupBox("Telemóvel / tablet / Stream Deck (Wi-Fi)")
        v3 = QVBoxLayout(g3)
        self.chk_remote = QCheckBox("Ativar controlo remoto na rede local")
        self.chk_remote.toggled.connect(self.toggle_remote)
        v3.addWidget(self.chk_remote)
        self.lbl_qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        v3.addWidget(self.lbl_qr)
        self.txt_remote = QPlainTextEdit(readOnly=True, maximumHeight=120)
        v3.addWidget(self.txt_remote)
        v3.addWidget(QLabel("Leia o QR com a câmara do telemóvel (mesma rede Wi-Fi). O VLC abre o endereço do "
                            "stream; o Bitfocus Companion usa os URLs da API (Stream Deck).", objectName="hint", wordWrap=True))
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    # --- Sistema -----------------------------------------------------------------------------
    def _tab_system(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        g = QGroupBox("Câmara")
        f = QFormLayout(g)
        cmb_res = QComboBox()
        cmb_res.addItems(["max", "3840x2160", "1920x1080", "1280x720"])
        cmb_res.setCurrentText(self.settings["resolution"])
        cmb_res.currentTextChanged.connect(lambda t: (self.settings.__setitem__("resolution", t), self.open_device(self.device)))
        f.addRow("Resolução", cmb_res)
        cmb_ptzmode = QComboBox()
        for label, key in (("Automático (motores se existirem)", "auto"), ("Forçar PTZ digital (recorte)", "digital")):
            cmb_ptzmode.addItem(label, key)
        cmb_ptzmode.setCurrentIndex(max(0, cmb_ptzmode.findData(self.settings["ptz"]["mode"])))
        cmb_ptzmode.currentIndexChanged.connect(lambda _i: (self.settings["ptz"].__setitem__("mode", cmb_ptzmode.currentData()), self.open_device(self.device)))
        f.addRow("Modo PTZ", cmb_ptzmode)
        for key, label in (("invert_pan", "Inverter pan"), ("invert_tilt", "Inverter tilt")):
            c = QCheckBox(label)
            c.setChecked(self.settings["ptz"][key])
            c.toggled.connect(lambda v, k=key: self.settings["ptz"].__setitem__(k, v))
            f.addRow(c)
        lay.addWidget(g)

        g2 = QGroupBox("Atualizações")
        v2 = QVBoxLayout(g2)
        self.lbl_update = QLabel(f"Versão {__version__}", objectName="hint", wordWrap=True)
        v2.addWidget(self.lbl_update)
        b_check = QPushButton("Procurar atualizações agora")
        b_check.clicked.connect(lambda: threading.Thread(target=self._check_update_bg, args=(True,), daemon=True).start())
        v2.addWidget(b_check)
        chk_auto = QCheckBox("Instalar atualizações automaticamente")
        chk_auto.setChecked(self.settings["updates"]["auto_apply"])
        chk_auto.toggled.connect(lambda v: self.settings["updates"].__setitem__("auto_apply", v))
        v2.addWidget(chk_auto)
        lay.addWidget(g2)

        g3 = QGroupBox("Atalhos")
        v3 = QVBoxLayout(g3)
        txt = QPlainTextEdit(SHORTCUTS, readOnly=True)
        txt.setMinimumHeight(260)
        txt.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        v3.addWidget(txt)
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    # ================================================================== dispositivos
    def refresh_devices(self, select: str = "") -> None:
        if self.bridge:
            try:
                self.devices = self.bridge.list_devices()
            except BridgeError as exc:
                self.statusBar().showMessage(str(exc))
        else:
            self.devices = {"video": [f"Câmara {i}" for i in range(4)], "audio": []}
        self.cmb_device.blockSignals(True)
        self.cmb_device.clear()
        self.cmb_device.addItems(self.devices["video"])
        self.cmb_device.blockSignals(False)
        self.cmb_audio.blockSignals(True)
        self.cmb_audio.clear()
        self.cmb_audio.addItem("Sem áudio", "")
        for a in self.devices["audio"]:
            self.cmb_audio.addItem(a, a)
        idx = self.cmb_audio.findData(self.settings["record"]["audio"])
        self.cmb_audio.setCurrentIndex(max(0, idx))
        self.cmb_audio.blockSignals(False)
        target = select if select in self.devices["video"] else (self.devices["video"][0] if self.devices["video"] else "")
        if target:
            self.cmb_device.setCurrentText(target)
            self.open_device(target)

    def open_device(self, name: str) -> None:
        if not name:
            return
        if self.recorder:
            self.toggle_record(False)
        self._stop_capture()
        self.device = name
        self.settings["device"] = name
        index = self.devices["video"].index(name) if name in self.devices["video"] else 0
        info: dict = {}
        if self.bridge:
            try:
                info = self.bridge.open(index)
            except BridgeError as exc:
                self.statusBar().showMessage(f"Controlo de hardware indisponível: {exc}")
        self.cam_ranges = info.get("cam", {})
        motors = {k: v for k, v in self.cam_ranges.items() if k in ("pan", "tilt", "zoom")}
        self.pipeline.set_eptz(0.5, 0.5, 1.0)
        if motors and self.settings["ptz"]["mode"] != "digital":
            self.ptz.attach(PhysicalBackend(self.bridge, motors), motors)
            self.lbl_mode.setText("PTZ com motores")
        else:
            self.ptz.attach(DigitalBackend(self.pipeline), {k: v for k, v in DigitalBackend.RANGES.items()})
            self.lbl_mode.setText("PTZ digital (recorte)")
        self.framer.calibration = self.settings.device_calibration(name)
        self._show_calibration()
        self._rebuild_hw_controls(info)
        self._refresh_presets()
        self.preview.image = None
        self.preview.message = f"A abrir {name}…"
        self.preview.update()
        self.capture = CaptureThread(index, self.settings, self.pipeline, self.tracker, self)
        self.capture.frame_ready.connect(self._on_frame)
        self.capture.status.connect(self.statusBar().showMessage)
        self.capture.failed.connect(self._on_capture_failed)
        self.capture.opened.connect(self._on_capture_opened)
        self.capture.start()

    def _stop_capture(self) -> None:
        if self.vcam:
            self.chk_vcam.setChecked(False)
        if self.capture:
            self.capture.stop()
            self.capture = None

    def _on_capture_opened(self, w: int, h: int, fps: float) -> None:
        self.statusBar().showMessage(f"{self.device}: {w}×{h} @ {fps:.0f} fps", 5000)
        self.ptz.resync()  # esta PTZ só aceita comandos com vídeo ativo

    def _on_capture_failed(self, msg: str) -> None:
        self.preview.image = None
        extra = "\n\nModo só-controlo: o PTZ continua a funcionar daqui (teclado, rato, telemóvel)." if self.ptz.is_physical else ""
        self.preview.message = msg + extra
        self.preview.update()
        self.statusBar().showMessage(msg)

    # ================================================================== frames / UI tick
    def _on_frame(self, img: QImage, meta: dict) -> None:
        self.preview.composition = self.framer.target_point(self.tracker.mode) if self.tracker.enabled else (0.5, 0.5)
        self.preview.set_frame(img, meta)
        if self.btn_track.isChecked():  # só enquadra se o tracking estiver ligado (não só o desfoque)
            self.framer.step(meta.get("raw_box"), self.tracker.mode)

    def _ui_tick(self) -> None:
        if self.capture:
            w, h = self.capture.size
            self.lbl_stats.setText(f"{w}×{h} · {self.capture.fps:4.1f} fps · {self.ptz.state()['backend']}")
        if self.recorder:
            secs = int((time.time() - self.recorder.started.timestamp()))
            self.preview.rec_time = f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
        if self.remote:
            while not self.remote.commands.empty():
                self._remote_command(self.remote.commands.get_nowait())
        self._poll_gamepad()
        self._calibration_tick()

    def _on_ptz_changed(self, st: dict) -> None:
        self.lbl_pos.setText(f"P {st.get('pan', 0):.0f} · T {st.get('tilt', 0):.0f} · Z {st.get('zoom', 0):.0f}")
        if self.ptz.has("zoom") and not self.sld_zoom.isSliderDown():
            z = self.ptz.axes["zoom"]
            self.sld_zoom.blockSignals(True)
            self.sld_zoom.setValue(int(1000 * (z.value - z.min) / max(1e-6, z.span)))
            self.sld_zoom.blockSignals(False)
        mag = self.framer.magnification()
        self.preview.info = f"{self.device}  ·  zoom ×{mag:.1f}  ·  P {st.get('pan', 0):.0f}  T {st.get('tilt', 0):.0f}"

    # ================================================================== PTZ via rato
    def click_to_center(self, u: float, v: float) -> None:
        """Centra o ponto clicado (física ou digitalmente)."""
        if self.ptz.is_physical:
            x, y = self.pipeline.out_to_raw(u, v)
            mag = self.framer.magnification()
            cal = self.framer.calibration
            self.ptz.nudge(dpan=(x - 0.5) * cal["pan_fw"] / mag, dtilt=-(y - 0.5) * cal["tilt_fh"] / mag)
        else:
            scale = self.pipeline.crop_rect()[2]
            free = max(1e-3, 0.5 - 0.5 * scale)
            self.ptz.nudge(dpan=(u - 0.5) * scale / free * 1000, dtilt=(v - 0.5) * scale / free * 1000)

    def _wheel_zoom(self, direction: int) -> None:
        if self.ptz.has("zoom"):
            self.ptz.nudge(dzoom=direction * self.ptz.axes["zoom"].span * 0.04)

    def _zoom_abs(self, v: int) -> None:
        if self.ptz.has("zoom") and self.sld_zoom.isSliderDown():
            z = self.ptz.axes["zoom"]
            self.ptz.move_to(zoom=z.min + z.span * v / 1000)

    def stop_all(self) -> None:
        self.ptz.stop()
        self._held_keys.clear()

    # ================================================================== presets
    def _thumb_path(self, n: int) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in self.device)
        return APP_DATA / "thumbs" / f"{safe}_{n}.jpg"

    def save_preset(self, n: int, name: str | None = None) -> None:
        presets = self.settings.device_presets(self.device)
        st = self.ptz.state()
        old = presets.get(str(n), {})
        entry = {"p": st.get("pan", 0), "t": st.get("tilt", 0), "z": st.get("zoom", 0), "name": name or old.get("name", f"Preset {n}")}
        img = self.capture.latest() if self.capture else None
        if img is not None:
            tp = self._thumb_path(n)
            tp.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(tp), cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])
            entry["thumb"] = str(tp)
        presets[str(n)] = entry
        self.settings.save()
        self._refresh_presets()
        self.statusBar().showMessage(f"Preset {n} guardado", 3000)

    def recall_preset(self, n: int) -> None:
        p = self.settings.device_presets(self.device).get(str(n))
        if not p:
            self.statusBar().showMessage(f"Preset {n} vazio - Ctrl+{n} ou botão direito para guardar", 3000)
            return
        self.ptz.move_to(p["p"], p["t"], p["z"], duration=self.spn_transition.value(), easing=self.cmb_easing.currentText())
        self.statusBar().showMessage(f"→ {p.get('name', n)}", 2000)

    def _preset_menu(self, n: int) -> None:
        menu = QMenu(self)
        menu.addAction("Guardar posição atual", lambda: self.save_preset(n))
        menu.addAction("Renomear…", lambda: self._rename_preset(n))
        menu.addAction("Limpar", lambda: (self.settings.device_presets(self.device).pop(str(n), None), self.settings.save(), self._refresh_presets()))
        menu.exec(self.preset_buttons[n].mapToGlobal(self.preset_buttons[n].rect().bottomLeft()))

    def _rename_preset(self, n: int) -> None:
        presets = self.settings.device_presets(self.device)
        if str(n) not in presets:
            self.save_preset(n)
        name, ok = QInputDialog.getText(self, "Renomear preset", "Nome:", text=presets[str(n)].get("name", ""))
        if ok and name:
            presets[str(n)]["name"] = name
            self.settings.save()
            self._refresh_presets()

    def _refresh_presets(self) -> None:
        presets = self.settings.device_presets(self.device)
        for n, b in self.preset_buttons.items():
            p = presets.get(str(n))
            b.setText(f"{n} · {p.get('name', '')}"[:22] if p else f"{n} · vazio")
            thumb = p.get("thumb") if p else None
            b.setIcon(QIcon(thumb) if thumb and Path(thumb).exists() else QIcon())

    # ================================================================== travelling
    def _kf_label(self, i: int, k: Keyframe) -> str:
        return f"{i + 1}.  P {k.pan:.0f}  T {k.tilt:.0f}  Z {k.zoom:.0f}   ·  {k.duration:.1f}s  {k.easing}" + (f"  · pausa {k.hold:.1f}s" if k.hold else "")

    def _refresh_keys(self) -> None:
        self.lst_keys.clear()
        for i, k in enumerate(self._keyframes):
            self.lst_keys.addItem(self._kf_label(i, k))

    def add_keyframe(self, pos: dict | None = None) -> None:
        st = pos or self.ptz.state()
        self._keyframes.append(Keyframe(st.get("pan", 0), st.get("tilt", 0), st.get("zoom", 0),
                                        self.spn_kf_dur.value(), self.spn_kf_hold.value(), self.cmb_kf_ease.currentText()))
        self._refresh_keys()

    def add_keyframe_from_preset(self) -> None:
        presets = self.settings.device_presets(self.device)
        if not presets:
            QMessageBox.information(self, APP_NAME, "Ainda não há presets guardados.")
            return
        items = [f"{n} · {p.get('name', '')}" for n, p in sorted(presets.items())]
        choice, ok = QInputDialog.getItem(self, "Adicionar preset", "Preset:", items, 0, False)
        if ok:
            p = presets[choice.split(" ·")[0]]
            self.add_keyframe({"pan": p["p"], "tilt": p["t"], "zoom": p["z"]})

    def remove_keyframe(self) -> None:
        row = self.lst_keys.currentRow()
        if 0 <= row < len(self._keyframes):
            self._keyframes.pop(row)
            self._refresh_keys()

    def play_travel(self) -> None:
        if not self._keyframes:
            self.statusBar().showMessage("Adicione pontos ao travelling primeiro", 3000)
            return
        self.ptz.play(list(self._keyframes), loop=self.chk_loop.isChecked())

    def save_sequence(self) -> None:
        name, ok = QInputDialog.getText(self, "Guardar travelling", "Nome:")
        if ok and name:
            self.settings["sequences"][name] = [k.__dict__ for k in self._keyframes]
            self.settings.save()

    def load_sequence(self) -> None:
        seqs = self.settings["sequences"]
        if not seqs:
            QMessageBox.information(self, APP_NAME, "Não há travellings guardados.")
            return
        name, ok = QInputDialog.getItem(self, "Abrir travelling", "Travelling:", sorted(seqs), 0, False)
        if ok:
            self._keyframes = [Keyframe(**k) for k in seqs[name]]
            self._refresh_keys()

    # ================================================================== tracking
    def set_tracking(self, on: bool) -> None:
        self.tracker.enabled = on or self.settings["pipeline"]["bg_blur"]
        if not on:
            if not self.tracker.enabled:
                self.tracker.reset()
            if self.ptz.is_physical:
                self.ptz.set_velocity(pan=0, tilt=0)
        for w in (self.btn_track, self.chk_track):
            if w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        self.statusBar().showMessage("Tracking ligado" if on else "Tracking desligado", 2000)

    def _track_mode_changed(self, _i: int) -> None:
        mode = self.cmb_track_mode.currentData()
        self.settings["tracking"]["mode"] = mode
        self.tracker.set_mode(mode)
        self._update_detector_label()
        if mode == "subject":
            self.statusBar().showMessage("Arraste com o botão direito sobre o sujeito a seguir", 5000)

    def select_subject(self, u: float, v: float, w: float, h: float) -> None:
        x0, y0 = self.pipeline.out_to_raw(u, v)
        x1, y1 = self.pipeline.out_to_raw(u + w, v + h)
        self.tracker.select((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        self.cmb_track_mode.blockSignals(True)
        self.cmb_track_mode.setCurrentIndex(self.cmb_track_mode.findData("subject"))
        self.cmb_track_mode.blockSignals(False)
        self._update_detector_label()
        self.set_tracking(True)

    def track_at(self, u: float, v: float) -> None:
        """Ctrl + clique: encontra o objeto pela cor à volta do ponto e passa a segui-lo."""
        img = self.capture.latest(processed=False) if self.capture else None
        x, y = self.pipeline.out_to_raw(u, v)
        box = None
        if img is not None:
            h, w = img.shape[:2]
            sw = 480
            small = cv2.GaussianBlur(cv2.resize(img, (sw, int(h * sw / w)), interpolation=cv2.INTER_AREA), (5, 5), 0)
            sh = small.shape[0]
            seed = (min(sw - 1, int(x * sw)), min(sh - 1, int(y * sh)))
            mask = np.zeros((sh + 2, sw + 2), np.uint8)
            flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
            cv2.floodFill(small, mask, seed, (0, 0, 0), (22, 22, 22), (22, 22, 22), flags)
            ys, xs = np.nonzero(mask[1:-1, 1:-1])
            if len(xs) > 40:
                bx, by, bw, bh = xs.min() / sw, ys.min() / sh, (xs.max() - xs.min() + 1) / sw, (ys.max() - ys.min() + 1) / sh
                if 0.02 < bw < 0.6 and 0.02 < bh < 0.7:  # ignora fundos inteiros (ex.: a parede verde)
                    box = (bx, by, bw, bh)
        if box is None:  # caixa média à volta do clique
            box = (max(0.0, x - 0.07), max(0.0, y - 0.1), 0.14, 0.2)
        self.tracker.select(box)
        self.cmb_track_mode.blockSignals(True)
        self.cmb_track_mode.setCurrentIndex(self.cmb_track_mode.findData("subject"))
        self.cmb_track_mode.blockSignals(False)
        self._update_detector_label()
        self.set_tracking(True)
        self.statusBar().showMessage("A seguir o objeto escolhido (Esc para parar)", 4000)

    def _update_detector_label(self) -> None:
        self.lbl_detector.setText(self.tracker.detector_name)

    def _toggle_bg_blur(self, on: bool) -> None:
        self.settings["pipeline"]["bg_blur"] = on
        # o desfoque precisa de saber onde está o sujeito: liga a deteção sem mover a câmara
        self.tracker.enabled = on or self.btn_track.isChecked()
        if on:
            self.statusBar().showMessage("Desfoque ativo: deteção do sujeito ligada (a câmara não se move)", 4000)

    def enable_yunet(self) -> None:
        def work():
            try:
                download_yunet()
                self.notify.emit("Detetor IA YuNet instalado")
            except Exception as exc:
                self.notify.emit(f"Falha ao descarregar YuNet: {exc}")
        threading.Thread(target=work, daemon=True).start()
        QTimer.singleShot(4000, lambda: (self.tracker.load_yunet(), self._update_detector_label(), self.btn_yunet.setVisible(not YUNET_PATH.exists())))

    # --- calibração ----------------------------------------------------------------------
    def _show_calibration(self) -> None:
        c = self.framer.calibration
        measured = self.device in self.settings["calibration"]
        self.lbl_calib.setText(("Calibrada" if measured else "Valores estimados (calibre para mais precisão)") +
                               f": pan {c['pan_fw']:.0f} u/ecrã · tilt {c['tilt_fh']:.0f} u/ecrã · zoom ótico ×{c['zoom_max_mag']:.1f}")

    def start_calibration(self) -> None:
        if not (self.ptz.is_physical and self.capture):
            QMessageBox.information(self, APP_NAME, "A calibração precisa de uma PTZ com motores e imagem ativa.")
            return
        if QMessageBox.question(self, APP_NAME, "A câmara vai mover-se sozinha durante ~25 s e voltar à posição atual.\n"
                                "Aponte para uma cena com detalhe (não uma parede lisa). Continuar?") != QMessageBox.StandardButton.Yes:
            return
        self.set_tracking(False)
        st = self.ptz.state()
        z = self.ptz.axes.get("zoom")
        self._calib_state = {"step": "zoom_min", "t": time.monotonic(), "orig": st, "res": {},
                             "D_pan": 120.0, "D_tilt": 80.0}
        if z:
            self.ptz.move_to(zoom=z.min)
        self.statusBar().showMessage("Calibração: a preparar…")

    def _calib_gray(self):
        img = self.capture.latest(processed=False) if self.capture else None
        if img is None:
            return None
        g = cv2.cvtColor(cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        return np.float32(g)

    def _calibration_tick(self) -> None:
        cs = self._calib_state
        if not cs:
            return
        now = time.monotonic()
        wait = {"zoom_min": 4.0, "pan_a": 0.5, "pan_b": 2.5, "tilt_b": 2.5, "zoom_b": 5.0, "restore": 0.1}
        if now - cs["t"] < wait.get(cs["step"], 2.0):
            return
        cs["t"] = now
        win = cv2.createHanningWindow((640, 360), cv2.CV_32F)
        step = cs["step"]
        try:
            if step == "zoom_min":
                cs["step"] = "pan_a"
            elif step == "pan_a":
                cs["A"] = self._calib_gray()
                cs["pan0"] = self.ptz.axes["pan"].value if self.ptz.has("pan") else 0
                self.ptz.nudge(dpan=cs["D_pan"])
                cs["step"] = "pan_b"
                self.statusBar().showMessage("Calibração: pan…")
            elif step == "pan_b":
                B = self._calib_gray()
                (dx, dy), resp = cv2.phaseCorrelate(cs["A"], B, win)
                if abs(dx) > 2 and resp > 0.03:
                    cs["res"]["pan_fw"] = -cs["D_pan"] / (dx / 640.0)
                self.ptz.nudge(dpan=-cs["D_pan"])
                cs["step"] = "tilt_a"
            elif step == "tilt_a":
                cs["A"] = self._calib_gray()
                self.ptz.nudge(dtilt=cs["D_tilt"])
                cs["step"] = "tilt_b"
                self.statusBar().showMessage("Calibração: tilt…")
            elif step == "tilt_b":
                B = self._calib_gray()
                (dx, dy), resp = cv2.phaseCorrelate(cs["A"], B, win)
                if abs(dy) > 2 and resp > 0.03:
                    cs["res"]["tilt_fh"] = cs["D_tilt"] / (dy / 360.0)
                self.ptz.nudge(dtilt=-cs["D_tilt"])
                cs["step"] = "zoom_a"
            elif step == "zoom_a":
                cs["A8"] = self.capture.latest(processed=False)
                if self.ptz.has("zoom"):
                    self.ptz.move_to(zoom=self.ptz.axes["zoom"].max)
                cs["step"] = "zoom_b"
                self.statusBar().showMessage("Calibração: zoom…")
            elif step == "zoom_b":
                mag = self._estimate_scale(cs["A8"], self.capture.latest(processed=False))
                if mag and mag > 1.05:
                    cs["res"]["zoom_max_mag"] = mag
                o = cs["orig"]
                self.ptz.move_to(o.get("pan"), o.get("tilt"), o.get("zoom"), duration=2.0)
                cs["step"] = "done"
            elif step == "done":
                res = cs["res"]
                self._calib_state = None
                if res:
                    cal = self.settings["calibration"].setdefault(self.device, {})
                    cal.update({k: round(v, 1) for k, v in res.items()})
                    self.settings.save()
                    self.framer.calibration = self.settings.device_calibration(self.device)
                self._show_calibration()
                self.statusBar().showMessage("Calibração concluída" + ("" if len(res) == 3 else f" (parcial: {', '.join(res) or 'nada medido'})"), 6000)
        except Exception as exc:
            self._calib_state = None
            self.statusBar().showMessage(f"Calibração interrompida: {exc}")

    @staticmethod
    def _estimate_scale(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
        """Razão de ampliação entre duas imagens (zoom mínimo vs máximo) com ORB."""
        if a is None or b is None:
            return None
        orb = cv2.ORB_create(3000)
        ga = cv2.cvtColor(cv2.resize(a, (960, 540)), cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(cv2.resize(b, (960, 540)), cv2.COLOR_BGR2GRAY)
        ka, da = orb.detectAndCompute(ga, None)
        kb, db = orb.detectAndCompute(gb, None)
        if da is None or db is None:
            return None
        matches = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db), key=lambda m: m.distance)[:400]
        if len(matches) < 12:
            return None
        pa = np.float32([ka[m.queryIdx].pt for m in matches])
        pb = np.float32([kb[m.trainIdx].pt for m in matches])
        M, inliers = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=4)
        if M is None or inliers is None or inliers.sum() < 10:
            return None
        return float(np.hypot(M[0, 0], M[0, 1]))

    # ================================================================== foto / vídeo
    def choose_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta de saída", self.settings["output_dir"])
        if d:
            self._set_output(d)

    def _set_output(self, d: str) -> None:
        self.settings["output_dir"] = d
        self.lbl_out.setText(d)
        self.settings.save()

    def take_photo(self, mode: str = "single") -> None:
        if not self.capture:
            self.statusBar().showMessage("Sem imagem para fotografar")
            return
        if mode in ("timer3", "timer10"):
            self._countdown = 3 if mode == "timer3" else 10
            self.preview.countdown = self._countdown
            def tick():
                self._countdown -= 1
                self.preview.countdown = self._countdown
                self.preview.update()
                if self._countdown <= 0:
                    self._ct_timer.stop()
                    self.take_photo("single")
            self._ct_timer = QTimer(self, interval=1000, timeout=tick)
            self._ct_timer.start()
            return
        ph = self.settings["photo"]
        processed = ph["apply_look"]
        cap = self.capture
        fmt = ph["format"]
        snapshot = {k: self.settings[k] for k in ("output_dir", "project", "scene", "take")}

        def work():
            try:
                if mode == "burst":
                    img = best_sharpness(cap.grab(int(ph["burst"]), processed))
                elif mode == "stack":
                    self.notify.emit(f"A empilhar {ph['stack']} frames - mantenha a câmara parada…")
                    img = stack_frames(cap.grab(int(ph["stack"]), processed))
                else:
                    img = cap.latest(processed)
                if img is None:
                    raise RuntimeError("sem frame")
                path = save_image(img, output_path(snapshot, "photo", fmt), fmt)
                self.photo_saved.emit(str(path))
            except Exception as exc:
                self.notify.emit(f"Falha na foto: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _on_photo_saved(self, path: str) -> None:
        self.preview.countdown = 0
        QApplication.beep()
        self.statusBar().showMessage(f"📷 {path}", 6000)

    def toggle_record(self, on: bool | None = None) -> None:
        on = (self.recorder is None) if on is None or not isinstance(on, bool) else on
        if on and not self.recorder:
            if not (self.capture and self.ffmpeg):
                self.btn_rec.setChecked(False)
                self.statusBar().showMessage("Gravação indisponível (sem imagem ou sem FFmpeg)")
                return
            rc = self.settings["record"]
            path = output_path(self.settings, "video", rc["container"])
            try:
                self.recorder = Recorder(self.ffmpeg, path, self.capture.size, float(self.settings["fps"]),
                                         rc["codec"], rc["quality"], rc["audio"])
            except Exception as exc:
                self.btn_rec.setChecked(False)
                self.statusBar().showMessage(f"Erro a iniciar gravação: {exc}")
                return
            self.capture.sinks.append(self.recorder)
            self.preview.recording = True
            self.btn_rec.setChecked(True)
            self.btn_rec.setText("■  Parar")
            self.statusBar().showMessage(f"● A gravar ({self.recorder.codec}) → {path}")
            if self.settings["obs"]["sync_record"] and self.obs.connected:
                self._obs_do(self.obs.start_record)
        elif not on and self.recorder:
            rec = self.recorder
            self.recorder = None
            if self.capture and rec in self.capture.sinks:
                self.capture.sinks.remove(rec)
            self.preview.recording = False
            self.btn_rec.setChecked(False)
            self.btn_rec.setText("●  Gravar")
            if self.settings["obs"]["sync_record"] and self.obs.connected:
                self._obs_do(self.obs.stop_record)
            def finish():
                err = rec.stop()
                msg = f"Vídeo guardado: {rec.path}  ({rec.frames} frames" + (f", {rec.dropped} descartados" if rec.dropped else "") + ")"
                self.notify.emit(msg if rec.path.exists() else f"Falha na gravação: {err[-300:]}")
            threading.Thread(target=finish, daemon=True).start()

    # ================================================================== ligações
    def obs_connect(self) -> None:
        if self.obs.connect():
            try:
                scenes, current = self.obs.scenes()
                self.cmb_scene.clear()
                self.cmb_scene.addItems(scenes)
                self.cmb_scene.setCurrentText(current)
            except Exception:
                pass
            self.lbl_obs.setText("✓ ligado ao OBS")
        else:
            self.lbl_obs.setText(self.obs.error)

    def _obs_do(self, fn) -> None:
        try:
            fn()
        except Exception as exc:
            self.lbl_obs.setText(f"OBS: {exc}")

    def toggle_vcam(self, on: bool) -> None:
        if on:
            if not self.capture or not self.capture.size[0]:
                self.chk_vcam.setChecked(False)
                return
            try:
                self.vcam = VirtualCam(self.capture.size, float(self.settings["fps"]))
                self.capture.sinks.append(self.vcam)
                self.statusBar().showMessage(f"Câmara virtual ativa: {self.vcam.device}", 5000)
            except Exception as exc:
                self.vcam = None
                self.chk_vcam.blockSignals(True)
                self.chk_vcam.setChecked(False)
                self.chk_vcam.blockSignals(False)
                QMessageBox.warning(self, APP_NAME, f"Não foi possível iniciar a câmara virtual.\n"
                                    f"(Se o OBS tiver a câmara virtual ligada, desligue-a lá.)\n\n{exc}")
        elif self.vcam:
            if self.capture and self.vcam in self.capture.sinks:
                self.capture.sinks.remove(self.vcam)
            self.vcam.close()
            self.vcam = None

    def toggle_remote(self, on: bool) -> None:
        r = self.settings["remote"]
        if on and not self.remote:
            try:
                self.remote = RemoteServer(int(r["port"]), r["token"], self._remote_jpeg, self._remote_state)
            except OSError as exc:
                self.chk_remote.setChecked(False)
                QMessageBox.warning(self, APP_NAME, f"Não foi possível abrir a porta {r['port']}: {exc}")
                return
            url = self.remote.url()
            self.lbl_qr.setPixmap(self._qr_pixmap(url))
            k = r["token"]
            base = url.split("/?")[0]
            self.txt_remote.setPlainText(
                f"Telemóvel:  {url}\n"
                f"VLC (stream):  {base}/stream.mjpg?k={k}\n"
                f"Foto (Companion):  {base}/api/cmd?k={k}&a=photo\n"
                f"Preset 1:  {base}/api/cmd?k={k}&a=preset&n=1\n"
                f"Gravar:  {base}/api/cmd?k={k}&a=record")
            r["enabled"] = True
        elif not on and self.remote:
            self.remote.stop()
            self.remote = None
            self.lbl_qr.clear()
            self.txt_remote.clear()
            r["enabled"] = False
        self.settings.save()

    @staticmethod
    def _qr_pixmap(text: str, size: int = 220) -> QPixmap:
        import qrcode
        qr = qrcode.QRCode(border=2)
        qr.add_data(text)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        img = QImage(n, n, QImage.Format.Format_RGB32)
        for y, row in enumerate(m):
            for x, v in enumerate(row):
                img.setPixel(x, y, 0xFF000000 if v else 0xFFFFFFFF)
        return QPixmap.fromImage(img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def _remote_jpeg(self) -> bytes:
        return self.capture.jpeg() if self.capture else b""

    def _remote_state(self) -> dict:
        return {
            "device": self.device, "fps": round(self.capture.fps, 1) if self.capture else 0,
            "recording": self.recorder is not None, "rec_time": self.preview.rec_time if self.recorder else "",
            "tracking": self.tracker.enabled, "night": self.settings["pipeline"]["night"],
            "bg_blur": self.settings["pipeline"]["bg_blur"],
            "presets": [int(n) for n in self.settings.device_presets(self.device)], "ptz": self.ptz.state(),
        }

    def _remote_command(self, cmd: dict) -> None:
        a = cmd.get("a", "")
        num = lambda key, d=0.0: float(cmd.get(key, d) or 0)
        if a == "vel":
            self.ptz.set_velocity(pan=num("pan") if "pan" in cmd else None, tilt=num("tilt") if "tilt" in cmd else None,
                                  zoom=num("zoom") if "zoom" in cmd else None)
        elif a == "stop":
            self.stop_all()
        elif a == "home":
            self.ptz.home()
        elif a == "preset":
            self.recall_preset(int(num("n", 1)))
        elif a == "preset_save":
            self.save_preset(int(num("n", 1)))
        elif a == "photo":
            self.take_photo(cmd.get("mode", "single"))
        elif a == "record":
            self.toggle_record()
        elif a == "track":
            self.set_tracking(not self.tracker.enabled)
        elif a == "motion":
            self.ptz.motion(cmd.get("kind", "push_in"), num("duration", self.spn_motion.value()))
        elif a == "zoom" and self.ptz.has("zoom"):
            z = self.ptz.axes["zoom"]
            self.ptz.move_to(zoom=z.min + z.span * num("value") / 100.0, duration=num("duration", 1.0))
        elif a == "night":
            self.chk_night.setChecked(not self.chk_night.isChecked())
        elif a == "bgblur":
            self.chk_blur.setChecked(not self.chk_blur.isChecked())
        elif a == "look_next":
            self.cmb_look.setCurrentIndex((self.cmb_look.currentIndex() + 1) % self.cmb_look.count())
        elif a == "look":
            self.cmb_look.setCurrentText(cmd.get("name", "Natural"))

    # ================================================================== comando de jogo
    def _poll_gamepad(self) -> None:
        st = self.gamepad.poll()
        if st is None:
            return
        vel = (st["lx"], st["ly"], (st["rt"] - st["lt"]) or st["ry"])
        if vel != self._gp_last_vel:
            self._gp_last_vel = vel
            self.ptz.set_velocity(pan=vel[0], tilt=vel[1], zoom=vel[2])
        pressed = st["pressed"]
        if "a" in pressed:
            self.take_photo()
        if "b" in pressed:
            self.toggle_record()
        if "x" in pressed:
            self.set_tracking(not self.tracker.enabled)
        if "y" in pressed:
            self.ptz.home()
        if "start" in pressed:
            self.stop_all()
        if pressed & {"lb", "rb", "left", "right"}:
            used = sorted(int(n) for n in self.settings.device_presets(self.device))
            if used:
                cur = getattr(self, "_gp_preset", 0)
                step = -1 if pressed & {"lb", "left"} else 1
                cur = used[(used.index(cur) + step) % len(used)] if cur in used else used[0]
                self._gp_preset = cur
                self.recall_preset(cur)

    # ================================================================== teclado
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        t = ev.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if self.test_mode:
            return True  # engole as teclas: nada de fotos, gravações ou movimentos acidentais
        if not self.isActiveWindow():
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, TEXT_INPUTS) or (isinstance(focus, QComboBox) and focus.isEditable()):
            return False
        if ev.isAutoRepeat():
            return t == QEvent.Type.KeyPress and ev.key() in self._MOVE_KEYS
        if t == QEvent.Type.KeyPress:
            return self._key_press(ev)
        return self._key_release(ev)

    _MOVE_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_A, Qt.Key.Key_D,
                  Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus, Qt.Key.Key_E,
                  Qt.Key.Key_Q, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown}

    def _apply_held(self, mods) -> None:
        k = self._held_keys
        mag = 1.0 if mods & Qt.KeyboardModifier.ShiftModifier else (0.25 if mods & Qt.KeyboardModifier.ControlModifier else 0.6)
        pan = (Qt.Key.Key_Right in k or Qt.Key.Key_D in k) - (Qt.Key.Key_Left in k or Qt.Key.Key_A in k)
        tilt = (Qt.Key.Key_Up in k or Qt.Key.Key_W in k) - (Qt.Key.Key_Down in k or Qt.Key.Key_S in k)
        zin = any(x in k for x in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_E, Qt.Key.Key_PageUp))
        zout = any(x in k for x in (Qt.Key.Key_Minus, Qt.Key.Key_Q, Qt.Key.Key_PageDown))
        self.ptz.set_velocity(pan=pan * mag, tilt=tilt * mag, zoom=(zin - zout) * mag)

    def _key_press(self, ev) -> bool:
        key, mods = ev.key(), ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key in self._MOVE_KEYS and not (ctrl and key in (Qt.Key.Key_S, Qt.Key.Key_Q)):
            self._held_keys.add(key)
            self._apply_held(mods)
            return True
        digit = key - Qt.Key.Key_0
        if 1 <= digit <= 9:
            self.save_preset(digit) if ctrl else self.recall_preset(digit)
            return True
        ov = self.overlay_checks
        actions = {
            Qt.Key.Key_Home: lambda: self.ptz.home(), Qt.Key.Key_H: lambda: self.ptz.home(),
            Qt.Key.Key_Escape: lambda: (self.stop_all(), self.set_tracking(False), self.isFullScreen() and self.toggle_fullscreen()),
            Qt.Key.Key_Space: lambda: self.take_photo(), Qt.Key.Key_R: lambda: self.toggle_record(),
            Qt.Key.Key_T: lambda: self.set_tracking(not self.tracker.enabled),
            Qt.Key.Key_G: lambda: ov["grid"].toggle(), Qt.Key.Key_Z: lambda: ov["zebra"].toggle(),
            Qt.Key.Key_N: lambda: self.chk_night.toggle(), Qt.Key.Key_B: lambda: self.chk_blur.toggle(),
            Qt.Key.Key_M: lambda: self.chk_mirror.toggle(), Qt.Key.Key_F11: self.toggle_fullscreen,
        }
        if key == Qt.Key.Key_P:
            self.play_travel() if ctrl else ov["peaking"].toggle()
            return True
        if key in actions and not (ctrl and key not in (Qt.Key.Key_Home,)):
            actions[key]()
            return True
        return False

    def _key_release(self, ev) -> bool:
        key = ev.key()
        if key in self._held_keys:
            self._held_keys.discard(key)
            self._apply_held(ev.modifiers())
            return True
        return False

    def changeEvent(self, ev) -> None:
        # Ao perder o foco, o Windows entrega o "largar tecla" a outra janela: sem isto uma seta
        # ficava "presa" e a câmara continuava a rodar sozinha.
        if ev.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            if self._held_keys or any(self.ptz.velocity.values()):
                self._held_keys.clear()
                self.ptz.set_velocity(pan=0, tilt=0, zoom=0)
        super().changeEvent(ev)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.tabs.show()
            self.showNormal()
        else:
            self.tabs.hide()
            self.showFullScreen()

    # ================================================================== atualizações
    def _check_update_bg(self, manual: bool = False) -> None:
        info = check_update()
        self.settings["updates"]["last_check"] = time.time()
        if info:
            self.update_found.emit(info)
        elif manual:
            self.notify.emit(f"Está na versão mais recente ({__version__})")

    def _on_update_found(self, info: dict) -> None:
        self.lbl_update.setText(f"Nova versão {info['tag']} disponível.\n{info.get('notes', '')[:300]}")
        if self.settings["updates"]["auto_apply"] or QMessageBox.question(
                self, APP_NAME, f"Há uma nova versão ({info['tag']}). Atualizar e reiniciar agora?") == QMessageBox.StandardButton.Yes:
            try:
                apply_update(info)
                restart_app()
                self.close()
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"A atualização falhou: {exc}")

    # ================================================================== fecho
    def closeEvent(self, ev) -> None:
        if self.recorder:
            rec = self.recorder
            self.toggle_record(False)
            rec._thread.join(3)
        if self.remote:
            self.remote.stop()
        self._stop_capture()
        if self.bridge:
            self.bridge.shutdown()
        self.obs.disconnect()
        if not self.test_mode:
            self.settings.save()
        super().closeEvent(ev)


def main() -> int:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Alflor2022.AlflorStudio")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    test_mode = "--screenshot" in sys.argv
    win = MainWindow(test_mode=test_mode)
    if test_mode:  # não rouba o foco a quem está a escrever noutra janela
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        win.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
    win.show()
    # Teste automático: --screenshot <ficheiro> [segundos] guarda uma captura da janela e sai
    if test_mode:
        i = sys.argv.index("--screenshot")
        out = sys.argv[i + 1]
        delay = float(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 6.0
        tab = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tab=")), None)
        if tab:
            names = [win.tabs.tabText(j) for j in range(win.tabs.count())]
            if tab in names:
                win.tabs.setCurrentIndex(names.index(tab))
        QTimer.singleShot(int(delay * 1000), lambda: (win.grab().save(out), win.close()))
    return app.exec()
