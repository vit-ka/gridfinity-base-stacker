"""Independent check of a generated stack: geometry, gaps, blocker safety."""
from __future__ import annotations

import sys
from pathlib import Path

import gridfinity as gf
import stack_plates as sp
import stl_io


def inside(mesh, px, py, pz) -> bool:
    """Majority of three jittered rays.

    A single ray on a socket's plane of symmetry passes through shared vertices
    of the tessellated corners and double-counts, reading solid where the cell is
    open. The jitter is smaller than any feature we care about.
    """
    votes = 0
    for dy in (-0.017, 0.0, 0.019):
        crossings = gf._ray_crossings(mesh, py + dy, pz)
        votes += sum(1 for x in crossings if x > px) % 2
    return votes >= 2


def non_manifold(mesh) -> list[tuple[tuple, int]]:
    """Edges not shared by exactly two facets, with how many use them.

    Slicers reject a mesh that fails this and offer it for third-party repair,
    and one the slicer considers broken cannot be relied on to slice as intended.
    Nothing in this project looked for it, which is why Bambu found it first.

    The count distinguishes the causes. One means a boundary -- an actual hole in
    the surface. Four means two closed shells meeting along an edge, which is what
    two boxes touching edge to edge produce, and is a different problem entirely.
    """
    used: dict[tuple, int] = {}
    for f in mesh:
        v = [(round(f[3 + k * 3], 5), round(f[4 + k * 3], 5), round(f[5 + k * 3], 5))
             for k in range(3)]
        for a, b in ((v[0], v[1]), (v[1], v[2]), (v[2], v[0])):
            e = (a, b) if a <= b else (b, a)
            used[e] = used.get(e, 0) + 1
    return [(e, n) for e, n in used.items() if n != 2]


def film_regions(mesh, gaps, step: float = 0.15) -> dict:
    """Connected regions of film per gap, read back from the written geometry.

    Independent of the plan that produced it, which is the point: the generator
    counts regions on the raster it is about to emit, and this counts them on
    what actually reached the file.

    Rasterised rather than compared shell by shell, because the film's pieces
    overlap deliberately (they must, or their shared edges would be
    non-manifold) and overlapping boxes share no vertices -- so splitting the
    mesh into connected shells reports every box separately and says nothing
    about whether the sheet is joined.
    """
    boxes = [stl_io.bounds_of(mesh[i:i + 12]) for i in range(0, len(mesh), 12)]
    if not boxes:
        return {}
    x0 = min(b.x0 for b in boxes)
    y0 = min(b.y0 for b in boxes)
    w = max(1, int((max(b.x1 for b in boxes) - x0) / step) + 2)
    h = max(1, int((max(b.y1 for b in boxes) - y0) / step) + 2)

    out = {}
    for n, (lo, hi) in enumerate(gaps, 1):
        rows = [0] * h
        for b in boxes:
            if not (lo - 1e-6 <= (b.z0 + b.z1) / 2 <= hi + 1e-6):
                continue
            ix0 = max(0, int((b.x0 - x0) / step))
            ix1 = min(w, int((b.x1 - x0) / step) + 1)
            iy0 = max(0, int((b.y0 - y0) / step))
            iy1 = min(h, int((b.y1 - y0) / step) + 1)
            if ix1 <= ix0:
                continue
            mask = ((1 << (ix1 - ix0)) - 1) << ix0
            for iy in range(iy0, iy1):
                rows[iy] |= mask
        out[n] = sp.components(rows, w, h)
    return out


def main(src: Path, stack: Path, blockers: Path, gap: float = 0.8) -> int:
    orig = stl_io.read_stl(src)
    out = stl_io.read_stl(stack)
    shells_in = stl_io.split_shells(orig)
    shells_out = stl_io.split_shells(out)
    fails: list[str] = []

    if len(out) != len(orig):
        fails.append(f"facet count {len(out)} != {len(orig)}")
    if len(shells_out) != len(shells_in):
        fails.append(f"shell count {len(shells_out)} != {len(shells_in)}")

    vin = sorted(stl_io.signed_volume(s) for s in shells_in)
    vout = sorted(stl_io.signed_volume(s) for s in shells_out)
    drift = max(abs(a - b) / a for a, b in zip(vin, vout))
    if drift > 1e-6:
        fails.append(f"volumes changed by {drift:.2e} relative")
    if not all(v > 0 for v in vout):
        fails.append("a shell has inverted winding")
    print(f"facets {len(out)}, shells {len(shells_out)}, "
          f"volume {sum(vout)/1000:.1f} cm3 (~{sum(vout)*0.00124:.0f} g PLA)")

    placements = sp.plan(tuple(sp.build_plate(s) for s in shells_in),
                         gap, flip=True, register=True)
    meshes = [(pl, pl.placed_mesh()) for pl in placements]

    b0 = stl_io.bounds_of(meshes[0][1])
    prev = None
    for pl, mesh in meshes:
        b = stl_io.bounds_of(mesh)
        if not (b.x0 >= b0.x0 - 1e-6 and b.x1 <= b0.x1 + 1e-6
                and b.y0 >= b0.y0 - 1e-6 and b.y1 <= b0.y1 + 1e-6):
            fails.append(f"{pl.plate.label} overhangs the bottom plate")
        if prev is not None and abs((b.z0 - prev) - gap) > 1e-6:
            fails.append(f"{pl.plate.label} gap {b.z0 - prev:.4f} != {gap}")
        prev = b.z1
    print(f"all plates contained in {b0.width:.0f}x{b0.depth:.0f}, "
          f"all {len(meshes)-1} gaps = {gap} mm")

    # Containment against the bottom plate is not enough: a plate can sit inside
    # the footprint of the stack and still hang over the plate directly below it.
    found = sp.ledges(placements, gap)
    best = sp.order_plates(tuple(pl.plate for pl in placements))
    if tuple(p.label for p in best) != tuple(pl.plate.label for pl in placements):
        fails.append("plate order is not the least-ledge order")
    if found:
        for l in found:
            print(f"ledge: plate {l.index} ({l.label}) {l.area:.0f} mm2 "
                  f"reaching down {l.drop:.2f} mm")
    else:
        print("no ledges: every plate sits on the one below")

    for lower, upper in sp.interfaces(placements):
        if lower.up_face != upper.down_face:
            fails.append(f"face mismatch at z={lower.z1}")
        ex, ey = sp.registration_error(lower, upper)
        if max(ex, ey) > 1e-6:
            fails.append(f"lattice off by x{ex} y{ey} at z={lower.z1}")
    print(f"all {len(placements)-1} interfaces matched and registered")

    blk = stl_io.split_shells(stl_io.read_stl(blockers))
    if not all(stl_io.signed_volume(s) > 0 for s in blk):
        fails.append("a blocker solid has inverted winding")
    hits, sampled = 0, 0
    frac = (0.02, 0.11, 0.23, 0.37, 0.49, 0.61, 0.74, 0.88, 0.98)
    for solid in blk:
        sb = stl_io.bounds_of(solid)
        for fx in frac:
            for fy in frac:
                px, py = sb.x0 + sb.width * fx, sb.y0 + sb.depth * fy
                for pl, mesh in meshes:
                    lo, hi = max(sb.z0, pl.z0), min(sb.z1, pl.z1)
                    if hi - lo <= 1e-9:
                        continue
                    for fz in (0.03, 0.5, 0.97):
                        pz = lo + (hi - lo) * fz
                        if not inside(solid, px, py, pz):
                            continue
                        sampled += 1
                        if inside(mesh, px, py, pz):
                            hits += 1
    if hits:
        fails.append(f"{hits} blocker samples land inside plate material")
    print(f"{len(blk)} blocker solids, {sampled} interior samples, "
          f"{hits} overlapping plate material")

    print()
    if fails:
        print("FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
