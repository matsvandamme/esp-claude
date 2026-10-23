"""Self-checks for the stand before anything gets printed.

  fit         the reference board inside bezel + back: no part may intrude into the board
  cradle      bezel + back on the desk inside the base: no collisions, clearance as designed
  overhangs   every part in its print orientation: no downward face steeper than 45 degrees
              except tiny ones (<= 0.8 mm across) or flat bridges supported on both ends
  stability   centre of mass of the assembled stand lies inside the base footprint
  meshes      watertight, positive volume (plus the 3d-print skill's validate_mesh.py)

Run after exporting the STLs: uv run python check.py   (exit 1 on any failure)
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import trimesh

HERE = Path(__file__).parent
OUT = HERE / "out"
REF = HERE / "ref" / "esp32_c6_lcd_147.stl"
SCAD = HERE / "stand.scad"
VALIDATE = Path.home() / ".claude/skills/3d-print/scripts/validate_mesh.py"

PARTS = ["bezel", "bezel_inlay", "back", "base", "base_inlay"]
FAILS: list[str] = []


def scad_num(name: str) -> float:
    m = re.search(rf"^{name}\s*=\s*([-\d.]+)\s*;", SCAD.read_text(), re.M)
    if not m:
        raise SystemExit(f"{name} not found in stand.scad")
    return float(m.group(1))


def fail(msg: str) -> None:
    FAILS.append(msg)
    print("  FAIL", msg)


def ok(msg: str) -> None:
    print("  ok  ", msg)


# ---------- geometry from stand.scad (kept in sync by reading its parameters) ----------
clear, wall, front, tilt = scad_num("clear"), scad_num("wall"), scad_num("front"), scad_num("tilt")
floor_h, ledge, base_clear = scad_num("floor_h"), scad_num("ledge"), scad_num("base_clear")
board_l, board_w, glass_top, back_depth = 36.36, 20.32, 5.6, 3.21
cav_w = board_w + 2 * clear
out_w = cav_w + 2 * wall
z_front = glass_top + 0.1 + front
z_back = -(back_depth + 0.39) - 1.6


def desk_matrix() -> np.ndarray:
    t = math.radians(tilt)
    ymin = -z_front * math.cos(t) - out_w / 2 * math.sin(t)
    zmin = z_back * math.sin(t) - out_w / 2 * math.cos(t)
    m = trimesh.transformations.rotation_matrix(math.radians(-tilt), [1, 0, 0])
    m = m @ trimesh.transformations.rotation_matrix(math.radians(90), [1, 0, 0])
    m = m @ trimesh.transformations.rotation_matrix(math.radians(180), [0, 0, 1])
    return trimesh.transformations.translation_matrix([0, ledge - ymin, floor_h - zmin]) @ m


def load(name: str) -> trimesh.Trimesh:
    return trimesh.load(OUT / f"{name}.stl", force="mesh")


def board_points(n: int = 40000) -> np.ndarray:
    ref = trimesh.load(REF, force="mesh")
    keep = [b for b in ref.split(only_watertight=False) if b.bounds[0][2] > -3.3]  # no header pins
    board = trimesh.util.concatenate(keep)
    pts, _ = trimesh.sample.sample_surface(board, n, seed=1)
    return np.vstack([pts, board.vertices])


def penetration(mesh: trimesh.Trimesh, pts: np.ndarray) -> tuple[float, np.ndarray]:
    """Deepest point of `pts` inside `mesh` (mm, > 0 means collision) and where."""
    inside = mesh.contains(pts)
    if not inside.any():
        return 0.0, np.zeros(3)
    d = trimesh.proximity.signed_distance(mesh, pts[inside])
    i = int(np.argmax(d))
    return float(d[i]), pts[inside][i]


def check_meshes(meshes: dict) -> None:
    print("meshes")
    for name, m in meshes.items():
        if m.is_watertight and m.volume > 0:
            ok(f"{name}: watertight, {m.volume:.0f} mm3, {len(m.faces)} faces")
        else:
            fail(f"{name}: watertight={m.is_watertight} volume={m.volume:.1f}")
        if VALIDATE.exists():
            r = subprocess.run([sys.executable, str(VALIDATE), str(OUT / f"{name}.stl")], capture_output=True, text=True)
            if r.returncode != 0:
                fail(f"{name}: 3d-print validate_mesh: {r.stdout.strip().splitlines()[-1] if r.stdout else r.stderr[-200:]}")


def check_fit(meshes: dict) -> None:
    print("fit (reference board in bezel + back)")
    pts = board_points()
    for name in ("bezel", "back"):
        depth, where = penetration(meshes[name], pts)
        if depth > 0.02:
            fail(f"{name} intrudes {depth:.2f} mm into the board at {np.round(where, 2).tolist()}")
        else:
            ok(f"{name}: no contact with the board")
    # Clearance actually achieved around the board edge and in front of the glass.
    d = trimesh.proximity.closest_point(meshes["bezel"], pts)[1]
    ok(f"closest bezel surface to the board: {d.min():.2f} mm (designed {min(clear, 0.1):.2f})")


def check_cradle(meshes: dict) -> None:
    print("cradle (case in the base, on the desk)")
    m = desk_matrix()
    case = trimesh.util.concatenate([meshes["bezel"].copy().apply_transform(m), meshes["back"].copy().apply_transform(m)])
    pts = np.vstack([case.vertices, trimesh.sample.sample_surface(case, 20000, seed=2)[0]])
    depth, where = penetration(meshes["base"], pts)
    if depth > 0.02:
        fail(f"case intrudes {depth:.2f} mm into the base at {np.round(where, 2).tolist()}")
    else:
        ok("case sits in the cradle without collisions")
    gap = trimesh.proximity.closest_point(meshes["base"], pts)[1].min()
    ok(f"smallest case-to-cradle gap {gap:.2f} mm (designed {base_clear:.2f})")
    zmin = case.bounds[0][2]
    ok(f"case bottom {zmin:.2f} mm above the desk (designed {floor_h:.2f}); tilt {tilt:.0f} deg")
    return case


def check_stability(meshes: dict, case: trimesh.Trimesh) -> None:
    print("stability")
    base = meshes["base"]
    # PLA parts at ~40 % effective density (walls + 15 % infill), the board ~6 g at the case centre.
    m_case, m_base, m_board = case.volume * 1.24e-3 * 0.4, base.volume * 1.24e-3 * 0.4, 6.0
    com = (case.center_mass * m_case + base.center_mass * m_base + case.center_mass * m_board) / (m_case + m_base + m_board)
    foot = base.bounds
    margin = min(com[1] - foot[0][1], foot[1][1] - com[1])
    if margin > 3:
        ok(f"centre of mass {margin:.1f} mm inside the footprint front/back edge ({m_case + m_base + m_board:.0f} g total)")
    else:
        fail(f"centre of mass only {margin:.1f} mm from the footprint edge: may tip")
    # Pressing BOOT (about 1.6 N straight down on the top edge) must not tip it backwards.
    top = case.vertices[np.argmax(case.vertices[:, 2])]
    w = (m_case + m_base + m_board) * 9.81e-3
    back_edge = foot[1][1]
    if top[1] < back_edge or (back_edge - com[1]) * w > (top[1] - back_edge) * 1.6:
        ok("pressing BOOT on the top edge does not tip it over")
    else:
        fail("pressing BOOT can tip the stand backwards")


def check_thin(meshes: dict) -> None:
    """Slivers (walls thinner than one extrusion) print as nothing or as loose strings."""
    print("thin walls (rays through each part, 0.4 mm minimum)")
    for name in PARTS:
        m = meshes[name]
        pts, idx = trimesh.sample.sample_surface(m, 6000, seed=3)
        normals = m.face_normals[idx]
        hits, ray_idx, tri = m.ray.intersects_location(pts - normals * 1e-3, -normals, multiple_hits=False)
        thick = np.full(len(pts), np.inf)
        d = np.linalg.norm(hits - pts[ray_idx], axis=1)
        # Only a sliver when the face on the other side is (nearly) parallel; near a deliberate
        # 45 degree edge the ray just clips the neighbouring face.
        parallel = np.einsum("ij,ij->i", normals[ray_idx], m.face_normals[tri]) < -0.95
        np.minimum.at(thick, ray_idx[parallel], d[parallel])
        thin = thick < 0.4
        if thin.mean() > 0.002:
            where = pts[thin]
            fail(f"{name}: {thin.sum()} samples thinner than 0.4 mm around {np.round(where.mean(0), 1).tolist()}")
        else:
            ok(f"{name}: thinnest sampled wall {thick.min():.2f} mm")


def print_pose(name: str, m: trimesh.Trimesh) -> trimesh.Trimesh:
    m = m.copy()
    if name in ("bezel", "bezel_inlay"):  # face down on the bed
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
    m.apply_translation([0, 0, -m.bounds[0][2]])
    return m


def check_overhangs(meshes: dict) -> None:
    print("overhangs (print orientation, 45 deg rule, no supports)")
    limit = -math.cos(math.radians(45)) - 1e-3
    for name in PARTS:
        m = print_pose(name, meshes[name])
        down = (m.face_normals[:, 2] < limit) & (m.triangles_center[:, 2] > 0.05)
        if name == "bezel":
            # The ceiling of the inlay pocket is carried by the yellow inlay printed beneath it.
            pocket_top = scad_num("inlay_d")
            down &= ~np.isclose(m.triangles_center[:, 2], pocket_top, atol=0.02)
        if name == "bezel_inlay":
            # The inlay is printed inside the bezel's pocket: its underside rests on the bed.
            down &= m.triangles_center[:, 2] > 0.6
        if not down.any():
            ok(f"{name}: no overhangs")
            continue
        sub = m.submesh([np.nonzero(down)[0]], append=True)
        bad = []
        for g in sub.split(only_watertight=False):
            ext = g.extents[:2]
            z = g.bounds[0][2]
            flat = g.extents[2] < 0.05
            if min(ext) <= 0.8:
                continue  # a nozzle-width or two: prints fine
            if flat and m.contains(np.array([[*(g.bounds.mean(0)[:2]), z - 0.3]]))[0] is np.False_ and min(ext) <= 12:
                # A flat ceiling: fine as a bridge when it is short in one direction.
                bad.append((f"bridge {ext[0]:.1f} x {ext[1]:.1f} mm at z {z:.1f}", "bridge"))
                continue
            bad.append((f"{ext[0]:.1f} x {ext[1]:.1f} mm at z {z:.1f}, bbox {np.round(g.bounds, 1).tolist()}", "overhang"))
        real = [b for b in bad if b[1] == "overhang"]
        bridges = [b for b in bad if b[1] == "bridge"]
        for b, _ in bridges:
            ok(f"{name}: {b} (short bridge)")
        if real:
            for b, _ in real:
                fail(f"{name}: unsupported overhang {b}")
        elif not bridges:
            ok(f"{name}: only tiny overhangs (<= 0.8 mm)")


def main() -> int:
    meshes = {n: load(n) for n in PARTS}
    check_meshes(meshes)
    check_fit(meshes)
    case = check_cradle(meshes)
    check_stability(meshes, case)
    check_overhangs(meshes)
    check_thin(meshes)
    print()
    print("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
