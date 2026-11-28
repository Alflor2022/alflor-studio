"""Formatos profissionais: metadados (EXIF/IPTC/XMP), perfil de cor sRGB, DNG linear,
curva Log para vídeo e exportação de LUT .cube.

RAW / DNG — a PTZ USB só envia MJPEG/H.264 de 8 bits (não há sensor em bruto). O DNG gerado é
«linear»: os frames são convertidos para luz linear e EMPILHADOS (média de N frames), o que dá
bits reais a mais (ruído √N menor) guardados em 16 bits, sem looks nem compressão adicional.
O Lightroom/Camera Raw trata-o como RAW (balanço de brancos e exposição em espaço linear).
Com uma Canon (EDSDK) o RAW será o do sensor.

LOG — curva Sony S-Log3 aplicada ao sinal linearizado e gravada a 10 bits (ProRes 422 HQ), com
LUT .cube de regresso a Rec.709. Num editor: «S-Log3» como curva de entrada e Rec.709 como gama
de cor, ou aplicar a LUT exportada.
"""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from . import APP_NAME, __version__

_ICC: bytes | None = None
XYZ_TO_SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560],
                        [0.0556434, -0.2040259, 1.0572252]])


def srgb_icc() -> bytes:
    """Perfil ICC sRGB (embutido nas fotos para cores corretas no Lightroom/Photoshop/web)."""
    global _ICC
    if _ICC is None:
        from PIL import ImageCms
        _ICC = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _ICC


# --- metadados -------------------------------------------------------------------------------
def build_xmp(meta: dict) -> bytes:
    """Pacote XMP (Adobe) com autor, direitos, cliente, palavras-chave, descrição e classificação."""
    m = meta or {}
    kw = [k.strip() for k in str(m.get("keywords", "")).replace(";", ",").split(",") if k.strip()]
    if m.get("client"):
        kw.append(str(m["client"]))
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    bag = "".join(f"<rdf:li>{escape(k)}</rdf:li>" for k in kw)
    alt = lambda v: f'<rdf:Alt><rdf:li xml:lang="x-default">{escape(str(v))}</rdf:li></rdf:Alt>'
    body = f"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"
    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
    xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"
    xmp:CreatorTool="{escape(APP_NAME)} {__version__}" xmp:CreateDate="{now}" xmp:MetadataDate="{now}"
    xmp:Rating="{int(m.get('rating', 0))}" photoshop:Credit="{escape(str(m.get('author', '')))}"
    photoshop:Source="{escape(str(m.get('client', '')))}">
   <dc:creator><rdf:Seq><rdf:li>{escape(str(m.get('author', '')))}</rdf:li></rdf:Seq></dc:creator>
   <dc:rights>{alt(m.get('copyright', ''))}</dc:rights>
   <dc:description>{alt(m.get('description', ''))}</dc:description>
   <dc:subject><rdf:Bag>{bag}</rdf:Bag></dc:subject>
   <xmpRights:Marked>{"True" if m.get('copyright') else "False"}</xmpRights:Marked>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n' + body + '\n<?xpacket end="w"?>').encode("utf-8")


def _ascii(s) -> str:
    """EXIF/TIFF só aceitam ASCII: «Fotógrafo» → «Fotografo», «©» → «(c)». O XMP guarda o texto completo."""
    import unicodedata
    s = str(s).replace("©", "(c)").replace("®", "(r)")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _exif(meta: dict):
    from PIL import Image
    e = Image.Exif()
    m = meta or {}
    now = dt.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    e[0x010F] = _ascii(m.get("make", "Alflor Studio"))       # Make
    e[0x0110] = _ascii(m.get("model", "Camera"))              # Model
    e[0x0131] = f"{APP_NAME} {__version__}"                   # Software
    e[0x0132] = now                                           # DateTime
    if m.get("author"):
        e[0x013B] = _ascii(m["author"])                       # Artist
        e[0x9C9D] = (str(m["author"]) + "\0").encode("utf-16-le")      # XPAuthor (Unicode, Explorador do Windows)
    if m.get("copyright"):
        e[0x8298] = _ascii(m["copyright"])                    # Copyright
    if m.get("description"):
        e[0x010E] = _ascii(m["description"])                  # ImageDescription
        e[0x9C9C] = (str(m["description"]) + "\0").encode("utf-16-le")  # XPComment
    if m.get("keywords"):
        e[0x9C9E] = (str(m["keywords"]) + "\0").encode("utf-16-le")     # XPKeywords
    exif_ifd = e.get_ifd(0x8769)
    exif_ifd[0x9003] = now                                    # DateTimeOriginal
    exif_ifd[0x9004] = now                                    # DateTimeDigitized
    return e


def _tiff_tags(meta: dict) -> list:
    m = meta or {}
    now = dt.datetime.now().strftime("%Y:%m:%d %H:%M:%S")
    # (o Software, tag 305, é escrito pelo tifffile através do parâmetro `software`)
    tags = [(306, "s", 0, now, True),
            (271, "s", 0, _ascii(m.get("make", "Alflor Studio")), True), (272, "s", 0, _ascii(m.get("model", "Camera")), True),
            (700, "B", 0, build_xmp(m), True)]
    if m.get("author"):
        tags.append((315, "s", 0, _ascii(m["author"]), True))
    if m.get("copyright"):
        tags.append((33432, "s", 0, _ascii(m["copyright"]), True))
    return tags   # a descrição (tag 270) vai pelo parâmetro `description` do tifffile


def _tiff_desc(meta: dict) -> dict:
    d = _ascii((meta or {}).get("description", ""))
    return {"description": d or None, "metadata": None}   # metadata=None: sem o JSON interno do tifffile


# --- gravação de fotos ----------------------------------------------------------------------
def save_photo(img_bgr: np.ndarray, path: Path, fmt: str, alpha: np.ndarray | None = None, meta: dict | None = None) -> Path:
    """JPEG/PNG/TIFF com perfil sRGB e metadados (EXIF + XMP) — prontos para Lightroom/Photoshop."""
    import cv2
    from PIL import Image
    path = Path(path)
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    if alpha is not None and fmt in ("png", "tiff"):
        if alpha.shape[:2] != rgb.shape[:2]:
            alpha = cv2.resize(alpha, (rgb.shape[1], rgb.shape[0]))
        rgb = np.dstack([rgb, alpha])
    xmp = build_xmp(meta or {})
    if fmt == "tiff":
        import tifffile
        tifffile.imwrite(str(path), rgb, photometric="rgb", compression="zlib", software=f"{APP_NAME} {__version__}",
                         extratags=_tiff_tags(meta or {}) + [(34675, "B", 0, srgb_icc(), True)], **_tiff_desc(meta))
        return path
    im = Image.fromarray(rgb)
    buf = io.BytesIO()
    if fmt == "png":
        from PIL.PngImagePlugin import PngInfo
        info = PngInfo()
        info.add_itxt("XML:com.adobe.xmp", xmp.decode("utf-8"))
        im.save(buf, "PNG", compress_level=3, icc_profile=srgb_icc(), exif=_exif(meta or {}), pnginfo=info)
    else:
        im.save(buf, "JPEG", quality=100, subsampling=0, optimize=True, icc_profile=srgb_icc(),
                exif=_exif(meta or {}), xmp=xmp)
    path.write_bytes(buf.getvalue())   # write_bytes: funciona com acentos no caminho
    return path


def srgb_to_linear(img_u8: np.ndarray) -> np.ndarray:
    x = img_u8.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_stack(frames_bgr: list[np.ndarray]) -> np.ndarray:
    """Média de N frames em luz LINEAR (o ruído cai √N) → RGB float32 0..1."""
    acc = np.zeros(frames_bgr[0].shape, np.float32)
    for f in frames_bgr:
        acc += srgb_to_linear(f)
    acc /= len(frames_bgr)
    return acc[..., ::-1].copy()   # BGR → RGB


def save_dng(linear_rgb: np.ndarray, path: Path, meta: dict | None = None) -> Path:
    """DNG «LinearRaw» de 16 bits (RGB linear, primárias sRGB, D65), lido pelo Lightroom como RAW."""
    import tifffile
    data = np.clip(linear_rgb * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    cm = []
    for v in XYZ_TO_SRGB.ravel():                          # ColorMatrix1: XYZ → «câmara» (sRGB linear)
        cm += [int(round(v * 10000)), 10000]
    tags = _tiff_tags(meta or {}) + [
        (254, "I", 1, 0, True),                            # NewSubFileType: imagem principal
        (50706, "B", 4, (1, 4, 0, 0), True),               # DNGVersion 1.4
        (50707, "B", 4, (1, 1, 0, 0), True),               # DNGBackwardVersion
        (50708, "s", 0, _ascii(f"Alflor Studio {(meta or {}).get('model', 'Camera')}"), True),  # UniqueCameraModel
        (50721, 10, 9, tuple(cm), True),                   # SRATIONAL = código 10                  # ColorMatrix1 (SRATIONAL)
        (50778, "H", 1, 21, True),                         # CalibrationIlluminant1 = D65
        (50728, 5, 3, (1, 1, 1, 1, 1, 1), True),   # RATIONAL = código 5        # AsShotNeutral = branco já equilibrado
        (50714, "I", 3, (0, 0, 0), True),                  # BlackLevel
        (50717, "I", 3, (65535, 65535, 65535), True),      # WhiteLevel
        (50730, 10, 1, (0, 1), True),                     # BaselineExposure = 0
    ]
    tifffile.imwrite(str(path), data, photometric=34892, planarconfig="contig", extratags=tags,
                     software=f"{APP_NAME} {__version__}", **_tiff_desc(meta))
    return path


# --- curva Log e LUTs -----------------------------------------------------------------------
def slog3_encode(lin: np.ndarray) -> np.ndarray:
    """Sony S-Log3 (reflectância linear → valor de código normalizado 0..1)."""
    lin = np.asarray(lin, np.float64)
    return np.where(lin >= 0.01125,
                    (420.0 + np.log10((np.maximum(lin, 1e-9) + 0.01) / (0.18 + 0.01)) * 261.5) / 1023.0,
                    (lin * (171.2102946929 - 95.0) / 0.01125 + 95.0) / 1023.0)


def slog3_decode(code: np.ndarray) -> np.ndarray:
    code = np.asarray(code, np.float64) * 1023.0
    return np.where(code >= 171.2102946929,
                    10 ** ((code - 420.0) / 261.5) * (0.18 + 0.01) - 0.01,
                    (code - 95.0) * 0.01125 / (171.2102946929 - 95.0))


def log_table_u16() -> np.ndarray:
    """Tabela 256 → 16 bits: valor sRGB de 8 bits → S-Log3 (sem perder nenhum dos 256 níveis)."""
    x = np.arange(256, dtype=np.float64)
    lin = srgb_to_linear(x.astype(np.uint8)).astype(np.float64)
    return np.clip(slog3_encode(lin) * 65535 + 0.5, 0, 65535).astype(np.uint16)


def write_cube(path: Path, size: int, fn, title: str) -> Path:
    """Escreve uma LUT 3D .cube (Premiere, DaVinci, OBS, Photoshop, Final Cut). `fn` mapeia RGB (N,3)→(N,3)."""
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")           # ordem .cube: R varia mais depressa
    rgb = np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)
    out = np.clip(fn(rgb), 0, 1)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")
    return Path(path)


def log_to_rec709_cube(path: Path, size: int = 33) -> Path:
    """LUT de monitorização/edição: S-Log3 (Alflor) → Rec.709/sRGB, para usar no Premiere/DaVinci."""
    def fn(rgb):
        lin = np.clip(slog3_decode(rgb), 0, 1)
        return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return write_cube(path, size, fn, "Alflor Studio S-Log3 para Rec709")


def look_cube(path: Path, apply_bgr_u8, size: int = 33, title: str = "Alflor Studio look") -> Path:
    """Exporta o look atual (ajuste inteligente + look de cor) como LUT .cube para outros programas."""
    def fn(rgb):
        bgr = (rgb[:, ::-1] * 255 + 0.5).astype(np.uint8).reshape(1, -1, 3)
        out = apply_bgr_u8(bgr).reshape(-1, 3)[:, ::-1].astype(np.float64) / 255.0
        return out
    return write_cube(path, size, fn, title)
