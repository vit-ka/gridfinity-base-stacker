"""Gridfinity baseplate geometry analysis: lattice, socket profile, contact faces."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

from stl_io import Bounds, Facet, Mesh, bounds_of

EPS = 1e-6


@dataclass(frozen=True)
class Lattice:
    """The 42 mm cell grid of a baseplate, in the plate's own coordinates."""
    pitch: float
    xs: tuple[float, ...]      # hole-column centres
    ys: tuple[float, ...]      # hole-row centres
    bottom_opening: float      # clear span at the plate's bottom face
    top_opening: float         # clear span at the plate's top face

    @property
    def cells(self) -> int:
        return len(self.xs) * len(self.ys)

    def phase(self, coords: tuple[float, ...]) -> float:
        """Modal residual mod pitch.

        The mode, not the first value: a clipped partial row sits off-lattice
        and would otherwise poison the phase.
        """
        residuals = Counter(round(c % self.pitch, 3) for c in coords)
        return residuals.most_common(1)[0][0]

    @property
    def phase_x(self) -> float:
        return self.phase(self.xs)

    @property
    def phase_y(self) -> float:
        return self.phase(self.ys)

    def mirrored_x(self) -> Lattice:
        """Lattice as seen after a 180-degree rotation about Y (x -> -x)."""
        return Lattice(self.pitch, tuple(sorted(-x for x in self.xs)), self.ys,
                       self.bottom_opening, self.top_opening)

    def mirrored_y(self) -> Lattice:
        """Lattice as seen after a 180-degree rotation about X (y -> -y)."""
        return Lattice(self.pitch, self.xs, tuple(sorted(-y for y in self.ys)),
                       self.bottom_opening, self.top_opening)


def z_levels(mesh: Mesh, tol: int = 4) -> tuple[float, ...]:
    """Distinct vertex Z heights, ascending."""
    return tuple(sorted({round(f[5 + k * 3], tol) for f in mesh for k in range(3)}))


def _horizontal_at(mesh: Mesh, z: float) -> list[tuple[float, ...]]:
    """XY triangles of facets lying flat in the plane z."""
    return [
        (f[3], f[4], f[6], f[7], f[9], f[10])
        for f in mesh
        if abs(f[5] - z) < EPS and abs(f[8] - z) < EPS and abs(f[11] - z) < EPS
    ]


def _point_in_tri(px: float, py: float, t: tuple[float, ...]) -> bool:
    ax, ay, bx, by, cx, cy = t
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def _ray_crossings(mesh: Mesh, y: float, z: float) -> list[float]:
    """X coordinates where a +X ray at (y, z) pierces the surface."""
    out: list[float] = []
    for f in mesh:
        ay, az, ax = f[4], f[5], f[3]
        by, bz, bx = f[7], f[8], f[6]
        cy, cz, cx = f[10], f[11], f[9]
        det = (bz - cz) * (ay - cy) + (cy - by) * (az - cz)
        if abs(det) < 1e-12:
            continue
        l1 = ((bz - cz) * (y - cy) + (cy - by) * (z - cz)) / det
        l2 = ((cz - az) * (y - cy) + (ay - cy) * (z - cz)) / det
        l3 = 1.0 - l1 - l2
        if l1 < -1e-9 or l2 < -1e-9 or l3 < -1e-9:
            continue
        out.append(l1 * ax + l2 * bx + l3 * cx)
    return sorted(out)


def _opening_at(mesh: Mesh, hx: float, hy: float, z: float) -> float | None:
    """Clear span in X across the void at (hx, hy, z), or None if it is solid.

    Sorted crossings alternate entering and leaving material, so voids are the
    odd-indexed spans. Scanning every consecutive pair instead would happily
    return a solid span -- which reads a closed rim as a 200 mm wide opening.
    """
    xs = _ray_crossings(mesh, hy, z)
    for i in range(1, len(xs) - 1, 2):
        if xs[i] <= hx <= xs[i + 1] and xs[i + 1] - xs[i] > 1.0:
            return xs[i + 1] - xs[i]
    return None


def horizontal_area(mesh: Mesh, z: float) -> float:
    """Total area of facets lying flat in the plane z."""
    return sum(
        abs((t[2] - t[0]) * (t[5] - t[1]) - (t[4] - t[0]) * (t[3] - t[1])) / 2
        for t in _horizontal_at(mesh, z)
    )


def _snap_to_lattice(coords: tuple[float, ...], pitch: float) -> tuple[float, ...]:
    """Pull each centre onto the modal lattice.

    A cell clipped by the plate edge has its centroid pulled inwards; its true
    centre is the lattice position, which is what a blocker must be built around.
    """
    phase = Counter(round(c % pitch, 3) for c in coords).most_common(1)[0][0]
    return tuple(sorted(phase + round((c - phase) / pitch) * pitch for c in coords))


def hole_profile(mesh: Mesh, hx: float, hy: float, z: float, opening: float,
                 samples: int = 24, span: float = 0.97,
                 fill: bool = True) -> tuple[tuple[float, float | None], ...]:
    """Trace the outline of one cell opening as (dy, half-width) pairs.

    Gridfinity sockets have rounded corners, so a square blocker would bite into
    the ribs. Sampling the real outline keeps the blocker inside the void.
    """
    half = opening / 2
    raw: list[tuple[float, float | None]] = []
    for i in range(samples):
        dy = (-span + 2 * span * i / (samples - 1)) * half
        width = _opening_at(mesh, hx, hy + dy, z)
        raw.append((dy, None if width is None else width / 2))

    valid = [w for _, w in raw if w is not None]
    if not valid:
        return ()
    if not fill:
        # Caller intersects the valid samples across z levels; used for cells the
        # plate outline clips, where a missing sample means real material.
        return tuple(raw)
    # A missed sample gets the narrowest measured width, never a wider guess:
    # the blocker must stay inside the void, so erring small is the safe side.
    return tuple((dy, min(valid) if w is None else w) for dy, w in raw)


def profile_polygon(profile: tuple[tuple[float, float], ...],
                    inset: float) -> tuple[tuple[float, float], ...]:
    """Closed CCW polygon for a profile, shrunk radially by `inset` mm."""
    pts = [(w, dy) for dy, w in profile] + [(-w, dy) for dy, w in reversed(profile)]
    out: list[tuple[float, float]] = []
    for x, y in pts:
        r = (x * x + y * y) ** 0.5
        k = 0.0 if r <= inset else (r - inset) / r
        out.append((x * k, y * k))
    return tuple(out)


def detect_lattice(mesh: Mesh, bounds: Bounds | None = None,
                   step: float = 1.5, min_cells: int = 20) -> Lattice | None:
    """Find the cell grid by flood-filling the interior voids of the bottom face.

    Returns None when no regular grid of through-holes is found.
    """
    b = bounds or bounds_of(mesh)
    flats = _horizontal_at(mesh, b.z0)
    if not flats:
        return None

    bucket_size = 8.0
    buckets: dict[tuple[int, int], list[tuple[float, ...]]] = {}
    for t in flats:
        bx0 = int((min(t[0], t[2], t[4]) - b.x0) // bucket_size)
        bx1 = int((max(t[0], t[2], t[4]) - b.x0) // bucket_size)
        by0 = int((min(t[1], t[3], t[5]) - b.y0) // bucket_size)
        by1 = int((max(t[1], t[3], t[5]) - b.y0) // bucket_size)
        for i in range(bx0, bx1 + 1):
            for j in range(by0, by1 + 1):
                buckets.setdefault((i, j), []).append(t)

    w, h = int(b.width / step), int(b.depth / step)
    if w < 3 or h < 3:
        return None
    solid = [[False] * h for _ in range(w)]
    for ix in range(w):
        px = b.x0 + (ix + 0.5) * step
        bi = int((px - b.x0) // bucket_size)
        for iy in range(h):
            py = b.y0 + (iy + 0.5) * step
            for t in buckets.get((bi, int((py - b.y0) // bucket_size)), ()):
                if _point_in_tri(px, py, t):
                    solid[ix][iy] = True
                    break

    seen = [[False] * h for _ in range(w)]
    centres: list[tuple[float, float]] = []
    for ix in range(w):
        for iy in range(h):
            if solid[ix][iy] or seen[ix][iy]:
                continue
            queue = deque([(ix, iy)])
            seen[ix][iy] = True
            cells: list[tuple[int, int]] = []
            touches_edge = False
            while queue:
                cx, cy = queue.popleft()
                cells.append((cx, cy))
                if cx in (0, w - 1) or cy in (0, h - 1):
                    touches_edge = True
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not seen[nx][ny] and not solid[nx][ny]:
                        seen[nx][ny] = True
                        queue.append((nx, ny))
            if not touches_edge and len(cells) >= min_cells:
                mx = sum(c[0] for c in cells) / len(cells)
                my = sum(c[1] for c in cells) / len(cells)
                centres.append((b.x0 + (mx + 0.5) * step, b.y0 + (my + 0.5) * step))

    if len(centres) < 2:
        return None

    xs = tuple(sorted({round(c[0], 1) for c in centres}))
    ys = tuple(sorted({round(c[1], 1) for c in centres}))
    gaps = [round(hi - lo, 1) for seq in (xs, ys) for lo, hi in zip(seq, seq[1:])]
    if not gaps:
        return None
    pitch = Counter(gaps).most_common(1)[0][0]

    xs = _snap_to_lattice(xs, pitch)
    ys = _snap_to_lattice(ys, pitch)

    hx, hy = xs[len(xs) // 2], ys[len(ys) // 2]
    bottom = _opening_at(mesh, hx, hy, b.z0 + 0.01)
    top = _opening_at(mesh, hx, hy, b.z1 - 0.01)
    if bottom is None or top is None:
        return None
    return Lattice(pitch, xs, ys, round(bottom, 3), round(top, 3))
