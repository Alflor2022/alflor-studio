"""Tracking de sujeito com e sem IA.

Modos
- "face":    rosto (YuNet → Haar como alternativa).
- "person":  corpo inteiro (MediaPipe Pose com IA, ou HOG + CSRT sem IA), com cabeça
             para enquadrar com headroom.
- "subject": qualquer objeto escolhido pelo operador (CSRT), com verificação de
             aparência e **recuperação automática** quando se perde (template + cor).

Identidade: depois de escolhido, o alvo é seguido pela posição prevista e pela
aparência (histograma de cor), para não saltar para outra pessoa que passe perto.

Gestos (IA): 👌 OK inicia o tracking na pessoa que fez o gesto, ✋ palma aberta para,
✌ vitória tira uma foto… (configurável). Os eventos vão para `events` e são executados
pela interface.

Tudo corre na thread de captura; os modelos de IA correm em paralelo no `AIWorker`.
Devolve caixas em coordenadas normalizadas do frame original ("raw").
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import models
from .gestures import GestureGate

YUNET_PATH = models.USER_DIR / models.MODELS["yunet"]["file"]


def download_yunet() -> Path:
    return models.download("yunet")


# Tamanho do rosto (fração da altura do frame) por tipo de plano
SHOT_FACE = {
    "Grande plano": 0.42,
    "Primeiro plano": 0.27,
    "Plano médio": 0.15,
    "Plano americano": 0.10,
    "Plano geral": 0.06,
}
# Tamanho do corpo/objeto (fração da altura) por tipo de plano
SHOT_BODY = {
    "Grande plano": 1.6,
    "Primeiro plano": 1.25,
    "Plano médio": 0.95,
    "Plano americano": 0.75,
    "Plano geral": 0.45,
}
CLOSE_SHOTS = ("Grande plano", "Primeiro plano", "Plano médio")
TARGETS = {  # ponto de composição (x, y) para o centro do sujeito
    "center": (0.5, 0.5),
    "left_third": (1 / 3, 0.5),
    "right_third": (2 / 3, 0.5),
}
Box = tuple[float, float, float, float]
INNER = 0.6   # fração central da caixa usada para comparar cores (modo objeto)


def _make_tracker():
    for name in ("TrackerCSRT_create", "TrackerKCF_create", "TrackerMIL_create"):
        factory = getattr(cv2, name, None)
        if factory:
            return factory()
    raise RuntimeError("Nenhum tracker OpenCV disponível")


def _center(b: Box) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def _clip_box(b: Box) -> Box:
    x, y, w, h = b
    x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    return x, y, max(0.0, min(1.0 - x, w)), max(0.0, min(1.0 - y, h))


def _hist(img: np.ndarray, box: Box, inner: float = 1.0) -> np.ndarray | None:
    """Histograma H-S (aparência) da região; None se for pequena demais.

    `inner` < 1 usa só o miolo da caixa: as seleções feitas à mão incluem fundo nas margens,
    e é o centro que representa o objeto.
    """
    h, w = img.shape[:2]
    if inner < 1.0:
        x, y, bw, bh = box
        box = (x + bw * (1 - inner) / 2, y + bh * (1 - inner) / 2, bw * inner, bh * inner)
    x, y, bw, bh = _clip_box(box)
    x0, y0, x1, y1 = int(x * w), int(y * h), int((x + bw) * w), int((y + bh) * h)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 40, 30), (180, 255, 255))  # ignora cinzentos/escuros sem cor
    if cv2.countNonZero(mask) < 0.15 * mask.size:
        mask = None
    hist = cv2.calcHist([hsv], [0, 1], mask, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 0.5
    return float(max(0.0, cv2.compareHist(a, b, cv2.HISTCMP_CORREL)))


class SubjectTracker:
    WORK_W = 640

    def __init__(self, settings: dict | None = None, ai=None) -> None:
        self.settings = settings if settings is not None else {}
        self.ai = ai
        self.enabled = False
        self.mode = "face"                 # "face" | "person" | "subject"
        self.box: Box | None = None
        self.head: Box | None = None
        self.state = "parado"              # parado | a seguir | à procura | perdido
        self.last_seen = 0.0
        self.hands: list = []              # últimas mãos detetadas (para desenhar)
        self.events: queue.Queue = queue.Queue()
        self.gate = GestureGate()
        self._lock = threading.Lock()
        self._frame_i = 0
        self._tracker = None
        self._pending_select: Box | None = None
        self._pending_lock: Box | None = None
        self._ref_hist: np.ndarray | None = None
        self._template: np.ndarray | None = None
        self._recent_template: np.ndarray | None = None
        self._recent_t = 0.0
        self._bad_checks = 0
        self._last_ai_ts = 0.0
        self._last_hand_ts = 0.0
        cascades = Path(cv2.data.haarcascades)
        self._haar = cv2.CascadeClassifier(str(cascades / "haarcascade_frontalface_default.xml"))
        self._haar_profile = cv2.CascadeClassifier(str(cascades / "haarcascade_profileface.xml"))
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._yunet = None
        self._yunet_size = (0, 0)
        self.load_yunet()

    # --- informação --------------------------------------------------------------------
    @property
    def use_ai(self) -> bool:
        return bool(self.ai and self.ai.available and self.settings.get("ai", True))

    @property
    def detector_name(self) -> str:
        if self.mode == "face":
            return "IA YuNet" if self._yunet is not None else "Haar (clássico)"
        if self.mode == "person":
            return "IA MediaPipe Pose" if self.use_ai else "HOG + CSRT (clássico)"
        return "CSRT + recuperação por aparência"

    def load_yunet(self) -> bool:
        p = models.path("yunet")
        if p and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._yunet = cv2.FaceDetectorYN.create(str(p), "", (320, 240), 0.75, 0.3, 50)
                return True
            except cv2.error:
                self._yunet = None
        return False

    # --- controlo (thread da UI) -----------------------------------------------------
    def set_mode(self, mode: str) -> None:
        with self._lock:
            self.mode = mode
            self._tracker = None
            self.box = None
            self.head = None
            self._ref_hist = None

    def select(self, box: Box) -> None:
        """Seleção manual de um objeto (coordenadas raw normalizadas) → segue esse objeto."""
        with self._lock:
            self.mode = "subject"
            self._pending_select = _clip_box(box)
            self._template = None            # novo objeto: esquece os modelos do anterior
            self._recent_template = None
            self._ref_hist = None
            self.enabled = True

    def lock_on(self, box: Box, mode: str) -> None:
        """Começa a seguir este alvo concreto (ex.: a pessoa que fez o gesto OK)."""
        with self._lock:
            self.mode = mode
            self._tracker = None
            self._pending_lock = _clip_box(box)
            self._template = None
            self._recent_template = None
            self.box = None
            self.enabled = True

    def reset(self) -> None:
        with self._lock:
            self._tracker = None
            self.box = None
            self.head = None
            self._ref_hist = None
            self._template = None
            self._recent_template = None
            self.state = "parado"

    def configure_ai(self, tracking_enabled: bool) -> None:
        """Diz ao motor de IA o que calcular (só o necessário, para poupar CPU)."""
        if not self.ai:
            return
        ai_on = self.use_ai
        gestures = ai_on and bool(self.settings.get("gestures", False))
        self.ai.want["hands"] = gestures
        self.ai.want["pose"] = ai_on and ((tracking_enabled and self.mode == "person") or gestures)

    # --- processamento (thread de captura) ---------------------------------------
    def update(self, frame: np.ndarray) -> Box | None:
        self.configure_ai(self.enabled)
        self._gestures(frame)
        if not self.enabled:
            self.state = "parado"
            return None
        h, w = frame.shape[:2]
        scale = self.WORK_W / w
        small = cv2.resize(frame, (self.WORK_W, int(h * scale)), interpolation=cv2.INTER_AREA)
        self._frame_i += 1
        with self._lock:
            mode = self.mode
            pending, self._pending_select = self._pending_select, None
            lock, self._pending_lock = self._pending_lock, None
        if lock is not None:  # alvo imposto (gesto/OBS): guarda a aparência e começa daí
            self.box = lock
            self._ref_hist = _hist(small, lock)
            self.last_seen = time.monotonic()
            if mode == "subject":
                pending = lock
        found: Box | None = None
        if mode == "subject":
            found = self._update_subject(small, pending)
        elif mode == "face":
            found = self._update_face(small)
        elif mode == "person":
            found = self._update_person(small)
        now = time.monotonic()
        if found:
            self.last_seen = now
            if self.box is not None:
                a = 0.45  # suavização exponencial da caixa
                found = tuple(p + (n - p) * a for p, n in zip(self.box, found))
            self.box = found
            if self._ref_hist is None:
                self._ref_hist = _hist(small, found)
            self.state = "a seguir"
        elif now - self.last_seen > 1.0:
            self.box = None
            self.state = "à procura" if mode == "subject" and self._template is not None else "perdido"
        return self.box

    # --- rosto --------------------------------------------------------------------------
    def _update_face(self, small: np.ndarray) -> Box | None:
        if self._frame_i % 2 and self.box is not None:
            return self.box
        sh, sw = small.shape[:2]
        boxes: list[Box] = []
        if self._yunet is not None:
            if self._yunet_size != (sw, sh):
                self._yunet.setInputSize((sw, sh))
                self._yunet_size = (sw, sh)
            _, faces = self._yunet.detect(small)
            boxes = [] if faces is None else [(f[0] / sw, f[1] / sh, f[2] / sw, f[3] / sh) for f in faces]
        if not boxes and self.use_ai and self.ai.results.faces:
            boxes = list(self.ai.results.faces)
        if not boxes:
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            rects = list(self._haar.detectMultiScale(gray, 1.15, 5, minSize=(22, 22)))
            if not rects:
                rects = list(self._haar_profile.detectMultiScale(gray, 1.15, 5, minSize=(22, 22)))
            boxes = [(x / sw, y / sh, w / sw, h / sh) for x, y, w, h in rects]
        self.head = None
        return self._pick(small, boxes)

    # --- pessoa ------------------------------------------------------------------------
    def _update_person(self, small: np.ndarray) -> Box | None:
        sh, sw = small.shape[:2]
        if self.use_ai:
            r = self.ai.results
            if r.ts == self._last_ai_ts:   # sem resultado novo: mantém
                return self.box
            self._last_ai_ts = r.ts
            persons = list(r.persons)
            chosen = self._pick(small, [p.box for p in persons])
            if chosen is not None:
                # cabeça da pessoa escolhida (para headroom)
                best = min(persons, key=lambda p: sum(abs(a - b) for a, b in zip(p.box, chosen)))
                self.head = best.head
            return chosen
        found = None
        if self._frame_i % 6 == 0 or self._tracker is None:
            rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.06)
            boxes = [(x / sw, y / sh, w / sw, h / sh) for (x, y, w, h), wt in zip(rects, np.ravel(weights)) if wt > 0.4]
            det = self._pick(small, boxes)
            if det:
                found = det
                self._tracker = _make_tracker()
                self._tracker.init(small, tuple(int(v) for v in (det[0] * sw, det[1] * sh, det[2] * sw, det[3] * sh)))
        if found is None and self._tracker is not None:
            ok, r = self._tracker.update(small)
            if ok:
                found = (r[0] / sw, r[1] / sh, r[2] / sw, r[3] / sh)
        self.head = None
        return found

    # --- objeto escolhido (com recuperação) -------------------------------------------
    def _update_subject(self, small: np.ndarray, pending: Box | None) -> Box | None:
        sh, sw = small.shape[:2]
        if pending:
            self._init_subject(small, pending)
        if self._tracker is None:
            return self._search(small) if self._template is not None and self.settings.get("recover", True) else None
        ok, r = self._tracker.update(small)
        if not ok or r[2] < 3 or r[3] < 3:
            self._tracker = None
            return None
        box = (r[0] / sw, r[1] / sh, r[2] / sw, r[3] / sh)
        if self._frame_i % 5 == 0 and self._ref_hist is not None:
            sim = _similarity(self._ref_hist, _hist(small, box, INNER))
            # tolerante ao arrasto da imagem enquanto a PTZ se move (medido: 0,88–0,94 no objeto certo, ~0 fora)
            self._bad_checks = self._bad_checks + 1 if sim < 0.25 else 0
            if self._bad_checks >= 4:  # o tracker escorregou para outra coisa → procurar de novo
                self._bad_checks = 0
                self._tracker = None
                return None
            if sim > 0.8 and time.monotonic() - self._recent_t > 2.0:
                # 2.º modelo, recente: acompanha mudanças de zoom/luz sem perder o original
                x, y, bw, bh = _clip_box(box)
                px, py, pw, ph = int(x * sw), int(y * sh), max(4, int(bw * sw)), max(4, int(bh * sh))
                self._recent_template = cv2.cvtColor(small[py:py + ph, px:px + pw], cv2.COLOR_BGR2GRAY).copy()
                self._recent_t = time.monotonic()
        return box

    def _init_subject(self, small: np.ndarray, box: Box) -> None:
        sh, sw = small.shape[:2]
        x, y, bw, bh = _clip_box(box)
        px, py, pw, ph = int(x * sw), int(y * sh), max(4, int(bw * sw)), max(4, int(bh * sh))
        self._tracker = _make_tracker()
        self._tracker.init(small, (px, py, pw, ph))
        patch = cv2.cvtColor(small[py:py + ph, px:px + pw], cv2.COLOR_BGR2GRAY).copy()
        if self._template is None:          # o modelo original fica (a recuperação reutiliza-o)
            self._template = patch
            self._ref_hist = _hist(small, box, INNER)
        self._recent_template = patch
        self._recent_t = time.monotonic()
        self._bad_checks = 0

    def _search(self, small: np.ndarray) -> Box | None:
        """Procura o objeto perdido em todo o frame (template multi-escala + cor)."""
        if self._frame_i % 3:
            return None
        sh, sw = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        best = (0.0, None)
        templates = [self._template] + ([self._recent_template] if self._recent_template is not None else [])
        for tpl in templates:
            th, tw = tpl.shape[:2]
            for s in (0.6, 0.8, 1.0, 1.25, 1.6):   # o zoom pode ter mudado entretanto
                t = cv2.resize(tpl, (max(4, int(tw * s)), max(4, int(th * s))))
                if t.shape[0] >= sh or t.shape[1] >= sw:
                    continue
                res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(res)
                if mx > best[0]:
                    best = (mx, (loc[0] / sw, loc[1] / sh, t.shape[1] / sw, t.shape[0] / sh))
        score, box = best
        if box is None or score < 0.5:
            return None
        # correspondência de forma muito forte vale por si; uma média tem de confirmar pela cor
        if score < 0.75 and _similarity(self._ref_hist, _hist(small, box, INNER)) < 0.35:
            return None
        self._init_subject(small, box)
        return box

    # --- escolha com identidade -------------------------------------------------------------
    def _pick(self, small: np.ndarray, boxes: list[Box]) -> Box | None:
        if not boxes:
            return None
        if self.box is not None:
            px, py = _center(self.box)
            def cost(b):
                cx, cy = _center(b)
                dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                return dist - 0.15 * _similarity(self._ref_hist, _hist(small, b))
            best = min(boxes, key=cost)
            cx, cy = _center(best)
            if ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 < 0.3:   # perto da posição esperada
                return best
            return None
        if self._ref_hist is not None and time.monotonic() - self.last_seen < 6.0:
            # reaquisição após perda: só aceita quem se pareça com o alvo original
            scored = [(b, _similarity(self._ref_hist, _hist(small, b))) for b in boxes]
            b, sim = max(scored, key=lambda t: t[1])
            return b if sim > 0.5 else None
        return max(boxes, key=lambda b: b[2] * b[3])

    # --- gestos -------------------------------------------------------------------------------
    def _gestures(self, frame: np.ndarray) -> None:
        if not (self.ai and self.ai.want.get("hands")):
            self.hands = []
            return
        r = self.ai.results
        self.hands = r.hands
        if r.ts == self._last_hand_ts:
            return
        self._last_hand_ts = r.ts
        ev = self.gate.update([(h.gesture, h.center, h.box) for h in r.hands], now=r.ts)
        if ev:
            self.events.put((ev, self._owner_of(ev.hand_center, r)))

    def _owner_of(self, hand_xy: tuple[float, float], r) -> tuple[Box | None, Box | None]:
        """Pessoa (corpo, cabeça) a quem pertence a mão que fez o gesto."""
        hx, hy = hand_xy
        best, best_d = None, 9.0
        for p in r.persons:
            x, y, w, h = p.box
            inside = x - 0.05 <= hx <= x + w + 0.05 and y - 0.05 <= hy <= y + h + 0.05
            d = 0.0 if inside else min(abs(hx - (x + w / 2)), 1.0)
            if d < best_d:
                best, best_d = p, d
        if best is not None:
            return best.box, best.head
        return None, None


class AutoFramer:
    """Converte a posição do sujeito em movimentos PTZ (físicos ou digitais)."""

    def __init__(self, settings: dict, ptz, pipeline) -> None:
        self.settings = settings  # settings["tracking"]
        self.ptz = ptz
        self.pipeline = pipeline
        self.calibration = {"pan_fw": 1100.0, "tilt_fh": 620.0, "zoom_max_mag": 3.0}
        self._last_cmd = 0.0
        self._last_zoom = 0.0
        self._lost_since = 0.0
        self._settle_until = 0.0  # PTZ físico: a imagem chega atrasada face ao motor
        self._last_seen_pos: dict | None = None
        self._recovering = False

    def magnification(self) -> float:
        if not self.ptz.has("zoom"):
            return 1.0
        z = self.ptz.axes["zoom"]
        frac = (z.value - z.min) / max(1e-6, z.span)
        return 1.0 + frac * (self.calibration["zoom_max_mag"] - 1.0)

    def target_point(self, mode: str, head: bool = False) -> tuple[float, float]:
        tx, ty = TARGETS.get(self.settings["target"], (0.5, 0.5))
        if mode == "face" or head:
            ty = 0.36  # espaço acima da cabeça (headroom) correto
        return tx, ty

    def reset(self) -> None:
        self._lost_since = 0.0
        self._last_seen_pos = None
        self._recovering = False

    def step(self, box, mode: str, head=None) -> None:
        now = time.monotonic()
        if box is None:
            self._on_lost(now)
            return
        self._lost_since = 0
        self._recovering = False
        if 0.1 < box[0] + box[2] / 2 < 0.9:   # visto dentro do quadro: lembra a posição da câmara
            self._last_seen_pos = {k: v for k, v in self.ptz.state().items() if k in ("pan", "tilt", "zoom")}
        # pessoa em plano fechado: enquadra pela cabeça (headroom), não pelo centro do corpo
        use_head = head is not None and mode == "person" and self.settings.get("shot") in CLOSE_SHOTS
        ref = head if use_head else box
        sens = float(self.settings["sensitivity"])
        interval = 0.22 - 0.12 * sens  # mais sensível = corrige mais vezes
        if now - self._last_cmd < interval or now < self._settle_until:
            return
        self._last_cmd = now
        x, y, w, h = ref
        cx, cy = x + w / 2, y + h / 2
        if self.ptz.is_physical:
            ox, oy = cx, cy   # o PTZ físico atua no frame raw
        else:
            ox, oy = self.pipeline.raw_to_out(cx, cy)
        tx, ty = self.target_point(mode, head=use_head)
        ex, ey = ox - tx, oy - ty
        dead = 0.07 - 0.04 * sens
        gain = 0.25 + 0.35 * sens
        mag = self.magnification()
        if self.ptz.is_physical:
            # Malha aberta com atraso: corrige uma fração do erro, no máximo 12% do ecrã por passo,
            # e espera o motor chegar + a imagem estabilizar antes de medir outra vez.
            gain = 0.35 + 0.25 * sens
            pfw = self.calibration["pan_fw"] / mag
            tfh = self.calibration["tilt_fh"] / mag
            ex = max(-0.12, min(0.12, ex)) if abs(ex) > dead else 0.0
            ey = max(-0.12, min(0.12, ey)) if abs(ey) > dead else 0.0
            dpan = ex * pfw * gain   # calibração com sinal (sentido incluído)
            dtilt = -ey * tfh * gain
            if dpan or dtilt:
                self._settle_until = now + 0.45 + max(abs(dpan) / 500.0, abs(dtilt) / 300.0)
        else:
            scale = self.pipeline.crop_rect()[2]
            free = max(1e-3, 0.5 - 0.5 * scale)
            dpan = ex * scale / free * 1000 * gain if abs(ex) > dead else 0.0
            dtilt = ey * scale / free * 1000 * gain if abs(ey) > dead else 0.0
        if dpan or dtilt:
            self.ptz.nudge(dpan=dpan, dtilt=dtilt)
        if self.settings.get("auto_zoom") and self.ptz.has("zoom") and now - self._last_zoom > 0.6:
            if use_head or mode == "face":
                want, have_h = SHOT_FACE.get(self.settings["shot"], 0.15), ref[3]
            else:
                want, have_h = SHOT_BODY.get(self.settings["shot"], 0.95), h
            have = have_h if self.ptz.is_physical else have_h / max(1e-3, self.pipeline.crop_rect()[3])
            ratio = want / max(1e-3, have)
            if abs(ratio - 1) > 0.18:
                self._last_zoom = now
                z = self.ptz.axes["zoom"]
                self.ptz.nudge(dzoom=z.span * 0.03 * (1 if ratio > 1 else -1))

    def _on_lost(self, now: float) -> None:
        if not self._lost_since:
            self._lost_since = now
            return
        lost_for = now - self._lost_since
        # 1) procurar: voltar à última posição de câmara onde o sujeito foi visto
        if (self.settings.get("recover", True) and not self._recovering and lost_for > 0.8
                and self._last_seen_pos and self.ptz.is_physical):
            cur = self.ptz.state()
            if any(abs(cur.get(k, 0) - v) > 15 for k, v in self._last_seen_pos.items()):
                self.ptz.move_to(self._last_seen_pos.get("pan"), self._last_seen_pos.get("tilt"),
                                 self._last_seen_pos.get("zoom"), duration=1.2)
            self._recovering = True
        # 2) desistir: posição inicial
        if self.settings.get("lost_home") and lost_for > 5:
            self.ptz.home()
            self._lost_since = 0
