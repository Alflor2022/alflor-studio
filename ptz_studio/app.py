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

import queue

from . import APP_NAME, __version__, models
from .ai import AIWorker
from .bridge import Bridge, BridgeError
from .capture import CaptureThread
from .chroma import BACKGROUNDS as CHROMA_BG
from .chroma import KEY_COLORS, auto_key
from .chroma import MODES as CHROMA_MODES
from .chroma import VIEWS as CHROMA_VIEWS
from .config import APP_DATA, Settings
from .gestures import ACTIONS as GESTURE_ACTIONS
from .gestures import GESTURES
from . import hud as hudmod
from .hud import HudBurner
from .integrations import Gamepad, OBSLink, apply_update, check_update, restart_app, scene_commands
from .media import CODECS, Recorder, VirtualCam, best_sharpness, find_ffmpeg, output_path, save_image, stack_frames
from . import photo_formats as pf
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
Shift+Espaço ...... foto c/ temporizador   Shift+R ...... gravar c/ temporizador
G grelha · Z zebras · P peaking · N noite · B desfoque · M espelho
F11 / duplo clique . ecrã inteiro     Ctrl+P ...... reproduzir travelling
K ................. chroma on/off      Ctrl+M ...... mesa de realização
Ctrl+Shift+V ...... revisão de fotos (estrelas 1–5, P escolher, X rejeitar, C comparar)
Alt + clique ...... conta-gotas do chroma (cor do ecrã verde)
Gestos (IA): 👌 OK = seguir quem fez · ✋ palma = parar · ✌ = foto
Mesa: 1–8 preview · Ctrl+1–8 programa · Espaço CUT · Enter AUTO · F FTB
Rato: clique = centrar ali · arrastar = mover · roda = zoom
       Ctrl + clique = seguir esse objeto (contorno automático)
       botão direito arrastado (ou Shift + arrastar) = escolher sujeito
Comando Xbox: stick = mover · gatilhos = zoom · A foto · B gravar
       X tracking · Y início · LB/RB presets · Start parar"""

QSS = """
* { font-family: 'Segoe UI'; font-size: 10pt; color: #e8ecf2; }
QMainWindow, QWidget { background: #0e1116; }
QTabWidget::pane { border: 1px solid #2a313c; border-radius: 6px; top: -1px; }
QTabBar::tab { background: #151a21; padding: 6px 7px; border: 1px solid #2a313c; border-bottom: none;
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
        self.settings.readonly = test_mode   # bloqueia TODAS as gravações de definições em teste
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 900)
        self.pipeline = Pipeline(self.settings["pipeline"])
        self.ai = AIWorker()   # MediaPipe numa thread própria (só calcula o que estiver ligado)
        self.tracker = SubjectTracker(self.settings["tracking"], self.ai)
        self.tracker.mode = self.settings["tracking"]["mode"] if self.settings["tracking"]["mode"] != "subject" else "face"
        self.switcher = None
        self._obs_scenes: queue.Queue = queue.Queue()
        self.hud = HudBurner(self.settings)   # elementos a queimar na gravação / foto
        self._last_meta: dict = {}
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
        threading.Thread(target=self._warm_encoders, daemon=True).start()

    def _warm_encoders(self) -> None:
        """Testa os codificadores em segundo plano, para a 1.ª gravação arrancar sem ~1,5 s de atraso."""
        if self.ffmpeg:
            from .media import encoder_available
            for codec in ("libx264", "h264_qsv"):
                encoder_available(self.ffmpeg, codec)

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
        self.cmb_profile = QComboBox(toolTip="Perfil da interface: mostra só o que cada trabalho precisa")
        for k, (lbl, _t, _h) in self.UI_PROFILES.items():
            self.cmb_profile.addItem(lbl, k)
        top.addWidget(QLabel("Perfil:"))
        top.addWidget(self.cmb_profile)
        self.btn_photo = QPushButton("📷  Foto", objectName="big",
                                     toolTip="Foto agora (Espaço) · com temporizador: Shift+Espaço ou a seta ▾")
        self.btn_photo.clicked.connect(self._photo_clicked)
        self.btn_photo_menu = QToolButton(text="⏱ ▾", toolTip="Temporizador da foto")
        self.btn_photo_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_photo_menu.setMenu(self._build_photo_menu())
        self._photo_cd_timer = QTimer(self, interval=1000, timeout=self._photo_countdown_tick)
        self._photo_cd = 0
        self._photo_cd_mode = "single"
        self.btn_rec = QPushButton("●  Gravar", objectName="rec", checkable=True,
                                   toolTip="Gravar agora (R) · com temporizador: Shift+R ou a seta ▾")
        self.btn_rec.setMinimumWidth(130)
        self.btn_rec.clicked.connect(self._rec_clicked)
        self.btn_rec_menu = QToolButton(text="⏱ ▾", toolTip="Temporizador e duração da gravação")
        self.btn_rec_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_rec_menu.setMenu(self._build_rec_menu())
        self._rec_cd_timer = QTimer(self, interval=1000, timeout=self._rec_countdown_tick)
        self._rec_cd = 0
        self._auto_stop = QTimer(self, singleShot=True, timeout=lambda: self.toggle_record(False))
        self.btn_track = QPushButton("◎  Seguir", objectName="big", checkable=True, toolTip="T")
        self.btn_track.toggled.connect(self.set_tracking)
        btn_home = QPushButton("⌂  Início", objectName="big", toolTip="Home")
        btn_home.clicked.connect(lambda: self.ptz.home())
        self.btn_gest = QPushButton("👌  Gestos", objectName="big", checkable=True,
                                    toolTip="Comandar com gestos: 👌 OK = seguir quem fez o gesto · ✋ palma = parar")
        self.btn_gest.setChecked(bool(self.settings["tracking"].get("gestures")))
        self.btn_gest.toggled.connect(self.set_gestures)
        self.btn_chroma = QPushButton("🟩  Chroma", objectName="big", checkable=True, toolTip="Ligar/desligar o chroma key (K)")
        self.btn_chroma.setChecked(bool(self.settings["pipeline"]["chroma"].get("enabled")))
        self.btn_chroma.toggled.connect(self.set_chroma)
        btn_mesa = QPushButton("🎬  Mesa", objectName="big", toolTip="Mesa de realização multi-câmara (Ctrl+M)")
        btn_mesa.clicked.connect(self.open_switcher)
        for b in (self.btn_photo, self.btn_photo_menu, self.btn_rec, self.btn_rec_menu, self.btn_track, self.btn_gest, self.btn_chroma, btn_home, btn_mesa):
            top.addWidget(b)
        self._top_groups = {"photo_menu": [self.btn_photo_menu], "rec": [self.btn_rec, self.btn_rec_menu],
                            "track": [self.btn_track], "gest": [self.btn_gest], "mesa": [btn_mesa]}
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.preview = PreviewWidget(self.settings)
        self.preview.hud_state = self.hud.state
        self.preview.clicked.connect(self.click_to_center)
        self.preview.drag_velocity.connect(lambda vx, vy: self.ptz.set_velocity(pan=vx, tilt=vy))
        self.preview.drag_end.connect(lambda: self.ptz.set_velocity(pan=0, tilt=0))
        self.preview.wheel_zoom.connect(self._wheel_zoom)
        self.preview.subject_selected.connect(self.select_subject)
        self.preview.click_track.connect(self.track_at)
        self.preview.color_pick.connect(self.pick_chroma_color)
        self.preview.double_clicked.connect(self.toggle_fullscreen)
        split.addWidget(self.preview)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(400)
        self.tabs.setMaximumWidth(520)
        self.tabs.setMaximumWidth(640)
        self.tabs.tabBar().setExpanding(False)
        for title, builder in (("PTZ", self._tab_ptz), ("Imagem", self._tab_image), ("Chroma", self._tab_chroma),
                               ("Tracking", self._tab_tracking),
                               ("Captura", self._tab_capture), ("Travelling", self._tab_travel),
                               ("Ligações", self._tab_links), ("Sistema", self._tab_system)):
            scroll = QScrollArea(widgetResizable=True)
            scroll.setWidget(builder())
            self.tabs.addTab(scroll, title)
        split.addWidget(self.tabs)
        self.cmb_profile.setCurrentIndex(max(0, self.cmb_profile.findData(self.settings.get("ui_profile", "completo"))))
        self.apply_ui_profile(self.cmb_profile.currentData())
        self.cmb_profile.currentIndexChanged.connect(lambda _i: self.apply_ui_profile(self.cmb_profile.currentData()))
        split.setStretchFactor(0, 1)
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.lbl_stats = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_stats)

    # perfil → (nome, separadores visíveis (None = todos), botões da barra escondidos)
    UI_PROFILES = {
        "completo": ("Completo", None, ()),
        "fotografo": ("Fotógrafo", {"PTZ", "Imagem", "Chroma", "Captura", "Sistema"}, ("rec", "track", "gest", "mesa")),
        "video": ("Vídeo", {"PTZ", "Imagem", "Chroma", "Tracking", "Captura", "Travelling", "Sistema"}, ("photo_menu",)),
        "live": ("Live / streaming", {"PTZ", "Imagem", "Chroma", "Tracking", "Captura", "Ligações", "Sistema"}, ("photo_menu",)),
    }

    def apply_ui_profile(self, key: str) -> None:
        _lbl, tabs, hidden = self.UI_PROFILES.get(key, self.UI_PROFILES["completo"])
        for i in range(self.tabs.count()):
            self.tabs.setTabVisible(i, tabs is None or self.tabs.tabText(i) in tabs)
        if not self.tabs.isTabVisible(self.tabs.currentIndex()):
            self.tabs.setCurrentIndex(next(i for i in range(self.tabs.count()) if self.tabs.isTabVisible(i)))
        for name, widgets in self._top_groups.items():
            for wdg in widgets:
                wdg.setVisible(name not in hidden)
        self.settings["ui_profile"] = key if key in self.UI_PROFILES else "completo"

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
        lay.addWidget(self._grading_group())

        lay.addWidget(self._hud_group())
        hint = QLabel("Nota: esta PTZ não expõe foco, exposição nem íris por USB. Se uma câmara os tiver "
                      "(ex.: outra PTZ ou webcam), aparecem automaticamente em «Câmara (hardware)».", objectName="hint", wordWrap=True)
        lay.addWidget(hint)
        lay.addStretch(1)
        return w

    # --- elementos no ecrã / gravação / foto ---------------------------------------------------
    def _hud_group(self) -> QGroupBox:
        g = QGroupBox("Elementos: no ecrã, na gravação e na foto")
        gl = QGridLayout(g)
        for col, txt in enumerate(("Elemento", "Ecrã", "Gravação", "Foto")):
            gl.addWidget(QLabel(txt, objectName="hint"), 0, col)
        ov, hs = self.settings["overlays"], self.settings["hud"]
        self.overlay_checks = {}
        center = Qt.AlignmentFlag.AlignCenter
        row = 1
        for key, label, _d in hudmod.ITEMS:
            gl.addWidget(QLabel(label), row, 0)
            for col, target in enumerate(hudmod.TARGETS, start=1):
                c = QCheckBox()
                c.setChecked(hudmod.on(self.settings, key, target))
                if target == "screen" and key in hudmod.LEGACY_SCREEN:   # mantém os atalhos G/Z/P
                    c.toggled.connect(lambda v, k=key: (ov.__setitem__(k, v), hs[k].__setitem__("screen", v)))
                    self.overlay_checks[key] = c
                else:
                    c.toggled.connect(lambda v, k=key, tg=target: hs[k].__setitem__(tg, v))
                gl.addWidget(c, row, col, alignment=center)
            row += 1
            if key == "logo":
                lrow = QHBoxLayout()
                b_logo = QPushButton("Escolher logótipo…")
                b_logo.clicked.connect(self._choose_hud_logo)
                cmb = QComboBox()
                for k2, v2 in (("br", "Inferior direito"), ("bl", "Inferior esquerdo"), ("tr", "Superior direito"), ("tl", "Superior esquerdo")):
                    cmb.addItem(v2, k2)
                cmb.setCurrentIndex(max(0, cmb.findData(hs.get("_logo_corner", "br"))))
                cmb.currentIndexChanged.connect(lambda _i, c2=cmb: hs.__setitem__("_logo_corner", c2.currentData()))
                lrow.addWidget(b_logo)
                lrow.addWidget(cmb)
                gl.addLayout(lrow, row, 0, 1, 4)
                row += 1
        gl.addWidget(QLabel("«Gravação» inclui a câmara virtual (OBS, Zoom, Teams…). Zebras e peaking na gravação "
                            "custam ~10 ms por frame. A transparência do chroma não cobre os elementos queimados.",
                            objectName="hint", wordWrap=True), row, 0, 1, 4)
        return g

    def _choose_hud_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Logótipo (PNG com transparência)", str(Path.home()),
                                              "Imagens (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.settings["hud"]["_logo_path"] = path
            self.settings.save()
            self.statusBar().showMessage(f"Logótipo: {Path(path).name} — marque «Ecrã», «Gravação» ou «Foto» na linha Logótipo", 5000)

    # --- ajuste inteligente: automático (ambiente) / igualar a uma referência -----------------
    def _grading_group(self) -> QGroupBox:
        from .grading import MODES as GRADE_MODES
        gr = self.settings["pipeline"]["grade"]
        g = QGroupBox("Ajuste inteligente da imagem (como o Lightroom)")
        f = QFormLayout(g)
        self.cmb_grade = QComboBox()
        for k, v in GRADE_MODES.items():
            self.cmb_grade.addItem(v, k)
        self.cmb_grade.setCurrentIndex(max(0, self.cmb_grade.findData(gr["mode"])))
        self.cmb_grade.currentIndexChanged.connect(lambda _i: self.set_grade_mode(self.cmb_grade.currentData()))
        f.addRow("Modo", self.cmb_grade)
        f.addRow("Intensidade", self._slider(0, 100, int(gr["strength"] * 100), lambda v: gr.__setitem__("strength", v), 100.0))
        chk_cont = QCheckBox("Adaptar continuamente (acompanha a luz do dia, sem cintilação)")
        chk_cont.setChecked(bool(gr["continuous"]))
        chk_cont.toggled.connect(lambda v: (gr.__setitem__("continuous", v), self.pipeline.grader.request_update()))
        f.addRow(chk_cont)
        chk_dn = QCheckBox("No automático, ajustar também a redução de ruído")
        chk_dn.setChecked(bool(gr.get("auto_denoise", True)))
        chk_dn.toggled.connect(lambda v: gr.__setitem__("auto_denoise", v))
        f.addRow(chk_dn)
        row = QHBoxLayout()
        b_ref = QPushButton("Imagem de referência…")
        b_ref.setToolTip("Uma foto editada (Lightroom, Photoshop) ou um frame de outro trabalho com o look desejado")
        b_ref.clicked.connect(self.choose_grade_reference)
        b_cur = QPushButton("Usar imagem atual")
        b_cur.setToolTip("Congela o look atual como referência (para repetir noutra sessão ou noutra câmara)")
        b_cur.clicked.connect(self.grade_reference_from_current)
        b_now = QPushButton("Analisar agora")
        b_now.clicked.connect(self.pipeline.grader.request_update)
        row.addWidget(b_ref)
        row.addWidget(b_cur)
        row.addWidget(b_now)
        f.addRow(row)
        b_cube = QPushButton("Exportar look (.cube)…")
        b_cube.setToolTip("Ajuste inteligente + look de cor como LUT 3D para Premiere, DaVinci, Photoshop, OBS ou Final Cut")
        b_cube.clicked.connect(self.export_look_cube)
        f.addRow(b_cube)
        self.lbl_grade_ref = QLabel(alignment=Qt.AlignmentFlag.AlignLeft)
        f.addRow("Referência", self.lbl_grade_ref)
        self.lbl_grade = QLabel("", objectName="hint", wordWrap=True)
        f.addRow(self.lbl_grade)
        if gr.get("ref_path") and Path(gr["ref_path"]).exists():
            self.pipeline.grader.load_reference(gr["ref_path"])
        self._show_grade_reference()
        return g

    def apply_look_u8(self, bgr: np.ndarray) -> np.ndarray:
        """Aplica a uma imagem o mesmo ajuste + look de cor do pipeline (sem ruído/nitidez/chroma)."""
        p = self.settings["pipeline"]
        out = bgr
        if p.get("grade", {}).get("mode", "off") != "off":
            out = self.pipeline.grader.apply(out)
        matrix, lut = LOOKS.get(p["look"], (None, None))
        if matrix is not None:
            out = cv2.transform(out, matrix)
        if lut is not None:
            out = cv2.LUT(out, lut)
        return out

    def export_look_cube(self) -> None:
        from .media import _safe
        name = f"Alflor_look_{_safe(self.settings['pipeline']['look'])}.cube"
        path, _ = QFileDialog.getSaveFileName(self, "Exportar look como LUT 3D", str(Path(self.settings["output_dir"]) / name),
                                              "LUT 3D (*.cube)")
        if not path:
            return
        try:
            pf.look_cube(Path(path), self.apply_look_u8, 33, f"Alflor Studio - {self.settings['pipeline']['look']}")
            self.statusBar().showMessage(f"LUT do look exportada: {path}", 6000)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Não foi possível exportar a LUT: {exc}")

    def export_log_lut(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "LUT S-Log3 → Rec.709", str(Path(self.settings["output_dir"]) / "Alflor_SLog3_para_Rec709.cube"),
                                              "LUT 3D (*.cube)")
        if path:
            pf.log_to_rec709_cube(Path(path))
            self.statusBar().showMessage(f"LUT Log→Rec709 guardada: {path} (aplicar no Premiere/DaVinci aos clips Log)", 8000)

    def set_grade_mode(self, mode: str) -> None:
        gr = self.settings["pipeline"]["grade"]
        if mode == "reference" and not self.pipeline.grader.has_reference:
            self.statusBar().showMessage("Escolha primeiro uma imagem de referência (ou «Usar imagem atual»)", 5000)
        gr["mode"] = mode
        self.pipeline.grader.request_update()
        if self.cmb_grade.currentData() != mode:
            self.cmb_grade.blockSignals(True)
            self.cmb_grade.setCurrentIndex(max(0, self.cmb_grade.findData(mode)))
            self.cmb_grade.blockSignals(False)

    def _show_grade_reference(self) -> None:
        p = self.settings["pipeline"]["grade"].get("ref_path", "")
        if p and Path(p).exists():
            pix = QPixmap(p)
            if not pix.isNull():
                self.lbl_grade_ref.setPixmap(pix.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.lbl_grade_ref.setToolTip(p)
                return
        self.lbl_grade_ref.setText("— (nenhuma)")

    def choose_grade_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Imagem de referência", str(Path.home()),
                                              "Imagens (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp)")
        if path and self.pipeline.grader.load_reference(path):
            self.settings["pipeline"]["grade"]["ref_path"] = path
            self.settings.save()
            self._show_grade_reference()
            self.set_grade_mode("reference")
        elif path:
            QMessageBox.warning(self, APP_NAME, "Não foi possível abrir essa imagem.")

    def grade_reference_from_current(self) -> None:
        img = self.capture.latest(processed=True) if self.capture else None
        if img is None:
            return
        dest = APP_DATA / "referencia_look.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        save_image(img, dest, "png")
        self.pipeline.grader.set_reference(img, "imagem atual")
        self.settings["pipeline"]["grade"]["ref_path"] = str(dest)
        self.settings.save()
        self._show_grade_reference()
        self.statusBar().showMessage("Look atual guardado como referência", 4000)

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
    def _tab_chroma(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        ck = self.settings["pipeline"]["chroma"]
        g = QGroupBox("Chroma key / recorte do fundo")
        f = QFormLayout(g)
        self.chk_chroma = QCheckBox("Ativo (K)")
        self.chk_chroma.setChecked(bool(ck.get("enabled")))
        self.chk_chroma.toggled.connect(lambda v: self.btn_chroma.setChecked(v))
        f.addRow(self.chk_chroma)
        cmb_mode = QComboBox()
        for k, v in CHROMA_MODES.items():
            cmb_mode.addItem(v, k)
        cmb_mode.setCurrentIndex(max(0, cmb_mode.findData(ck["mode"])))
        cmb_mode.currentIndexChanged.connect(lambda _i: (ck.__setitem__("mode", cmb_mode.currentData()), self._sync_ai_wants()))
        f.addRow("Modo", cmb_mode)
        krow = QHBoxLayout()
        self.cmb_key = QComboBox()
        for label, key in (("Verde", "green"), ("Azul", "blue"), ("Personalizada", "custom")):
            self.cmb_key.addItem(label, key)
        self.cmb_key.setCurrentIndex(max(0, self.cmb_key.findData(ck["key"])))
        self.cmb_key.currentIndexChanged.connect(self._chroma_key_changed)
        self.lbl_swatch = QLabel()
        self.lbl_swatch.setFixedSize(28, 22)
        b_auto = QPushButton("Calibrar automaticamente")
        b_auto.clicked.connect(self.auto_calibrate_chroma)
        krow.addWidget(self.cmb_key, 1)
        krow.addWidget(self.lbl_swatch)
        krow.addWidget(b_auto)
        f.addRow("Cor", krow)
        f.addRow(QLabel("Conta-gotas: Alt + clique no fundo, na imagem.", objectName="hint"))
        self.chroma_sliders = {}
        for key, label, lo, hi, scale in (("similarity", "Semelhança", 5, 400, 1000.0), ("smoothness", "Suavidade", 5, 300, 1000.0),
                                           ("spill", "Supressão de spill", 0, 100, 100.0), ("choke", "Contrair contorno (px)", 0, 6, 1.0),
                                           ("feather", "Esbater contorno", 0, 80, 10.0), ("light_wrap", "Light wrap (+12 ms)", 0, 100, 100.0)):
            s = self._slider(lo, hi, int(float(ck[key]) * scale), lambda v, k=key: ck.__setitem__(k, v), scale)
            self.chroma_sliders[key] = (s, scale)
            f.addRow(label, s)
        lay.addWidget(g)

        g2 = QGroupBox("Garbage matte (cortar zonas fora do ecrã verde)")
        f2 = QFormLayout(g2)
        for i, label in enumerate(("Esquerda", "Topo", "Direita", "Base")):
            f2.addRow(label, self._slider(0, 45, int(ck["garbage"][i] * 100),
                                          lambda v, i=i: ck["garbage"].__setitem__(i, v), 100.0))
        lay.addWidget(g2)

        g3 = QGroupBox("Fundo")
        f3 = QFormLayout(g3)
        cmb_bg = QComboBox()
        for k, v in CHROMA_BG.items():
            cmb_bg.addItem(v, k)
        cmb_bg.setCurrentIndex(max(0, cmb_bg.findData(ck["bg"])))
        cmb_bg.currentIndexChanged.connect(lambda _i: ck.__setitem__("bg", cmb_bg.currentData()))
        f3.addRow("Tipo", cmb_bg)
        brow = QHBoxLayout()
        b_color = QPushButton("Cor…")
        b_color.clicked.connect(self._chroma_bg_color)
        b_file = QPushButton("Imagem / vídeo…")
        b_file.clicked.connect(self._chroma_bg_file)
        brow.addWidget(b_color)
        brow.addWidget(b_file)
        f3.addRow(brow)
        self.lbl_bgfile = QLabel(Path(ck["bg_path"]).name if ck.get("bg_path") else "", objectName="hint")
        f3.addRow(self.lbl_bgfile)
        f3.addRow("Desfocar fundo", self._slider(0, 30, int(ck["bg_blur"]), lambda v: ck.__setitem__("bg_blur", int(v))))
        f3.addRow(QLabel("«Transparente» grava PNG com alfa e ProRes 4444 (.mov) e alimenta o cenário virtual da Mesa.",
                         objectName="hint", wordWrap=True))
        lay.addWidget(g3)

        g4 = QGroupBox("Vista e desempenho")
        f4 = QFormLayout(g4)
        cmb_view = QComboBox()
        for k, v in CHROMA_VIEWS.items():
            cmb_view.addItem(v, k)
        cmb_view.setCurrentIndex(max(0, cmb_view.findData(ck["view"])))
        cmb_view.currentIndexChanged.connect(lambda _i: ck.__setitem__("view", cmb_view.currentData()))
        f4.addRow("Vista", cmb_view)
        chk_gpu = QCheckBox("Acelerar com a GPU (OpenCL)")
        chk_gpu.setChecked(bool(ck.get("gpu", True)))
        chk_gpu.toggled.connect(lambda v: ck.__setitem__("gpu", v))
        f4.addRow(chk_gpu)
        self.lbl_chroma_perf = QLabel("", objectName="hint")
        f4.addRow(self.lbl_chroma_perf)
        lay.addWidget(g4)
        lay.addStretch(1)
        self._update_swatch()
        return w

    def _update_swatch(self) -> None:
        b, g_, r = self.settings["pipeline"]["chroma"].get("color", [60, 180, 60])
        self.lbl_swatch.setStyleSheet(f"background: rgb({r},{g_},{b}); border:1px solid #2a313c; border-radius:4px;")

    def _chroma_key_changed(self, _i: int) -> None:
        ck = self.settings["pipeline"]["chroma"]
        key = self.cmb_key.currentData()
        ck["key"] = key
        if key in KEY_COLORS:
            ck["color"] = list(KEY_COLORS[key])
        self._update_swatch()

    def set_chroma(self, on: bool) -> None:
        self.settings["pipeline"]["chroma"]["enabled"] = on
        for w in (self.btn_chroma, getattr(self, "chk_chroma", None)):
            if w is not None and w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        self._sync_ai_wants()
        self.statusBar().showMessage("Chroma ligado" if on else "Chroma desligado", 2000)

    def auto_calibrate_chroma(self) -> None:
        img = self.capture.latest(processed=False) if self.capture else None
        if img is None:
            return
        ck = self.settings["pipeline"]["chroma"]
        key = "blue" if ck["key"] == "blue" else "green"
        found = auto_key(img, key)
        if not found:
            QMessageBox.information(self, APP_NAME, "Não encontrei um ecrã verde/azul suficientemente grande na imagem.")
            return
        ck["color"], ck["similarity"] = found
        s, scale = self.chroma_sliders["similarity"]
        s.setValue(int(ck["similarity"] * scale))
        self._update_swatch()
        self.statusBar().showMessage(f"Chroma calibrado: cor {ck['color']} · semelhança {ck['similarity']:.3f}", 5000)

    def pick_chroma_color(self, u: float, v: float) -> None:
        img = self.capture.latest(processed=False) if self.capture else None
        if img is None:
            return
        x, y = self.pipeline.out_to_raw(u, v)
        h, w = img.shape[:2]
        cx, cy = int(x * (w - 1)), int(y * (h - 1))
        patch = img[max(0, cy - 6): cy + 7, max(0, cx - 6): cx + 7].reshape(-1, 3)
        ck = self.settings["pipeline"]["chroma"]
        ck["color"] = [int(c) for c in np.median(patch, axis=0)]
        ck["key"] = "custom"
        self.cmb_key.blockSignals(True)
        self.cmb_key.setCurrentIndex(self.cmb_key.findData("custom"))
        self.cmb_key.blockSignals(False)
        self._update_swatch()
        self.statusBar().showMessage(f"Cor do chroma: BGR {ck['color']}", 4000)

    def _chroma_bg_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        ck = self.settings["pipeline"]["chroma"]
        b, g_, r = ck["bg_color"]
        c = QColorDialog.getColor(QColor(r, g_, b), self, "Cor de fundo")
        if c.isValid():
            ck["bg_color"] = [c.blue(), c.green(), c.red()]
            ck["bg"] = "color" if ck["bg"] == "transparent" else ck["bg"]

    def _chroma_bg_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Fundo", str(Path.home()),
                                              "Imagens e vídeos (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.avi *.webm)")
        if path:
            ck = self.settings["pipeline"]["chroma"]
            ck["bg_path"] = path
            ck["bg"] = "video" if Path(path).suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm") else "image"
            self.lbl_bgfile.setText(Path(path).name)

    def _sync_ai_wants(self) -> None:
        ck = self.settings["pipeline"]["chroma"]
        self.ai.want["segment"] = bool(ck.get("enabled") and ck.get("mode") in ("ai", "hybrid") and self.ai.available)

    def _update_models_label(self) -> None:
        miss = models.missing()
        if not self.ai.available:
            self.lbl_models.setText("MediaPipe não instalado: a app usa os detetores clássicos.")
            self.btn_models.setVisible(False)
        elif miss:
            self.lbl_models.setText("Modelos em falta: " + ", ".join(models.MODELS[k]["label"] for k in miss))
            self.btn_models.setVisible(True)
        else:
            self.lbl_models.setText("Modelos de IA: todos instalados ✓")
            self.btn_models.setVisible(False)

    def download_models(self) -> None:
        self.btn_models.setEnabled(False)
        self.lbl_models.setText("A descarregar modelos (Google MediaPipe / OpenCV Zoo)…")

        def work():
            res = models.download_all()
            self.notify.emit("Modelos: " + ", ".join(f"{k} {v}" for k, v in res.items()))
            QTimer.singleShot(0, lambda: (self.btn_models.setEnabled(True), self._update_models_label(),
                                          self.tracker.load_yunet(), self._update_detector_label()))
        threading.Thread(target=work, daemon=True).start()

    def set_gestures(self, on: bool) -> None:
        self.settings["tracking"]["gestures"] = on
        for w in (self.btn_gest, getattr(self, "chk_gestures", None)):
            if w is not None and w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        if on and not self.ai.available:
            self.statusBar().showMessage("Os gestos precisam do MediaPipe (IA), que não está disponível", 5000)
        else:
            self.statusBar().showMessage("Gestos ligados: 👌 OK = seguir · ✋ palma = parar" if on else "Gestos desligados", 4000)

    def _handle_gesture(self, ev, owner) -> None:
        body, head = owner
        action = self.settings["tracking"].get("gesture_actions", {}).get(ev.gesture, "none")
        label = GESTURES.get(ev.gesture, ev.gesture)
        if action == "none":
            return
        if action in ("track_start", "track_toggle") and (action == "track_start" or not self.tracker.enabled):
            if body is not None:
                mode = "person" if self.tracker.use_ai else "subject"
                self.tracker.lock_on(body, mode)
            else:  # sem pose: escolhe o rosto mais próximo da mão
                hx, hy = ev.hand_center
                self.tracker.lock_on((max(0.0, hx - 0.12), max(0.0, hy - 0.35), 0.24, 0.3), "face")
            self.cmb_track_mode.blockSignals(True)
            self.cmb_track_mode.setCurrentIndex(max(0, self.cmb_track_mode.findData(self.tracker.mode)))
            self.cmb_track_mode.blockSignals(False)
            self._update_detector_label()
            self.set_tracking(True)
        elif action in ("track_stop", "track_toggle"):
            self.set_tracking(False)
        elif action == "photo":
            self.take_photo("timer3")
        elif action == "record_toggle":
            self.toggle_record()
        elif action == "home":
            self.ptz.home()
        elif action == "preset_next":
            used = sorted(int(n) for n in self.settings.device_presets(self.device))
            if used:
                cur = getattr(self, "_gp_preset", 0)
                cur = used[(used.index(cur) + 1) % len(used)] if cur in used else used[0]
                self._gp_preset = cur
                self.recall_preset(cur)
        self.statusBar().showMessage(f"Gesto {label} → {GESTURE_ACTIONS.get(action, action)}", 4000)

    # --- mesa de realização -----------------------------------------------------------------
    def open_switcher(self) -> None:
        if self.switcher is None:
            from .switcher_ui import SwitcherWindow
            self.switcher = SwitcherWindow(self)
            if self.remote:
                self.remote.program_source = self.switcher.program_jpeg
        self.switcher.show()
        self.switcher.raise_()
        self.switcher.activateWindow()

    def on_switcher_closed(self) -> None:
        self.switcher = None
        if self.remote:
            self.remote.program_source = None

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
        chk_rec = QCheckBox("Procurar quando o perde (volta à última posição onde o viu)")
        chk_rec.setChecked(t.get("recover", True))
        chk_rec.toggled.connect(lambda v: t.__setitem__("recover", v))
        f.addRow(chk_rec)
        chk_lost = QCheckBox("Voltar ao início se continuar perdido (5 s)")
        chk_lost.setChecked(t["lost_home"])
        chk_lost.toggled.connect(lambda v: t.__setitem__("lost_home", v))
        f.addRow(chk_lost)
        chk_ai = QCheckBox("Usar IA (MediaPipe) — pessoas, gestos, recorte")
        chk_ai.setChecked(t.get("ai", True))
        chk_ai.setEnabled(self.ai.available)
        chk_ai.toggled.connect(lambda v: (t.__setitem__("ai", v), self._update_detector_label()))
        f.addRow(chk_ai)
        self.lbl_detector = QLabel("", objectName="hint")
        f.addRow("Detetor", self.lbl_detector)
        self.lbl_track_state = QLabel("parado", objectName="hint")
        f.addRow("Estado", self.lbl_track_state)
        lay.addWidget(g)

        gg = QGroupBox("Gestos (IA) — o alvo comanda a câmara")
        fg = QFormLayout(gg)
        self.chk_gestures = QCheckBox("Reconhecer gestos")
        self.chk_gestures.setChecked(bool(t.get("gestures")))
        self.chk_gestures.toggled.connect(lambda v: self.btn_gest.setChecked(v))
        fg.addRow(self.chk_gestures)
        acts = t.setdefault("gesture_actions", {})
        for g_key, g_label in GESTURES.items():
            cmb = QComboBox()
            for a_key, a_label in GESTURE_ACTIONS.items():
                cmb.addItem(a_label, a_key)
            cmb.setCurrentIndex(max(0, cmb.findData(acts.get(g_key, "none"))))
            cmb.currentIndexChanged.connect(lambda _i, k=g_key, c=cmb: acts.__setitem__(k, c.currentData()))
            fg.addRow(g_label, cmb)
        fg.addRow(QLabel("Mantenha o gesto ~0,6 s, de frente para a câmara. Depois de um gesto há 2,5 s de pausa "
                         "para evitar repetições. 👌 OK escolhe como alvo a pessoa que o fez.",
                         objectName="hint", wordWrap=True))
        self.lbl_models = QLabel("", objectName="hint", wordWrap=True)
        fg.addRow(self.lbl_models)
        self.btn_models = QPushButton("Descarregar modelos de IA em falta")
        self.btn_models.clicked.connect(self.download_models)
        fg.addRow(self.btn_models)
        self._update_models_label()
        lay.addWidget(gg)

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
        for label, key in (("PNG (sem perdas)", "png"), ("JPEG qualidade máxima", "jpg"), ("TIFF (edição)", "tiff"),
                           ("DNG RAW · 16 bits lineares (empilhado)", "dng"), ("DNG RAW + JPEG", "dng+jpg")):
            cmb_fmt.addItem(label, key)
        cmb_fmt.setCurrentIndex(max(0, cmb_fmt.findData(ph["format"])))
        cmb_fmt.currentIndexChanged.connect(lambda _i: ph.__setitem__("format", cmb_fmt.currentData()))
        f2.addRow("Formato", cmb_fmt)
        sraw = QSpinBox(minimum=1, maximum=32, value=int(ph.get("raw_frames", 8)), suffix=" frames",
                        toolTip="O DNG junta N frames em luz linear: o ruído cai √N (8 frames ≈ ÷2,8)")
        sraw.valueChanged.connect(lambda v: ph.__setitem__("raw_frames", v))
        f2.addRow("RAW: juntar", sraw)
        f2.addRow(QLabel("DNG: imagem linear de 16 bits sem look (abre no Lightroom/Camera Raw como RAW, com "
                         "exposição e balanço de brancos editáveis). Esta câmara USB entrega 8 bits por cor, por isso "
                         "não é um RAW de sensor: os frames empilhados reduzem o ruído e dão margem nas sombras. "
                         "Com uma Canon (EDSDK), no futuro, será o RAW verdadeiro.", objectName="hint", wordWrap=True))
        chk_look = QCheckBox("Aplicar ajustes e look à foto")
        chk_look.setChecked(ph["apply_look"])
        chk_look.toggled.connect(lambda v: ph.__setitem__("apply_look", v))
        f2.addRow(chk_look)
        chk_mask = QCheckBox("Com chroma: guardar também a máscara e o original (para o designer)")
        chk_mask.setChecked(bool(ph.get("export_mask")))
        chk_mask.toggled.connect(lambda v: ph.__setitem__("export_mask", v))
        f2.addRow(chk_mask)
        comp = s["compose"]
        cmb_guide = QComboBox(toolTip="Mostra no ecrã o corte do formato final (não aparece na foto nem na gravação)")
        from .widgets import GUIDES
        for k, (lbl, _a) in GUIDES.items():
            cmb_guide.addItem(lbl, k)
        cmb_guide.setCurrentIndex(max(0, cmb_guide.findData(comp.get("guide", "off"))))
        cmb_guide.currentIndexChanged.connect(lambda _i: (comp.__setitem__("guide", cmb_guide.currentData()), self.preview.update()))
        f2.addRow("Guia de corte", cmb_guide)
        grow = QHBoxLayout()
        chk_ghost = QCheckBox("Fantasma da última foto")
        chk_ghost.setToolTip("Sobrepõe a última foto em transparência para alinhar a seguinte (produto, antes/depois, série)")
        chk_ghost.setChecked(bool(comp.get("ghost")))
        chk_ghost.toggled.connect(lambda v: (comp.__setitem__("ghost", v), self.preview.update()))
        grow.addWidget(chk_ghost)
        grow.addWidget(self._slider(10, 80, int(float(comp.get("ghost_opacity", 0.35)) * 100),
                                    lambda v: (comp.__setitem__("ghost_opacity", v), self.preview.update()), 100.0), 1)
        f2.addRow(grow)
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
        b_review = QPushButton("🗂  Revisão: estrelas, escolher, comparar…")
        b_review.setToolTip("Triagem das fotos de hoje (1–5 estrelas, P escolher, X rejeitar, C comparar) — Ctrl+Shift+V")
        b_review.clicked.connect(self.open_review)
        f2.addRow(b_review)
        lay.addWidget(g2)

        gm = QGroupBox("Metadados e Lightroom")
        fm = QFormLayout(gm)
        md = s["meta"]
        for key, label, tip in (("author", "Autor", "Nome do fotógrafo/estúdio (EXIF Artist + XMP dc:creator)"),
                                ("copyright", "Direitos", "Ex.: © 2026 Alflor Digital — todos os direitos reservados"),
                                ("client", "Cliente", "Também vai como palavra-chave, para filtrar no Lightroom"),
                                ("keywords", "Palavras-chave", "Separadas por vírgulas (o projeto e a cena são acrescentados)"),
                                ("description", "Descrição", "")):
            e = QLineEdit(str(md.get(key, "")), toolTip=tip)
            e.textChanged.connect(lambda txt, k=key: md.__setitem__(k, txt))
            fm.addRow(label, e)
        sr = QSpinBox(minimum=0, maximum=5, value=int(md.get("rating", 0)), specialValueText="sem estrelas", suffix=" ★")
        sr.valueChanged.connect(lambda v: md.__setitem__("rating", v))
        fm.addRow("Classificação inicial", sr)
        lr_row = QHBoxLayout()
        self.ed_lr = QLineEdit(ph.get("lightroom_dir", ""), readOnly=True, placeholderText="(não copiar)")
        b_lr = QToolButton(text="…", toolTip="Pasta vigiada pela Importação automática do Lightroom Classic")
        b_lr.clicked.connect(self.choose_lightroom_dir)
        b_lr_clear = QToolButton(text="✕", toolTip="Deixar de copiar")
        b_lr_clear.clicked.connect(lambda: (ph.__setitem__("lightroom_dir", ""), self.ed_lr.setText("")))
        lr_row.addWidget(self.ed_lr, 1)
        lr_row.addWidget(b_lr)
        lr_row.addWidget(b_lr_clear)
        fm.addRow("Copiar para", lr_row)
        b_open_lr = QPushButton("Abrir Lightroom")
        b_open_lr.clicked.connect(self.open_lightroom)
        fm.addRow(b_open_lr)
        fm.addRow(QLabel("Cada foto leva perfil sRGB, EXIF e XMP (autor, direitos, palavras-chave, estrelas). No Lightroom "
                         "Classic: Ficheiro › Importação automática › Definições, com a pasta acima como «pasta vigiada».",
                         objectName="hint", wordWrap=True))
        lay.addWidget(gm)

        g3 = QGroupBox("Vídeo")
        f3 = QFormLayout(g3)
        rc = s["record"]
        cmb_codec = QComboBox()
        for key, label in CODECS.items():
            cmb_codec.addItem(label, key)
        cmb_codec.setCurrentIndex(max(0, cmb_codec.findData(rc["codec"])))
        cmb_codec.currentIndexChanged.connect(lambda _i: rc.__setitem__("codec", cmb_codec.currentData()))
        f3.addRow("Codec", cmb_codec)
        chk_log = QCheckBox("Gravar em LOG (S-Log3 · ProRes 422 HQ 10 bits · .mov)")
        chk_log.setToolTip("Imagem plana com mais latitude para color grading no DaVinci/Premiere. Ignora o codec acima.")
        chk_log.setChecked(bool(rc.get("log")))
        chk_log.toggled.connect(lambda v: rc.__setitem__("log", v))
        f3.addRow(chk_log)
        lrow = QHBoxLayout()
        chk_neutral = QCheckBox("Log sem o look (imagem neutra)")
        chk_neutral.setToolTip("Recomendado: o ajuste inteligente e o look de cor ficam só no ecrã; o ficheiro Log sai neutro")
        chk_neutral.setChecked(bool(rc.get("log_neutral", True)))
        chk_neutral.toggled.connect(lambda v: rc.__setitem__("log_neutral", v))
        b_lut = QPushButton("Exportar LUT Log→Rec709…")
        b_lut.clicked.connect(self.export_log_lut)
        lrow.addWidget(chk_neutral)
        lrow.addWidget(b_lut)
        f3.addRow(lrow)
        f3.addRow(QLabel("Log ≈ 750 MB/min a 1080p30 (o ProRes é pesado mas edita sem esforço). A câmara entrega 8 bits: "
                         "o Log a 10 bits preserva todos os níveis sem banding, mas não cria gama dinâmica que o sensor não tem. "
                         "Enquanto grava em Log, o ecrã atualiza a meio ritmo para o ficheiro não perder frames.",
                         objectName="hint", wordWrap=True))
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
        trow = QHBoxLayout()
        spn_timer = QSpinBox(minimum=1, maximum=3600, value=int(rc.get("timer", 3)), suffix=" s",
                             toolTip="Usado por Shift+R, pelo telemóvel e pela seta ▾ do botão Gravar")
        spn_timer.valueChanged.connect(lambda v: rc.__setitem__("timer", v))
        b_timer = QPushButton("⏱ Gravar com temporizador")
        b_timer.clicked.connect(lambda: self.record_with_timer(spn_timer.value()))
        trow.addWidget(spn_timer)
        trow.addWidget(b_timer, 1)
        f3.addRow("Temporizador", trow)
        self.spn_auto_stop = QSpinBox(minimum=0, maximum=24 * 3600, value=int(rc.get("auto_stop", 0)), suffix=" s",
                                      specialValueText="sem limite")
        self.spn_auto_stop.valueChanged.connect(self._set_auto_stop)
        f3.addRow("Parar após", self.spn_auto_stop)
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
        self.capture.hud = self.hud
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
        self._last_meta = meta
        if self.btn_track.isChecked():  # só enquadra se o tracking estiver ligado (não só o desfoque)
            self.framer.step(meta.get("raw_box"), self.tracker.mode, head=self.tracker.head)

    def _ui_tick(self) -> None:
        if self.capture:
            w, h = self.capture.size
            extra = ""
            if self.ai.active():
                ms = self.ai.results.timings
                extra = " · IA " + " ".join(f"{k} {v:.0f}ms" for k, v in ms.items())
            self.lbl_stats.setText(f"{w}×{h} · {self.capture.fps:4.1f} fps · {self.ptz.state()['backend']}{extra}")
        if self.recorder:
            secs = int((time.time() - self.recorder.started.timestamp()))
            self.preview.rec_time = f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
            limit = int(self.settings["record"].get("auto_stop", 0))
            if limit:
                self.preview.rec_time += f" / {limit // 3600:02d}:{limit % 3600 // 60:02d}:{limit % 60:02d}"
        if self.remote:
            while not self.remote.commands.empty():
                self._remote_command(self.remote.commands.get_nowait())
        while not self.tracker.events.empty():      # gestos reconhecidos na thread de captura
            ev, owner = self.tracker.events.get_nowait()
            self._handle_gesture(ev, owner)
        while not self._obs_scenes.empty():          # cenas do OBS com etiquetas [seguir], [preset 2]…
            for cmd in scene_commands(self._obs_scenes.get_nowait()):
                self._remote_command(cmd)
        self._poll_gamepad()
        self._calibration_tick()
        self._update_hud_state()
        if hasattr(self, "_tick_slow"):
            self._tick_slow += 1
        else:
            self._tick_slow = 0
        if self._tick_slow % 15 == 0:  # ~2× por segundo
            self.lbl_track_state.setText(self.tracker.state if self.tracker.enabled else "parado")
            ck = self.settings["pipeline"]["chroma"]
            if ck.get("enabled"):
                self.lbl_chroma_perf.setText(f"{self.pipeline.chroma_fps:.0f} imagens/s · {self.pipeline.chroma.last_ms:.0f} ms/frame · "
                                             f"{'GPU (OpenCL)' if ck.get('gpu', True) and self.pipeline.chroma._gpu() else 'CPU'}"
                                             " · 2 threads em paralelo")
            else:
                self.lbl_chroma_perf.setText("desligado")
            gr = self.settings["pipeline"]["grade"]
            if gr.get("mode", "off") != "off":
                rep = self.pipeline.grader.report
                self.lbl_grade.setText(" · ".join(f"{k}: {v}" for k, v in rep.items()) if rep else "a analisar…")
            else:
                self.lbl_grade.setText("")

    def _update_hud_state(self) -> None:
        st = self.hud.state
        st["recording"] = self.recorder is not None
        if self.recorder:
            st["rec_start"] = self.recorder.started.timestamp()
        st["fps"] = float(self.settings["fps"])
        icons = []
        if self.tracker.enabled and self.btn_track.isChecked():
            icons.append(f"◎ {self.tracker.state.upper()}")
        if self.settings["tracking"].get("gestures"):
            icons.append("👌 GESTOS")
        if self.settings["pipeline"]["chroma"].get("enabled"):
            icons.append("🟩 CHROMA")
        if self.settings["pipeline"].get("night"):
            icons.append("🌙 NOITE")
        gm = self.settings["pipeline"].get("grade", {}).get("mode", "off")
        if gm != "off":
            icons.append("✨ AUTO" if gm == "auto" else "🎨 REFERÊNCIA")
        st["status"] = "   ".join(icons)
        st["info"] = self.preview.info
        st["slate"] = f"{self.settings['project']} · {self.settings['scene']} · T{int(self.settings['take']):02d}"
        st["countdown"] = self.preview.countdown
        st["countdown_label"] = self.preview.countdown_label

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
        self.framer.reset()
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

    # --- foto: agora ou com temporizador (as mesmas opções da gravação) ------------------------
    PHOTO_TIMERS = (3, 5, 10, 15, 30)

    def _build_photo_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("📷  Foto agora   (Espaço)", lambda: self.take_photo("single"))
        menu.addSection("Temporizador (Shift+Espaço repete o último)")
        for s in self.PHOTO_TIMERS:
            menu.addAction(f"⏱  Foto daqui a {s} s", lambda s=s: self.photo_with_timer(s))
        menu.addAction("⏱  Personalizado…", self._photo_timer_custom)
        menu.addSection("Com temporizador, em modo")
        self._photo_timer_mode = "single"
        grp = []
        for label, mode in (("Foto simples", "single"), ("Burst (escolhe a mais nítida)", "burst"), ("Noite (empilhar frames)", "stack")):
            act = menu.addAction(label, lambda m=mode: self._set_photo_timer_mode(m))
            act.setCheckable(True)
            act.setChecked(mode == "single")
            grp.append((mode, act))
        self._photo_mode_actions = grp
        return menu

    def _set_photo_timer_mode(self, mode: str) -> None:
        self._photo_timer_mode = mode
        for m, act in self._photo_mode_actions:
            act.setChecked(m == mode)

    def _photo_timer_custom(self) -> None:
        secs, ok = QInputDialog.getInt(self, "Temporizador", "Tirar a foto daqui a (segundos):",
                                       int(self.settings["photo"].get("timer", 3)), 1, 3600)
        if ok:
            self.photo_with_timer(secs)

    def _photo_clicked(self) -> None:
        if self._photo_cd_timer.isActive():   # clicar durante a contagem cancela
            self.cancel_photo_timer()
            return
        self.take_photo("single")

    def photo_with_timer(self, secs: int | None = None, mode: str | None = None) -> None:
        if not self.capture:
            self.statusBar().showMessage("Sem imagem para fotografar")
            return
        if self._photo_cd_timer.isActive():
            self.cancel_photo_timer()
            return
        secs = int(secs or self.settings["photo"].get("timer", 3))
        self.settings["photo"]["timer"] = secs
        self._photo_cd = secs
        self._photo_cd_mode = mode or getattr(self, "_photo_timer_mode", "single")
        self.preview.countdown = secs
        self.preview.countdown_label = "📷 FOTO em"
        self.btn_photo.setText(f"⏱  {secs} s · cancelar")
        self._photo_cd_timer.start()

    def cancel_photo_timer(self) -> None:
        self._photo_cd_timer.stop()
        self.preview.countdown = 0
        self.preview.countdown_label = ""
        self.btn_photo.setText("📷  Foto")
        self.preview.update()
        self.statusBar().showMessage("Temporizador da foto cancelado", 2000)

    def _photo_countdown_tick(self) -> None:
        self._photo_cd -= 1
        if self._photo_cd > 0:
            self.preview.countdown = self._photo_cd
            self.btn_photo.setText(f"⏱  {self._photo_cd} s · cancelar")
            if self.settings["record"].get("countdown_beep", True):
                QApplication.beep()
            self.preview.update()
            return
        self._photo_cd_timer.stop()
        self.preview.countdown = 0
        self.preview.countdown_label = ""
        self.btn_photo.setText("📷  Foto")
        self.take_photo(self._photo_cd_mode)

    def take_photo(self, mode: str = "single") -> None:
        if not self.capture:
            self.statusBar().showMessage("Sem imagem para fotografar")
            return
        if mode.startswith("timer"):   # compatibilidade: "timer3", "timer10", "timer5"…
            self.photo_with_timer(int(mode[5:] or 3), "single")
            return
        ph = self.settings["photo"]
        processed = ph["apply_look"]
        cap = self.capture
        fmt = ph["format"]
        snapshot = {k: self.settings[k] for k in ("output_dir", "project", "scene", "take")}
        meta = self._photo_meta()
        lr_dir = ph.get("lightroom_dir", "")
        pl = self.settings["pipeline"]
        mirror, flip = bool(pl["mirror"]), bool(pl["flip"])
        chroma_on = bool(pl["chroma"].get("enabled"))
        want_mask = bool(ph.get("export_mask")) and chroma_on
        transparent = processed and self.pipeline.chroma.transparent
        ghost_on = bool(self.settings["compose"].get("ghost"))

        def orient(f):   # os frames «crus» vêm antes do pipeline: aplicar só espelho/inversão
            if mirror and flip:
                return cv2.flip(f, -1)
            if mirror:
                return cv2.flip(f, 1)
            return cv2.flip(f, 0) if flip else f

        def finish(saved: list, ghost_src) -> None:
            if lr_dir:   # cópia para a pasta vigiada pela Importação automática do Lightroom
                try:
                    Path(lr_dir).mkdir(parents=True, exist_ok=True)
                    import shutil
                    for pth in saved:
                        shutil.copy2(pth, Path(lr_dir) / Path(pth).name)
                except OSError as exc:
                    self.notify.emit(f"Foto guardada, mas a cópia para o Lightroom falhou: {exc}")
            if ghost_on and ghost_src is not None:
                self._ghost_pending = cv2.resize(ghost_src, (960, int(ghost_src.shape[0] * 960 / ghost_src.shape[1])))
            self.photo_saved.emit(str(saved[0]))

        if fmt in ("dng", "dng+jpg"):
            def work_raw():
                try:
                    n = int(ph.get("raw_frames", 8))
                    if n > 1:
                        self.notify.emit(f"RAW: a juntar {n} frames em luz linear - mantenha a câmara parada…")
                    frames = [orient(f) for f in cap.grab(n, False)]
                    base = output_path(snapshot, "photo", "dng")
                    saved = [pf.save_dng(pf.linear_stack(frames), base, meta)]
                    look = cap.latest(True)
                    if fmt == "dng+jpg" and look is not None:   # o JPEG com o look, mesmo nome (par RAW+JPEG)
                        if hudmod.any_on(self.settings, "photo"):
                            look = self.hud.burn(look, dict(self._last_meta), "photo")
                        saved.append(pf.save_photo(look, base.with_suffix(".jpg"), "jpg", None, meta))
                    finish(saved, look)
                except Exception as exc:
                    self.notify.emit(f"Falha na foto RAW: {exc}")
            threading.Thread(target=work_raw, daemon=True).start()
            return

        def work():
            try:
                alpha = None
                if mode == "burst":
                    img = best_sharpness(cap.grab(int(ph["burst"]), processed))
                elif mode == "stack":
                    self.notify.emit(f"A empilhar {ph['stack']} frames - mantenha a câmara parada…")
                    img = stack_frames(cap.grab(int(ph["stack"]), processed))
                elif transparent:   # chroma com fundo transparente → PNG/TIFF com alfa
                    img, alpha = cap.latest_with_alpha()
                else:
                    img = cap.latest(processed)
                if img is None:
                    raise RuntimeError("sem frame")
                if transparent and alpha is None:
                    alpha = cap.latest_alpha()
                if hudmod.any_on(self.settings, "photo"):   # ex.: logótipo, data/hora, claquete
                    img = self.hud.burn(img, dict(self._last_meta), "photo")
                path = pf.save_photo(img, output_path(snapshot, "photo", fmt), fmt, alpha if transparent else None, meta)
                saved = [path]
                if want_mask:   # máscara (branco = sujeito) + original sem chroma, para recortes finos
                    m = alpha if alpha is not None else cap.latest_alpha()
                    raw = cap.latest(False)
                    if m is not None:
                        mpath = path.with_name(path.stem + "_mascara.png")
                        ok, buf = cv2.imencode(".png", m)
                        if ok:
                            buf.tofile(str(mpath))
                            saved.append(mpath)
                    if raw is not None:
                        saved.append(pf.save_photo(orient(raw), path.with_name(path.stem + "_original" + path.suffix), fmt, None, meta))
                finish(saved, img)
            except Exception as exc:
                self.notify.emit(f"Falha na foto: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _photo_meta(self) -> dict:
        m = dict(self.settings["meta"])
        m["make"] = "Alflor Studio"
        m["model"] = self.device or "Camera"
        extra = [self.settings["project"], self.settings["scene"]]
        m["keywords"] = ", ".join(k for k in [str(m.get("keywords", "")).strip(", ")] + extra if k)
        return m

    def open_review(self) -> None:
        from .review import ReviewWindow
        import datetime as _dt
        folder = Path(self.settings["output_dir"]) / _dt.date.today().strftime("%Y-%m-%d") / "Fotos"
        if not folder.exists():
            folder = Path(self.settings["output_dir"])
        self._review_win = ReviewWindow(folder, self)
        self._review_win.show()

    def choose_lightroom_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta vigiada pelo Lightroom (Importação automática)",
                                             self.settings["photo"].get("lightroom_dir") or str(Path.home() / "Pictures"))
        if d:
            self.settings["photo"]["lightroom_dir"] = d
            self.ed_lr.setText(d)
            self.settings.save()

    def open_lightroom(self) -> None:
        import subprocess
        pfiles = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files")]
        names = (r"Adobe\Adobe Lightroom Classic\Lightroom.exe", r"Adobe\Adobe Lightroom CC\lightroom.exe",
                 r"Adobe\Adobe Lightroom\lightroom.exe")
        for base in pfiles:
            for n in names:
                exe = Path(base) / n
                if exe.exists():
                    subprocess.Popen([str(exe)])
                    return
        QMessageBox.information(self, APP_NAME, "Não encontrei o Lightroom instalado neste PC.\n\nAs fotos têm EXIF/XMP e "
                                "perfil sRGB: pode importá-las em qualquer editor (Lightroom, Capture One, darktable…).")

    def _on_photo_saved(self, path: str) -> None:
        self.preview.countdown = 0
        g = getattr(self, "_ghost_pending", None)
        if g is not None:   # fantasma da última foto (só no ecrã)
            g = np.ascontiguousarray(g)
            self.preview.ghost = QImage(g.data, g.shape[1], g.shape[0], g.strides[0], QImage.Format.Format_BGR888).copy()
            self._ghost_pending = None
            self.preview.update()
        QApplication.beep()
        self.statusBar().showMessage(f"📷 {path}", 6000)

    # --- gravação: agora, com temporizador, com duração automática --------------------------
    REC_TIMERS = (3, 5, 10, 15, 30)
    REC_DURATIONS = ((0, "Sem limite (parar à mão)"), (10, "10 s"), (30, "30 s"), (60, "1 min"), (300, "5 min"), (600, "10 min"))

    def _build_rec_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("●  Gravar agora   (R)", lambda: self.toggle_record(True))
        menu.addSection("Temporizador (Shift+R repete o último)")
        for s in self.REC_TIMERS:
            menu.addAction(f"⏱  Gravar daqui a {s} s", lambda s=s: self.record_with_timer(s))
        menu.addAction("⏱  Personalizado…", self._rec_timer_custom)
        menu.addSection("Parar automaticamente após")
        rc = self.settings["record"]
        self._dur_actions = []
        for secs, label in self.REC_DURATIONS:
            act = menu.addAction(label, lambda secs=secs: self._set_auto_stop(secs))
            act.setCheckable(True)
            act.setChecked(int(rc.get("auto_stop", 0)) == secs)
            self._dur_actions.append((secs, act))
        menu.addAction("Duração personalizada…", self._rec_duration_custom)
        menu.addSeparator()
        self._act_beep = menu.addAction("Bip na contagem decrescente")
        self._act_beep.setCheckable(True)
        self._act_beep.setChecked(bool(rc.get("countdown_beep", True)))
        self._act_beep.toggled.connect(lambda v: rc.__setitem__("countdown_beep", v))
        return menu

    def _set_auto_stop(self, secs: int) -> None:
        self.settings["record"]["auto_stop"] = int(secs)
        for s, act in self._dur_actions:
            act.setChecked(s == secs)
        if hasattr(self, "spn_auto_stop"):
            self.spn_auto_stop.blockSignals(True)
            self.spn_auto_stop.setValue(int(secs))
            self.spn_auto_stop.blockSignals(False)
        self.statusBar().showMessage("Gravação sem limite" if not secs else f"A gravação para sozinha após {secs} s", 3000)

    def _rec_timer_custom(self) -> None:
        secs, ok = QInputDialog.getInt(self, "Temporizador", "Começar a gravar daqui a (segundos):",
                                       int(self.settings["record"].get("timer", 3)), 1, 3600)
        if ok:
            self.record_with_timer(secs)

    def _rec_duration_custom(self) -> None:
        secs, ok = QInputDialog.getInt(self, "Duração", "Parar a gravação automaticamente após (segundos, 0 = sem limite):",
                                       int(self.settings["record"].get("auto_stop", 0)), 0, 24 * 3600)
        if ok:
            self._set_auto_stop(secs)

    def _rec_clicked(self, _checked: bool = False) -> None:
        if self._rec_cd_timer.isActive():   # clicar durante a contagem cancela
            self.cancel_record_timer()
            return
        self.toggle_record()

    def record_with_timer(self, secs: int | None = None) -> None:
        """Começa a gravar após uma contagem decrescente visível na imagem."""
        if self.recorder:
            self.toggle_record(False)
            return
        if not (self.capture and self.ffmpeg):
            self.statusBar().showMessage("Gravação indisponível (sem imagem ou sem FFmpeg)")
            return
        secs = int(secs or self.settings["record"].get("timer", 3))
        self.settings["record"]["timer"] = secs
        threading.Thread(target=self._warm_encoders, daemon=True).start()  # pronto quando a contagem acabar
        self._rec_cd = secs
        self.preview.countdown = secs
        self.preview.countdown_label = "● REC em"
        self.btn_rec.setChecked(True)
        self.btn_rec.setText(f"⏱  {secs} s · cancelar")
        self._rec_cd_timer.start()
        self.statusBar().showMessage(f"A gravação começa daqui a {secs} s (clique em Gravar para cancelar)", secs * 1000)

    def cancel_record_timer(self) -> None:
        self._rec_cd_timer.stop()
        self._rec_cd = 0
        self.preview.countdown = 0
        self.preview.countdown_label = ""
        self.btn_rec.setChecked(False)
        self.btn_rec.setText("●  Gravar")
        self.preview.update()
        self.statusBar().showMessage("Temporizador de gravação cancelado", 2000)

    def _rec_countdown_tick(self) -> None:
        self._rec_cd -= 1
        if self._rec_cd > 0:
            self.preview.countdown = self._rec_cd
            self.btn_rec.setText(f"⏱  {self._rec_cd} s · cancelar")
            if self.settings["record"].get("countdown_beep", True):
                QApplication.beep()
            self.preview.update()
            return
        self._rec_cd_timer.stop()
        self.preview.countdown = 0
        self.preview.countdown_label = ""
        self.btn_rec.setChecked(False)
        self.toggle_record(True)

    def toggle_record(self, on: bool | None = None) -> None:
        on = (self.recorder is None) if on is None or not isinstance(on, bool) else on
        if self._rec_cd_timer.isActive():
            self.cancel_record_timer()
            if not on:
                return
        if on and not self.recorder:
            if not (self.capture and self.ffmpeg):
                self.btn_rec.setChecked(False)
                self.statusBar().showMessage("Gravação indisponível (sem imagem ou sem FFmpeg)")
                return
            rc = self.settings["record"]
            log = bool(rc.get("log"))
            path = output_path(self.settings, "video", "mov" if log else rc["container"])
            try:
                self.recorder = Recorder(self.ffmpeg, path, self.capture.size, float(self.settings["fps"]),
                                         "prores_log" if log else rc["codec"], rc["quality"], rc["audio"])
                self.recorder.wants_neutral = log and bool(rc.get("log_neutral", True))
                self.capture.preview_div = 2 if log else 1   # Log é pesado: o ecrã a meio ritmo garante 25/25 fps
            except Exception as exc:
                self.btn_rec.setChecked(False)
                self.statusBar().showMessage(f"Erro a iniciar gravação: {exc}")
                return
            self.capture.sinks.append(self.recorder)
            self.preview.recording = True
            self.btn_rec.setChecked(True)
            self.btn_rec.setText("■  Parar")
            limit = int(rc.get("auto_stop", 0))
            if limit > 0:   # paragem automática (takes com duração fixa)
                self._auto_stop.start(limit * 1000)
            self.statusBar().showMessage(f"● A gravar ({self.recorder.codec}) → {path}" + (f" · para sozinha após {limit} s" if limit else ""))
            if self.settings["obs"]["sync_record"] and self.obs.connected:
                self._obs_do(self.obs.start_record)
        elif not on and self.recorder:
            rec = self.recorder
            self.recorder = None
            self._auto_stop.stop()
            if self.capture and rec in self.capture.sinks:
                self.capture.sinks.remove(rec)
            if self.capture:
                self.capture.preview_div = 1
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
            triggers = ""
            if self.settings["obs"].get("scene_triggers", True) and self.obs.start_events(self._obs_scenes.put):
                triggers = " · gatilhos de cena ativos ([seguir], [parar], [preset N]…)"
            self.lbl_obs.setText("✓ ligado ao OBS" + triggers)
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
                f"Painel no OBS (Docas › Docas de browser personalizadas):  http://127.0.0.1:{r['port']}/dock?k={k}\n"
                f"VLC (stream):  {base}/stream.mjpg?k={k}\n"
                f"Programa da Mesa (VLC):  {base}/program.mjpg?k={k}\n"
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
            "track_state": self.tracker.state, "gestures": bool(self.settings["tracking"].get("gestures")),
            "chroma": bool(self.settings["pipeline"]["chroma"].get("enabled")),
            "switcher": self.switcher.state() if self.switcher else None,
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
        elif a == "photo":   # opcional: delay=N (temporizador) e mode=single|burst|stack
            delay = int(num("delay", 0))
            if delay > 0:
                self.photo_with_timer(delay, cmd.get("mode", "single"))
            else:
                self.take_photo(cmd.get("mode", "single"))
        elif a == "record":   # opcional: delay=N (segundos de temporizador)
            delay = int(num("delay", 0))
            self.record_with_timer(delay) if delay > 0 and not self.recorder else self.toggle_record()
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
        elif a == "grade":   # mode=auto|reference|off
            self.set_grade_mode(cmd.get("mode", "auto"))
        elif a in ("track_on", "track_off"):
            self.set_tracking(a == "track_on")
        elif a == "gestures":
            self.set_gestures(not self.settings["tracking"].get("gestures"))
        elif a in ("chroma", "chroma_on", "chroma_off"):
            self.set_chroma(not self.settings["pipeline"]["chroma"]["enabled"] if a == "chroma" else a == "chroma_on")
        elif a in ("record_on", "record_off"):
            self.toggle_record(a == "record_on")
        elif a.startswith("sw_"):
            if self.switcher is None:
                if a == "sw_program":   # um gatilho [cam N] abre a mesa automaticamente
                    self.open_switcher()
                else:
                    return
            eng = self.switcher.engine
            n = int(num("n", 1)) - 1
            if a == "sw_program" and 0 <= n < 8:
                eng.set_program(n)
            elif a == "sw_preview" and 0 <= n < 8:
                eng.set_preview(n)
            elif a == "sw_cut":
                eng.cut()
            elif a == "sw_auto":
                eng.auto()
            elif a == "sw_ftb":
                eng.toggle_ftb()

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
            Qt.Key.Key_K: lambda: self.set_chroma(not self.settings["pipeline"]["chroma"]["enabled"]),
        }
        if key == Qt.Key.Key_M and ctrl:
            self.open_switcher()
            return True
        if key == Qt.Key.Key_V and ctrl and mods & Qt.KeyboardModifier.ShiftModifier:
            self.open_review()                  # Ctrl+Shift+V: revisão/triagem das fotos de hoje
            return True
        if key == Qt.Key.Key_Space and mods & Qt.KeyboardModifier.ShiftModifier:
            self.photo_with_timer()             # Shift+Espaço: foto com o último temporizador
            return True
        if key == Qt.Key.Key_Space and self._photo_cd_timer.isActive():
            self.cancel_photo_timer()
            return True
        if key == Qt.Key.Key_R and mods & Qt.KeyboardModifier.ShiftModifier:
            self.record_with_timer()            # Shift+R: gravar com o último temporizador
            return True
        if key == Qt.Key.Key_R and self._rec_cd_timer.isActive():
            self.cancel_record_timer()
            return True
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
        if self.switcher:
            self.switcher.close()
        if self.recorder:
            rec = self.recorder
            self.toggle_record(False)
            rec._thread.join(3)
        if self.remote:
            self.remote.stop()
        self._stop_capture()
        self.ai.stop()
        self.pipeline.chroma.close()
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
