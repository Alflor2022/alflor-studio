"""Reconhecimento de gestos a partir dos 21 pontos da mão (MediaPipe HandLandmarker).

A classificação é geométrica e invariante à rotação/escala da mão (usa distâncias
relativas ao tamanho da palma), por isso funciona com a mão inclinada e a várias
distâncias. O `GestureGate` só dispara um evento quando o gesto é mantido de forma
estável (evita disparos acidentais) e aplica um tempo de espera entre eventos.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

# índices MediaPipe
WRIST = 0
THUMB = (1, 2, 3, 4)
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

GESTURES = {
    "ok": "👌 OK",
    "palm": "✋ Palma aberta",
    "victory": "✌ Vitória",
    "thumbs_up": "👍 Polegar para cima",
    "fist": "✊ Punho fechado",
}

ACTIONS = {
    "none": "Nada",
    "track_start": "Começar a seguir quem fez o gesto",
    "track_stop": "Parar o tracking",
    "track_toggle": "Ligar/desligar tracking",
    "photo": "Tirar foto (temporizador 3 s)",
    "record_toggle": "Começar/parar gravação",
    "home": "Câmara para o início",
    "preset_next": "Próximo preset",
}

DEFAULT_ACTIONS = {"ok": "track_start", "palm": "track_stop", "victory": "photo",
                   "thumbs_up": "none", "fist": "none"}


def _d(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a[:2] - b[:2]))


def finger_extended(lm: np.ndarray, finger: tuple[int, int, int, int]) -> bool:
    """Dedo esticado: a ponta está claramente mais longe do pulso do que a articulação PIP."""
    mcp, pip, _dip, tip = finger
    w = lm[WRIST]
    return _d(w, lm[tip]) > _d(w, lm[pip]) * 1.15 and _d(w, lm[tip]) > _d(w, lm[mcp]) * 1.3


def thumb_extended(lm: np.ndarray) -> bool:
    palm = _d(lm[WRIST], lm[MIDDLE[0]])
    # ponta do polegar afastada da base do indicador e do mindinho
    return _d(lm[THUMB[3]], lm[INDEX[0]]) > 0.45 * palm and _d(lm[THUMB[3]], lm[PINKY[0]]) > 0.9 * palm


def classify(lm: np.ndarray) -> str | None:
    """lm: array (21, 2|3) normalizado. Devolve o nome do gesto ou None."""
    lm = np.asarray(lm, dtype=np.float32)
    if lm.shape[0] < 21:
        return None
    palm = _d(lm[WRIST], lm[MIDDLE[0]])
    if palm < 1e-4:
        return None
    idx, mid, ring, pinky = (finger_extended(lm, f) for f in (INDEX, MIDDLE, RING, PINKY))
    thumb = thumb_extended(lm)
    tip_gap = _d(lm[THUMB[3]], lm[INDEX[3]]) / palm
    # 👌 OK: polegar e indicador a tocar (círculo) com médio, anelar e mindinho esticados
    if tip_gap < 0.35 and mid and ring and pinky:
        return "ok"
    # ✋ palma aberta: quatro dedos esticados e o polegar aberto (ou claramente afastado do indicador)
    if idx and mid and ring and pinky and (thumb or tip_gap > 0.8) and tip_gap > 0.6:
        return "palm"
    # ✌ vitória: indicador e médio esticados, anelar e mindinho dobrados
    if idx and mid and not ring and not pinky:
        return "victory"
    # 👍 polegar para cima: polegar esticado e acima do pulso, restantes dobrados
    if thumb and not (idx or mid or ring or pinky) and lm[THUMB[3]][1] < lm[WRIST][1] - 0.5 * palm:
        return "thumbs_up"
    # ✊ punho: nenhum dedo esticado
    if not (idx or mid or ring or pinky) and not thumb:
        return "fist"
    return None


@dataclass
class GestureEvent:
    gesture: str
    hand_center: tuple[float, float]   # coordenadas normalizadas do frame
    hand_box: tuple[float, float, float, float]


@dataclass
class GestureGate:
    """Confirma um gesto mantido `hold` segundos e espera `cooldown` antes do próximo."""

    hold: float = 0.6
    cooldown: float = 2.5
    min_ratio: float = 0.7             # fração mínima de leituras com o gesto durante o `hold`
    _history: list = field(default_factory=list)
    _last_event: float = 0.0

    def update(self, gestures: list[tuple[str | None, tuple, tuple]], now: float | None = None) -> GestureEvent | None:
        """gestures: lista (gesto, centro, caixa) das mãos visíveis neste instante."""
        now = time.monotonic() if now is None else now
        best = next(((g, c, b) for g, c, b in gestures if g), (None, None, None))
        self._history.append((now, best))
        self._history = [h for h in self._history if now - h[0] <= self.hold]
        if now - self._last_event < self.cooldown or not best[0]:
            return None
        window = self._history
        if window[-1][0] - window[0][0] < self.hold * 0.8 or len(window) < 3:
            return None
        same = sum(1 for _, (g, _c, _b) in window if g == best[0])
        if same / len(window) >= self.min_ratio:
            self._last_event = now
            self._history.clear()
            return GestureEvent(best[0], best[1], best[2])
        return None
