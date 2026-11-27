# -*- mode: python ; coding: utf-8 -*-
# Especificação PyInstaller do Alflor Studio (pasta única, sem consola).
# Usado por packaging\build.ps1, que define as variáveis de ambiente com os caminhos.
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
MODELS = os.environ["ALFLOR_MODELS"]       # pasta com os modelos de IA
BRIDGE = os.environ["ALFLOR_BRIDGE"]       # ptzbridge.exe já compilado
FFMPEG = os.environ["ALFLOR_FFMPEG"]       # ffmpeg.exe
ICON = os.environ["ALFLOR_ICON"]

datas = [
    (os.path.join(ROOT, "ptz_studio", "web"), "ptz_studio/web"),
    (os.path.join(ROOT, "ptz_studio", "native"), "ptz_studio/native"),
    (MODELS, "models"),
]
binaries = [(BRIDGE, "bin"), (FFMPEG, "bin")]
hidden = ["obsws_python", "pyvirtualcam", "qrcode", "websocket"]
for pkg in ("mediapipe", "pyvirtualcam"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
datas += collect_data_files("cv2")          # inclui os classificadores Haar (rostos)

a = Analysis(
    [os.path.join(SPECPATH, "run_alflor.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "IPython", "jupyter", "pytest", "PyQt5", "PyQt6", "pandas", "jax", "tensorflow", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Alflor Studio",
    icon=ICON,
    console=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Alflor Studio")
