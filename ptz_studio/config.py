"""Definições persistentes da aplicação (JSON em %APPDATA%\\AlflorStudio)."""
from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
from pathlib import Path

APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "AlflorStudio"
LOCAL_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AlflorStudio"
CONFIG_FILE = APP_DATA / "config.json"
# nomes anteriores da aplicação: definições, calibração, presets e modelos migram automaticamente
LEGACY_NAMES = ("PTZStudio",)
PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent

DEFAULTS: dict = {
    "device": "UVC Camera",
    "resolution": "max",            # "max" | "3840x2160" | "1920x1080" | "1280x720"
    "fps": 30,
    "output_dir": str(Path.home() / "Videos" / "Alflor Studio"),
    "project": "Projeto",
    "scene": "Cena1",
    "take": 1,
    "photo": {"format": "png", "apply_look": True, "burst": 5, "stack": 8, "timer": 3},
    "record": {"codec": "auto", "quality": 20, "audio": "", "container": "mp4",
               "timer": 3, "auto_stop": 0, "countdown_beep": True},
    "ptz": {"speed": 0.5, "invert_pan": False, "invert_tilt": False, "mode": "auto"},
    "calibration": {},              # por dispositivo: pan_fw, tilt_fh, zoom_max_mag
    "pipeline": {
        "denoise": 0, "night": False, "night_gain": 1.8, "sharpen": 0.0,
        "look": "Natural", "bg_blur": False, "mirror": False, "flip": False,
        "grade": {"mode": "off", "strength": 0.8, "continuous": True, "interval": 2.0,
                  "ref_path": "", "auto_denoise": True},
        "chroma": {
            "enabled": False, "mode": "chroma", "key": "green", "color": [60, 180, 60],
            "similarity": 0.10, "smoothness": 0.06, "spill": 0.7, "choke": 1, "feather": 1.5,
            "garbage": [0.0, 0.0, 0.0, 0.0], "bg": "transparent", "bg_color": [40, 30, 20],
            "bg_path": "", "bg_blur": 0, "light_wrap": 0.0, "view": "composite", "gpu": True,
        },
    },
    "overlays": {"grid": True, "safe": False, "zebra": False, "peaking": False, "hist": True},
    "tracking": {
        "mode": "face", "target": "center", "shot": "Plano médio",
        "drive": "ptz", "auto_zoom": False, "sensitivity": 0.5, "lost_home": False,
        "ai": True, "gestures": False, "recover": True,
        "gesture_actions": {"ok": "track_start", "palm": "track_stop", "victory": "photo",
                            "thumbs_up": "none", "fist": "none"},
    },
    "remote": {"enabled": False, "port": 8765, "token": ""},
    "obs": {"auto_connect": True, "sync_record": False, "scene_triggers": True},
    "switcher": {"inputs": [], "transition": "mix", "duration": 1.0, "pip_corner": "tr",
                 "pip_size": 0.3, "lower_title": "", "lower_sub": "", "logo_path": "",
                 "logo_corner": "tr"},
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

    @staticmethod
    def _legacy_data() -> dict:
        """Junta os dados de versões com nome antigo (ex.: «PTZ Studio»). Corre uma única vez."""
        marker = APP_DATA / ".migrado"
        if marker.exists():
            return {}
        merged: dict = {}
        for old in LEGACY_NAMES:
            for new_dir, env in ((APP_DATA, "APPDATA"), (LOCAL_DATA, "LOCALAPPDATA")):
                old_dir = Path(os.environ.get(env, Path.home())) / old
                if not old_dir.is_dir():
                    continue
                for src in old_dir.rglob("*"):  # copia o que falta (miniaturas, modelos IA); nunca sobrescreve
                    rel = src.relative_to(old_dir)
                    if src.is_file() and rel.parts[0] != "bin" and rel.name != "config.json":
                        dst = new_dir / rel
                        if not dst.exists():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
            old_cfg = Path(os.environ.get("APPDATA", Path.home())) / old / "config.json"
            try:
                data = json.loads(old_cfg.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("output_dir") == str(Path.home() / "Videos" / "PTZ Studio"):
                data.pop("output_dir")  # usa a nova pasta por omissão
            text = json.dumps(data, ensure_ascii=False).replace(f"\\\\{old}\\\\", f"\\\\{APP_DATA.name}\\\\")
            merged = _merge(merged, json.loads(text))
        APP_DATA.mkdir(parents=True, exist_ok=True)
        marker.write_text("1")
        return merged

    @classmethod
    def load(cls) -> "Settings":
        data = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                shutil.copy(CONFIG_FILE, CONFIG_FILE.with_suffix(".corrompido.json"))
        legacy = cls._legacy_data()
        if legacy:  # o que já existe na pasta nova tem prioridade
            data = _merge(legacy, data)
        s = cls(_merge(DEFAULTS, data))
        if legacy:
            s.save()
        if not s["remote"]["token"]:
            s["remote"]["token"] = secrets.token_urlsafe(8)
        return s

    readonly = False   # modo de teste: NUNCA escrever no disco (nem por caminhos indiretos)

    def save(self) -> None:
        if self.readonly:
            return
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
