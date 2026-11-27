"""Integrações: OBS (WebSocket), comando XInput, atualização automática via GitHub."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from . import GITHUB_REPO, __version__
from .config import ROOT_DIR

NO_WINDOW = 0x08000000
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)  # OBS fechado não é um erro


# --- OBS ------------------------------------------------------------------------------
def obs_websocket_config() -> dict:
    path = Path(os.environ.get("APPDATA", "")) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        "enabled": data.get("server_enabled", False),
        "port": data.get("server_port", 4455),
        "password": data.get("server_password", "") if data.get("auth_required", True) else "",
    }


class OBSLink:
    """Controla o OBS: cenas, gravação, transmissão. Lê a password local do OBS."""

    def __init__(self) -> None:
        self.client = None
        self.error = ""

    @property
    def connected(self) -> bool:
        return self.client is not None

    def connect(self) -> bool:
        cfg = obs_websocket_config()
        if not cfg or not cfg.get("enabled"):
            self.error = ("Servidor WebSocket do OBS desligado. No OBS: Ferramentas › Definições do servidor "
                          "WebSocket › «Ativar servidor WebSocket». A password é lida automaticamente.")
            return False
        try:
            import obsws_python as obs
            self.client = obs.ReqClient(host="localhost", port=cfg["port"], password=cfg["password"], timeout=3)
            self.client.get_version()
            self.error = ""
            return True
        except Exception as exc:  # OBS fechado, servidor desligado, password errada…
            self.client = None
            self.error = f"OBS indisponível: {exc}"
            return False

    def disconnect(self) -> None:
        try:
            if self.client:
                self.client.disconnect()
        except Exception:
            pass
        self.client = None

    def _call(self, name: str, *args):
        if not self.client:
            raise RuntimeError("OBS não ligado")
        try:
            return getattr(self.client, name)(*args)
        except Exception:
            self.client = None
            raise

    def scenes(self) -> tuple[list[str], str]:
        r = self._call("get_scene_list")
        names = [s["sceneName"] for s in reversed(r.scenes)]
        return names, r.current_program_scene_name

    def set_scene(self, name: str) -> None:
        self._call("set_current_program_scene", name)

    def record_status(self) -> bool:
        return bool(self._call("get_record_status").output_active)

    def start_record(self) -> None:
        self._call("start_record")

    def stop_record(self) -> None:
        self._call("stop_record")

    def toggle_stream(self) -> None:
        self._call("toggle_stream")

    def stream_status(self) -> bool:
        return bool(self._call("get_stream_status").output_active)


# --- Comando de jogo (XInput: Xbox e compatíveis) ----------------------------------------
class _XGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _XState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XGamepad)]


BUTTONS = {
    "up": 0x0001, "down": 0x0002, "left": 0x0004, "right": 0x0008, "start": 0x0010, "back": 0x0020,
    "lb": 0x0100, "rb": 0x0200, "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000,
}


class Gamepad:
    def __init__(self) -> None:
        self._dll = None
        for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                self._dll = getattr(ctypes.windll, name)
                break
            except (OSError, AttributeError):
                continue
        self._prev_buttons = 0

    def poll(self) -> dict | None:
        """Devolve eixos normalizados e botões recém-premidos, ou None sem comando."""
        if not self._dll:
            return None
        for i in range(4):
            st = _XState()
            if self._dll.XInputGetState(i, ctypes.byref(st)) == 0:
                g = st.Gamepad
                def axis(v, dead=7849):
                    return 0.0 if abs(v) < dead else max(-1.0, min(1.0, v / 32767.0))
                pressed = g.wButtons & ~self._prev_buttons
                self._prev_buttons = g.wButtons
                return {
                    "lx": axis(g.sThumbLX), "ly": axis(g.sThumbLY), "ry": axis(g.sThumbRY),
                    "lt": g.bLeftTrigger / 255.0, "rt": g.bRightTrigger / 255.0,
                    "pressed": {n for n, m in BUTTONS.items() if pressed & m},
                }
        return None


# --- Atualização automática ----------------------------------------------------------------
def _ver(tag: str) -> tuple:
    nums = [int(x) for x in tag.lstrip("vV").split(".") if x.isdigit()]
    return tuple(nums + [0] * (3 - len(nums)))


def check_update(timeout: float = 6.0) -> dict | None:
    """Consulta a última release no GitHub. Devolve {tag, url, notes} se for mais recente."""
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "alflor-studio-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name", "")
    if tag and _ver(tag) > _ver(__version__):
        return {"tag": tag, "zip": data.get("zipball_url"), "notes": data.get("body", ""), "page": data.get("html_url")}
    return None


def apply_update(info: dict) -> str:
    """Atualiza com git (se for um clone) ou descarregando o código da release."""
    python = sys.executable
    if (ROOT_DIR / ".git").exists() and shutil.which("git"):
        out = subprocess.run(["git", "-C", str(ROOT_DIR), "pull", "--ff-only"], capture_output=True, text=True, creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise RuntimeError(out.stderr or out.stdout)
    else:
        tmp = Path(tempfile.mkdtemp(prefix="alflorstudio-update-"))
        archive = tmp / "update.zip"
        urllib.request.urlretrieve(info["zip"], archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp / "src")
        src = next((tmp / "src").iterdir())
        for item in src.iterdir():
            if item.name in (".venv", ".git"):
                continue
            dest = ROOT_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    subprocess.run([python, "-m", "pip", "install", "-q", "-r", str(ROOT_DIR / "requirements.txt")],
                   capture_output=True, creationflags=NO_WINDOW)
    return info["tag"]


def restart_app() -> None:
    subprocess.Popen([sys.executable, "-m", "ptz_studio"], cwd=str(ROOT_DIR))
    time.sleep(0.2)
