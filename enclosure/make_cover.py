"""MakerWorld cover (4:3): the stand rendered from the exported meshes, with a real screen capture of the
display warped onto its screen, on a warm backdrop with a soft shadow.

  uv run python make_cover.py [screen.png]    default makerworld/screen.png (320x172, from the bridge's /shot)

Three OpenSCAD passes with one fixed camera (render_assembly.scad `paint`): the picture, a mask of where
the LCD's active area is visible, and four coloured dots at its corners. Writes makerworld/images/0_cover.png.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
MW = HERE / "makerworld"
CAMERA = "0,15,13,70,0,-28,135"     # tx,ty,tz,rx,ry,rz,dist: three-quarter front, slightly from above
RENDER = (3200, 2400)               # rendered at 2x and scaled down, which smooths OpenSCAD's hard edges
SIZE = (1600, 1200)
FILL = 0.60                         # stand width as a share of the cover width
BG_TOP, BG_BOTTOM = (246, 242, 234), (226, 218, 203)


def render(paint: str, out: Path) -> np.ndarray:
    subprocess.run(["openscad", f"--imgsize={RENDER[0]},{RENDER[1]}", "--colorscheme=Tomorrow", "--projection=p",
                    f"--camera={CAMERA}", "-D", "show_board=false", "-D", f'paint="{paint}"', "-o", str(out),
                    str(HERE / "render_assembly.scad")], check=True, capture_output=True, cwd=HERE)
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def corners(img: np.ndarray) -> np.ndarray:
    """Centroids of the red, green, blue and yellow dots, returned as top-left, top-right, bottom-right,
    bottom-left in the image (the stand is upright, so the screen's top edge is the higher pair)."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    tests = [(r > 90) & (g < 60) & (b < 60), (g > 70) & (r < 60) & (b < 60),
             (b > 70) & (r < 60) & (g < 60), (r > 60) & (g > 60) & (b < 40)]
    pts = []
    for t in tests:
        ys, xs = np.nonzero(t)
        if len(xs) == 0:
            raise SystemExit("corner dot not found; is the active area in view?")
        pts.append((xs.mean(), ys.mean()))
    pts = sorted(pts, key=lambda p: p[1])
    top, bottom = sorted(pts[:2]), sorted(pts[2:])
    return np.array([top[0], top[1], bottom[1], bottom[0]])


def perspective(src_size: tuple[int, int], dst: np.ndarray) -> list[float]:
    """PIL PERSPECTIVE coefficients mapping output pixels (dst quad) back to the source image."""
    w, h = src_size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    a, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        a += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        rhs += [u, v]
    return list(np.linalg.solve(np.array(a, float), np.array(rhs, float)))


def main() -> int:
    shot = Path(sys.argv[1]) if len(sys.argv) > 1 else MW / "screen.png"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        pic, mask, dots = (render(p, t / f"{p}.png") for p in ("normal", "mask", "corners"))
    bg = pic[0, 0]
    obj = np.abs(pic - bg).max(axis=2) > 3
    visible = (mask[..., 1] > 50) & (mask[..., 0] < 40) & (mask[..., 2] < 40)

    # Screen: the capture warped onto the active area, cut to what the bezel doesn't hide.
    screen = Image.open(shot).convert("RGB")
    screen = screen.resize((screen.width * 8, screen.height * 8), Image.NEAREST)   # crisp LCD pixels
    quad = corners(dots)
    warped = np.asarray(screen.transform(RENDER, Image.PERSPECTIVE, perspective(screen.size, quad),
                                         Image.BICUBIC)).astype(float)
    # Faint glass sheen, brighter towards the top of the screen.
    ys = np.linspace(0, 1, RENDER[1])[:, None, None]
    top_y, bot_y = quad[:, 1].min() / RENDER[1], quad[:, 1].max() / RENDER[1]
    sheen = np.clip(1 - (ys - top_y) / max(bot_y - top_y, 1e-6), 0, 1) * 18
    out = pic.astype(float)
    out[visible] = np.clip(warped[visible] + sheen.repeat(RENDER[0], 1)[visible], 0, 255)

    # Cut the stand out, scale it into the cover.
    ys_, xs_ = np.nonzero(obj)
    x0, x1, y0, y1 = xs_.min(), xs_.max() + 1, ys_.min(), ys_.max() + 1
    rgba = np.dstack([out, obj * 255.0]).astype(np.uint8)[y0:y1, x0:x1]
    cut = Image.fromarray(rgba, "RGBA")
    k = SIZE[0] * FILL / cut.width
    cut = cut.resize((round(cut.width * k), round(cut.height * k)), Image.LANCZOS)
    px = (SIZE[0] - cut.width) // 2
    py = (SIZE[1] - cut.height) // 2

    # Backdrop: vertical warm gradient.
    g = np.linspace(0, 1, SIZE[1])[:, None, None]
    back = (np.array(BG_TOP) * (1 - g) + np.array(BG_BOTTOM) * g).repeat(SIZE[0], 1)
    cover = Image.fromarray(back.astype(np.uint8), "RGB")

    # Shadow: a wide soft pool under the base plus a tighter contact shadow from the silhouette.
    alpha = cut.getchannel("A")
    shadow = Image.new("L", SIZE, 0)
    pool = alpha.resize((round(cut.width * 1.04), max(1, round(cut.height * 0.22))))
    shadow.paste(pool, (px - round(cut.width * 0.02), py + cut.height - round(pool.height * 0.62)), pool)
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))
    contact = Image.new("L", SIZE, 0)
    contact.paste(alpha, (px + 6, py + 10), alpha)
    contact = contact.filter(ImageFilter.GaussianBlur(14))
    sh = np.maximum(np.asarray(shadow) * 0.45, np.asarray(contact) * 0.30) / 255.0
    cover = Image.fromarray((np.asarray(cover) * (1 - sh[..., None] * 0.55)).astype(np.uint8), "RGB")

    cover.paste(cut, (px, py), cut)
    dst = MW / "images" / "0_cover.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cover.save(dst, optimize=True)
    print(dst, cover.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
