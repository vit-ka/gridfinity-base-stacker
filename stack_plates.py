#!/usr/bin/env python3
"""Stack the plates of a multi-plate Gridfinity baseplate STL for one-run printing.

Plates are ordered largest footprint first, alternately rotated 180 degrees about
X, and translated so every plate's 42 mm cell lattice shares one origin. That makes
each interface either land-to-land or rib-to-rib -- matching contact faces, so the
socket funnels never fill with support.
"""
from __future__ import annotations

import argparse
import json
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
SECTION_DZ = 0.2    # vertical spacing of cell-outline samples


def section_heights(levels: tuple[float, ...], dz: float = SECTION_DZ) -> tuple[float, ...]:
    """Sample heights that follow the socket as a surface, not as a few sections.

    Just inside each face, just either side of every internal step so a vertical
    jump stays a jump, and then every `dz` in between. The sparse version -- one
    section per level -- is a 2D outline extruded: it interpolates straight
    through the taper, and the socket's corner radius changes continuously with
    height, so the chords cut across the corners. Support collects in exactly
    those gaps.
    """
    out = {round(levels[0] + SKIN, 4), round(levels[-1] - SKIN, 4)}
    for L in levels[1:-1]:
        out.add(round(L - SKIN, 4)); out.add(round(L + SKIN, 4))
    lo, hi = levels[0] + SKIN, levels[-1] - SKIN
    n = int((hi - lo) / dz)
    out.update(round(lo + i * dz, 4) for i in range(1, n + 1))
    return tuple(sorted(out))


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
    # sampled finely: a blocker built from these traces the socket outline with
    # chords, and at 24 samples the chords cut across the rounded corners, leaving
    # notches the slicer fills with exactly the support the blocker is there to stop
    profiles = tuple(gf.hole_profile(mesh, hx, hy, z,
                                     gf._opening_at(mesh, hx, hy, z) or lat.top_opening,
                                     samples=64, span=0.995)
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


def contains(lower: Plate, upper: Plate) -> bool:
    """True when `upper` sits entirely on `lower` with nothing overhanging."""
    return (lower.bounds.width >= upper.bounds.width
            and lower.bounds.depth >= upper.bounds.depth)


def nesting_groups(plates: tuple[Plate, ...]) -> tuple[tuple[Plate, ...], ...]:
    """Split into the fewest stacks in which every plate rests fully on the one below.

    A ledge forces the slicer to build a tall, thin freestanding wall from
    whatever is beneath it, which is the least printable thing in the whole
    arrangement. Ordering alone cannot always avoid one: containment is a partial
    order, and a set with incomparable plates has no single chain. The fewest
    chains covering a poset is Dilworth's theorem, computed here as a maximum
    bipartite matching -- each matched pair becomes an adjacency in some chain.
    """
    order = sorted(range(len(plates)), key=lambda i: -plates[i].bounds.footprint)
    match: dict[int, int] = {}          # upper -> lower

    def augment(lower: int, seen: set[int]) -> bool:
        for upper in order:
            if upper == lower or upper in seen:
                continue
            if not contains(plates[lower], plates[upper]):
                continue
            seen.add(upper)
            if upper not in match or augment(match[upper], seen):
                match[upper] = lower
                return True
        return False

    for lower in order:
        augment(lower, set())

    nxt = {lo: up for up, lo in match.items()}
    heads = [i for i in order if i not in match]
    chains = []
    for head in heads:
        chain, cur = [], head
        while cur is not None:
            chain.append(plates[cur])
            cur = nxt.get(cur)
        chains.append(tuple(chain))
    return tuple(chains)


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


def ledge_regions(placements: tuple[Placement, ...], gap: float
                  ) -> tuple[tuple[int, float, float, float, float, float], ...]:
    """(plate index, x0, y0, x1, y1, base_z) for each area a plate hangs over nothing.

    `base_z` is the top of the highest plate underneath that area, or 0 for the
    bed. Rectangles merge only within one plate at one base height, never across
    either -- merging across levels produces a block that runs through a plate.
    """
    rects = [stl_bounds(pl) for pl in placements]
    xs = sorted({v for r in rects for v in (r[0], r[2])})
    ys = sorted({v for r in rects for v in (r[1], r[3])})
    out = []
    for i, pl in enumerate(placements):
        if i == 0:
            continue
        x0, y0, x1, y1 = rects[i]
        by_base: dict[float, list[tuple[float, float, float, float]]] = {}
        for xa, xb in zip(xs, xs[1:]):
            if xb - xa < 1e-6 or xa < x0 - 1e-9 or xb > x1 + 1e-9:
                continue
            for ya, yb in zip(ys, ys[1:]):
                if yb - ya < 1e-6 or ya < y0 - 1e-9 or yb > y1 + 1e-9:
                    continue
                cx, cy = (xa + xb) / 2, (ya + yb) / 2
                tops = [placements[j].z1 for j in range(i)
                        if rects[j][0] <= cx <= rects[j][2]
                        and rects[j][1] <= cy <= rects[j][3]]
                base = max(tops) if tops else 0.0
                if pl.z0 - base > gap + 1e-6:
                    by_base.setdefault(round(base, 6), []).append((xa, ya, xb, yb))
        for base, boxes in by_base.items():
            rows: dict[tuple[float, float], list[list[float]]] = {}
            for xa, ya, xb, yb in sorted(boxes):
                spans = rows.setdefault((ya, yb), [])
                if spans and xa <= spans[-1][1] + 1e-9:
                    spans[-1][1] = max(spans[-1][1], xb)
                else:
                    spans.append([xa, xb])
            for (ya, yb), spans in rows.items():
                for xa, xb in spans:
                    out.append((i, xa, ya, xb, yb, base))
    return tuple(out)


def face_grid(mesh: Mesh, z: float, x0: float, y0: float, w: int, h: int,
              step: float) -> list[bytearray]:
    """Occupancy of a horizontal slice, one row of cells per Y sample.

    Crossings along a +X ray alternate entering and leaving material, so the
    solid stretches are the even-indexed pairs and each one can be filled as a
    slice of the row. Testing every cell against every crossing instead is what
    the ledge code used to do, and it is quadratic in the sampling resolution
    for no gain.
    """
    rows = [bytearray(w) for _ in range(h)]
    for iy in range(h):
        cr = gf._ray_crossings(mesh, y0 + (iy + 0.5) * step, z)
        row = rows[iy]
        for k in range(0, len(cr) - 1, 2):
            a = max(0, int(math.floor((cr[k] - x0) / step - 0.5)) + 1)
            b = min(w, int(math.ceil((cr[k + 1] - x0) / step - 0.5)))
            if b > a:
                row[a:b] = b"\x01" * (b - a)
    return rows


def disc(radius_cells: float) -> tuple[tuple[int, int], ...]:
    """Offsets within a disc. A square would square off the socket's round corners."""
    r = int(math.ceil(radius_cells - 1e-9))
    return tuple((dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                 if dx * dx + dy * dy <= radius_cells * radius_cells)


def dilate(rows: list[bytearray], radius_cells: float, w: int, h: int
           ) -> list[bytearray]:
    if radius_cells < 1e-9:
        return rows
    off = disc(radius_cells)
    out = [bytearray(w) for _ in range(h)]
    for iy in range(h):
        row = rows[iy]
        for ix in range(w):
            if not row[ix]:
                continue
            for dx, dy in off:
                ny, nx = iy + dy, ix + dx
                if 0 <= ny < h and 0 <= nx < w:
                    out[ny][nx] = 1
    return out


def grid_rects(rows: list[bytearray], x0: float, y0: float, w: int, h: int,
               step: float) -> tuple[tuple[float, float, float, float], ...]:
    """Maximal horizontal runs, merged across rows that share one."""
    out = []
    open_runs: dict[tuple[int, int], list[int]] = {}
    for iy in range(h):
        row = rows[iy]
        runs, start = [], None
        for ix in range(w):
            if row[ix] and start is None:
                start = ix
            elif not row[ix] and start is not None:
                runs.append((start, ix)); start = None
        if start is not None:
            runs.append((start, w))
        seen = set(runs)
        for run in runs:
            if run in open_runs:
                open_runs[run][1] = iy + 1
            else:
                open_runs[run] = [iy, iy + 1]
        for run in [k for k in open_runs if k not in seen]:
            a, b = open_runs.pop(run)
            out.append((x0 + run[0] * step, y0 + a * step,
                        x0 + run[1] * step, y0 + b * step))
    for run, (a, b) in open_runs.items():
        out.append((x0 + run[0] * step, y0 + a * step,
                    x0 + run[1] * step, y0 + b * step))
    return tuple(out)


def support_fillers(placements: tuple[Placement, ...], gap: float,
                    grow: float = 0.0, step: float = 0.3) -> Mesh:
    """Blocks standing in wherever a plate has material and nothing beneath it.

    This asks the question directly rather than comparing footprints. Comparing
    footprints only finds a plate hanging past the edge of the one below, and
    misses the case that actually bites: a plate whose solid border lands over a
    lower plate's socket opening. Plates in a set have different cell counts, so
    once the lattices are registered a narrower plate's frame can sit squarely
    over a wider plate's shaft -- and since those shafts are through-holes, that
    material may have nothing under it the whole way to the bed.

    It matters more than it used to. While the slicer still generated support it
    quietly caught these; with the whole model under a blocker (see make3mf.py)
    nothing does.

    Each level is checked in turn going down, so a block is emitted at every
    level a column has to pass through, and the descent stops as soon as
    something solid appears underneath.
    """
    meshes = {i: pl.placed_mesh() for i, pl in enumerate(placements)}
    b = bounds_of(tuple(f for m in meshes.values() for f in m))
    x0, y0 = b.x0, b.y0
    w = max(1, int(round(b.width / step)))
    h = max(1, int(round(b.depth / step)))

    # A plate blocks a filler anywhere it has material at that level, and the
    # socket tapers, so both faces are taken: the narrower opening wins.
    solid_at: dict[int, list[bytearray]] = {}
    def material(i: int) -> list[bytearray]:
        if i not in solid_at:
            pl, m = placements[i], meshes[i]
            up = face_grid(m, pl.z1 - SKIN, x0, y0, w, h, step)
            dn = face_grid(m, pl.z0 + SKIN, x0, y0, w, h, step)
            solid_at[i] = [bytearray(a | c for a, c in zip(r1, r2))
                           for r1, r2 in zip(up, dn)]
        return solid_at[i]

    tops: dict[int, list[bytearray]] = {}
    def top(i: int) -> list[bytearray]:
        if i not in tops:
            tops[i] = face_grid(meshes[i], placements[i].z1 - SKIN,
                                x0, y0, w, h, step)
        return tops[i]

    out: list = []
    for i in range(1, len(placements)):
        # Not dilated. Growing the region outward was right when it was a ledge
        # projection whose thinnest webs the slicer would drop, but here it walks
        # the region off the edge of the plate and a millimetre into every shaft,
        # putting blocks in mid-air where nothing is overhead. What needs
        # carrying is where the plate actually has material.
        need = face_grid(meshes[i], placements[i].z0 + SKIN, x0, y0, w, h, step)
        for j in range(i - 1, -1, -1):
            supported = top(j)
            blocked = dilate(material(j), gap / step, w, h)
            still = [bytearray(n & ~s & ~bl for n, s, bl in zip(rn, rs, rb))
                     for rn, rs, rb in zip(need, supported, blocked)]
            if not any(any(r) for r in still):
                break
            lo, hi = placements[j].z0, placements[j].z1
            for rx0, ry0, rx1, ry1 in grid_rects(still, x0, y0, w, h, step):
                out.extend(box(rx0, ry0, lo, rx1, ry1, hi))
            need = still
    return tuple(out)


def solid_spans(pl: Placement, x0: float, y0: float, x1: float, y1: float,
                step: float = 0.15, grow: float = 0.5
                ) -> tuple[tuple[float, float, float, float], ...]:
    """Rectangles covering this plate's own footprint inside the given area.

    Projected from the plate's bottom face, so the cell openings stay empty --
    a solid slab would cost three times the material and pay for top and bottom
    shells over the whole area.

    Taken from the face directly above the filler, traced at `step`.

    Not the plate's widest section: the socket tapers, and projecting the wide end
    makes a filler broader than both the face it carries and the face it stands
    on, so its own footprint then needs bridging support underneath.

    `grow` dilates the result outward by that many mm, using a disc so the socket's
    rounded corners stay round. It defaults to 0.5, roughly two extra perimeters:
    a faithful projection reproduces the plate's thinnest webs exactly, and the
    slicer drops the thinnest of them entirely. Much beyond this the webs start
    doubling and the corners fill in.
    """
    mesh = pl.placed_mesh()
    z = pl.z0 + 0.01
    w = max(1, int(round((x1 - x0) / step)))
    h = max(1, int(round((y1 - y0) / step)))
    grid = [[False] * h for _ in range(w)]
    for iy in range(h):
        py = y0 + (iy + 0.5) * step
        crossings = gf._ray_crossings(mesh, py, z)
        for ix in range(w):
            px = x0 + (ix + 0.5) * step
            grid[ix][iy] = sum(1 for c in crossings if c > px) % 2 == 1

    rr = grow / step
    r = int(math.ceil(rr - 1e-9))
    if r:
        # A disc, not a square: dilating with a square offsets corners by r in
        # each axis at once, squaring off the socket's rounded corners. A disc
        # offsets every direction equally, which is what an outward offset means
        # and what keeps the arcs.
        # radius from `grow` directly, not the rounded cell count, so the offset
        # is the millimetres asked for whatever the sampling step
        disc = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                if dx * dx + dy * dy <= rr * rr]
        grown = [[False] * h for _ in range(w)]
        for ix in range(w):
            for iy in range(h):
                if not grid[ix][iy]:
                    continue
                for dx, dy in disc:
                    nx, ny = ix + dx, iy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        grown[nx][ny] = True
        grid = grown

    # maximal horizontal runs, then merge rows that share a run
    out: list[tuple[float, float, float, float]] = []
    open_runs: dict[tuple[int, int], list[int]] = {}
    for iy in range(h):
        runs = []
        start_ix = None
        for ix in range(w):
            if grid[ix][iy] and start_ix is None:
                start_ix = ix
            elif not grid[ix][iy] and start_ix is not None:
                runs.append((start_ix, ix)); start_ix = None
        if start_ix is not None:
            runs.append((start_ix, w))
        seen = set(runs)
        for run in runs:
            if run in open_runs:
                open_runs[run][1] = iy + 1
            else:
                open_runs[run] = [iy, iy + 1]
        for run in [k for k in open_runs if k not in seen]:
            a, b = open_runs.pop(run)
            out.append((x0 + run[0] * step, y0 + a * step,
                        x0 + run[1] * step, y0 + b * step))
    for run, (a, b) in open_runs.items():
        out.append((x0 + run[0] * step, y0 + a * step,
                    x0 + run[1] * step, y0 + b * step))
    return tuple(out)


def ledge_fillers(placements: tuple[Placement, ...], gap: float,
                  grow: float = 0.5, step: float = 0.15) -> Mesh:
    """Loose blocks filling each ledge void, one per plate level it spans.

    A ledge otherwise leaves the slicer to raise a tall thin fin under the
    overhanging rib, which is the least printable thing in the arrangement. These
    stand in for the plates missing under that area: one block per level, taking
    that plate's own z range and inset by `gap` in XY so it touches nothing
    sideways. The stack's gaps already separate the levels vertically, so each
    block ends up clear on all six sides and lifts out with the support.
    """
    mesh: list = []
    for i, x0, y0, x1, y1, base in ledge_regions(placements, gap):
        if x1 - x0 <= 2 * gap or y1 - y0 <= 2 * gap:
            continue
        spans = solid_spans(placements[i], x0 + gap, y0 + gap, x1 - gap, y1 - gap,
                            step=step, grow=grow)
        # One block per plate level, aligned exactly to that level's z range, so
        # the columns take the same gaps the plates do and print on the interface
        # below them like everything else.
        for level in placements[:i]:
            if level.z0 < base - 1e-9:
                continue
            for sx0, sy0, sx1, sy1 in spans:
                if sx1 - sx0 < 1e-9 or sy1 - sy0 < 1e-9:
                    continue
                mesh.extend(box(sx0, sy0, level.z0, sx1, sy1, level.z1))
    return tuple(mesh)


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
    levels = gf.z_levels(plate.mesh)
    radius = {z: max(w for _, w in prof)
              for z, prof in zip(plate.section_zs, plate.profiles)}

    def nearest(z):
        return radius[min(radius, key=lambda s: abs(s - z))]

    # two samples per level interval, just inside each end -- the sections in
    # between are for the blockers and would confuse the taper measurement
    radii = tuple(v for lo, hi in zip(levels, levels[1:])
                  for v in (nearest(lo + SKIN), nearest(hi - SKIN)))
    return overhang_from_sections(levels, radii)


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


BBOX_PIN = 0.1      # mm; large enough that the slicer keeps it, small enough to ignore


def match_bbox(mesh: Mesh, target: Bounds) -> Mesh:
    """Pad a blocker mesh so its bounding box matches the model's.

    Bambu Studio centres a loaded part on the object it joins, so a part whose
    bbox centre differs lands that far out and silently does the wrong thing.

    All three axes, Z included. Pinning only X and Y is enough for a part that
    already spans the model vertically, and wrong for one that does not -- a set
    of thin slabs ending at the last plate's bottom face has its centre 2 mm low,
    and every slab arrives 2 mm off its target layer.
    """
    return (mesh
            + box(target.x0, target.y0, target.z0,
                  target.x0 + BBOX_PIN, target.y0 + BBOX_PIN, target.z0 + BBOX_PIN)
            + box(target.x1 - BBOX_PIN, target.y1 - BBOX_PIN, target.z1 - BBOX_PIN,
                  target.x1, target.y1, target.z1))


def make_enforcers(placements: tuple[Placement, ...], layer: float = 0.2,
                   shrink: float = 0.4, step: float = 0.5) -> Mesh:
    """One box over the whole stack, to be loaded as a support *enforcer*.

    Paired with support type "normal(manual)", which skips automatic overhang
    detection entirely (SupportMaterial.cpp gates it on
    `auto_normal_support = support_type == stNormalAuto`), so contacts come from
    enforcers alone.

    An enforcer's contact is computed as

        diff(intersection(layer.lslices, enforcer), expand(lower_layer_polygons))

    -- model material at this layer with nothing under it. So the enforcer must be
    a thin slab over each plate's *first layer* only. A box enclosing the whole
    stack instead reproduces every overhang in the model at a 90 degree
    threshold, which is more support than automatic detection gives, not less.

    The socket walls are 45 degrees and carry themselves, so dropping automatic
    detection costs nothing and takes every balcony with it.
    """
    b = bounds_of(tuple(f for pl in placements for f in pl.placed_mesh()))
    mesh: list = []
    for lower, upper in interfaces(placements):
        # Trim to where the upper plate actually rests on the lower one. A contact
        # over solid ground terminates as a bottom contact; one hanging past it is
        # projected downward and becomes a balcony. Interfaces here are always
        # land-to-land or rib-to-rib on a registered lattice, so the upper plate's
        # own down face, clipped to the lower plate's footprint, is that region.
        lo = stl_bounds(lower)
        up = stl_bounds(upper)
        x0, y0 = max(lo[0], up[0]) + shrink, max(lo[1], up[1]) + shrink
        x1, y1 = min(lo[2], up[2]) - shrink, min(lo[3], up[3]) - shrink
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        for sx0, sy0, sx1, sy1 in solid_spans(upper, x0, y0, x1, y1,
                                              step=step, grow=-shrink):
            if sx1 - sx0 < 1e-9 or sy1 - sy0 < 1e-9:
                continue
            mesh.extend(box(sx0, sy0, upper.z0 - layer / 2,
                            sx1, sy1, upper.z0 + layer / 2))
    # The slabs stop at the last plate's bottom, so their bounding box centre sits
    # below the model's, and Bambu -- which centres a loaded part on the object --
    # would shift every slab off its first layer.
    return match_bbox(tuple(mesh), b)


def make_blockers(placements: tuple[Placement, ...], gap: float,
                  layer: float = 0.2, margin: float = 2.0) -> Mesh:
    """One solid slab per plate: its whole footprint, inset in Z from both faces.

    Everything a plate needs support for is in the gaps around it, not inside its
    own height -- the socket walls are 45 degrees and carry themselves. So the
    blocker does not need to trace sockets at all: a slab spanning a plate's
    thickness, held `inset` clear of each face, deletes every balcony and every
    column inside that plate and leaves the gap interfaces alone.

    Tracing the socket surface instead is the thing that kept leaking: support
    collects in the four-way rib junctions between cells, which are outside any
    socket outline however finely it is traced.

    Each slab runs from one layer above its plate's bottom face to its top face.
    The offset at the bottom is not slop, it is required, and the reason is in
    the slicer: SupportMaterial.cpp subtracts blockers from the *overhang*
    polygons at the layer where the overhang is detected --

        auto blocker = expand(union_(annotations.blockers_layers[layer_id]), ...);
        diff_polygons = diff(diff_polygons, blocker);

    -- and the support for an overhang is laid down in the layers *below* it. The
    interface carrying a plate is generated from the overhang detected at that
    plate's first layer. A slab starting at the plate's bottom face covers that
    layer and deletes the interface; starting one layer higher leaves it, while
    still blocking every overhang inside the plate's own body.
    """
    if layer <= 0:
        raise ValueError("blocker layer offset must be positive: at zero the slab "
                         "covers the plate's first layer, whose overhang generates "
                         "the interface below it")
    b = bounds_of(tuple(f for pl in placements for f in pl.placed_mesh()))
    mesh: list = []
    for pl in placements:
        lo, hi = pl.z0 + layer, pl.z1
        if hi - lo < layer:
            continue
        mesh.extend(box(b.x0 - margin, b.y0 - margin, lo,
                        b.x1 + margin, b.y1 + margin, hi))
    return match_bbox(tuple(mesh), b)


def decoy_column(placements: tuple[Placement, ...], size: float = 15.0) -> Mesh:
    """A miniature of the stack's z profile, to stand beside it on the plate.

    The real stack has all of its support blocked, because we lay the interface
    down ourselves. But the interface is a second filament, and the slicer only
    changes filament for something it knows about -- so this column exists purely
    to be supported. Its slabs sit at exactly the stack's plate heights, so the
    gaps the slicer fills fall on exactly the stack's gap layers, and the
    interface material is in the nozzle at the moment we need it.

    Flat, fully-overhanging faces at every level: nothing subtle for overhang
    detection to decide, and far too big for remove-small-overhang to discard.
    """
    h = size / 2
    return tuple(f for pl in placements
                 for f in box(-h, -h, pl.z0, h, h, pl.z1))


def full_blocker(placements: tuple[Placement, ...], margin: float = 1.0) -> Mesh:
    """One box over the whole stack: no slicer support on the model at all.

    A single slab covering everything is the one blocker arrangement measured to
    work exactly as documented (ADR 0003) -- per-plate slabs and traced socket
    profiles both leaked. Here we want the blunt version: nothing generated
    anywhere, because every gap gets its interface from us instead.
    """
    b = bounds_of(tuple(f for pl in placements for f in pl.placed_mesh()))
    return box(b.x0 - margin, b.y0 - margin, b.z0 - margin,
               b.x1 + margin, b.y1 + margin, b.z1 + margin)


def plates_json(placements: tuple[Placement, ...], mesh: Mesh, gap: float,
                layer_height: float) -> dict:
    """Where every plate ended up, for the G-code post-processor.

    A balcony is support inside a plate's own footprint within its own height.
    Legitimate support at those same heights -- the column under a plate that
    overhangs from above -- is *outside* that footprint. So one box per plate is
    the whole discriminator, and it is the same reasoning make_blockers uses,
    just applied where the slicer does not get a vote.

    The overall bbox is the mesh as written, fillers included: postprocess.py
    matches it against the sliced outer walls to recover where the slicer put
    the object on the bed, so it has to describe the same solid.
    """
    b = bounds_of(mesh)
    return {
        "version": 1,
        "gap_mm": round(gap, 4),
        "layer_height": round(layer_height, 4),
        "bbox": {k: round(getattr(b, k), 4)
                 for k in ("x0", "x1", "y0", "y1", "z0", "z1")},
        "plates": [
            {"index": i,
             "size": f"{pb.width:.0f}x{pb.depth:.0f}",
             "flipped": pl.flipped,
             **{k: round(getattr(pb, k), 4)
                for k in ("x0", "x1", "y0", "y1", "z0", "z1")}}
            for i, pl in enumerate(placements, 1)
            for pb in (bounds_of(pl.placed_mesh()),)
        ],
    }


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
| Normal Support expansion | **-0.25 mm** | contracts the support region, removing the ribbons and nubs that print inside the open cells. Verified end to end: 4.93 h and 6.7 g, with all five gaps carried (thinnest 0.85 of a solid layer). See the curve below -- -0.3 is a cliff |
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

  -0.25 is the setting to use and is what the table above recommends; -0.2 is the
  cautious one. Past -0.25 the gap interfaces start losing the lands they have to
  carry, so stop there. At -0.25 the thinnest gap is always the topmost
  land-to-land joint, which has the smallest contact area of the five.
- Two things cut it and simply break the gaps. Do not use them: "don't support
  bridges" (coverage 0.93x -> 0.40x), and tree support in any style, which either
  fills the cells with branches (97 g) or supports the gaps not at all.
- A thin ribbon of support following the socket walls survives everything. It is
  how the slicer descends from the gap interface above; roughly 3.5 g on a
  six-plate stack. Left alone, it is loose in an open cell and falls out.
- Do **not** enable "independent support layer height".

## Removing the balconies (optional)

That ribbon of support along the socket walls is the one thing no setting reaches.
It is the downward projection of interface that overhangs the rib it lands on --
contacts snap to a grid of about 2.9 mm, the land under them is {land:.2f} mm --
and the descent in SupportMaterial.cpp is unconditional. Every setting that looks
like it should stop it was measured and does not: blockers, enforcers, threshold
angle, remove-small-overhangs, base pattern spacing, grid alignment, tree support,
and OrcaSlicer. Pushing support/object XY distance high does clear it, but takes
the legitimate support under the ledges with it.

So delete the toolpaths after slicing instead, where the slicer gets no vote.
Install it into the saved project once, and the 3mf carries it from then on:

```
{postprocess} --install PROJECT.3mf
```

This writes the script itself into the project's post-processing setting, so
nothing has to stay on disk and no path points anywhere machine-specific -- the
3mf keeps working when it is moved or shared. Re-run it whenever the stack is
regenerated, since the plate positions are baked into what it writes.

To check the result, export the G-code and open it; Bambu Studio reads `.gcode`
directly. The preview before export still shows the balconies, because
post-processing runs at export.

It strips support extrusions inside each plate's own footprint and height, which
is exactly the balconies -- the ledge columns stand *outside* those footprints and
survive, and `Support interface` is never touched. It is safe because Bambu emits
M83: extrusion is relative, so dropping an E word changes nothing downstream.

It refuses rather than guesses: if the object on the bed does not match the stack
this `plates.json` describes -- rotated, rescaled, or simply a different model --
it exits without touching the file.

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


def stable_python() -> str:
    """An absolute interpreter path that will still be there next month.

    The line goes in a slicer settings box and is not regenerated when the box is
    not, so a versioned Homebrew path (.../python@3.14/bin/python3.14) is the
    wrong thing to write down: an upgrade moves it. /usr/bin/python3 does not
    move, and postprocess.py is stdlib-only and runs on the 3.9 that ships there.
    """
    system = Path("/usr/bin/python3")
    return str(system) if system.exists() else sys.executable


def write_printing_notes(path: Path, placements, gap, layer, report_text,
                         stl_name, blocker_name, source, interface="same",
                         blockers=False, plates_path: Path | None = None) -> None:
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
        postprocess=(f"{stable_python()} "
                     f"{Path(__file__).resolve().parent / 'postprocess.py'} "
                     f"{plates_path.resolve()}" if plates_path else
                     "/abs/path/python3 /abs/path/postprocess.py "
                     "/abs/path/NAME.plates.json"),
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

    ap.add_argument("--enforcers", action="store_true",
                    help="emit a support enforcer box. Load it as a support "
                         "enforcer and set support type to normal(manual): "
                         "contacts then come only from it, which is exactly the "
                         "plate interfaces, with no in-plate support at all")
    ap.add_argument("--blockers", action="store_true",
                    help="also emit support blockers. Measured to make no "
                         "difference with snug support; kept for grid, or for "
                         "plate profiles this tool has not seen")
    ap.add_argument("--no-fillers", action="store_true",
                    help="omit the loose blocks that stand in under a ledge")
    ap.add_argument("--filler-step", type=float, default=0.3,
                    help="resolution the filler outline is traced at, mm. Finer is "
                         "smoother and slower (default 0.15, below what a 0.4 mm "
                         "nozzle can render)")
    ap.add_argument("--filler-grow", type=float, default=0.5,
                    help="dilate the filler footprint outward by this much, mm "
                         "(default 0.5, about two perimeters). A faithful "
                         "projection reproduces webs the slicer then drops as too "
                         "thin; much more than this doubles them")
    ap.add_argument("--decoy", action="store_true",
                    help="also emit a decoy column and a whole-model support "
                         "blocker: the column is supported so the slicer changes "
                         "to the interface filament at each gap layer, the "
                         "blocker keeps that support off the stack itself")
    ap.add_argument("--decoy-size", type=float, default=15.0,
                    help="side of the decoy column, mm (default 15)")
    ap.add_argument("--split", action="store_true",
                    help="emit several stacks, each one in which every plate rests "
                         "fully on the one below. Avoids the tall thin support wall "
                         "a ledge forces, at the cost of one print job per stack")
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

    groups = nesting_groups(plates) if args.split else (plates,)
    if args.split:
        print(f"split into {len(groups)} stack(s) so no plate overhangs the one below")
    stem = args.name or f"gf-stack-{len(plates)}"

    for i, group in enumerate(groups, 1):
        placements = plan(group, gap, flip=not args.no_flip,
                          register=not args.no_register, order=args.order)
        report_text = report(placements, gap, bed)
        name = stem if len(groups) == 1 else f"{stem}-{i}of{len(groups)}"
        print()
        if len(groups) > 1:
            print(f"--- stack {i} of {len(groups)} ---")
        print(report_text)

        out = args.out_dir
        body = tuple(f for pl in placements for f in pl.placed_mesh())
        fillers = (() if args.no_fillers
                   else support_fillers(placements, gap, args.filler_grow,
                                        args.filler_step))
        stl_path = out / f"{name}.stl"
        write_stl(stl_path, body + fillers,
                  f"gridfinity stack of {len(placements)} plates")
        print(f"\nwrote {stl_path}"
              + (f" (with {len(split_shells(fillers))} ledge filler blocks)"
                 if fillers else ""))

        plates_path = out / f"{name}.plates.json"
        plates_path.write_text(json.dumps(
            plates_json(placements, body + fillers, gap, args.layer_height),
            indent=2) + "\n")
        print(f"wrote {plates_path}")

        blocker_path = out / f"{name}-blockers.stl"
        if args.blockers:
            blockers = make_blockers(placements, gap, args.layer_height)
            write_stl(blocker_path, blockers, "support blockers")
            print(f"wrote {blocker_path} ({len(blockers)} facets)")

        if args.enforcers:
            enf = out / f"{name}-enforcers.stl"
            write_stl(enf, make_enforcers(placements, args.layer_height),
                      "support enforcer")
            print(f"wrote {enf}")

        if args.decoy:
            dpath = out / f"{name}-decoy.stl"
            write_stl(dpath, decoy_column(placements, args.decoy_size),
                      "decoy column, supported so the filament changes")
            bpath = out / f"{name}-noSupport.stl"
            write_stl(bpath, full_blocker(placements), "whole-model support blocker")
            print(f"wrote {dpath} ({args.decoy_size:g} mm square, "
                  f"{len(placements)} slabs)")
            print(f"wrote {bpath} (load as a support blocker on the stack)")
            print("  gap layers the decoy forces a filament change at: "
                  + ", ".join(f"{pl.z0:.2f}" for pl in placements[1:]))

        notes = out / f"{name}-PRINTING.md"
        write_printing_notes(notes, placements, gap, args.layer_height, report_text,
                             stl_path.name, blocker_path.name, args.stl.name,
                             interface=args.interface, blockers=args.blockers,
                             plates_path=plates_path)
        print(f"wrote {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
