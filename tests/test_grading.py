"""Testa o ajuste inteligente: corrige imagens degradadas (auto) e iguala a uma referência.

Uso: python tests\\test_grading.py foto_real.png [pasta_saida]
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio.grading import DEFAULTS, Grader, LUMA_BGR  # noqa: E402

src = cv2.imread(sys.argv[1])
out = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
fails = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


def lab_stats(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    return lab.mean(0), lab.std(0)


def lab_dist(a, b):
    (ma, sa), (mb, sb) = lab_stats(a), lab_stats(b)
    return float(np.linalg.norm(ma - mb) + np.linalg.norm(sa - sb))


def neutral_cast(img):
    """Desvio de cor nas zonas neutras/claras (paredes brancas): máx − mín dos canais médios."""
    px = img.reshape(-1, 3).astype(np.float32) / 255
    y = px @ LUMA_BGR
    sel = (y > 0.55) & ((px.max(1) - px.min(1)) < 0.25)
    m = px[sel].mean(0) if sel.mean() > 0.01 else px.mean(0)
    return float(m.max() - m.min())


print("Automático (luz/cor do ambiente)")
g0 = Grader(dict(DEFAULTS, mode="auto", strength=1.0, continuous=False))
g0.analyze(src)
auto_src = g0.apply(src)
clip = lambda im: float(((im == 0) | (im == 255)).any(2).mean())
check("imagem já boa quase não muda", lab_dist(auto_src, src) < 12 and clip(auto_src) < 0.02,
      f"distância {lab_dist(auto_src, src):.1f} · {clip(auto_src) * 100:.1f}% no limite · {g0.report}")
dark = cv2.convertScaleAbs(src, alpha=0.45)                                   # pouca luz
warm = cv2.merge([cv2.convertScaleAbs(c, alpha=a) for c, a in zip(cv2.split(src), (0.78, 0.97, 1.15))])  # luz amarela
flat = cv2.addWeighted(src, 0.55, np.full_like(src, 128), 0.45, 0)          # sem contraste
for name, img in (("escura", dark), ("amarelada", warm), ("sem contraste", flat)):
    g = Grader(dict(DEFAULTS, mode="auto", strength=1.0, continuous=False))
    g.analyze(img)
    fixed = g.apply(img)
    # objetivo: o mesmo resultado qualquer que seja a luz → auto(degradada) perto de auto(original)
    before, after = lab_dist(img, auto_src), lab_dist(fixed, auto_src)
    check(f"{name}: corrigida fica mais perto da boa", after < before * 0.75, f"distância {before:.1f} → {after:.1f} · {g.report}")
    if name == "amarelada":
        check("dominante amarela reduzida", neutral_cast(fixed) < neutral_cast(img), f"{neutral_cast(img):.3f} → {neutral_cast(fixed):.3f}")
    cv2.imwrite(str(out / f"grade_auto_{name.replace(' ', '_')}.jpg"), np.hstack([cv2.resize(x, (480, 270)) for x in (img, fixed)]))

print("Igualar a uma referência")
# referência = o mesmo plano com um "look" editado (quente, contrastado, mais saturado)
look = cv2.merge([cv2.convertScaleAbs(c, alpha=a, beta=bb) for c, a, bb in zip(cv2.split(src), (0.85, 1.0, 1.12), (-8, 0, 10))])
look = cv2.LUT(look, np.clip((np.linspace(0, 1, 256) ** 1.15) * 255 * 1.05, 0, 255).astype(np.uint8))
g = Grader(dict(DEFAULTS, mode="reference", strength=1.0, continuous=False))
g.set_reference(look, "look_editado.jpg")
g.analyze(src)
matched = g.apply(src)
before, after = lab_dist(src, look), lab_dist(matched, look)
check("mesmo plano: aproxima-se muito da referência", after < before * 0.35, f"distância {before:.1f} → {after:.1f}")
# conteúdo diferente: referência = outra zona do estúdio com o mesmo look → não pode estragar a imagem
other = look[:, : look.shape[1] // 2]
g2 = Grader(dict(DEFAULTS, mode="reference", strength=1.0, continuous=False))
g2.set_reference(other, "outra_cena.jpg")
g2.analyze(src)
m2 = g2.apply(src)
clip = float(((m2 == 0) | (m2 == 255)).any(2).mean())
check("conteúdo diferente: sem cores queimadas", clip < 0.03, f"{clip * 100:.2f}% píxeis no limite")
cv2.imwrite(str(out / "grade_referencia.jpg"), np.hstack([cv2.resize(x, (480, 270)) for x in (src, look, matched, m2)]))

print("Desempenho e suavidade")
big = cv2.resize(src, (1920, 1080))
t = []
for _ in range(20):
    t0 = time.perf_counter(); g.apply(big); t.append((time.perf_counter() - t0) * 1000)
check(f"aplicar a 1080p ({np.median(t):.1f} ms)", np.median(t) < 20)
t0 = time.perf_counter(); g.analyze(big); ta = (time.perf_counter() - t0) * 1000
check(f"análise ({ta:.0f} ms, corre a cada 2 s)", ta < 80)
gc = Grader(dict(DEFAULTS, mode="auto", strength=1.0, continuous=True))
gc.analyze(src)
m_before = gc.M.copy()
gc.analyze(dark)   # luz baixa de repente
step = float(np.abs(gc.M - m_before).max())
gc2 = Grader(dict(DEFAULTS, mode="auto", strength=1.0, continuous=False)); gc2.analyze(dark)
full = float(np.abs(gc2.M - m_before).max()) + 1e-6
check("modo contínuo muda devagar (sem cintilação)", step <= full * 0.35 + 1e-4, f"passo {step:.3f} vs mudança total {full:.3f}")
print("RESULTADO:", "TUDO OK" if not fails else f"{len(fails)} FALHA(S): {fails}")
sys.exit(1 if fails else 0)
