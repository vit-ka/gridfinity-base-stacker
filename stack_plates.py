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
from stl_io import (Bounds, Mesh, bounds_of, box, read_stl, rotate_x180,
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


def face_grid(mesh: Mesh, z: float, x0: float, y0: float, w: int, h: int,
              step: float) -> list[int]:
    """Occupancy of a horizontal slice, one integer per Y sample.

    A row is a bitmask, one bit per cell. Everything downstream is then a
    bitwise operation on a w-bit integer, which Python does in C: dilating a
    1173 x 1387 grid by a disc costs a few thousand integer operations instead of
    fifty million element-at-a-time ones.

    Crossings along a +X ray alternate entering and leaving material, so the
    solid stretches are the even-indexed pairs, and each is a contiguous run of
    bits.
    """
    rows = [0] * h
    for iy in range(h):
        cr = gf._ray_crossings(mesh, y0 + (iy + 0.5) * step, z)
        m = 0
        for k in range(0, len(cr) - 1, 2):
            a = max(0, int(math.floor((cr[k] - x0) / step - 0.5)) + 1)
            b = min(w, int(math.ceil((cr[k + 1] - x0) / step - 0.5)))
            if b > a:
                m |= ((1 << (b - a)) - 1) << a
        rows[iy] = m
    return rows


def disc(radius_cells: float) -> tuple[tuple[int, int], ...]:
    """Offsets within a disc of this many cells.

    A disc rather than a square: a square offsets corners by r on both axes at
    once, squaring off the sockets' rounded corners.

    A radius under one cell yields the centre alone, which is no dilation. That
    is right for thickening a projection -- there is nothing to add -- and wrong
    for clearance, where it silently means no clearance at all. Callers wanting
    clearance must round the radius out to at least one whole cell of *reach*:
    disc(1.83) stops at one cell, because two cells away is a distance of two.
    """
    r = int(math.ceil(radius_cells - 1e-9))
    return tuple((dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                 if dx * dx + dy * dy <= radius_cells * radius_cells)


def _reach(radius_cells: float) -> dict[int, int]:
    """How far a disc of this radius extends in X, for each Y offset."""
    r = int(math.ceil(radius_cells - 1e-9))
    out = {}
    for dy in range(-r, r + 1):
        t = radius_cells * radius_cells - dy * dy
        if t >= 0:
            out[dy] = int(math.floor(math.sqrt(t)))
    return out


def dilate(rows: list[int], radius_cells: float, w: int, h: int) -> list[int]:
    if radius_cells < 1e-9:
        return list(rows)
    full = (1 << w) - 1
    reach = _reach(radius_cells)
    widened: dict[int, list[int]] = {}
    for k in set(reach.values()):
        if k == 0:
            widened[k] = rows
            continue
        acc = []
        for m in rows:
            e = m
            for i in range(1, k + 1):
                e |= (m << i) | (m >> i)
            acc.append(e & full)
        widened[k] = acc
    out = [0] * h
    for dy, k in reach.items():
        src = widened[k]
        lo, hi = max(0, dy), min(h, h + dy)
        for iy in range(lo, hi):
            out[iy] |= src[iy - dy]
    return out


def erode(rows: list[int], radius_cells: float, w: int, h: int) -> list[int]:
    """Dilation of the complement: shrinks a region by the same disc."""
    full = (1 << w) - 1
    grown = dilate([full & ~m for m in rows], radius_cells, w, h)
    return [full & ~m for m in grown]


def opened(rows: list[int], radius_cells: float, w: int, h: int) -> list[int]:
    """Erode then dilate: drops anything thinner than the disc, keeps the rest.

    Applied to the region, never to the rectangles it decomposes into.
    grid_rects merges only rows whose runs match exactly, so a wide region with a
    curved edge comes apart into many one-row strips -- and a minimum size
    applied to those deletes real support. Measured: plate 5's north border came
    out as a 34.20 x 0.30 mm strip and was discarded whole, leaving the corner
    hanging in air.
    """
    if radius_cells < 1e-9:
        return list(rows)
    return dilate(erode(rows, radius_cells, w, h), radius_cells, w, h)


def closed(rows: list[int], radius_cells: float, w: int, h: int) -> list[int]:
    """Dilate then erode: fills any gap too narrow to hold the disc.

    With a radius of half the span, the threshold falls straight out -- an empty
    stretch narrower than the span closes, a wider one is left exactly as it was.
    Each side of a region closes or not on its own local width, and the disc
    makes that judgement isotropic rather than privileging the axes.
    """
    if radius_cells < 1e-9:
        return list(rows)
    return erode(dilate(rows, radius_cells, w, h), radius_cells, w, h)


def grid_rects(rows: list[int], x0: float, y0: float, w: int, h: int,
               step: float, inset: float = 0.0
               ) -> tuple[tuple[float, float, float, float], ...]:
    """Maximal horizontal runs, merged across rows that share one.

    With `inset` the rectangle is pulled in by that much on every side. Half a
    step pulls it back to the outermost cell *centres*, which is the only part of
    the cell the occupancy test vouches for. Beware that inset rectangles no
    longer share edges, so a region that should be one solid comes apart into
    loose pieces; paying for clearance in the dilation instead keeps it whole.
    """
    out = []
    open_runs: dict[tuple[int, int], list[int]] = {}
    for iy in range(h):
        m = rows[iy]
        runs = []
        while m:
            lo = (m & -m).bit_length() - 1
            nt = ~(m >> lo)
            span = (nt & -nt).bit_length() - 1
            runs.append((lo, lo + span))
            m &= ~(((1 << span) - 1) << lo)
        seen = set(runs)
        for run in runs:
            if run in open_runs:
                open_runs[run][1] = iy + 1
            else:
                open_runs[run] = [iy, iy + 1]
        for run in [k for k in open_runs if k not in seen]:
            a, b = open_runs.pop(run)
            out.append((x0 + run[0] * step + inset, y0 + a * step + inset,
                        x0 + run[1] * step - inset, y0 + b * step - inset))
    for run, (a, b) in open_runs.items():
        out.append((x0 + run[0] * step + inset, y0 + a * step + inset,
                    x0 + run[1] * step - inset, y0 + b * step - inset))
    return tuple(out)


def contours(rows: list[int], w: int, h: int
              ) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Closed loops around every filled region, walking the cell boundaries.

    The alternative to decomposing a region into rectangles. Boxes cannot be made
    manifold by arranging them better: two that share only an edge put four
    facets on it, and two that share a whole face put four on five edges, so any
    touching pair fails. One region has to become one solid, and that starts with
    its outline.

    Loops are traced on the lattice of cell corners, so a loop is a sequence of
    integer points and the geometry is exact rather than fitted. Each unit edge
    between a filled cell and an empty one is walked once, keeping filled cells
    to the left, which yields outer boundaries counter-clockwise and holes
    clockwise -- the winding a prism needs to know which is which.
    """
    def filled(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and (rows[y] >> x) & 1

    # Directed boundary edges: for each filled cell, any side facing an empty
    # neighbour. Stored as start -> end so the walk is a lookup.
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for y in range(h):
        row = rows[y]
        if not row:
            continue
        for x in range(w):
            if not (row >> x) & 1:
                continue
            if not filled(x, y - 1):
                nxt.setdefault((x, y), []).append((x + 1, y))
            if not filled(x + 1, y):
                nxt.setdefault((x + 1, y), []).append((x + 1, y + 1))
            if not filled(x, y + 1):
                nxt.setdefault((x + 1, y + 1), []).append((x, y + 1))
            if not filled(x - 1, y):
                nxt.setdefault((x, y + 1), []).append((x, y))

    loops = []
    for start in list(nxt):
        while nxt.get(start):
            loop = [start]
            cur = nxt[start].pop()
            while cur != start:
                loop.append(cur)
                opts = nxt.get(cur)
                if not opts:
                    loop = None
                    break
                # At a pinch point four edges meet; take the one that turns most
                # sharply left, which keeps each loop simple instead of letting
                # two of them merge into a figure of eight.
                px, py = loop[-2]
                dx, dy = cur[0] - px, cur[1] - py
                opts.sort(key=lambda n: -(dx * (n[1] - cur[1]) - dy * (n[0] - cur[0])))
                cur = opts.pop(0)
            if loop:
                loops.append(tuple(simplify(loop)))
    return tuple(loops)


def simplify(loop: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop points that continue in the same direction."""
    out = []
    n = len(loop)
    for i, pt in enumerate(loop):
        a, b = loop[i - 1], loop[(i + 1) % n]
        if (pt[0] - a[0]) * (b[1] - pt[1]) != (pt[1] - a[1]) * (b[0] - pt[0]):
            out.append(pt)
    return out or loop


def area2(loop) -> int:
    """Twice the signed area: positive counter-clockwise, so outer, not a hole."""
    n = len(loop)
    return sum(loop[i][0] * loop[(i + 1) % n][1] - loop[(i + 1) % n][0] * loop[i][1]
               for i in range(n))


def _visible_vertex(ring: list, m: tuple[int, int]) -> int:
    """Index of a ring vertex mutually visible from `m`, looking along +x.

    Eberly's construction. Cast the ray, take the nearest edge it crosses, and
    consider the endpoint P of that edge with the greater x. P is visible unless
    some reflex vertex of the ring falls inside the triangle (m, hit, P) and
    blocks it; of those that do, the one subtending the smallest angle to the ray
    is visible.

    Choosing a nearby vertex by distance instead -- which is what this did before
    -- lets two bridges cross once a region has many holes. A self-intersecting
    ring has no valid ear anywhere, so the clipper stops early and leaves the cap
    with a hole in it: measured, 230 triangles short of 2,172 on one film region
    with 20 holes.
    """
    mx, my = m
    n = len(ring)
    best_x, best_i = None, None
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        if (ay > my) == (by > my):
            continue                      # edge does not straddle the ray
        t = ax + (my - ay) * (bx - ax) / (by - ay)
        if t < mx:
            continue                      # behind the hole
        if best_x is None or t < best_x:
            best_x, best_i = t, i
    if best_i is None:
        return 0

    a, b = ring[best_i], ring[(best_i + 1) % n]
    p_i = best_i if a[0] > b[0] else (best_i + 1) % n
    hit = (best_x, my)

    def cross(o, u, v):
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])

    def in_tri(q, t0, t1, t2):
        d1, d2, d3 = cross(t0, t1, q), cross(t1, t2, q), cross(t2, t0, q)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    tri = (m, hit, ring[p_i])
    blocking = []
    for i in range(n):
        if i == p_i:
            continue
        prv, cur, nxt = ring[i - 1], ring[i], ring[(i + 1) % n]
        if cross(prv, cur, nxt) >= 0:
            continue                      # convex, cannot block
        if in_tri(cur, *tri):
            blocking.append(i)
    if not blocking:
        return p_i
    # Smallest angle to the ray; nearer wins a tie.
    def key(i):
        dx, dy = ring[i][0] - mx, ring[i][1] - my
        return (abs(dy) / (dx if dx else 1e-9), dx * dx + dy * dy)
    return min(blocking, key=key)


def _bridge_holes(outer: list, holes: list) -> list:
    """Cut each hole into the outer loop, giving one simply-connected ring.

    Ear clipping cannot see a hole. The standard remedy is to slice a channel
    from the hole to the boundary and walk in and back out along it, which turns
    a ring with holes into a single loop tracing the same region. The seam is two
    coincident edges and vanishes once triangulated.

    Rightmost hole first, and each bridge is cut against the ring as it stands --
    holes already merged included -- so a later bridge cannot cross an earlier
    one.
    """
    ring = list(outer)
    for hole in sorted(holes, key=lambda hl: -max(p[0] for p in hl)):
        hi = max(range(len(hole)), key=lambda i: (hole[i][0], hole[i][1]))
        pick = _visible_vertex(ring, hole[hi])
        ring = ring[:pick + 1] + hole[hi:] + hole[:hi + 1] + ring[pick:]
    return ring


def _ear_clip(loop: list) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon by clipping ears. Indices into `loop`."""
    n = len(loop)
    idx = list(range(n))
    if area2(loop) < 0:
        idx.reverse()

    def cross(a, b, c):
        return ((loop[b][0] - loop[a][0]) * (loop[c][1] - loop[a][1])
                - (loop[b][1] - loop[a][1]) * (loop[c][0] - loop[a][0]))

    def inside(p, a, b, c):
        d1, d2, d3 = cross(a, b, p), cross(b, c, p), cross(c, a, p)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    tris = []
    while len(idx) > 3:
        cut = None
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(a, b, c) <= 0:
                continue                       # reflex, or degenerate
            # Compared by position, not by index. Bridging a hole duplicates two
            # vertices, and a duplicate sits exactly on the candidate ear's edge,
            # where the containment test says "inside" and blocks every ear in
            # the polygon.
            # Compared by position, not by index. Bridging a hole duplicates two
            # vertices, and a duplicate sits exactly on the candidate ear's edge,
            # where the containment test says "inside" and blocks every ear in
            # the polygon.
            if any(inside(p, a, b, c) for p in idx
                   if p not in (a, b, c)
                   and loop[p] not in (loop[a], loop[b], loop[c])):
                continue                       # another vertex is in the ear
            tris.append((a, b, c))
            cut = k
            break
        if cut is None:
            # No convex ear anywhere. A bridge seam leaves vertices whose
            # triangle has zero area -- the channel doubles back on itself -- and
            # no convex-ear test will ever accept one, so the clip stalls with
            # them still in the ring. They enclose nothing, so drop one without
            # emitting a triangle and carry on. Without this the largest film
            # region finished 106 triangles short of 2,172, and the shortfall is
            # a hole in the cap.
            for k in range(len(idx)):
                a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
                if cross(a, b, c) == 0:
                    cut = k
                    break
        if cut is None:
            break                              # genuinely stuck; leave the rest
        idx.pop(cut)
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def prism(loops: tuple, x0: float, y0: float, step: float,
          z0: float, z1: float) -> Mesh:
    """One closed solid from a region's outlines: walls, and a cap at each end.

    Loops arrive in cell coordinates; the caps are the triangulated footprint and
    the walls are a quad per boundary edge. Every edge then belongs to exactly two
    facets -- one cap triangle and one wall quad half, or two wall halves -- which
    is the whole point of building it this way rather than from boxes.
    """
    outer = [l for l in loops if area2(l) > 0]
    holes = [l for l in loops if area2(l) < 0]
    out: list = []
    for ring_outer in outer:
        mine = [h for h in holes if _contains(ring_outer, h[0])]
        ring = _bridge_holes(list(ring_outer), [list(h) for h in mine])
        pts = [(x0 + px * step, y0 + py * step) for px, py in ring]
        for a, b, c in _ear_clip(ring):
            out.append((0.0, 0.0, -1.0, *pts[a], z0, *pts[c], z0, *pts[b], z0))
            out.append((0.0, 0.0, 1.0, *pts[a], z1, *pts[b], z1, *pts[c], z1))
        for loop in [ring_outer] + mine:
            q = [(x0 + px * step, y0 + py * step) for px, py in loop]
            for i, (ax, ay) in enumerate(q):
                bx, by = q[(i + 1) % len(q)]
                out.append((0.0, 0.0, 0.0, ax, ay, z0, bx, by, z0, bx, by, z1))
                out.append((0.0, 0.0, 0.0, ax, ay, z0, bx, by, z1, ax, ay, z1))
    return tuple(out)


def _contains(loop, pt) -> bool:
    """Even-odd test of a point against a loop, in cell coordinates."""
    x, y = pt
    inside = False
    n = len(loop)
    for i in range(n):
        ax, ay = loop[i]
        bx, by = loop[(i + 1) % n]
        if (ay > y) != (by > y) and x < ax + (y - ay) / (by - ay) * (bx - ax):
            inside = not inside
    return inside


def components(rows: list[int], w: int, h: int) -> int:
    """How many disconnected regions a raster holds.

    The measurement the film's bridging rests on: an island of film on a pillar
    top is a region of its own, and it stays behind in the socket when the sheet
    is lifted. Counting them is how "the film comes off as one sheet" stops being
    a hope and becomes a number.

    Four-connected, matching how the film prints: two cells touching only at a
    corner are two regions, because a corner join is not a join a printed sheet
    survives being pulled by.
    """
    seen = [0] * h
    n = 0
    for y0 in range(h):
        while True:
            fresh = rows[y0] & ~seen[y0]
            if not fresh:
                break
            n += 1
            bit = fresh & -fresh
            stack = [(y0, bit.bit_length() - 1)]
            while stack:
                y, x = stack.pop()
                if not (rows[y] >> x) & 1 or (seen[y] >> x) & 1:
                    continue
                # Flood the whole horizontal run at once; the rows are integers,
                # so a run is a mask rather than a loop over cells.
                lo = x
                while lo > 0 and (rows[y] >> (lo - 1)) & 1 and not (seen[y] >> (lo - 1)) & 1:
                    lo -= 1
                hi = x
                while hi + 1 < w and (rows[y] >> (hi + 1)) & 1 and not (seen[y] >> (hi + 1)) & 1:
                    hi += 1
                seen[y] |= ((1 << (hi - lo + 1)) - 1) << lo
                for ny in (y - 1, y + 1):
                    if 0 <= ny < h:
                        run = rows[ny] & ~seen[ny] & (((1 << (hi - lo + 1)) - 1) << lo)
                        while run:
                            b = run & -run
                            stack.append((ny, b.bit_length() - 1))
                            run &= run - 1
    return n


def region_solid(rows: list[int], x0: float, y0: float, w: int, h: int,
                 step: float, z0: float, z1: float, weld: float = 0.001) -> Mesh:
    """One solid per connected region, checked, with a fallback that always holds.

    Tracing the outline gives what a pillar wants: one solid, following the
    socket's curve rather than stepping around it. It does not survive every
    shape. A region that touches itself corner to corner pinches, and the two
    loops meeting there put four facets on one edge -- the very defect this is
    meant to remove.

    So the traced solid is checked, and anything that fails falls back to boxes
    overlapping by `weld`. Overlapping solids share no edge and slicers union
    them regardless, so the manifold guarantee holds unconditionally rather than
    holding only for shapes the tracer happens to manage.
    """
    traced = prism(contours(rows, w, h), x0, y0, step, z0, z1)
    if traced and not _has_bad_edge(traced):
        return traced
    # Welded in x and y only. Pieces of one region meet each other sideways;
    # in z the region has a face the caller measures clearance against, and
    # overhanging it by even a micron makes that measurement a lie.
    return tuple(f for rx0, ry0, rx1, ry1 in grid_rects(rows, x0, y0, w, h, step)
                 for f in box(rx0 - weld, ry0 - weld, z0,
                              rx1 + weld, ry1 + weld, z1))


def _has_bad_edge(mesh: Mesh) -> bool:
    """Whether any edge is used by other than two facets."""
    used: dict[tuple, int] = {}
    for f in mesh:
        v = [(round(f[3 + k * 3], 5), round(f[4 + k * 3], 5), round(f[5 + k * 3], 5))
             for k in range(3)]
        for a, b in ((v[0], v[1]), (v[1], v[2]), (v[2], v[0])):
            e = (a, b) if a <= b else (b, a)
            used[e] = used.get(e, 0) + 1
    return any(n != 2 for n in used.values())


def support_regions(placements: tuple[Placement, ...], gap: float,
                    grow: float = 0.4, step: float = 0.15,
                    min_width: float = 0.42, margin: float = 8.0) -> tuple:
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
    # Room to dilate into. Sized to the stack exactly, the grid clips every
    # dilation at the model's own edge -- and a closing that is clipped on the
    # way out erodes back from the clipped edge rather than the real one, which
    # squares off the plate's rounded corners and leaves film hanging in the
    # wedge outside them. The margin only has to exceed the largest radius any
    # step uses; the cells cost little because the rows are integers.
    x0, y0 = b.x0 - margin, b.y0 - margin
    w = max(1, int(round((b.width + 2 * margin) / step)))
    h = max(1, int(round((b.depth + 2 * margin) / step)))

    # The margin is room for the morphology, not licence to grow into it. Every
    # result is clipped back to the stack's own extent, or a dilation that was
    # previously stopped by the grid's edge now runs on past the plate and puts
    # support in open air -- measured, the model grew from 176.0 x 208.0 to
    # 177.0 x 208.5. Clipping the result rather than the operands is what keeps
    # both properties: rounded corners survive a closing, and nothing escapes.
    ix0 = max(0, int(round(margin / step)))
    ix1 = min(w, ix0 + int(round(b.width / step)) + 1)
    iy0 = max(0, int(round(margin / step)))
    iy1 = min(h, iy0 + int(round(b.depth / step)) + 1)
    span = ((1 << (ix1 - ix0)) - 1) << ix0
    extent = [span if iy0 <= i < iy1 else 0 for i in range(h)]

    solid_at: dict[int, list[bytearray]] = {}
    def material(i: int) -> list[bytearray]:
        if i not in solid_at:
            pl, m = placements[i], meshes[i]
            up = face_grid(m, pl.z1 - SKIN, x0, y0, w, h, step)
            dn = face_grid(m, pl.z0 + SKIN, x0, y0, w, h, step)
            solid_at[i] = [a | c for a, c in zip(up, dn)]
        return solid_at[i]

    tops: dict[int, list[bytearray]] = {}
    def top(i: int) -> list[bytearray]:
        if i not in tops:
            tops[i] = face_grid(meshes[i], placements[i].z1 - SKIN,
                                x0, y0, w, h, step)
        return tops[i]

    keep_off: dict[int, list[int]] = {}
    def blocked(i: int) -> list[int]:
        if i not in keep_off:
            keep_off[i] = dilate(material(i), clear_r, w, h)
        return keep_off[i]

    slice_at: dict[tuple[int, float], list[int]] = {}
    def between(i: int, zlo: float, zhi: float) -> list[int]:
        """Where plate i has material anywhere within this band of its height.

        The union of the band's two ends. Socket walls run straight between the
        profile's z levels, so the widest material in a band is at one end or the
        other, never in the middle.
        """
        out = None
        for z in (zlo + SKIN, zhi - SKIN):
            key = (i, round(z, 6))
            if key not in slice_at:
                slice_at[key] = face_grid(meshes[i], z, x0, y0, w, h, step)
            g = slice_at[key]
            out = g if out is None else [a | b for a, b in zip(out, g)]
        return out

    def bands(pl: Placement) -> list[tuple[float, float]]:
        n = max(1, int(round((pl.z1 - pl.z0) / slab)))
        e = [pl.z0 + (pl.z1 - pl.z0) * k / n for k in range(n + 1)]
        return list(zip(e, e[1:]))

    full = (1 << w) - 1
    # Half a cell more than the gap asks for, because a rectangle covers whole
    # cells while the occupancy test only vouches for their centres: the block's
    # edge reaches half a step past its outermost centre, and the plate's true
    # boundary can sit half a step inside its own. Rounded up to a whole cell of
    # *reach* -- disc(1.83) stops at one cell, because two cells away is a
    # distance of two, and asking for 1.83 while getting 1 halves the clearance.
    clear_r = float(max(1, math.ceil(gap / step + 0.5)))
    thin = max(1.0, min_width / 2 / step)

    full = (1 << w) - 1
    # Half a cell more than the gap asks for, because a rectangle covers whole
    # cells while the occupancy test only vouches for their centres: the block's
    # edge reaches half a step past its outermost centre, and the plate's true
    # boundary can sit half a step inside its own. Rounded up to a whole cell of
    # *reach* -- disc(1.83) stops at one cell, because two cells away is a
    # distance of two, and asking for 1.83 while getting 1 halves the clearance.
    clear_r = float(max(1, math.ceil(gap / step + 0.5)))
    thin = max(1.0, min_width / 2 / step)

    regions: dict[int, list[int]] = {}
    for i in range(1, len(placements)):
        # Not dilated. What needs carrying is where the plate actually has
        # material; growing this before the descent walks it off the plate edge
        # and into the sockets, leaving blocks under nothing.
        need = face_grid(meshes[i], placements[i].z0 + SKIN, x0, y0, w, h, step)
        for j in range(i - 1, -1, -1):
            supported = top(j)
            still = [n & ~s & full for n, s in zip(need, supported)]
            # Thin ribs are kept rather than dropped: the interface laid over a
            # pillar overhangs it by 0.2 mm a layer on every side, so a 0.3 mm
            # rib carries a bead more than a millimetre wide -- and there is no
            # other support anywhere on this model, so anything discarded here is
            # simply not carried.
            still = opened(still, thin, w, h)
            if not any(still):
                break

            # Grown outward first, then held off the plate only where it would
            # actually meet one. A pillar cut to exactly the footprint it carries
            # is far thinner than the hole it stands in, and thin free-standing
            # columns are the least printable thing here; the socket has room to
            # spare, so take it. Clearance stays local because dilating the
            # plate's own occupancy only reaches cells near the plate.
            wide = dilate(still, grow / step, w, h) if grow > 0 else still
            here = [x & ~bl & e & full
                    for x, bl, e in zip(wide, blocked(j), extent)]
            here = opened(here, thin, w, h)

            prev = regions.get(j)
            regions[j] = (here if prev is None
                          else [a | b for a, b in zip(prev, here)])
            need = still
    return x0, y0, w, h, step, regions, extent


def support_fillers(placements: tuple[Placement, ...], gap: float,
                    grow: float = 0.4, step: float = 0.15,
                    min_width: float = 0.42) -> Mesh:
    """The pillars themselves: one block per level a column passes through."""
    x0, y0, w, h, step, regions, _ = support_regions(placements, gap, grow, step,
                                                     min_width)
    out: list = []
    for j, rows in regions.items():
        lo, hi = placements[j].z0, placements[j].z1
        out.extend(region_solid(rows, x0, y0, w, h, step, lo, hi))
    return tuple(out)


@dataclass(frozen=True)
class InterfaceLayer:
    """One printed layer of the interface, in one gap.

    `rows` is the filled region as a bitmask, one integer per grid row with bit
    `ix` set where the cell at (ix, iy) is filled -- the same representation the
    pillar tracing uses, and the reason no geometry library is needed here.
    """
    gap: int            # which gap, counting up from 0
    index: int          # which layer within that gap, counting up from 0
    z0: float           # bottom of the layer's material
    z1: float           # top of it, which is the Z the printer is given
    rows: tuple[int, ...]

    @property
    def height(self) -> float:
        return self.z1 - self.z0


@dataclass(frozen=True)
class Interface:
    """Every interface layer in the stack, on one shared raster grid."""
    x0: float
    y0: float
    step: float
    w: int
    h: int
    layers: tuple[InterfaceLayer, ...]

    def cell(self, ix: int, iy: int) -> tuple[float, float]:
        """Centre of a grid cell in bed coordinates."""
        return self.x0 + (ix + 0.5) * self.step, self.y0 + (iy + 0.5) * self.step


def interface_layers(placements: tuple[Placement, ...], gap: float,
                     layer: float = 0.2, grow: float = 0.4,
                     step: float = 0.15, min_width: float = 0.42,
                     flare: float = 0.2, clearance: float = 0.1,
                     clearance_above: float | None = None,
                     bridge_span: float = 6.0,
                     trim: bool = True) -> Interface:
    """Where interface material goes: one region per layer per gap.

    This is what the slicer used to make as support interface, made deliberately
    instead: a continuous sheet rather than the stub-ridden lattice a support
    pattern leaves, with no support machinery involved and so no balconies to
    remove.

    The regions are the whole of the film's shape logic and they do not care what
    is done with them afterwards. They were boxes handed to the slicer until the
    slicer's layer grid turned out to own the film's position (ADR 0009); they
    are extrusions written straight into the sliced file now. Nothing between
    here and `support_regions()` changed when that did.

    It carries the pillars as well as the plates. Pillars occupy the plates' own
    z bands, so the same gaps fall between them, and an empty gap above a pillar
    leaves its next segment standing on nothing.

    Built a layer at a time, each stepping `flare` further out than the one
    below, so the bottom layer matches exactly what it rests on and the stack
    leans outward at 45 degrees -- 0.2 mm out for 0.2 mm up is precisely the
    angle a printer bridges unaided. That widening is what lets a 0.3 mm rib of
    pillar carry a bead more than a millimetre across.

    `clearance` holds the film clear of the plates above and below. It has to:
    at zero the film's surfaces are coincident with theirs, the slicer merges the
    volumes rather than seeing two, and the result carries no interface filament
    anywhere -- there is nothing to peel because nothing was laid down. Bonding
    does not enter into it; this fails at slicing, not at printing.

    Zero is still permitted. It was the previous default and is a reasonable
    thing to ask for deliberately.
    """
    x0, y0, w, h, step, regions, extent = support_regions(
        placements, gap, grow, step, min_width, margin=max(8.0, bridge_span))
    meshes = {i: pl.placed_mesh() for i, pl in enumerate(placements)}
    out: list = []
    for j in range(len(placements) - 1):
        lower, upper = placements[j], placements[j + 1]
        # What the film rests on: the plate below, plus any pillar at that level.
        base = face_grid(meshes[j], lower.z1 - SKIN, x0, y0, w, h, step)
        r = regions.get(j)
        if r is not None:
            base = [a | b for a, b in zip(base, r)]

        # Trimmed to what it actually carries. The base so far answers "what can
        # this rest on"; nothing yet asks whether anything rests on it, and film
        # beneath an empty socket carries nothing -- it is printed, paid for in
        # interface filament, and peeled off as waste. Measured on the nine-plate
        # drawer stack: 16.5% of the film's area, about 3.9 cm3.
        #
        # What needs carrying is the plate above's downward face together with
        # any pillar standing at the level above. The pillar has to be in it: the
        # film under a pillar has no plate material directly overhead, and a
        # trim against the plate alone would delete exactly that film and leave
        # the pillar's next segment standing on nothing.
        if trim:
            must = face_grid(meshes[j + 1], upper.z0 + SKIN, x0, y0, w, h, step)
            above = regions.get(j + 1)
            if above is not None:
                must = [a | b for a, b in zip(must, above)]
            base = [b & m for b, m in zip(base, must)]
        # Bridge after the trim, never before. A bridge span carries nothing by
        # construction -- anywhere above it that needed carrying would already
        # stand a pillar, and that pillar would already be in the base, so there
        # would have been nothing to bridge -- so a trim run afterwards deletes
        # every bridge. Film on a pillar *top* is the opposite case and survives
        # the trim: it carries the plate border the pillar exists for.
        if bridge_span > 0:
            base = closed(base, bridge_span / 2 / step, w, h)
        base = [b & e for b, e in zip(base, extent)]
        above = clearance if clearance_above is None else clearance_above
        lo, hi = lower.z1 + clearance, upper.z0 - above
        if hi - lo < layer - 1e-9:
            raise ValueError(
                f"a {gap:g} mm gap cannot hold {clearance:g} mm of clearance "
                f"below the film and {above:g} mm above it and still leave a "
                f"layer of film: {gap:g} - {clearance:g} - {above:g} = "
                f"{gap - clearance - above:g} mm, and a layer is {layer:g} mm. "
                f"Widen the gap to at least {clearance + above + layer:g} mm, "
                f"or lower the clearance.")
        n = max(1, int(round((hi - lo) / layer)))
        for k in range(n):
            # Not clipped to the stack's extent. The flare is meant to overhang
            # by a layer's worth on every side, all the way around, including
            # past the outer edge -- that lip is the point of it, not spill.
            # Only the base is held inside the extent, so growth that escapes is
            # the flare's and nothing else's.
            rows = dilate(base, k * flare / step, w, h) if k else base
            out.append(InterfaceLayer(j, k, lo + (hi - lo) * k / n,
                                      lo + (hi - lo) * (k + 1) / n, tuple(rows)))
    return Interface(x0, y0, step, w, h, tuple(out))


def slab_mesh(iface: Interface, weld: float = 0.001) -> Mesh:
    """The interface as boxes, which is what it was before it was toolpaths.

    Kept because it is the only view of the film a person can look at, and
    because every test of the film's *shape* -- the trim, the bridging, the
    flare, the clearances -- was written against it and still holds.

    Boxes overlap by `weld` rather than abutting. Two closed solids that touch
    put four facets on the shared edge; two that interpenetrate share no edge at
    all, and the slicer unions them either way. Welded sideways between
    neighbours and upward into the band above -- but not past the film's own
    top, which is what holds it clear of the plate, so the last band of each gap
    ends exactly at its `z1`.
    """
    out: list = []
    by_gap: dict[int, int] = {}
    for lay in iface.layers:
        by_gap[lay.gap] = max(by_gap.get(lay.gap, 0), lay.index)
    for lay in iface.layers:
        top = lay.z1 + (weld if lay.index < by_gap[lay.gap] else 0.0)
        for rx0, ry0, rx1, ry1 in grid_rects(list(lay.rows), iface.x0, iface.y0,
                                             iface.w, iface.h, iface.step):
            out.extend(box(rx0 - weld, ry0 - weld, lay.z0,
                           rx1 + weld, ry1 + weld, top))
    return tuple(out)


def interface_slabs(placements: tuple[Placement, ...], gap: float,
                    layer: float = 0.2, grow: float = 0.4,
                    step: float = 0.15, min_width: float = 0.42,
                    flare: float = 0.2, clearance: float = 0.1,
                    clearance_above: float | None = None,
                    weld: float = 0.001, bridge_span: float = 6.0,
                    trim: bool = True) -> Mesh:
    """The film as a mesh, for inspection and for the shape tests."""
    return slab_mesh(interface_layers(placements, gap, layer, grow, step,
                                      min_width, flare, clearance,
                                      clearance_above, bridge_span, trim),
                     weld)


def _runs(bits: int, n: int) -> tuple[tuple[int, int], ...]:
    """Maximal runs of set bits in the low `n` bits, as inclusive (first, last).

    Bit tricks rather than a loop over `n`: the regions here are a thousand cells
    across and are scanned once per bead, so the difference is seconds.
    """
    out: list[tuple[int, int]] = []
    bits &= (1 << n) - 1
    while bits:
        start = (bits & -bits).bit_length() - 1
        t = bits >> start
        length = ((t + 1) & ~t).bit_length() - 1
        out.append((start, start + length - 1))
        bits &= ~((1 << (start + length)) - 1)
    return tuple(out)


Bead = tuple[float, float, float, float]     # x0, y0, x1, y1


def raster(rows: tuple[int, ...], x0: float, y0: float, w: int, h: int,
           step: float, width: float, along_x: bool) -> tuple[Bead, ...]:
    """Fill a bitmask region with parallel beads, one extrusion width apart.

    Monotonic: the scanlines advance in one direction and every bead runs the
    same way, so each is laid beside one that is already down. That is what the
    film's part settings asked the slicer for -- no walls, no shells, 100% infill
    with a monotonic pattern -- and the emitter now has to say it directly.

    The beads carry width, so a centre line drawn to the end of a run would put
    half a bead past it. Each run is inset by half a width at both ends, which
    keeps every centre line inside the region and every bead end on its boundary.
    A run narrower than one bead is therefore not filled: it is the fringe of a
    socket rim, it is under `min_width` by construction, and a slicer without
    gap-fill does the same thing.

    *Across* the scan the beads are not inset, so one whose centre line runs
    along the region's edge spills half a width outside it. That is deliberate:
    insetting there as well would need the region eroded by a bead in one axis,
    which deletes the fringe teeth the film is trimmed to keep. The spill is
    horizontal and the film already flares 0.2 mm per layer on purpose; what
    holds the film clear of the plates is its Z, and that is untouched.
    """
    half = width / 2
    out: list[Bead] = []
    if along_x:
        n = max(0, int((h * step - width) / width) + 1)
        for i in range(n):
            y = y0 + half + i * width
            iy = min(h - 1, max(0, int((y - y0) / step)))
            for a, b in _runs(rows[iy], w):
                xa, xb = x0 + a * step + half, x0 + (b + 1) * step - half
                if xb > xa:
                    out.append((xa, y, xb, y))
    else:
        n = max(0, int((w * step - width) / width) + 1)
        for i in range(n):
            x = x0 + half + i * width
            ix = min(w - 1, max(0, int((x - x0) / step)))
            col = sum(((rows[iy] >> ix) & 1) << iy for iy in range(h))
            for a, b in _runs(col, h):
                ya, yb = y0 + a * step + half, y0 + (b + 1) * step - half
                if yb > ya:
                    out.append((x, ya, x, yb))
    return tuple(out)


def interface_beads(iface: Interface, width: float = 0.45
                    ) -> tuple[tuple[InterfaceLayer, tuple[Bead, ...]], ...]:
    """Every interface layer with the toolpath that fills it.

    The raster axis alternates layer to layer, which is what `infill_direction`
    did for the mesh film on its own -- 45 and 135 degrees. Ninety degrees apart
    on the grid's own axes rather than forty-five, because the region is a
    bitmask and a rotated scan would have to resample it; the angle is a
    constant, and nothing else here depends on which one it is.
    """
    return tuple((lay, raster(lay.rows, iface.x0, iface.y0, iface.w, iface.h,
                              iface.step, width, along_x=lay.index % 2 == 0))
                 for lay in iface.layers)


def region_counts(iface: Interface) -> dict[int, int]:
    """Connected regions of interface material per gap, counted on the regions.

    One region per gap is what the film wants: an island is a piece that stays in
    a socket when the rest of the sheet is peeled, which is what the bridging
    exists to prevent (ADR 0007). Counted on the union of the gap's layers, which
    is the top one -- each flares outward over the one below.
    """
    out: dict[int, int] = {}
    rows: dict[int, list[int]] = {}
    for lay in iface.layers:
        r = rows.setdefault(lay.gap, [0] * iface.h)
        for iy, bits in enumerate(lay.rows):
            r[iy] |= bits
    for gap, r in rows.items():
        out[gap + 1] = components(r, iface.w, iface.h)
    return out


def interface_plan(iface: Interface, width: float = 0.45) -> dict:
    """The interface as data a G-code writer can use, in model coordinates.

    Beads rather than regions: the shape logic belongs with the rest of the
    geometry, and a region grid for a nine-plate stack is ten megabytes of
    bitmask where the toolpath that matters is a few hundred kilobytes.
    """
    return {
        "version": 1,
        "line_width": width,
        "layers": [{"gap": lay.gap, "index": lay.index,
                    "z0": round(lay.z0, 6), "z1": round(lay.z1, 6),
                    "beads": [[round(v, 4) for v in b] for b in beads]}
                   for lay, beads in interface_beads(iface, width)],
    }


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
    w = max(1, int(round((x1 - x0) / step)))
    h = max(1, int(round((y1 - y0) / step)))
    rows = face_grid(mesh, pl.z0 + 0.01, x0, y0, w, h, step)
    return grid_rects(dilate(rows, grow / step, w, h), x0, y0, w, h, step)


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


def plates_json(placements: tuple[Placement, ...], mesh: Mesh, gap: float,
                layer_height: float) -> dict:
    """Where every plate ended up, for the G-code post-processor.

    A balcony is support inside a plate's own footprint within its own height.
    Legitimate support at those same heights -- the column under a plate that
    overhangs from above -- is *outside* that footprint. So one box per plate is
    the whole discriminator: everything a plate needs support for is in the gaps
    around it, and the column under a plate overhanging from above stands outside
    the footprint of the plate it passes.

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


PRINTING_TEMPLATE = """# Printing `{stl}`

Generated by `stack_plates.py` from `{source}`.

Build the project, slice it, and write the interface into the result:

    python3 make3mf.py --template templates/stack-template.3mf \\
        --model {stl} --plates {plates} \\
        --interface-plan {interface} --out {name}.3mf

    BambuStudio --no-check --outputdir slice --slice 0 {name}.3mf

    python3 emit_interface.py --project {name}.3mf \\
        --gcode slice/plate_1.gcode --out {name}.gcode

    python3 verify.py --project {name}.3mf --gcode {name}.gcode

`--no-check` is not optional: the gaps have no model material in them, the slicer
calls that an empty layer, and without the flag it exits without writing G-code.

Print `{name}.gcode`, not the sliced file. The sliced file has no interface in it
at all.

{report}

## What is in the model

The stack itself and the pillars that carry it, both in the object filament; a
support blocker over the whole of it; and a small decoy column beside it with the
stack's own z profile. The decoy is there to be supported: its gaps are what make
the slicer load and purge the interface filament at the heights the interface
needs, and the blocker is what keeps that support off the stack.

The interface is not in the model. It is written into the sliced G-code as
toolpaths at heights chosen to the micron, because a mesh film's height is
resolved against the slicer's sample planes and comes out quantised to the layer
(`docs/adr/0009-clearance-is-quantised-to-the-layer-height.md`).

Nothing on the stack is supported by the slicer. Every overhang is carried by
geometry already in the model, so anything the generator missed prints into air.
`verify.py` reads the emitted file and says so if any support reached the stack.

## Numbers for this stack

| | |
|---|---|
| gap between plates | {gap:g} mm |
| interface | {film:g} mm in {filmlayers} layers |
| clearance below the interface | {clearance:g} mm |
| clearance above the interface | {above:g} mm |
| cells | {cells} |
| socket taper | {angle:.0f} degrees from horizontal |

The two clearances do different jobs. The one below is what lets the interface
release from the plate beneath it. The one above is what the first layer of the
plate above has to bridge, so the underside of every plate is as poor as that
number is large -- which is why it is the smaller of the two, and why it is a
number a mesh could not have expressed.

## After printing

The stack comes off the bed as one block. Slide a thin blade into a gap and
twist. The interface is a continuous sheet in each gap; the intent is that it
lifts out in one piece, because it does not bond to the plates.

Work from the top down. The land-to-land gaps have the least contact area and
give first; the rib-to-rib gaps need more persuasion.

At a real clearance on both faces the stack does separate -- Bambu Support W and
PETG at 255 C both came apart by hand on a two-plate test
(`docs/adr/0009-clearance-is-quantised-to-the-layer-height.md`). What that test
also showed is a poor underside on the plate above, which is what the small
clearance above is meant to fix and what the next print has to answer.
"""


def write_printing_notes(path: Path, placements, gap, layer, report_text,
                         stl_name, plates_name, interface_name, source,
                         clearance: float = 0.1,
                         clearance_above: float | None = None) -> None:
    plate = placements[0].plate
    angle = min(steepest_overhang(pl.plate) for pl in placements)
    above = clearance if clearance_above is None else clearance_above
    film = max(0.0, gap - clearance - above)
    path.write_text(PRINTING_TEMPLATE.format(
        stl=stl_name, plates=plates_name, interface=interface_name,
        name=Path(stl_name).stem, source=source, report=report_text,
        gap=gap, film=film, filmlayers=max(1, round(film / layer)),
        clearance=clearance, above=above, angle=angle,
        cells=sum(p.plate.lattice.cells for p in placements),
    ))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stl", type=Path, help="multi-plate Gridfinity baseplate STL")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("out"))
    ap.add_argument("--name", default=None, help="output basename (default: derived)")
    ap.add_argument("--gap", type=float, default=0.6,
                    help="separation gap in mm (default 0.6: interface clearance "
                         "at both faces plus two layers of film between them)")
    ap.add_argument("--layer-height", type=float, default=0.2)
    ap.add_argument("--bed", default="256x256x256", help="build volume WxDxH in mm")
    ap.add_argument("--no-flip", action="store_true",
                    help="keep every plate the same way up")
    ap.add_argument("--no-register", action="store_true",
                    help="centre plates instead of aligning their cell lattices")

    ap.add_argument("--no-fillers", action="store_true",
                    help="omit the loose blocks that stand in under a ledge")
    ap.add_argument("--filler-step", type=float, default=0.15,
                    help="resolution the filler outline is traced at, mm. Finer is "
                         "smoother and slower (default 0.15, below what a 0.4 mm "
                         "nozzle can render)")
    ap.add_argument("--filler-grow", type=float, default=0.5,
                    help="dilate the filler footprint outward by this much, mm "
                         "(default 0.5, about two perimeters). A faithful "
                         "projection reproduces webs the slicer then drops as too "
                         "thin; much more than this doubles them")
    ap.add_argument("--no-interface", action="store_true",
                    help="skip the interface plan. The stack then has nothing "
                         "between its plates and cannot be printed as one job; "
                         "useful only for inspecting the stack on its own")
    ap.add_argument("--bridge-span", type=float, default=6.0,
                    help="widest empty span the film bridges across, mm "
                         "(default 6, 0 disables). Film on a pillar top is an "
                         "island ringed by the socket opening, and an island "
                         "stays behind in the socket when the sheet is peeled. "
                         "The default is past the knee, deliberately and on the "
                         "record: swept on the nine-plate drawer stack, islands "
                         "go 28 at 0 mm, 11 at 1-2 mm, and 0 from 3 mm upward, "
                         "while film volume keeps climbing -- 22.40, 22.70, "
                         "23.22 at the knee, 23.59 at 6. So 6 buys no fewer "
                         "islands than 3, only 0.37 cm3 more film, and 3 is the "
                         "value to use if that matters")
    ap.add_argument("--interface-line-width", type=float, default=0.45,
                    help="width of the interface's extrusions, mm (default 0.45, "
                         "the sparse_infill_line_width the mesh film printed at). "
                         "The bead spacing is the same number: the interface is "
                         "solid, with no walls and no gap fill")
    ap.add_argument("--interface-clearance-above", type=float, default=None,
                    metavar="MM",
                    help="clearance above the film only, mm; defaults to "
                         "--interface-clearance. The two faces are not "
                         "interchangeable: the gap below the film is what lets "
                         "it release, and the gap above is what the plate above "
                         "has to bridge, so the underside of every plate is as "
                         "poor as this number is large")
    ap.add_argument("--interface-clearance", type=float, default=0.1,
                    help="hold the film clear of the plates it sits between, mm "
                         "per face (default 0.1). It has to be enough to leave "
                         "one layer with no model material on it: the slicer "
                         "calls a surface a top only where the layer above is "
                         "empty of every region, so without that layer the plate "
                         "gets no top and the film no bottom, and they print as "
                         "one welded body. Measured at a 0.2 mm layer height: 0 "
                         "merges them outright, 0.05 welds, 0.1 leaves the layer "
                         "and separates. The number follows the layer height and "
                         "does not travel -- at 0.1 mm layers, 0.1 mm would weld "
                         "too. Costs no height either way, since that follows the "
                         "gap, which snaps to a whole layer")
    ap.add_argument("--split", action="store_true",
                    help="emit several stacks, each one in which every plate rests "
                         "fully on the one below. Avoids the tall thin support wall "
                         "a ledge forces, at the cost of one print job per stack")
    ap.add_argument("--order", choices=("nested", "area"), default="nested",
                    help="nested: each plate sits on the one below (default); "
                         "area: plain footprint order")
    args = ap.parse_args(argv)

    # Rounded to a sane number of decimals as well as to the layer: the plain
    # product lands on 0.6000000000000001 and that reaches the printing notes.
    gap = round(round(args.gap / args.layer_height) * args.layer_height, 6)
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

        plan_path = out / f"{name}.interface.json"
        if not args.no_interface:
            iface = interface_layers(placements, gap, args.layer_height,
                                     args.filler_grow, args.filler_step,
                                     clearance=args.interface_clearance,
                                     clearance_above=args.interface_clearance_above,
                                     bridge_span=args.bridge_span)
            # Not `plan`: that is this module's own plate-ordering function.
            iplan = interface_plan(iface, args.interface_line_width)
            plan_path.write_text(json.dumps(iplan) + "\n")
            beads = sum(len(l["beads"]) for l in iplan["layers"])
            print(f"wrote {plan_path} ({len(iplan['layers'])} layers, "
                  f"{beads} beads at {args.interface_line_width:g} mm)")

        plates_path = out / f"{name}.plates.json"
        plates_path.write_text(json.dumps(
            plates_json(placements, body + fillers, gap, args.layer_height),
            indent=2) + "\n")
        print(f"wrote {plates_path}")

        notes = out / f"{name}-PRINTING.md"
        write_printing_notes(notes, placements, gap, args.layer_height,
                             report_text, stl_path.name, plates_path.name,
                             plan_path.name, args.stl.name,
                             clearance=args.interface_clearance,
                             clearance_above=args.interface_clearance_above)
        print(f"wrote {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
