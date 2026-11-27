"""Definições persistentes da aplicação (JSON em %APPDATA%\\PTZStudio)."""
from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
from pathlib import Path

APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "PTZStudio"
LOCAL_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PTZStudio"
CONFIG_FILE = APP_DATA / "config.json"
PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent

DEFAULTS: dict = {
    "device": "UVC Camera",
    "resolution": "max",            # "max" | "3840x2160" | "1920x1080" | "1280x720"
    "fps": 30,
    "output_dir": str(Path.home() / "Videos" / "PTZ Studio"),
    "project": "Projeto",
    "scene": "Cena1",
    "take": 1,
    "photo": {"format": "png", "apply_look": True, "burst": 5, "stack": 8},
    "record": {"codec": "auto", "quality": 20, "audio": "", "container": "mp4"},
    "ptz": {"speed": 0.5, "invert_pan": False, "invert_tilt": False, "mode": "auto"},
    "calibration": {},              # por dispositivo: pan_fw, tilt_fh, zoom_max_mag
    "pipeline": {
        "denoise": 0, "night": False, "night_gain": 1.8, "sharpen": 0.0,
        "look": "Natural", "bg_blur": False, "mirror": False, "flip": False,
    },
    "overlays": {"grid": True, "safe": False, "zebra": False, "peaking": False, "hist": True},
    "tracking": {
        "mode": "face", "target": "center", "shot": "Plano médio",
        "drive": "ptz", "auto_zoom": False, "sensitivity": 0.5, "lost_home": False,
    },
    "remote": {"enabled": False, "port": 8765, "token": ""},
    "obs": {"auto_connect": True, "sync_record": False},
    "vcam": False,
    "updates": {"check": True, "auto_apply": False, "last_check": 0},
    "presets": {},                  # por dispositivo: {"1": {"p":..,"t":..,"z":..,"name":..,"thumb":..}}
    "sequences": {},                # travellings guardados
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings(dict):
    """Dicionário de definições com gravação atómica."""

    @classmethod
    def load(cls) -> "Settings":
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                shutil.copy(CONFIG_FILE, CONFIG_FILE.with_suffix(".corrompido.json"))
        s = cls(_merge(DEFAULTS, data))
        if not s["remote"]["token"]:
            s["remote"]["token"] = secrets.token_urlsafe(8)
        return s

    def save(self) -> None:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)

    def device_calibration(self, device: str) -> dict:
        cal = {"pan_fw": 1100.0, "tilt_fh": 620.0, "zoom_max_mag": 3.0}
        cal.update(self["calibration"].get(device, {}))
        return cal

    def device_presets(self, device: str) -> dict:
        return self["presets"].setdefault(device, {})
