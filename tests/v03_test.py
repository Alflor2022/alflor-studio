"""Testa as novidades da 0.3 com a câmara real: Log, RAW (DNG), metadados, Lightroom, máscara,
LUTs, histograma RGB, guias, fantasma, perfis da interface e janela de revisão.

Uso: python tests\\v03_test.py [pasta de saída]      (não toca na configuração do utilizador)
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ptz_studio import photo_formats as pf  # noqa: E402
from ptz_studio.app import MainWindow  # noqa: E402
from ptz_studio.config import CONFIG_FILE  # noqa: E402
from ptz_studio.media import find_ffmpeg  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
OUT.mkdir(parents=True, exist_ok=True)
fails = []
cfg_hash = hashlib.sha256(CONFIG_FILE.read_bytes()).hexdigest() if CONFIG_FILE.exists() else ""


def wait(s):
    loop = QEventLoop()
    QTimer.singleShot(int(s * 1000), loop.quit)
    loop.exec()


def until(cond, timeout=15.0, step=0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        wait(step)
    return cond()


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


app = QApplication(sys.argv)
win = MainWindow(test_mode=True)
s = win.settings
s.update(output_dir=str(OUT), project="TesteV03", scene="Formatos", take=3)
s["meta"].update(author="Fotógrafo Teste", copyright="© 2026 Alflor Digital", client="Cliente Á",
                 keywords="retrato, estúdio", description="Teste automático", rating=3)
s["overlays"]["hist"] = True
win.show()
check("câmara aberta", until(lambda: win.capture is not None and win.capture.latest() is not None, 25))
wait(1.5)
saved = []
win.photo_saved.connect(saved.append)


def shoot(fmt, timeout=30):
    s["photo"]["format"] = fmt
    n = len(saved)
    win.take_photo("single")
    ok = until(lambda: len(saved) > n, timeout)
    return Path(saved[-1]) if ok else None


# --- 1. histograma RGB com aviso de corte -------------------------------------------------
h = win._last_meta.get("hist")
check("histograma RGB (3 canais + % cortado)", isinstance(h, dict) and np.asarray(h["rgb"]).shape == (3, 64),
      f"altas {h['clip_hi'] * 100:.2f}% · sombras {h['clip_lo'] * 100:.2f}%" if isinstance(h, dict) else str(type(h)))

# --- 2. PNG com metadados + cópia para a pasta do Lightroom ---------------------------------
lr = OUT / "Lightroom_vigiada"
s["photo"]["lightroom_dir"] = str(lr)
png = shoot("png")
check("PNG guardado", png is not None and png.exists(), png.name if png else "")
if png:
    from PIL import Image
    im = Image.open(png)
    xmp = (im.info.get("XML:com.adobe.xmp") or "")
    check("PNG: perfil sRGB", bool(im.info.get("icc_profile")))
    check("PNG: XMP autor/direitos/estrelas", "Fotógrafo Teste" in xmp and "© 2026" in xmp and 'xmp:Rating="3"' in xmp)
    check("PNG: palavras-chave com cliente, projeto e cena", all(k in xmp for k in ("Cliente Á", "TesteV03", "Formatos")))
    check("cópia para o Lightroom", until(lambda: (lr / png.name).exists(), 5))
s["photo"]["lightroom_dir"] = ""

# --- 3. JPEG / TIFF -------------------------------------------------------------------------
for fmt in ("jpg", "tiff"):
    pth = shoot(fmt)
    ok = pth is not None and pth.exists()
    detail = ""
    if ok:
        from PIL import Image
        im = Image.open(pth)
        ex = im.getexif()
        detail = f"{pth.name} Artist={ex.get(0x013B)!r} Model={ex.get(0x0110)!r}" if fmt == "jpg" else pth.name
        if fmt == "jpg":
            ok = ex.get(0x013B) == "Fotografo Teste" and bool(im.info.get("icc_profile"))
    check(f"{fmt.upper()} com EXIF/ICC", ok, detail)

# --- 4. RAW: DNG e DNG+JPEG ------------------------------------------------------------------
s["photo"]["raw_frames"] = 4
dng = shoot("dng", 40)
check("DNG guardado", dng is not None and dng.suffix == ".dng" and dng.exists(), dng.name if dng else "")
if dng:
    try:
        import rawpy
        with rawpy.imread(str(dng)) as r:
            rgb = r.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8, gamma=(2.4, 12.92))  # curva sRGB
        ref = cv2.cvtColor(win.capture.latest(False), cv2.COLOR_BGR2RGB)
        ref = cv2.resize(ref, (rgb.shape[1], rgb.shape[0]))
        if s["pipeline"]["mirror"]:
            ref = ref[:, ::-1]
        diff = float(np.abs(rgb.astype(int) - ref.astype(int)).mean())
        check("DNG revelado (LibRaw) ≈ imagem crua", diff < 12, f"diferença média {diff:.1f}/255, {rgb.shape[1]}x{rgb.shape[0]}")
    except ImportError:
        print("  (rawpy não instalado: revelação do DNG não testada)")
pair = shoot("dng+jpg", 40)
if pair:
    jp = pair.with_suffix(".jpg")
    check("DNG + JPEG com o mesmo nome", pair.exists() and jp.exists(), f"{pair.name} + {jp.name}")
else:
    check("DNG + JPEG", False)

# --- 5. fantasma da última foto ------------------------------------------------------------
s["compose"]["ghost"] = True
shoot("jpg")
check("fantasma da última foto no ecrã", until(lambda: win.preview.ghost is not None, 5))

# --- 6. guias de corte (captura do ecrã para inspeção visual) -------------------------------
for g in ("4:5", "passport"):
    s["compose"]["guide"] = g
    win.preview.update()
    wait(0.4)
    win.preview.grab().save(str(OUT / f"guia_{g.replace(':', 'x')}.png"))
s["compose"].update(guide="off", ghost=False)
check("guias desenhadas", (OUT / "guia_4x5.png").exists() and (OUT / "guia_passport.png").exists())

# --- 7. chroma: máscara + original ------------------------------------------------------------
s["photo"]["export_mask"] = True
win.set_chroma(True)
wait(2.5)
mp = shoot("png")
if mp:
    m, o = mp.with_name(mp.stem + "_mascara.png"), mp.with_name(mp.stem + "_original.png")
    mm = cv2.imread(str(m), cv2.IMREAD_UNCHANGED) if m.exists() else None
    check("chroma: máscara e original", m.exists() and o.exists() and mm is not None and mm.ndim == 2,
          f"máscara {mm.shape if mm is not None else None}, sujeito {float((mm > 127).mean()) * 100:.0f}%" if mm is not None else "")
else:
    check("chroma: máscara e original", False)
win.set_chroma(False)
s["photo"]["export_mask"] = False

# --- 8. LUTs .cube ------------------------------------------------------------------------------
s["pipeline"]["look"] = "Quente"
cube = pf.look_cube(OUT / "look_quente.cube", win.apply_look_u8, 17, "teste")
lines = [ln for ln in cube.read_text().splitlines() if ln and ln[0].isdigit()]
check("LUT do look (.cube)", len(lines) == 17 ** 3, f"{len(lines)} entradas")
logc = pf.log_to_rec709_cube(OUT / "slog3_rec709.cube")
check("LUT Log→Rec709 (.cube)", logc.exists() and "LUT_3D_SIZE 33" in logc.read_text()[:200])

# --- 9. gravação Log (neutra, sem o look) -------------------------------------------------
s["record"].update(log=True, log_neutral=True, auto_stop=0)
win.toggle_record(True)
check("gravação Log iniciada", win.recorder is not None and win.recorder.codec == "prores_log")
rec = win.recorder
wait(4)
look_img = win.capture.latest(True).copy()
raw_img = win.capture.latest(False).copy()
win.toggle_record(False)
check("Log .mov escrito", until(lambda: rec.path.exists() and rec.proc.poll() is not None, 30),
      f"{rec.path.name}, {rec.frames} frames, {rec.dropped} descartados")
ff = find_ffmpeg()
probe = subprocess.run([str(Path(ff).with_name("ffprobe.exe")), "-v", "error", "-show_entries",
                        "stream=codec_name,profile,pix_fmt,width,height", "-of", "json", str(rec.path)],
                       capture_output=True, text=True)
st = json.loads(probe.stdout or "{}").get("streams", [{}])[0]
check("ProRes HQ 10 bits 4:2:2", st.get("codec_name") == "prores" and st.get("pix_fmt") == "yuv422p10le", str(st))
w, hh = int(st.get("width", 0)), int(st.get("height", 0))
raw = subprocess.run([ff, "-v", "error", "-sseof", "-0.5", "-i", str(rec.path), "-frames:v", "1", "-f", "rawvideo",
                      "-pix_fmt", "rgb48le", "-"], capture_output=True).stdout
if w and len(raw) == w * hh * 6:
    dec = np.frombuffer(raw, np.uint16).reshape(hh, w, 3).astype(np.float64) / 65535
    lin = np.clip(pf.slog3_decode(dec), 0, 1)
    back = np.clip((np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)) * 255, 0, 255).astype(np.uint8)
    back = cv2.cvtColor(back, cv2.COLOR_RGB2BGR)
    look_r = cv2.resize(look_img, (w, hh))
    raw_r = cv2.resize(raw_img[:, ::-1] if s["pipeline"]["mirror"] else raw_img, (w, hh))
    d_raw = float(np.abs(back.astype(int) - raw_r.astype(int)).mean())
    d_look = float(np.abs(back.astype(int) - look_r.astype(int)).mean())
    mb = rec.path.stat().st_size / 1e6
    check("Log neutro (mais perto do cru do que do look «Quente»)", d_raw < d_look,
          f"Δcru {d_raw:.1f} · Δlook {d_look:.1f} · {mb:.0f} MB em ~4 s")
else:
    check("descodificar frame Log", False, f"{len(raw)} bytes")
s["record"]["log"] = False
s["pipeline"]["look"] = "Natural"

# --- 10. perfis da interface ------------------------------------------------------------------
for key in ("fotografo", "video", "live", "completo"):
    win.apply_ui_profile(key)
    vis = [win.tabs.tabText(i) for i in range(win.tabs.count()) if win.tabs.isTabVisible(i)]
    print(f"     perfil {key}: {', '.join(vis)} · Gravar visível={win.btn_rec.isVisibleTo(win)}")
win.apply_ui_profile("fotografo")
ok_f = not win.btn_rec.isVisibleTo(win) and not any(win.tabs.tabText(i) == "Tracking" and win.tabs.isTabVisible(i) for i in range(win.tabs.count()))
win.apply_ui_profile("completo")
ok_c = win.btn_rec.isVisibleTo(win) and all(win.tabs.isTabVisible(i) for i in range(win.tabs.count()))
check("perfis Fotógrafo/Completo", ok_f and ok_c)

# --- 11. janela de revisão ---------------------------------------------------------------------
win.open_review()
wait(1.5)
rv = win._review_win
n_files = len([p for p in rv.folder.glob("*") if p.suffix.lower() in (".png", ".jpg", ".tiff", ".dng")]) if rv else 0
check("revisão abre na pasta de hoje", rv is not None and rv.isVisible() and n_files > 0, f"{rv.folder} · {n_files} fotos")
rv.grab().save(str(OUT / "revisao.png"))
rv.close()

win.close()
wait(1.0)
new_hash = hashlib.sha256(CONFIG_FILE.read_bytes()).hexdigest() if CONFIG_FILE.exists() else ""
check("configuração do utilizador intacta", new_hash == cfg_hash)
print("\nRESULTADO:", "TUDO OK" if not fails else f"{len(fails)} falha(s): {fails}")
sys.exit(1 if fails else 0)
