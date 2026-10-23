#!/usr/bin/env python3
"""Tile screenshots into one labeled sheet: contact_sheet.py OUT.png IN1.png IN2.png ..."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

out, files = sys.argv[1], sys.argv[2:]
cols, w, h, lab = 3, 320, 172, 14
rows = (len(files) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (w + 8) + 8, rows * (h + lab + 8) + 8), (60, 60, 60))
dr = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    x, y = 8 + (i % cols) * (w + 8), 8 + (i // cols) * (h + lab + 8)
    dr.text((x, y), Path(f).stem, fill=(255, 255, 255))
    sheet.paste(Image.open(f), (x, y + lab))
sheet.save(out)
print(out)
