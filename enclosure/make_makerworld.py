"""Build makerworld/: everything the MakerWorld upload needs, apart from the real print photo.

  makerworld/claude-display-stand.3mf   print profile: the Bambu project, sliced once by Bambu Studio's
                                        CLI so it carries the plate thumbnails and the time/filament
                                        estimate, with the G-code taken out again (a project, not a
                                        .gcode.3mf)
  makerworld/stl/*.stl                  every part in its print pose (bezel turned face down); a part and its
                                        inlay share one frame, so loading both as one object lines them up
  makerworld/images/*.png               4:3 renders of the exported meshes (no reference board)
  makerworld/stand.scad                 the parametric source, show_board off (the board model is not ours)

listing.md (the page text) is written by hand and not touched here.

Run after exporting the STLs: uv run python make_makerworld.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import make_3mf

HERE = Path(__file__).parent
MW = HERE / "makerworld"
NAME = "claude-display-stand"
DROP = {"Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/_rels/model_settings.config.rels"}
TITLE = "Claude Code Status Display Stand – ESP32-C6-LCD-1.47"
PROFILE_TITLE = "A1 + AMS · 0.20 mm · 2-colour PLA · no supports"
PROFILE_DESCRIPTION = ("All 3 parts on one plate, already in print orientation. Black PLA body (filament 1), "
                       "yellow PLA inlays (filament 2) in the first 3 layers of the bezel and the ledge top. "
                       "0.20 mm Standard, Textured PEI, no supports. About 40 min, 13 g black + 3 g yellow.")
DESCRIPTION = ("Desk stand for the Waveshare ESP32-C6-LCD-1.47. Board dimensions measured from the "
               "ESP32-C6-LCD-1.47 Reference CAD Model by x_giedrius_x (Printables 1633740); that model "
               "is not included.")
VIEWS = [  # image, view, camera (OpenSCAD: tx,ty,tz,rx,ry,rz,dist)
    ("1_front", "desk", "0,0,0,68,0,-30,0"),
    ("2_exploded", "exploded", "0,0,0,58,0,-35,0"),
    ("3_back", "desk", "0,0,0,60,0,140,0"),
]


def project() -> Path:
    """Bambu project with thumbnails and slice info, without G-code."""
    sliced = make_3mf.WORK / "sliced" / "sliced.3mf"
    tracked = make_3mf.OUT / "stand.3mf"
    keep = tracked.read_bytes()            # build() rewrites it with fresh UUIDs; leave the committed one
    try:
        est = make_3mf.slice_it(make_3mf.build(make_3mf.seed_settings()))
    finally:
        tracked.write_bytes(keep)
    out = MW / f"{NAME}.3mf"
    with zipfile.ZipFile(sliced) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename in DROP:
                continue
            data = src.read(item)
            if item.filename == "3D/3dmodel.model":
                data = re.sub(rb'(<metadata name="Description">)[^<]*(</metadata>)',
                              rb"\g<1>" + DESCRIPTION.encode() + rb"\g<2>", data)
                for key, value in (("Title", TITLE), ("ProfileTitle", PROFILE_TITLE),
                                   ("ProfileDescription", PROFILE_DESCRIPTION)):
                    data = re.sub(rb'(<metadata name="' + key.encode() + rb'">)[^<]*(</metadata>)',
                                  lambda m: m[1] + value.encode() + m[2], data)
            dst.writestr(item, data)
    print(out, json.dumps(est))
    return out


def reslice(path: Path) -> None:
    """The stripped project must still open and slice cleanly in Bambu Studio's CLI."""
    work = make_3mf.WORK / "mw_check"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    r = subprocess.run([str(make_3mf.STUDIO), "--debug", "1", "--outputdir", str(work), "--slice", "0",
                        str(path.resolve())], capture_output=True, text=True, cwd=work)
    res = json.loads((work / "result.json").read_text()) if (work / "result.json").exists() else {}
    if r.returncode != 0 or res.get("return_code", 1) != 0:
        raise SystemExit(f"re-slice of {path.name} failed: {res.get('error_string')}")
    print(f"re-slice ok: {path.name}")


def flip_x180(text: str) -> str:
    """Turn an ASCII STL 180 degrees about x (y, z -> -y, -z) by editing the numbers' signs only, so the
    mesh stays bit-for-bit the one OpenSCAD wrote (re-exporting merges its near-coincident vertices)."""
    def neg(v: str) -> str:
        return v[1:] if v.startswith("-") else v if float(v) == 0 else "-" + v
    return re.sub(r"^(\s*(?:facet normal|vertex)) (\S+) (\S+) (\S+)$",
                  lambda m: f"{m[1]} {m[2]} {neg(m[3])} {neg(m[4])}", text, flags=re.M)


def stls() -> None:
    """Print-pose STLs. Slicers drop each object onto the bed; a part and its inlay keep one frame."""
    d = MW / "stl"
    d.mkdir(parents=True, exist_ok=True)
    for _, parts, how, _ in make_3mf.OBJECTS:
        for stl, _ in parts:
            text = (make_3mf.OUT / f"{stl}.stl").read_text()
            (d / f"{stl}.stl").write_text(flip_x180(text) if how == "face_down" else text)
    print(d, sorted(p.name for p in d.glob("*.stl")))


def images() -> None:
    d = MW / "images"
    d.mkdir(parents=True, exist_ok=True)
    for name, view, cam in VIEWS:
        subprocess.run(["openscad", "--imgsize=1600,1200", "--colorscheme=Tomorrow", "--autocenter", "--viewall",
                        f"--camera={cam}", "-D", "show_board=false", "-D", f'view="{view}"',
                        "-o", str(d / f"{name}.png"), str(HERE / "render_assembly.scad")],
                       check=True, capture_output=True, cwd=HERE)
    print(d, sorted(p.name for p in d.glob("*.png")))


def main() -> int:
    MW.mkdir(exist_ok=True)
    reslice(project())
    stls()
    images()
    # The reference board isn't shipped, so the published copy previews without it by default.
    src = (HERE / "stand.scad").read_text()
    assert "show_board = true;" in src
    (MW / "stand.scad").write_text(src.replace("show_board = true;", "show_board = false;", 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
