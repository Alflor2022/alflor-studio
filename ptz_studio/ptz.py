"""Controlador PTZ: movimento contínuo, absoluto, presets, travellings e PTZ digital.

A câmara USB testada não devolve a posição real (lê sempre 0) e só obedece com vídeo
ativo. Por isso o estado é mantido aqui (posição comandada) e enviado à câmara de
forma limitada (~15 envios/s) para movimentos suaves.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

EASINGS = {
    "Linear": lambda x: x,
    "Suave": lambda x: x * x * (3 - 2 * x),
    "Cinema": lambda x: x * x * x * (x * (x * 6 - 15) + 10),
    "Acelerar": lambda x: x * x,
    "Travar": lambda x: 1 - (1 - x) * (1 - x),
}

AXES = ("pan", "tilt", "zoom")


@dataclass
class Axis:
    min: float
    max: float
    value: float = 0.0
    home: float = 0.0

    def clamp(self, v: float) -> float:
        return max(self.min, min(self.max, v))

    @property
    def span(self) -> float:
        return self.max - self.min


@dataclass
class Keyframe:
    pan: float
    tilt: float
    zoom: float
    duration: float = 3.0     # segundos até chegar a este ponto
    hold: float = 0.0         # pausa depois de chegar
    easing: str = "Cinema"


@dataclass
class Animation:
    frames: list[Keyframe]
    loop: bool = False
    index: int = 0
    start: dict = field(default_factory=dict)
    t0: float = 0.0
    holding_until: float = 0.0


class DigitalBackend:
    """PTZ digital (recorte na imagem) para câmaras sem motor: telemóveis, DSLR, webcams."""

    name = "digital"
    RANGES = {"pan": (-1000, 1000), "tilt": (-1000, 1000), "zoom": (0, 100)}
    MAX_SCALE = 4.0

    def __init__(self, pipeline) -> None:
        self.pipeline = pipeline

    def apply(self, pan: float, tilt: float, zoom: float) -> None:
        scale = 1.0 + (zoom / 100.0) * (self.MAX_SCALE - 1.0)
        # o centro do recorte pode deslocar-se até à margem permitida pelo zoom
        free = 0.5 - 0.5 / scale
        cx = 0.5 + (pan / 1000.0) * free
        cy = 0.5 + (tilt / 1000.0) * free
        self.pipeline.set_eptz(cx, cy, scale)


class PhysicalBackend:
    """Motores reais via DirectShow (IAMCameraControl)."""

    name = "physical"

    def __init__(self, bridge, ranges: dict) -> None:
        self.bridge = bridge
        self.ranges = ranges
        self._last: dict[str, int] = {}

    def apply(self, pan: float, tilt: float, zoom: float) -> None:
        for axis, value in (("pan", pan), ("tilt", tilt), ("zoom", zoom)):
            if axis not in self.ranges:
                continue
            v = int(round(value))
            if self._last.get(axis) != v:
                self._last[axis] = v
                try:
                    self.bridge.set_cam(axis, v)
                except Exception:  # a ponte pode ter caído; não interromper a UI
                    self._last.pop(axis, None)

    def force(self) -> None:
        self._last.clear()


class PTZController(QObject):
    changed = Signal(dict)
    animation_finished = Signal()

    TICK_HZ = 30
    SEND_HZ = 15

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.backend = None
        self.axes: dict[str, Axis] = {}
        self.velocity = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}
        self.anim: Animation | None = None
        self._dirty = False
        self._last_send = 0.0
        self._last_tick = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / self.TICK_HZ))

    # --- configuração ----------------------------------------------------
    def attach(self, backend, ranges: dict) -> None:
        self.backend = backend
        self.axes = {}
        for axis in AXES:
            if axis in ranges:
                r = ranges[axis]
                lo, hi = (r["min"], r["max"]) if isinstance(r, dict) else r
                home = r.get("def", 0) if isinstance(r, dict) else 0
                home = max(lo, min(hi, home))
                self.axes[axis] = Axis(lo, hi, home, home)
        self._dirty = True
        self.changed.emit(self.state())

    @property
    def is_physical(self) -> bool:
        return getattr(self.backend, "name", "") == "physical"

    def has(self, axis: str) -> bool:
        return axis in self.axes

    def state(self) -> dict:
        return {a: ax.value for a, ax in self.axes.items()} | {
            "backend": getattr(self.backend, "name", "none"),
            "animating": self.anim is not None,
        }

    # --- comandos ----------------------------------------------------------
    def set_velocity(self, pan: float | None = None, tilt: float | None = None, zoom: float | None = None) -> None:
        """Velocidade normalizada -1..1 por eixo (teclado, joystick, comando)."""
        if pan is not None:
            self.velocity["pan"] = max(-1.0, min(1.0, pan))
        if tilt is not None:
            self.velocity["tilt"] = max(-1.0, min(1.0, tilt))
        if zoom is not None:
            self.velocity["zoom"] = max(-1.0, min(1.0, zoom))
        if any(self.velocity.values()):
            self.anim = None

    def stop(self) -> None:
        self.velocity = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}
        self.anim = None

    def move_to(self, pan=None, tilt=None, zoom=None, duration: float = 0.0, easing: str = "Cinema") -> None:
        target = {
            "pan": pan if pan is not None else self._val("pan"),
            "tilt": tilt if tilt is not None else self._val("tilt"),
            "zoom": zoom if zoom is not None else self._val("zoom"),
        }
        if duration <= 0:
            self.anim = None
            for a, v in target.items():
                if a in self.axes:
                    self.axes[a].value = self.axes[a].clamp(v)
            self._dirty = True
            return
        self.play([Keyframe(target["pan"], target["tilt"], target["zoom"], duration, 0, easing)])

    def nudge(self, dpan: float = 0, dtilt: float = 0, dzoom: float = 0) -> None:
        for a, d in (("pan", dpan), ("tilt", dtilt), ("zoom", dzoom)):
            if a in self.axes and d:
                self.axes[a].value = self.axes[a].clamp(self.axes[a].value + d)
                self._dirty = True

    def home(self, duration: float = 1.5) -> None:
        self.move_to(*(self.axes[a].home if a in self.axes else None for a in AXES), duration=duration)

    def play(self, frames: list[Keyframe], loop: bool = False) -> None:
        if not frames:
            return
        self.velocity = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0}
        self.anim = Animation(frames=frames, loop=loop, start=self._snapshot(), t0=time.monotonic())

    def resync(self) -> None:
        """Reenvia a posição atual (ex.: depois de a câmara reabrir o vídeo)."""
        if isinstance(self.backend, PhysicalBackend):
            self.backend.force()
        self._dirty = True

    # --- movimentos de realizador -----------------------------------------
    def motion(self, kind: str, duration: float = 4.0, amount: float = 0.35) -> None:
        p, t, z = self._val("pan"), self._val("tilt"), self._val("zoom")
        zspan = self.axes["zoom"].span if "zoom" in self.axes else 0
        pspan = self.axes["pan"].span if "pan" in self.axes else 0
        tspan = self.axes["tilt"].span if "tilt" in self.axes else 0
        frames: list[Keyframe] = []
        if kind == "push_in":           # aproximação lenta (efeito dolly-in)
            frames = [Keyframe(p, t, z + zspan * amount, duration)]
        elif kind == "pull_out":        # afastamento / revelação
            frames = [Keyframe(p, t, z - zspan * amount, duration)]
        elif kind == "crash_zoom":      # zoom muito rápido
            frames = [Keyframe(p, t, z + zspan * 0.6, 0.6, 0, "Acelerar")]
        elif kind == "pan_sweep":       # panorâmica de um lado ao outro e volta
            a = pspan * amount / 2
            frames = [Keyframe(p - a, t, z, duration / 3), Keyframe(p + a, t, z, duration), Keyframe(p, t, z, duration / 3)]
        elif kind == "whip_pan":        # chicote para o lado
            frames = [Keyframe(p + pspan * 0.25, t, z, 0.5, 0, "Acelerar")]
        elif kind == "tilt_reveal":     # parte de baixo e sobe até ao enquadramento
            frames = [Keyframe(p, t - tspan * amount / 2, z, 0.8), Keyframe(p, t, z, duration)]
        elif kind == "orbit_feel":      # pan + zoom combinados (sensação de arco)
            a = pspan * amount / 3
            frames = [Keyframe(p - a, t, z + zspan * 0.15, duration / 2), Keyframe(p + a, t, z, duration / 2)]
        if frames:
            self.play(frames)

    # --- interno -------------------------------------------------------------
    def _val(self, axis: str) -> float:
        return self.axes[axis].value if axis in self.axes else 0.0

    def _snapshot(self) -> dict:
        return {a: ax.value for a, ax in self.axes.items()}

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        speed = float(self.settings["ptz"]["speed"])
        inv = self.settings["ptz"]
        if self.anim:
            self._step_animation(now)
        elif any(self.velocity.values()):
            # velocidade máxima: atravessa o pan inteiro em ~3 s; zoom em ~2,5 s
            for axis, v in self.velocity.items():
                if not v or axis not in self.axes:
                    continue
                if axis == "pan" and inv["invert_pan"]:
                    v = -v
                if axis == "tilt" and inv["invert_tilt"]:
                    v = -v
                curve = math.copysign(abs(v) ** 1.6, v)  # mais precisão em movimentos lentos
                rate = self.axes[axis].span / (2.5 if axis == "zoom" else 3.0)
                ax = self.axes[axis]
                ax.value = ax.clamp(ax.value + curve * rate * dt * (0.15 + 1.7 * speed))
            self._dirty = True
        if self._dirty and self.backend and now - self._last_send >= 1.0 / self.SEND_HZ:
            self._last_send = now
            self._dirty = False
            s = self._snapshot()
            self.backend.apply(s.get("pan", 0), s.get("tilt", 0), s.get("zoom", 0))
            self.changed.emit(self.state())

    def _step_animation(self, now: float) -> None:
        anim = self.anim
        assert anim
        if anim.holding_until:
            if now < anim.holding_until:
                return
            anim.holding_until = 0
            self._advance(now)
            return
        kf = anim.frames[anim.index]
        x = 1.0 if kf.duration <= 0 else min(1.0, (now - anim.t0) / kf.duration)
        e = EASINGS.get(kf.easing, EASINGS["Cinema"])(x)
        for axis, target in (("pan", kf.pan), ("tilt", kf.tilt), ("zoom", kf.zoom)):
            if axis in self.axes:
                a = anim.start.get(axis, 0.0)
                self.axes[axis].value = self.axes[axis].clamp(a + (target - a) * e)
        self._dirty = True
        if x >= 1.0:
            if kf.hold > 0:
                anim.holding_until = now + kf.hold
            else:
                self._advance(now)

    def _advance(self, now: float) -> None:
        anim = self.anim
        assert anim
        anim.index += 1
        if anim.index >= len(anim.frames):
            if anim.loop:
                anim.index = 0
            else:
                self.anim = None
                self.animation_finished.emit()
                return
        anim.start = self._snapshot()
        anim.t0 = now
