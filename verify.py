"""Independent check of a generated stack: the geometry, and the emitted G-code.

    python3 verify.py --source models/test-plain.stl --stack out/NAME.stl
    python3 verify.py --project out/NAME.3mf --gcode out/NAME.gcode

The second form is the one that matters. It reads the file that goes to the
printer and reports what the interface actually did -- the height of every layer,
the clearance above and below it in each gap, how many pieces the film is in, and
whether any support reached the stack. None of that is taken from the plan that
produced the file.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import emit_interface as ei
import gcode
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


def interface_regions(ms, tool: int, box, step: float = 0.15):
    """Count connected regions of interface material between two heights.

    Rasterised rather than counted from the plan, which is the point: the plan
    says what was asked for and this says what is in the file. A gap that comes
    out as several regions is several sheets, and an island the size of a socket
    stays in the socket when the rest is peeled (ADR 0007).
    """
    beads = [m for m in gcode.model_moves(ms, box) if m.tool == tool]
    if not beads:
        return lambda lo, hi: 0
    x0 = min(min(m.x0, m.x1) for m in beads) - 1.0
    y0 = min(min(m.y0, m.y1) for m in beads) - 1.0
    w = int((max(max(m.x0, m.x1) for m in beads) + 1.0 - x0) / step) + 2
    h = int((max(max(m.y0, m.y1) for m in beads) + 1.0 - y0) / step) + 2

    def count(lo: float, hi: float) -> int:
        rows = [0] * h
        for m in beads:
            if not (lo - 1e-6 <= m.z0 and m.z <= hi + 1e-6):
                continue
            r = m.width / 2
            ix0 = max(0, int((min(m.x0, m.x1) - r - x0) / step))
            ix1 = min(w - 1, int((max(m.x0, m.x1) + r - x0) / step))
            iy0 = max(0, int((min(m.y0, m.y1) - r - y0) / step))
            iy1 = min(h - 1, int((max(m.y0, m.y1) + r - y0) / step))
            mask = ((1 << (ix1 - ix0 + 1)) - 1) << ix0
            for iy in range(iy0, iy1 + 1):
                rows[iy] |= mask
        return sp.components(rows, w, h)

    return count


def plan_box(plan: dict) -> tuple[float, float, float, float]:
    """The interface's own footprint on the bed.

    Everything measured about the interface is measured inside this. The plate
    carries a decoy as well, and the decoy is supported in the same filament --
    measure the whole bed and the decoy's support is counted as film.
    """
    beads = [b for lay in plan["layers"] for b in lay["beads"]]
    return (min(min(b[0], b[2]) for b in beads), min(min(b[1], b[3]) for b in beads),
            max(max(b[0], b[2]) for b in beads), max(max(b[1], b[3]) for b in beads))


def check_interface(project: Path, emitted: Path) -> tuple[list[str], list[str]]:
    """What the emitted file printed in the gaps, and what is wrong with it.

    The heights of the interface layers, the filament they use and the absence of
    support are properties of the G-code, so they are read there and nowhere
    else. Every conclusion in this project drawn from the plan rather than from
    the output has been wrong at least once.
    """
    plan = ei.load_plan(project)
    tool = plan["interface_extruder"] - 1
    hdr, lines, ms = gcode.read(emitted)
    box = plan_box(plan)
    gaps = gcode.measure_gaps(ms, tool, interface_regions(ms, tool, box), box)

    out = [f"{emitted.name}: {hdr.layers} layers, interface in filament "
           f"{plan['interface_extruder']} at {plan['line_width']:g} mm"]
    fails: list[str] = []

    # Every planned layer at the Z it was planned for. A wrong height is the
    # failure this whole change exists to make impossible, so it is an error and
    # it names both numbers.
    printed = sorted({round(m.z, 4) for m in gcode.model_moves(ms, box)
                      if m.tool == tool})
    for lay in sorted(plan["layers"], key=lambda l: l["z1"]):
        if not lay["beads"]:
            continue
        want = round(lay["z1"], 4)
        near = min(printed, key=lambda z: abs(z - want)) if printed else None
        if near is None or abs(near - want) > 1e-6:
            fails.append(f"gap {lay['gap'] + 1} layer {lay['index'] + 1}: asked "
                         f"for z={want:g}, nearest interface extrusion is at "
                         f"z={'nothing' if near is None else format(near, 'g')}")

    for g in gaps:
        out.append(f"  gap {g.index}: film {g.film_lo:.3f}-{g.film_hi:.3f} "
                   f"({g.layers} layers, {g.regions} region"
                   f"{'' if g.regions == 1 else 's'}), plate below "
                   f"{g.plate_below:.3f}, plate above {g.plate_above:.3f}"
                   f"  ->  {g.below:.3f} clear below, {g.above:.3f} above")
        if g.regions > 1:
            out.append(f"    {g.regions} regions: the film in this gap peels as "
                       f"{g.regions} pieces, not one")

    # Does the interface land on the stack at all? Every clearance above is
    # measured inside the interface's own footprint, so an interface shifted
    # bodily off the model measures perfectly against itself -- which is exactly
    # what happened when the plate-to-machine offset was missed, and nothing
    # noticed until it was looked at in the preview. The reference here is what
    # the *plate* printed, in the layers on either side of the gap.
    for g in gaps:
        near = [m for m in gcode.model_moves(ms)
                if m.tool != tool and g.plate_below - 0.5 <= m.z0
                and m.z <= g.plate_above + 0.5]
        film = [m for m in gcode.model_moves(ms, box)
                if m.tool == tool and g.film_lo - 1e-6 <= m.z0
                and m.z <= g.film_hi + 1e-6]
        if not near or not film:
            continue
        # A bead's width, plus the flare's own deliberate overhang of one layer
        # per layer of film. Anything past that is not the film leaning outward.
        margin = plan["line_width"] + 0.2 * g.layers
        ref = (min(min(m.x0, m.x1) for m in near) - margin,
               min(min(m.y0, m.y1) for m in near) - margin,
               max(max(m.x0, m.x1) for m in near) + margin,
               max(max(m.y0, m.y1) for m in near) + margin)
        out_of = [m for m in film
                  if min(m.x0, m.x1) < ref[0] or max(m.x0, m.x1) > ref[2]
                  or min(m.y0, m.y1) < ref[1] or max(m.y0, m.y1) > ref[3]]
        if out_of:
            worst = max(out_of, key=lambda m: max(
                ref[0] - min(m.x0, m.x1), max(m.x0, m.x1) - ref[2],
                ref[1] - min(m.y0, m.y1), max(m.y0, m.y1) - ref[3]))
            fails.append(
                f"gap {g.index}: {len(out_of)} of {len(film)} interface "
                f"extrusions land outside the plates, by up to "
                f"{max(ref[0] - min(worst.x0, worst.x1), max(worst.x0, worst.x1) - ref[2], ref[1] - min(worst.y0, worst.y1), max(worst.y0, worst.y1) - ref[3]):.2f} mm "
                f"-- the interface is not on the stack")
        else:
            out.append(f"    on the plates, within {margin:.2f} mm of what they "
                       f"printed either side")

    support = gcode.model_moves(gcode.support_moves(ms), box)
    if support:
        fails.append(f"{len(support)} support extrusions inside the stack's "
                     f"footprint; the blocker is leaking")
    out.append(f"  no support on the stack ({len(gcode.support_moves(ms))} "
               f"support extrusions on the plate, all on the decoy)")
    return out, fails


def check_stack(src: Path, stack: Path, blockers: Path | None,
                gap: float = 0.8) -> int:
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

    if blockers is None:
        print()
        if fails:
            print("FAILED:")
            for f in fails:
                print("  -", f)
            return 1
        print("PASS")
        return 0

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path,
                    help="the multi-plate STL the stack was generated from")
    ap.add_argument("--stack", type=Path, help="the generated stack STL")
    ap.add_argument("--blockers", type=Path,
                    help="a blocker STL, if one was written")
    ap.add_argument("--gap", type=float, default=0.8)
    ap.add_argument("--project", type=Path,
                    help="the 3mf that was sliced; carries the interface plan")
    ap.add_argument("--gcode", type=Path,
                    help="the emitted G-code, checked against that plan")
    args = ap.parse_args(argv)

    rc = 0
    if (args.source is None) != (args.stack is None):
        ap.error("--source and --stack go together")
    if (args.project is None) != (args.gcode is None):
        ap.error("--project and --gcode go together")
    if args.source is None and args.project is None:
        ap.error("give --source/--stack, or --project/--gcode, or both")

    if args.source is not None:
        rc |= check_stack(args.source, args.stack, args.blockers, args.gap)
    if args.project is not None:
        if args.source is not None:
            print()
        lines, fails = check_interface(args.project, args.gcode)
        for ln in lines:
            print(ln)
        print()
        if fails:
            print("FAILED:")
            for f in fails:
                print("  -", f)
            rc |= 1
        else:
            print("PASS")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
