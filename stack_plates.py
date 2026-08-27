#!/usr/bin/env python3
"""Stack the plates of a multi-plate Gridfinity baseplate STL for one-run printing.

Plates are ordered largest footprint first, alternately rotated 180 degrees about
X, and translated so every plate's 42 mm cell lattice shares one origin. That makes
each interface either land-to-land or rib-to-rib -- matching contact faces, so the
socket funnels never fill with support.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import gridfinity as gf
from stl_io import (Bounds, Mesh, bounds_of, box, loft, read_stl, rotate_x180,
                    rotate_y180, split_shells, translate, write_stl)

LAND, RIB = "land", "rib"


@dataclass(frozen=True)
class Plate:
    mesh: Mesh
    bounds: Bounds
    lattice: gf.Lattice
    bottom_area: float          # flat face area at the plate's own z0 (ribs)
    top_area: float             # flat face area at the plate's own z1 (land)
    funnel_depth: float         # depth of the socket taper below the top face
    section_zs: tuple           # heights at which the cell outline is sampled
    profiles: tuple             # cell outline at each of those heights

    @property
    def label(self) -> str:
        return f"{self.bounds.width:.0f}x{self.bounds.depth:.0f}"


@dataclass(frozen=True)
class Placement:
    plate: Plate
    flip_axis: str | None      # None upright, "x" or "y" for a 180-degree rotation
    dx: float
    dy: float
    dz: float

    @property
    def flipped(self) -> bool:
        return self.flip_axis is not None

    @property
    def z0(self) -> float:
        return self.dz

    @property
    def z1(self) -> float:
        return self.dz + self.plate.bounds.height

    @property
    def down_face(self) -> str:
        return LAND if self.flipped else RIB

    @property
    def up_face(self) -> str:
        return RIB if self.flipped else LAND

    @property
    def down_area(self) -> float:
        return self.plate.top_area if self.flipped else self.plate.bottom_area

    @property
    def up_area(self) -> float:
        return self.plate.bottom_area if self.flipped else self.plate.top_area

    @property
    def lattice(self) -> gf.Lattice:
        return flipped_lattice(self.plate.lattice, self.flip_axis)

    def holes(self) -> tuple[tuple[float, float], ...]:
        lat = self.lattice
        return tuple((x + self.dx, y + self.dy) for x in lat.xs for y in lat.ys)

    def placed_mesh(self) -> Mesh:
        mesh = flipped_mesh(self.plate.mesh, self.flip_axis)
        b = bounds_of(mesh)
        return translate(mesh, self.dx, self.dy, self.dz - b.z0)


def flipped_lattice(lat: gf.Lattice, axis: str | None) -> gf.Lattice:
    if axis == "x":
        return lat.mirrored_y()
    if axis == "y":
        return lat.mirrored_x()
    return lat


def flipped_mesh(mesh: Mesh, axis: str | None) -> Mesh:
    if axis == "x":
        return rotate_x180(mesh)
    if axis == "y":
        return rotate_y180(mesh)
    return mesh


SKIN = 0.01     # how far inside a face or step to sample the cell outline


def section_heights(levels: tuple[float, ...]) -> tuple[float, ...]:
    """Sample heights that capture the socket exactly, steps included.

    Just inside each face, and just either side of every internal step, so a
    vertical jump in the opening is reproduced as a jump rather than smeared into
    a taper -- a loft straight through a step is wider than the hole it sits in.
    """
    inner = [z for L in levels[1:-1] for z in (L - SKIN, L + SKIN)]
    return (levels[0] + SKIN, *inner, levels[-1] - SKIN)


def build_plate(mesh: Mesh) -> Plate:
    b = bounds_of(mesh)
    lat = gf.detect_lattice(mesh, b)
    if lat is None:
        raise ValueError(f"no cell lattice found in the {b.width:.0f}x{b.depth:.0f} shell")
    levels = gf.z_levels(mesh)
    funnel = b.z1 - levels[-2] if len(levels) >= 2 else 0.0
    # Profile a mid-plate cell: edge cells can be clipped by the plate outline.
    hx, hy = lat.xs[len(lat.xs) // 2], lat.ys[len(lat.ys) // 2]
    zs = section_heights(levels)
    profiles = tuple(gf.hole_profile(mesh, hx, hy, z,
                                     gf._opening_at(mesh, hx, hy, z) or lat.top_opening)
                     for z in zs)
    return Plate(mesh, b, lat,
                 gf.horizontal_area(mesh, b.z0),
                 gf.horizontal_area(mesh, b.z1),
                 funnel, zs, profiles)


def snap(ideal: float, coord: float, phase: float, pitch: float) -> float:
    """Nearest shift to `ideal` that puts `coord` back on the `phase` lattice."""
    residual = (coord + ideal - phase) % pitch
    return ideal + (-residual if residual <= pitch / 2 else pitch - residual)


def uncovered(lower: Plate, upper: Plate) -> float:
    """Footprint of `upper` that hangs past `lower`, both centred."""
    b, a = lower.bounds, upper.bounds
    return a.footprint - min(a.width, b.width) * min(a.depth, b.depth)


def order_plates(plates: tuple[Plate, ...]) -> tuple[Plate, ...]:
    """Order so each plate sits on the one below, largest on the bed.

    Sorting by footprint area alone does not do this: 216x126 and 174x144 are
    incomparable -- neither contains the other -- so some ledge is unavoidable
    here, but area order picks a worse one than necessary. Exhaustive over
    subsets, which is fine for the handful of plates a bed can hold.
    """
    n = len(plates)
    if n < 3:
        return tuple(sorted(plates, key=lambda p: p.bounds.footprint, reverse=True))
    start = max(range(n), key=lambda i: plates[i].bounds.footprint)

    def step(lo: int, up: int) -> tuple[float, int]:
        # Ledge area first; break ties by keeping bigger plates lower down.
        area = uncovered(plates[lo], plates[up])
        rise = plates[up].bounds.footprint > plates[lo].bounds.footprint
        return area, int(rise)

    best: dict[tuple[int, int], tuple[tuple[float, int], list[int]]] = {
        (1 << start, start): ((0.0, 0), [start])}
    for _ in range(n - 1):
        nxt: dict[tuple[int, int], tuple[tuple[float, int], list[int]]] = {}
        for (mask, last), (cost, path) in best.items():
            for j in range(n):
                if mask & (1 << j):
                    continue
                area, rise = step(last, j)
                key = (mask | (1 << j), j)
                new = ((cost[0] + area, cost[1] + rise), path + [j])
                if key not in nxt or new[0] < nxt[key][0]:
                    nxt[key] = new
        best = nxt
    _, path = min(best.values(), key=lambda v: v[0])
    return tuple(plates[i] for i in path)


def plan(plates: tuple[Plate, ...], gap: float, flip: bool, register: bool,
         order: str = "nested") -> tuple[Placement, ...]:
    ordered = (order_plates(plates) if order == "nested"
               else tuple(sorted(plates, key=lambda p: p.bounds.footprint, reverse=True)))
    base = ordered[0]
    pitch = base.lattice.pitch
    # Bottom plate centred on the origin; its lattice defines the global phase.
    ox, oy = -base.bounds.cx, -base.bounds.cy
    phase_x = (base.lattice.phase_x + ox) % pitch
    phase_y = (base.lattice.phase_y + oy) % pitch

    def candidates(p: Plate, axis: str | None, z: float, below):
        """Candidate placements for one flip axis."""
        lat = flipped_lattice(p.lattice, axis)
        b = p.bounds
        # A rotation negates the mesh's extent on the mirrored axis, so its
        # centre negates with it.
        cx = -b.cx if axis == "y" else b.cx
        cy = -b.cy if axis == "x" else b.cy
        base = (-cx, -cy)
        if not register:
            grid = [base]
        else:
            sx = snap(base[0], lat.phase_x, phase_x, pitch)
            sy = snap(base[1], lat.phase_y, phase_y, pitch)
            grid = [(sx + kx * pitch, sy + ky * pitch)
                    for kx in (-1, 0, 1) for ky in (-1, 0, 1)]
        out = []
        for dx, dy in grid:
            rect = oriented_rect(b, axis, dx, dy)
            _, volume, _ = unsupported(rect, z, below, gap)
            drift = abs(dx - base[0]) + abs(dy - base[1])
            out.append(((round(volume, 6), drift), axis, dx, dy))
        return out

    placements: list[Placement] = []
    z = 0.0
    for i, p in enumerate(ordered):
        below = tuple((stl_bounds(q), q.z1) for q in placements)
        # Alternating orientation is what makes each interface land-to-land or
        # rib-to-rib; either rotation axis achieves it, so take whichever leaves
        # the least of the plate hanging over nothing.
        axes: tuple[str | None, ...] = ("x", "y") if flip and i % 2 else (None,)
        _, axis, dx, dy = min((c for a in axes for c in candidates(p, a, z, below)),
                              key=lambda c: c[0])
        placements.append(Placement(p, axis, dx, dy, z))
        z += p.bounds.height + gap
    return tuple(placements)


def interfaces(placements: tuple[Placement, ...]) -> tuple[tuple[Placement, Placement], ...]:
    return tuple(zip(placements, placements[1:]))


MIN_LEDGE = 1.0     # mm2; below this it is float noise in the mesh bounds


def unsupported(rect: tuple[float, float, float, float], z0: float,
                below: tuple[tuple[tuple[float, float, float, float], float], ...],
                gap: float) -> tuple[float, float, float]:
    """(area, volume, worst drop) of `rect` at z0 with nothing directly beneath.

    Exact for axis-aligned plates: cut the footprints on their own edges, then
    ask each cell for the highest plate below it.
    """
    rects = [rect, *(r for r, _ in below)]
    xs = sorted({v for r in rects for v in (r[0], r[2])})
    ys = sorted({v for r in rects for v in (r[1], r[3])})
    area = volume = worst = 0.0
    for xa, xb in zip(xs, xs[1:]):
        if xa < rect[0] - 1e-9 or xb > rect[2] + 1e-9:
            continue
        for ya, yb in zip(ys, ys[1:]):
            if ya < rect[1] - 1e-9 or yb > rect[3] + 1e-9:
                continue
            cx, cy = (xa + xb) / 2, (ya + yb) / 2
            tops = [z for r, z in below
                    if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
            drop = z0 - (max(tops) if tops else 0.0)
            if drop <= gap + 1e-6:
                continue
            cell = (xb - xa) * (yb - ya)
            area += cell
            volume += cell * drop
            worst = max(worst, drop)
    return area, volume, worst


def oriented_rect(b: Bounds, axis: str | None, dx: float, dy: float
                  ) -> tuple[float, float, float, float]:
    """Footprint of a plate after its rotation and translation."""
    x0, x1 = (-b.x1, -b.x0) if axis == "y" else (b.x0, b.x1)
    y0, y1 = (-b.y1, -b.y0) if axis == "x" else (b.y0, b.y1)
    return x0 + dx, y0 + dy, x1 + dx, y1 + dy


@dataclass(frozen=True)
class Ledge:
    """Part of a plate with no plate directly beneath it."""
    index: int
    label: str
    area: float
    volume: float       # envelope of the support column holding it up
    drop: float         # tallest unsupported drop, mm


def ledges(placements: tuple[Placement, ...], gap: float) -> tuple[Ledge, ...]:
    """Where a plate hangs past the one below, and how far it has to reach."""
    rects = [stl_bounds(pl) for pl in placements]
    out: list[Ledge] = []
    for i, pl in enumerate(placements[1:], 1):
        below = tuple((rects[j], placements[j].z1) for j in range(i))
        area, volume, drop = unsupported(rects[i], pl.z0, below, gap)
        if area < MIN_LEDGE:
            continue
        # only the rib lattice hangs, not the whole rectangle
        solid = pl.down_area / pl.plate.bounds.footprint
        out.append(Ledge(i + 1, pl.plate.label, area * solid, volume * solid, drop))
    return tuple(out)


def stl_bounds(pl: Placement) -> tuple[float, float, float, float]:
    b = bounds_of(pl.placed_mesh())
    return b.x0, b.y0, b.x1, b.y1


LEDGE = 0.02    # a step shallower than this is sampling noise, not a ledge


def overhang_from_sections(levels: tuple[float, ...], radii: tuple[float, ...],
                           skin: float = SKIN) -> float:
    """Shallowest wall angle, in degrees from horizontal, given sampled radii.

    `radii` holds two samples per level interval, taken `skin` inside each end.
    Each interval's taper is extrapolated back to its true levels before the
    steps between intervals are measured -- otherwise the taper's own drift
    across the skin reads as a ledge, and one continuous 45 degree chamfer
    reports as 0 degrees.
    """
    if len(levels) < 2 or len(radii) != 2 * (len(levels) - 1):
        return 90.0

    def angle(dz: float, dr: float, tol: float = 1e-9) -> float:
        return 90.0 if dr < tol else math.degrees(math.atan2(dz, dr))

    ends: list[tuple[float, float]] = []
    angles: list[float] = []
    for i, (lo, hi) in enumerate(zip(levels, levels[1:])):
        near, far = radii[2 * i], radii[2 * i + 1]
        span = (hi - skin) - (lo + skin)
        slope = 0.0 if span <= 1e-9 else (far - near) / span
        r_lo, r_hi = near - slope * skin, far + slope * skin
        ends.append((r_lo, r_hi))
        angles.append(angle(hi - lo, abs(r_hi - r_lo)))
    angles += [angle(0.0, abs(ends[i + 1][0] - ends[i][1]), LEDGE)
               for i in range(len(ends) - 1)]
    return min(angles)


def steepest_overhang(plate: Plate) -> float:
    """The overhang a flipped plate prints unsupported: the socket's shallowest wall.

    Sets the ceiling for the slicer's support threshold. Measured between true z
    levels -- the sampling heights sit a hair inside them and would flatter it.
    """
    return overhang_from_sections(
        gf.z_levels(plate.mesh),
        tuple(max(w for _, w in prof) for prof in plate.profiles))


def is_full_cell(plate: Plate, hx: float, hy: float) -> bool:
    """True when the plate outline does not clip this cell."""
    half = plate.lattice.top_opening / 2
    b = plate.bounds
    return (b.x0 <= hx - half and hx + half <= b.x1
            and b.y0 <= hy - half and hy + half <= b.y1)


def cell_sections(plate: Plate, hx: float, hy: float, clearance: float):
    """(z, polygon) sections filling one socket void, in the plate's own frame.

    Full cells reuse the plate's measured profile. A cell the outline clips is
    measured in place and keeps only the samples that find a void at every
    height, so the blocker stops at the rim instead of running over it.
    """
    zs = plate.section_zs
    if is_full_cell(plate, hx, hy):
        profiles = plate.profiles
    else:
        opening = plate.lattice.top_opening
        raw = [gf.hole_profile(plate.mesh, hx, hy, z, opening, fill=False) for z in zs]
        if any(not r for r in raw):
            return None
        keep = [i for i in range(len(raw[0])) if all(r[i][1] is not None for r in raw)]
        if len(keep) < 3:
            return None
        profiles = tuple(tuple((r[i][0], r[i][1]) for i in keep) for r in raw)

    counts = {len(p) for p in profiles}
    if len(counts) != 1 or counts == {0}:
        return None
    return tuple(
        (z, tuple((px + hx, py + hy) for px, py in gf.profile_polygon(prof, clearance)))
        for z, prof in zip(zs, profiles)
    )


def make_blockers(placements: tuple[Placement, ...], gap: float,
                  clearance: float = 0.25) -> Mesh:
    """One blocker per socket, lofted to that socket's own void.

    Built in each plate's own frame and then put through exactly the same flip
    and translation as the plate, so a blocker cannot drift out of its socket.
    Each one runs a little way into the gaps above and below; those are empty, and
    the facing plate always presents an opening at least as wide.
    """
    mesh: list = []
    reach = gap * 0.95
    for pl in placements:
        plate = pl.plate
        b = plate.bounds
        local: list = []
        for hx in plate.lattice.xs:
            for hy in plate.lattice.ys:
                sections = cell_sections(plate, hx, hy, clearance)
                if sections is None:
                    continue
                local.extend(loft((
                    (sections[0][0] - reach, sections[0][1]),
                    *sections,
                    (sections[-1][0] + reach, sections[-1][1]),
                )))
        oriented = flipped_mesh(tuple(local), pl.flip_axis)
        offset = bounds_of(flipped_mesh(plate.mesh, pl.flip_axis)).z0
        mesh.extend(translate(oriented, pl.dx, pl.dy, pl.dz - offset))
    return match_bbox(tuple(mesh),
                      bounds_of(tuple(f for pl in placements for f in pl.placed_mesh())))


BBOX_PIN = 0.1      # mm; large enough that the slicer keeps it, small enough to ignore


def match_bbox(mesh: Mesh, target: Bounds) -> Mesh:
    """Pad a blocker mesh so its bounding box matches the model's.

    Bambu Studio centres a loaded part on the object it joins. The cell lattice is
    not centred in the plate outline, so the blockers' own bbox centre sits a few
    mm off the model's -- and every blocker lands that far out, over the ribs
    instead of the chimneys, silently doing nothing. Two pin-sized cubes at
    opposite corners make the centres agree, so the placement is a no-op.
    """
    b = bounds_of(mesh)
    pins = (
        box(target.x0, target.y0, b.z0, target.x0 + BBOX_PIN, target.y0 + BBOX_PIN, b.z0 + BBOX_PIN),
        box(target.x1 - BBOX_PIN, target.y1 - BBOX_PIN, b.z1 - BBOX_PIN, target.x1, target.y1, b.z1),
    )
    return mesh + pins[0] + pins[1]


def registration_error(lower: Placement, upper: Placement) -> tuple[float, float]:
    """How far the two lattices sit apart, in mm, per axis (0 = perfect)."""
    pitch = lower.lattice.pitch
    out = []
    for lo, up, dl, du in ((lower.lattice.phase_x, upper.lattice.phase_x, lower.dx, upper.dx),
                           (lower.lattice.phase_y, upper.lattice.phase_y, lower.dy, upper.dy)):
        d = ((up + du) - (lo + dl)) % pitch
        out.append(round(min(d, pitch - d), 3))
    return out[0], out[1]


PLA_DENSITY = 0.00124       # g per mm3
SPARSE = 0.15               # density of support bulk below the interface


def support_estimate(placements: tuple[Placement, ...], gap: float
                     ) -> tuple[float, float, float]:
    """(interface mm3, column mm3, grams).

    The gap prints as solid interface, not sparse infill -- costing it at a
    sparse density understates it several times over. Ledge columns are bulk.
    """
    interface = sum(min(lo.up_area, up.down_area) * gap
                    for lo, up in interfaces(placements))
    columns = sum(l.volume for l in ledges(placements, gap))
    grams = (interface + columns * SPARSE) * PLA_DENSITY
    return interface, columns, grams


def report(placements: tuple[Placement, ...], gap: float, bed: tuple[float, float, float]) -> str:
    lines: list[str] = []
    stack = bounds_of(tuple(f for pl in placements for f in pl.placed_mesh()))
    lines.append(f"Stack: {len(placements)} plates, "
                 f"{stack.width:.1f} x {stack.depth:.1f} x {stack.height:.1f} mm, gap {gap} mm")
    fits = stack.width <= bed[0] and stack.depth <= bed[1] and stack.height <= bed[2]
    lines.append(f"Bed {bed[0]:.0f}x{bed[1]:.0f}x{bed[2]:.0f}: "
                 + ("fits" if fits else "DOES NOT FIT"))
    lines.append("")
    lines.append(f"{'#':>2}  {'plate':>9}  {'cells':>5}  {'orient':>7}  {'z':>7}  "
                 f"{'down face':>16}  {'up face':>16}")
    for i, pl in enumerate(placements, 1):
        lines.append(
            f"{i:>2}  {pl.plate.label:>9}  {pl.plate.lattice.cells:>5}  "
            f"{('flip ' + pl.flip_axis) if pl.flipped else 'up':>7}  {pl.z0:>6.2f}  "
            f"{pl.down_face + f' {pl.down_area:8.0f}mm2':>16}  "
            f"{pl.up_face + f' {pl.up_area:8.0f}mm2':>16}")
    lines.append("")
    lines.append("Interfaces:")
    for i, (lo, up) in enumerate(interfaces(placements), 1):
        ex, ey = registration_error(lo, up)
        kind = f"{lo.up_face}<->{up.down_face}"
        match = "matched" if lo.up_face == up.down_face else "MISMATCH"
        reg = "registered" if max(ex, ey) < 0.05 else f"OFF BY x{ex} y{ey} mm"
        lines.append(f"  {i}: z={lo.z1:6.2f}  {kind:<11} {match:<9} {reg:<22} "
                     f"contact {min(lo.up_area, up.down_area):7.0f} mm2")

    lines.append("")
    found = ledges(placements, gap)
    if found:
        lines.append("Ledges (plate hangs past the one below, needs a tall column):")
        for l in found:
            lines.append(f"  plate {l.index} ({l.label}): {l.area:6.0f} mm2 "
                         f"reaching down {l.drop:.2f} mm")
    else:
        lines.append("Ledges: none, every plate sits on the one below")

    iface, cols, grams = support_estimate(placements, gap)
    lines.append("")
    lines.append(f"Support estimate: {grams:.0f} g "
                 f"({iface:.0f} mm3 solid interface + {cols:.0f} mm3 columns at {SPARSE:.0%})")
    lines.append("  Floor, not a promise: excludes purge and prime tower, and assumes")
    lines.append("  the socket funnels stay empty. If the slicer reports several times")
    lines.append("  this, support is going somewhere it should not -- check the preview")
    lines.append("  at the first land-to-land gap.")
    return "\n".join(lines)


PRINTING_TEMPLATE = """# Printing `{stl}` in Bambu Studio

Generated by `stack_plates.py` from `{source}`.

{report}

## Is this worth stacking?

Stacking does not save time. It saves attention. The same plates printed flat,
one per bed, need **no support at all** -- printed right side up the ribs narrow
as they rise, so every layer is carried by the one below. Stacked, the support
and the purge are pure overhead.

Print flat if you want them quickly. Print stacked if you want to start one job
and walk away. Check the slicer's estimate against the support figure above
before committing to a long print.

## Why it is stacked this way

Every plate has the same socket profile: a **{rib:.2f} mm** wide rib lattice at the
bottom face tapering to a **{land:.2f} mm** wide land at the top face, over a
{funnel:.2f} mm funnel. Stacking all of them the same way up would land each plate's
{rib:.2f} mm rib on the plate below's {land:.2f} mm land, leaving a
{ledge:.2f} mm unsupported ledge around all {cells} sockets and forcing the slicer
to fill every funnel with support.

Rotating every other plate 180 degrees makes each interface **land-to-land** or
**rib-to-rib** instead -- identical contact faces, exactly aligned, so the funnels
stay empty and nothing overhangs.

The flipped plates need no internal support either: their steepest overhang is the
socket taper at **{angle:.1f} degrees** from horizontal.

## Import

1. Open `{stl}` in Bambu Studio. Keep it as **one object** -- do not "split to
   objects" or "split to parts", the whole point is that they print as one piece.
2. Do not let auto-arrange rotate it. Orientation is already correct: the bottom
   plate's {rib:.2f} mm ribs sit on the bed.
{blocker_step}

## Slicer settings

{filament_section}

**Support** (Prepare -> Support)

| Setting | Value | Why |
|---|---|---|
| Enable support | on | |
| Type | **normal (auto)** | tree supports will not build the thin flat pads this needs |
| Style | **snug** | grid expands support to a bounding box and packs the socket chimneys with it -- measured at 59 g against 23 g for snug on a six-plate stack, and 1.7 h of print time |
| Threshold angle | **30 deg** | must stay well below {angle:.0f} deg or it will fill the socket funnels |
| On build plate only | **OFF** | the whole point -- support must build on the plates |
| Top Z distance | **{ztop}** | {zwhy} |
| Bottom Z distance | **{ztop}** | |
| Interface layers (top and bottom) | **{iface}** | {ifacewhy} |
| Interface spacing | **0** | solid interface |
| Base pattern spacing | 2.5 mm | the support is only {gap} mm tall, it needs no bulk |
| Support/object XY distance | **0.8 mm** | the default 0.35 lets the slicer build columns up through the open cells to reach the socket corners of the plate above. Those corners are 45 deg walls that print unsupported anyway. Measured on a six-plate stack: 0.35 -> 0.8 mm cuts support from 19.0 g to 11.9 g and 5.38 h to 5.08 h, with the gap interfaces unchanged. It flattens out past 0.8 |

**Other**
- Layer height **{layer} mm**. The {gap} mm gap is exactly {layers:.0f} layers; a
  different layer height that does not divide {gap} evenly will make the gaps
  inconsistent.
- {gap_note}
- The cells are through-holes, so the slicer will happily run support columns up
  the inside of them. They show up in preview as small nubs on the socket walls
  with nothing above them. The XY distance above is what stops those.
- What does **not** touch them, all measured and all producing identical support:
  threshold angle (1 deg and 30 deg give the same result -- this support is not
  generated by overhang detection), critical-regions-only, remove-small-overhang,
  support wall loops, every base pattern including lightning and hollow, and
  support blockers.
- **Normal Support expansion, slightly negative, is the second real lever.** It
  contracts the support region, which removes the wall ribbons. It also contracts
  the interface under the narrow lands, so it trades against gap coverage. Measured
  at XY 0.8 mm, coverage being the thinnest gap as a fraction of one solid layer:

  | expansion | support | coverage |
  |---|---|---|
  | 0 | 11.9 g | 0.93 |
  | -0.2 | 9.8 g | 0.89 |
  | -0.25 | 6.7 g | 0.85 |
  | -0.3 | 6.3 g | 0.73 <- cliff |
  | -0.4 | 5.3 g | 0.58 |

  -0.2 is the safe setting and -0.25 buys a further 3 g. Past -0.25 the gap
  interfaces start losing the lands they have to carry, so stop there.
- Two things cut it and simply break the gaps. Do not use them: "don't support
  bridges" (coverage 0.93x -> 0.40x), and tree support in any style, which either
  fills the cells with branches (97 g) or supports the gaps not at all.
- A thin ribbon of support following the socket walls survives everything. It is
  how the slicer descends from the gap interface above; roughly 3.5 g on a
  six-plate stack. Left alone, it is loose in an open cell and falls out.
- Do **not** enable "independent support layer height".

## After printing

The stack comes off as one block. Slide a thin blade into each gap and twist.
Work from the top down; the land-to-land gaps have the least contact area and
give first, the rib-to-rib gaps need more persuasion.

If a gap resists, it is almost always because the threshold angle was left high
and support crept into the funnels. Check the preview before printing: at the
land-to-land interfaces you should see support only on the narrow {land:.2f} mm
webs, and the socket funnels should be empty.
"""

BLOCKER_STEP = """3. Add the blockers: right-click the object -> **Add support blocker** ->
   **Load...** -> pick `{blockers}`. Bambu Studio centres a loaded part on the
   object, and the blockers' bounding box is pinned to match the model's so that
   centring is a no-op -- do not move it afterwards."""

NO_BLOCKER_STEP = """3. No support blockers needed. Sliced both ways on a
   six-plate stack they came out the same, 5.66 h and 24.3 g with them against
   5.69 h and 24.5 g without: with snug support the slicer does not pack the
   chimneys in the first place. `--blockers` emits them if you want them anyway."""

SAME_FILAMENT = """**Filament -- one spool, no AMS**

Print the support in the same PLA as the model, and assign **no** support
interface filament. A second PLA in another slot buys nothing: same material,
same bonding, and every change costs a purge and a prime-tower deposit.
A support-interface filament only earns its tool changes when it is a *different*
material -- PETG against PLA, which releases with no gap at all. Re-run with
`--interface petg` for those settings.

With one material the gap does the separating, so the Z distances below are not
optional."""

PETG_FILAMENT = """**Filament (AMS)**
- Slot 1: PLA -- the model.
- Slot 2: **PETG** -- support/raft interface only. PETG does not bond to PLA, so
  the plates separate with almost no force and leave no scarring, and the Z
  distances can be 0.

**Bambu Support W is not a substitute here.** In practice it bonds to PLA enough
to want a real clearance, so it needs Z distance {layer:g} mm and a gap wide enough
to hold a support layer between the two clearances.

The tool changes are not free. Measured on a six-plate stack, a separate
interface filament cost **0.88 h** against same-material support, plus a wipe
tower and a purge deposit at every change. Worth it for the release.
"""


SAME_GAP_NOTE = (
    "The gap is the cheapest lever left. With both Z distances at {layer:g} mm a "
    "{gap} mm gap leaves **{n} support layer(s)**. A single layer is printed in "
    "mid-air with nothing above it to consolidate it, so it can droop and the "
    "plate above then starts on an uneven surface -- prefer a gap that leaves "
    "two. 0.8 mm leaves two; 0.6 mm leaves one and saves 0.27 h.")

PETG_GAP_NOTE = (
    "With Z distance 0 the interface sits directly on the plate below and the "
    "plate above prints directly on it, so a {gap} mm gap is **{n} solid layer(s)** "
    "with nothing floating. This is why a non-bonding interface lets the gap go "
    "so much tighter than same-material support can.")


SAME_Z_WHY = (
    "never 0 with same-material support. At 0 the support fills the whole gap "
    "instead of the part left between the clearances -- measured +1.0 h and "
    "+7.5 g on a six-plate stack")


def SUPPORT_LAYERS(gap: float, layer: float, petg: bool) -> str:
    return ("PETG does not bond to PLA, so the interface can sit right against "
            "both faces. That is what lets the gap be this tight. Note that "
            "Bambu Support W is NOT equivalent here -- it bonds to PLA enough to "
            "want a clearance, so give it {0:g} mm and a wider gap").format(layer)


def support_layers(gap: float, layer: float, petg: bool) -> int:
    return round(gap / layer) if petg else max(0, round((gap - 2 * layer) / layer))


def iface_layers(gap: float, layer: float, petg: bool) -> int:
    """One interface layer per side, but never more than the gap can hold."""
    n = support_layers(gap, layer, petg)
    return 1 if n < 3 else 2


def gap_advice(gap: float, layer: float, petg: bool) -> str:
    n = support_layers(gap, layer, petg)
    tmpl = PETG_GAP_NOTE if petg else SAME_GAP_NOTE
    return tmpl.format(gap=gap, layer=layer, n=n)


def write_printing_notes(path: Path, placements, gap, layer, report_text,
                         stl_name, blocker_name, source, interface="same",
                         blockers=False) -> None:
    lat = placements[0].lattice
    plate = placements[0].plate
    rib = lat.pitch - lat.bottom_opening
    land = lat.pitch - lat.top_opening
    angle = min(steepest_overhang(pl.plate) for pl in placements)
    petg = interface == "petg"
    path.write_text(PRINTING_TEMPLATE.format(
        stl=stl_name, blockers=blocker_name, source=source, report=report_text,
        rib=rib, land=land, funnel=plate.funnel_depth, ledge=(rib - land) / 2,
        angle=angle, cells=sum(p.plate.lattice.cells for p in placements),
        gap=gap, layer=layer, layers=round(gap / layer),
        gap_note=gap_advice(gap, layer, petg),
        ztop="0" if petg else f"{layer:g} mm",
        zwhy=(SUPPORT_LAYERS(gap, layer, petg) if petg else SAME_Z_WHY),
        blocker_step=(BLOCKER_STEP.format(blockers=blocker_name) if blockers else NO_BLOCKER_STEP),
        filament_section=(PETG_FILAMENT.format(layer=layer) if petg
                          else SAME_FILAMENT),
        iface=iface_layers(gap, layer, petg),
        ifacewhy=("every interface layer is solid; at this gap there is only room "
                  "for what is listed"),
    ))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl", type=Path, help="multi-plate Gridfinity baseplate STL")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("out"))
    ap.add_argument("--name", default=None, help="output basename (default: derived)")
    ap.add_argument("--gap", type=float, default=0.8, help="separation gap in mm")
    ap.add_argument("--layer-height", type=float, default=0.2)
    ap.add_argument("--bed", default="256x256x256", help="build volume WxDxH in mm")
    ap.add_argument("--no-flip", action="store_true",
                    help="keep every plate the same way up")
    ap.add_argument("--no-register", action="store_true",
                    help="centre plates instead of aligning their cell lattices")
    ap.add_argument("--blockers", action="store_true",
                    help="also emit support blockers. Measured to make no "
                         "difference with snug support; kept for grid, or for "
                         "plate profiles this tool has not seen")
    ap.add_argument("--order", choices=("nested", "area"), default="nested",
                    help="nested: each plate sits on the one below (default); "
                         "area: plain footprint order")
    ap.add_argument("--interface", choices=("same", "petg"), default="same",
                    help="support interface material, for the printing notes")
    args = ap.parse_args(argv)

    gap = round(args.gap / args.layer_height) * args.layer_height
    if abs(gap - args.gap) > 1e-9:
        print(f"note: gap {args.gap} snapped to {gap:.3f} mm "
              f"({round(gap / args.layer_height)} x {args.layer_height} mm layers)")
    if gap <= 0:
        print("error: gap must be at least one layer", file=sys.stderr)
        return 2

    bed = tuple(float(v) for v in args.bed.lower().split("x"))
    if len(bed) != 3:
        print(f"error: --bed wants WxDxH, got {args.bed!r}", file=sys.stderr)
        return 2

    mesh = read_stl(args.stl)
    shells = split_shells(mesh)
    print(f"{args.stl.name}: {len(mesh)} facets, {len(shells)} shells")
    plates = tuple(build_plate(s) for s in shells)

    placements = plan(plates, gap, flip=not args.no_flip,
                      register=not args.no_register, order=args.order)
    report_text = report(placements, gap, bed)
    print()
    print(report_text)

    stem = args.name or f"gf-stack-{len(placements)}"
    out = args.out_dir
    stl_path = out / f"{stem}.stl"
    write_stl(stl_path, tuple(f for pl in placements for f in pl.placed_mesh()),
              f"gridfinity stack of {len(placements)} plates")
    print(f"\nwrote {stl_path}")

    blocker_path = out / f"{stem}-blockers.stl"
    if args.blockers:
        blockers = make_blockers(placements, gap)
        write_stl(blocker_path, blockers, "support blockers")
        print(f"wrote {blocker_path} ({len(blockers)} facets)")

    # Named after the model: a fixed name silently overwrites the notes for an
    # earlier variant, leaving instructions that describe a different STL.
    notes = out / f"{stem}-PRINTING.md"
    write_printing_notes(notes, placements, gap, args.layer_height, report_text,
                         stl_path.name, blocker_path.name, args.stl.name,
                         interface=args.interface, blockers=args.blockers)
    print(f"wrote {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
