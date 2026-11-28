"""Ajuste inteligente de imagem: automático (ambiente/luz do dia) e igualar a uma referência.

Modo «auto» — como o "Auto" do Lightroom, mas contínuo e suave:
  · balanço de brancos por "mundo cinzento" calculado só em píxeis neutros (não é enganado
    por um ecrã verde grande ou por roupa colorida);
  · exposição: ponto de preto/branco (percentis 0,5 % / 99,5 %) e gama para levar os
    meios-tons ao alvo;
  · saturação moderada se a imagem estiver lavada (ex.: luz fluorescente);
  · estimativa de ruído → sugere/aplica redução de ruído quando a luz baixa.

Modo «reference» — como o "Color Match" do Premiere / "Shot Match" do DaVinci:
  · o operador dá uma imagem de que gosta (foto editada no Lightroom, frame de outro
    trabalho, ou o frame atual "congelado");
  · cor: transferência linear de Monge-Kantorovich (Pitié & Kokaram, 2007) — iguala média e
    covariância das cores, robusta mesmo com conteúdo diferente;
  · tons: correspondência da curva de luminância (CDF), suavizada e com declive limitado.

Resultado: uma matriz 3×4 + uma LUT de tons, aplicadas em tempo real (cv2.transform + LUT,
~5 ms a 1080p). Em modo contínuo os parâmetros mudam devagar (sem cintilação).
"""
from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np

DEFAULTS = {"mode": "off", "strength": 0.8, "continuous": True, "interval": 2.0,
            "ref_path": "", "auto_denoise": True}
MODES = {"off": "Desligado", "auto": "Automático (luz e cor do ambiente)",
         "reference": "Igualar a uma imagem de referência"}
LUMA_BGR = np.array([0.114, 0.587, 0.299], np.float32)
TARGET_MID = 0.46


def _sample(img: np.ndarray, w: int = 320) -> np.ndarray:
    h = max(1, int(img.shape[0] * w / img.shape[1]))
    small = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return small.reshape(-1, 3).astype(np.float32) / 255.0


def _sqrtm(c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d, u = np.linalg.eigh((c + c.T) / 2)
    d = np.clip(d, 1e-6, None)
    return (u * np.sqrt(d)) @ u.T, (u / np.sqrt(d)) @ u.T


def mkl_transfer(src: np.ndarray, ref: np.ndarray, limit: tuple[float, float] = (0.5, 2.0)) -> tuple[np.ndarray, np.ndarray]:
    """Transferência linear de cor (Monge-Kantorovich): T·(x−μs)+μt iguala média e covariância."""
    ms, mt = src.mean(0), ref.mean(0)
    eye = np.eye(3, dtype=np.float64) * 1e-4
    cs = np.cov((src - ms).T) + eye
    ct = np.cov((ref - mt).T) + eye
    cs_h, cs_ih = _sqrtm(cs)
    c_h, _ = _sqrtm(cs_h @ ct @ cs_h)
    t = cs_ih @ c_h @ cs_ih
    d, u = np.linalg.eigh((t + t.T) / 2)   # regularização: nada de ganhos extremos
    t = (u * np.clip(d, *limit)) @ u.T
    b = mt - t @ ms
    return t.astype(np.float32), b.astype(np.float32)


def _tone_lut_from_cdf(src_y: np.ndarray, ref_y: np.ndarray) -> np.ndarray:
    """LUT 256 que leva a distribuição de luminância da fonte à da referência (suave, monótona)."""
    hs, _ = np.histogram(src_y, 256, (0, 1))
    hr, _ = np.histogram(ref_y, 256, (0, 1))
    cs = np.cumsum(hs) / max(1, hs.sum())
    cr = np.cumsum(hr) / max(1, hr.sum())
    lut = np.interp(cs, cr, np.linspace(0, 1, 256))
    x = np.linspace(0, 1, 256)
    lut = x + cv2.GaussianBlur((lut - x).reshape(1, -1).astype(np.float32), (0, 0), 6).ravel()  # suaviza
    # declive limitado (sem posterização nem contraste exagerado) e monotonia
    d = np.clip(np.diff(lut), 0.4 / 255, 2.5 / 255)
    lut = np.concatenate([[max(0.0, lut[0])], max(0.0, lut[0]) + np.cumsum(d)])
    return _protect_highlights(np.clip(lut, 0, 1))


def _protect_highlights(lut: np.ndarray, knee: float = 0.8) -> np.ndarray:
    """Compressão suave acima do joelho: os brilhos aproximam-se do branco sem saturar cores."""
    x = np.linspace(0, 1, len(lut))
    over = lut > knee
    if over.any():
        room = 1.0 - knee
        excess = (lut[over] - knee) / room
        lut = lut.copy()
        lut[over] = knee + room * (1 - np.exp(-excess * 1.6)) / (1 - math.exp(-1.6))
    return np.clip(lut, 0, 1)


def _clipped(px: np.ndarray, m: np.ndarray, b: np.ndarray, lut: np.ndarray) -> float:
    """Fração da amostra que ficaria com algum canal saturado (0 ou 1) depois da correção.

    Simula a cadeia real: matriz → curva só na luminância (em YCrCb o Y desloca os três canais
    por igual).
    """
    out = px @ m.T + b
    y = np.clip(out @ LUMA_BGR, 0, 1)
    y2 = np.interp(y, np.linspace(0, 1, len(lut)), lut)
    out = out + (y2 - y)[:, None]
    return float(((out >= 0.999) | (out <= 0.001)).any(1).mean())


def _gamut_guard(px, m, b, lut):
    """Se a correção queimar mais do que a imagem original, aproxima-a da identidade até deixar
    de queimar (bisseção). Evita brancos e cores saturadas «estourados»."""
    eye, zero, ident = np.eye(3, dtype=np.float32), np.zeros(3, np.float32), np.linspace(0, 1, len(lut)).astype(np.float32)
    allowed = _clipped(px, eye, zero, ident) + 0.004
    if _clipped(px, m, b, lut) <= allowed:
        return m, b, lut, 1.0
    lo, hi = 0.0, 1.0
    for _ in range(7):
        f = (lo + hi) / 2
        if _clipped(px, eye + (m - eye) * f, b * f, ident + (lut - ident) * f) <= allowed:
            lo = f
        else:
            hi = f
    return eye + (m - eye) * lo, b * lo, ident + (lut - ident) * lo, lo


def _noise_sigma(img: np.ndarray) -> float:
    """Desvio do ruído (escala 0–255) pelo MAD do passa-alto numa versão 640 px."""
    g = cv2.cvtColor(cv2.resize(img, (640, int(img.shape[0] * 640 / img.shape[1]))), cv2.COLOR_BGR2GRAY).astype(np.float32)
    hp = g - cv2.GaussianBlur(g, (0, 0), 1.2)
    return float(1.4826 * np.median(np.abs(hp - np.median(hp))))


class Grader:
    def __init__(self, settings: dict) -> None:
        self.s = settings
        for k, v in DEFAULTS.items():
            self.s.setdefault(k, v)
        self.M: np.ndarray | None = None      # matriz 3×3 (BGR, 0..1)
        self.b: np.ndarray | None = None      # deslocamento (0..1)
        self.lut: np.ndarray | None = None    # 256 valores 0..1
        self.report: dict = {}
        self.suggested_denoise = 0
        self._ref_px: np.ndarray | None = None
        self._ref_y: np.ndarray | None = None
        self._ref_src = ""
        self._last = 0.0
        self._force = True
        self._eff_cache: tuple | None = None
        self._busy = False
        self._lock = threading.Lock()

    # --- referência ------------------------------------------------------------------------
    def set_reference(self, img: np.ndarray, source: str = "imagem") -> None:
        px = _sample(img, 400)
        with self._lock:
            self._ref_px = px
            self._ref_y = px @ LUMA_BGR
            self._ref_src = source
            self._force = True

    def load_reference(self, path: str) -> bool:
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return False
        self.set_reference(img, path)
        return True

    @property
    def has_reference(self) -> bool:
        return self._ref_px is not None

    def request_update(self) -> None:
        self._force = True

    # --- análise -------------------------------------------------------------------------------
    def _auto_params(self, px: np.ndarray, frame: np.ndarray):
        y = px @ LUMA_BGR
        mx, mn = px.max(1), px.min(1)
        sat = (mx - mn) / np.maximum(mx, 1e-3)
        neutral = (sat < 0.25) & (y > 0.12) & (y < 0.95)
        # "white patch": os neutros mais claros (paredes, roupa branca) dizem melhor a cor da luz
        # do que a média da cena, que um ecrã verde ou uma parede colorida enviesam
        bright = neutral & (y > np.percentile(y[neutral], 70)) if neutral.mean() > 0.02 else neutral
        if bright.mean() > 0.005:
            base = px[bright]
        elif neutral.mean() > 0.02:
            base = px[neutral]
        else:
            base = px
        means = base.mean(0)
        gains = np.clip(means.mean() / np.maximum(means, 1e-3), 0.8, 1.25)   # BGR
        gains = gains / gains.max()   # só reduz canais: o balanço de brancos nunca satura nada
        m = np.diag(gains).astype(np.float32)
        px2 = np.clip(px * gains, 0, 1)
        sat2 = ((px2.max(1) - px2.min(1)) / np.maximum(px2.max(1), 1e-3)).mean()
        sfac = 1.15 if sat2 < 0.18 else (0.93 if sat2 > 0.6 else 1.0)
        sm = (1 - sfac) * np.tile(LUMA_BGR, (3, 1)) + sfac * np.eye(3, dtype=np.float32)
        m = (sm @ m).astype(np.float32)
        y2 = np.clip(px2 @ LUMA_BGR, 0, 1)
        # níveis pelos extremos reais (0,1 % / 99,9 %): imagem escura → ganho; lavada → contraste;
        # imagem já boa → quase nada (no máximo 0,1 % dos brilhos podem saturar).
        # Os níveis são aplicados em RGB (como um ganho real de câmara: a cor acompanha a luz);
        # só a gama dos meios-tons atua na luminância. Assim nada fica "lavado".
        bp = min(float(np.percentile(y2, 0.1)), 0.25)
        wp = float(np.clip(np.percentile(y2, 99.9), 0.5, 1.0))
        scale = 1.0 / max(1e-3, wp - bp)
        m = (m * scale).astype(np.float32)
        b = np.full(3, -bp * scale, np.float32)
        med = float(np.median(np.clip((y2 - bp) * scale, 0, 1)))
        # meios-tons: zona morta 0,30–0,62 (cenas claras/escuras de propósito não são "corrigidas")
        target = min(max(med, 0.30), 0.62)
        gamma = float(np.clip(math.log(target) / math.log(min(max(med, 0.02), 0.98)), 0.6, 1.5))
        x = np.linspace(0, 1, 256)
        lut = _protect_highlights(x ** gamma)
        m, b, lut, applied = _gamut_guard(px, m, b, lut.astype(np.float32))
        # relatório legível
        ev = math.log2(TARGET_MID / max(float(np.median(y)), 1e-3))
        cb_, cg_, cr_ = means / max(means.mean(), 1e-6)   # (nomes próprios: «b» é o deslocamento)
        cast = "neutra"
        if cr_ - cb_ > 0.06:
            cast = "quente (amarelada)"
        elif cb_ - cr_ > 0.06:
            cast = "fria (azulada)"
        elif cg_ - (cr_ + cb_) / 2 > 0.05:
            cast = "esverdeada"
        elif (cr_ + cb_) / 2 - cg_ > 0.05:
            cast = "magenta"
        sigma = _noise_sigma(frame)
        self.suggested_denoise = 0 if sigma < 2.0 else 1 if sigma < 3.5 else 2 if sigma < 6 else 3
        light = "muito fraca" if ev > 1.5 else "fraca" if ev > 0.6 else "forte" if ev < -0.6 else "boa"
        report = {"luz": light, "exposição": f"{ev:+.1f} EV", "cor": cast, "contraste": f"{float(np.std(y)):.2f}",
                  "ruído": f"σ {sigma:.1f}", "denoise sugerido": self.suggested_denoise}
        if applied < 0.999:
            report["proteção de brilhos"] = f"correção a {applied * 100:.0f}%"
        return m, b, lut.astype(np.float32), report

    def _ref_params(self, px: np.ndarray, frame: np.ndarray):
        with self._lock:
            ref, ref_y = self._ref_px, self._ref_y
        t, b = mkl_transfer(px, ref)
        px2 = np.clip(px @ t.T + b, 0, 1)
        lut = _tone_lut_from_cdf(px2 @ LUMA_BGR, ref_y).astype(np.float32)
        t, b, lut, applied = _gamut_guard(px, t, b, lut)
        ev = math.log2(max(float(np.median(ref_y)), 1e-3) / max(float(np.median(px @ LUMA_BGR)), 1e-3))
        dm = ref.mean(0) - px.mean(0)
        self.suggested_denoise = 0
        report = {"referência": self._ref_src.split("\\")[-1].split("/")[-1], "exposição": f"{ev:+.1f} EV",
                  "cor (B/G/R)": " ".join(f"{v:+.2f}" for v in dm),
                  "contraste": f"{float(np.std(px @ LUMA_BGR)):.2f} → {float(np.std(ref_y)):.2f}"}
        if applied < 0.999:
            report["proteção de brilhos"] = f"correção a {applied * 100:.0f}%"
        return t, b, lut, report

    def analyze(self, frame: np.ndarray) -> None:
        mode = self.s.get("mode", "off")
        if mode == "off" or (mode == "reference" and self._ref_px is None):
            return
        px = _sample(frame)
        m, b, lut, report = self._auto_params(px, frame) if mode == "auto" else self._ref_params(px, frame)
        with self._lock:
            if self.M is not None and self.s.get("continuous") and not self._force:
                a = 0.3   # transição suave entre análises (sem cintilação)
                m = self.M + (m - self.M) * a
                b = self.b + (b - self.b) * a
                lut = self.lut + (lut - self.lut) * a
            self.M, self.b, self.lut, self.report = m, b, lut, report
            self._eff_cache = None
            self._force = False
        self._last = time.monotonic()

    def maybe_update(self, frame: np.ndarray) -> None:
        """Chamado a cada frame; a análise (~40 ms) corre numa thread à parte (sem soluços na imagem)."""
        if self.s.get("mode", "off") == "off" or self._busy:
            return
        due = self.s.get("continuous") and time.monotonic() - self._last > float(self.s.get("interval", 2.0))
        if self._force or self.M is None or due:
            self._busy = True
            self._last = time.monotonic()
            img = frame.copy()

            def work():
                try:
                    self.analyze(img)
                except Exception as exc:  # nunca parar a captura por uma análise falhada
                    self.report = {"erro": str(exc)}
                finally:
                    self._busy = False
            threading.Thread(target=work, name="grading", daemon=True).start()

    # --- aplicação ------------------------------------------------------------------------------
    def _effective(self):
        s = float(self.s.get("strength", 0.8))
        key = (s, id(self.M), id(self.lut))
        if self._eff_cache is None or self._eff_cache[0] != key:
            m = (1 - s) * np.eye(3, dtype=np.float32) + s * self.M
            b = s * self.b * 255.0
            mat = np.hstack([m, b.reshape(3, 1)]).astype(np.float32)   # 3×4 para cv2.transform
            x = np.linspace(0, 1, 256, dtype=np.float32)
            y = np.clip(((1 - s) * x + s * self.lut) * 255 + 0.5, 0, 255).astype(np.uint8)
            ident = np.arange(256, dtype=np.uint8)
            # a curva de tons atua SÓ na luminância (canal Y de YCrCb): aplicá-la a cada canal RGB
            # queimava cores saturadas (ex.: o verde do ecrã chegava a 255)
            lut3 = np.dstack([y, ident, ident]).reshape(256, 1, 3)
            self._eff_cache = (key, mat, lut3)
        return self._eff_cache[1], self._eff_cache[2]

    def apply(self, frame: np.ndarray) -> np.ndarray:
        if self.s.get("mode", "off") == "off" or self.M is None:
            return frame
        with self._lock:
            mat, lut3 = self._effective()
        ycc = cv2.cvtColor(cv2.transform(frame, mat), cv2.COLOR_BGR2YCrCb)
        return cv2.cvtColor(cv2.LUT(ycc, lut3), cv2.COLOR_YCrCb2BGR)
