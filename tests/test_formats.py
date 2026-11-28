"""Valida formatos profissionais: JPEG/PNG/TIFF com ICC e metadados, DNG lido pelo LibRaw
(o motor RAW usado por muitos programas), curva S-Log3 e LUTs .cube.

Uso: python tests\test_formats.py foto.png
"""
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
import rawpy
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ptz_studio import photo_formats as pf  # noqa: E402

src = cv2.imread(sys.argv[1])
d = Path(tempfile.mkdtemp())
fails = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  FALHOU ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


meta = {"author": "Fotógrafo Teste", "copyright": "© 2026 Alflor", "client": "Cliente X",
        "keywords": "estúdio, retrato", "description": "Sessão de teste", "rating": 3, "model": "UVC Camera"}
for fmt in ("jpg", "png", "tiff"):
    p = pf.save_photo(src, d / f"t.{fmt}", fmt, meta=meta)
    if fmt == "tiff":
        import tifffile
        tg = tifffile.TiffFile(p).pages[0].tags
        icc, xmp = 34675 in tg, bytes(tg[700].value)
        artist = tg[315].value if 315 in tg else ""
    else:
        im = Image.open(p)
        icc = bool(im.info.get("icc_profile"))
        raw_xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp") or b""
        xmp = raw_xmp if isinstance(raw_xmp, bytes) else str(raw_xmp).encode()
        artist = im.getexif().get(0x013B, "")
    pix = cv2.cvtColor(np.array(Image.open(p).convert("RGB")), cv2.COLOR_RGB2BGR)
    diff = float(np.abs(pix.astype(int) - src.astype(int)).mean())
    check(f"{fmt}: perfil sRGB, XMP com acentos, EXIF autor", icc and "Fotógrafo".encode() in xmp and b"Cliente X" in xmp and artist == "Fotografo Teste",
          f"ICC {icc} · autor EXIF «{artist}» · {p.stat().st_size // 1024} KB")
    check(f"{fmt}: imagem fiel", diff < 1.0, f"diferença média {diff:.2f}")

lin = pf.linear_stack([src, src, src])
dp = pf.save_dng(lin, d / "t.dng", meta)
with rawpy.imread(str(dp)) as r:
    dev = r.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8, gamma=(2.4, 12.92), output_color=rawpy.ColorSpace.sRGB)
dev = cv2.cvtColor(dev, cv2.COLOR_RGB2BGR)
dd = float(np.abs(dev.astype(int) - src.astype(int)).mean())
check("DNG lido e revelado pelo LibRaw com as cores certas", dd < 3.0, f"{dp.stat().st_size // 1024} KB · diferença média {dd:.2f}/255")
cv2.imwrite(str(d / "dng_revelado.png"), dev)

noisy = [np.clip(src.astype(np.int16) + np.random.default_rng(i).normal(0, 6, src.shape), 0, 255).astype(np.uint8) for i in range(8)]
one = pf.linear_stack(noisy[:1]); eight = pf.linear_stack(noisy)
ref = pf.srgb_to_linear(src)[..., ::-1]
n1, n8 = float(np.std(one - ref)), float(np.std(eight - ref))
check("empilhar 8 frames reduz o ruído (≈ √8)", n8 < n1 / 2.2, f"ruído {n1:.4f} → {n8:.4f} (÷{n1 / n8:.1f})")

t16 = pf.log_table_u16().astype(np.float64) / 65535
back = np.clip(pf.slog3_decode(t16), 0, 1)
srgb = np.where(back <= 0.0031308, back * 12.92, 1.055 * back ** (1 / 2.4) - 0.055) * 255
check("S-Log3: ida e volta sem perdas (8 bits)", np.abs(srgb - np.arange(256)).max() < 0.5, f"erro máx {np.abs(srgb - np.arange(256)).max():.3f}")
check("S-Log3: 256 níveis distintos em 10 bits (sem banding)", len(np.unique((t16 * 1023).round())) >= 200, f"{len(np.unique((t16 * 1023).round()))} níveis")
c = pf.log_to_rec709_cube(d / "log.cube")
lines = c.read_text().splitlines()
check("LUT S-Log3→Rec709 .cube válida (33³)", lines[1] == "LUT_3D_SIZE 33" and len(lines) == 4 + 33 ** 3)
print("RESULTADO:", "TUDO OK" if not fails else f"{len(fails)} FALHA(S): {fails}")
sys.exit(1 if fails else 0)
