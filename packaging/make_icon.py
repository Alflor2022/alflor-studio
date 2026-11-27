"""Gera o ícone da aplicação (lente PTZ estilizada nas cores Alflor). Uso: python make_icon.py saida.ico"""
import sys

from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else "alflor_studio.ico"
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(14, 17, 22, 255))
d.ellipse((40, 40, S - 40, S - 40), fill=(29, 35, 44, 255), outline=(255, 138, 61, 255), width=14)  # anel laranja
d.ellipse((86, 86, S - 86, S - 86), fill=(9, 11, 15, 255), outline=(255, 178, 122, 255), width=6)    # lente
d.ellipse((104, 100, 128, 124), fill=(255, 255, 255, 200))                                            # reflexo
d.pieslice((40, 40, S - 40, S - 40), 300, 330, fill=(255, 138, 61, 255))                            # "tally"
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
