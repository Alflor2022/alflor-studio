"""Modelos de IA (MediaPipe / OpenCV Zoo): onde estão e como obtê-los.

Procura primeiro os modelos incluídos no executável (pasta `models` junto ao .exe) e
depois a pasta de dados do utilizador. Os que faltarem podem ser descarregados das
fontes oficiais (Google MediaPipe e OpenCV Zoo, licença Apache 2.0).
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from .config import LOCAL_DATA

_MP = "https://storage.googleapis.com/mediapipe-models"
MODELS: dict[str, dict] = {
    "hand": {"file": "hand_landmarker.task", "label": "Mãos e gestos",
             "url": f"{_MP}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"},
    "pose": {"file": "pose_landmarker_lite.task", "label": "Corpo (pose)",
             "url": f"{_MP}/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"},
    "face": {"file": "blaze_face_short_range.tflite", "label": "Rostos (MediaPipe)",
             "url": f"{_MP}/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"},
    "segment": {"file": "selfie_segmenter.tflite", "label": "Recorte de pessoa (fundo por IA)",
                "url": f"{_MP}/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite"},
    "yunet": {"file": "face_detection_yunet_2023mar.onnx", "label": "Rostos (YuNet, longa distância)",
              "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"},
}
USER_DIR = LOCAL_DATA / "models"


def _bundled_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "models"
    return None


def path(key: str) -> Path | None:
    """Caminho do modelo se existir localmente, senão None."""
    name = MODELS[key]["file"]
    for folder in (_bundled_dir(), USER_DIR):
        if folder and (folder / name).exists():
            return folder / name
    return None


def missing() -> list[str]:
    return [k for k in MODELS if path(k) is None]


def download(key: str) -> Path:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    dest = USER_DIR / MODELS[key]["file"]
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(MODELS[key]["url"], headers={"User-Agent": "alflor-studio"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    if tmp.stat().st_size < 10_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download incompleto de {MODELS[key]['file']}")
    tmp.replace(dest)
    return dest


def download_all(keys: list[str] | None = None) -> dict[str, str]:
    """Descarrega os modelos em falta; devolve {chave: 'ok' | mensagem de erro}."""
    result = {}
    for k in keys or missing():
        try:
            download(k)
            result[k] = "ok"
        except Exception as exc:  # um modelo falhado não impede os outros
            result[k] = f"erro: {exc}"
    return result
