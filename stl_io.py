"""Binary STL read/write and mesh primitives. Stdlib only."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

# A facet is 12 floats: nx,ny,nz, ax,ay,az, bx,by,bz, cx,cy,cz
Facet = tuple[float, ...]
Mesh = tuple[Facet, ...]

_FACET = struct.Struct("<12fH")
_RAW = struct.Struct("<12f")


def read_stl(path: Path) -> Mesh:
    """Read a binary STL. Raises ValueError on ASCII or truncated input."""
    blob = path.read_bytes()
    if len(blob) < 84:
        raise ValueError(f"{path}: too short to be a binary STL")
    if blob[:5] == b"solid" and b"facet normal" in blob[:512]:
        raise ValueError(f"{path}: ASCII STL is not supported")
    count = struct.unpack_from("<I", blob, 80)[0]
    if len(blob) < 84 + count * 50:
        raise ValueError(f"{path}: truncated, want {count} facets")
    return tuple(_RAW.unpack_from(blob, 84 + i * 50) for i in range(count))


def write_stl(path: Path, mesh: Mesh, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = bytearray(header.encode("ascii", "replace")[:80].ljust(80, b"\0"))
    out += struct.pack("<I", len(mesh))
    for f in mesh:
        out += _FACET.pack(*f, 0)
    path.write_bytes(out)


@dataclass(frozen=True)
class Bounds:
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.y1 - self.y0

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    @property
    def footprint(self) -> float:
        return self.width * self.depth

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def bounds_of(mesh: Mesh) -> Bounds:
    xs = [f[3 + k * 3] for f in mesh for k in range(3)]
    ys = [f[4 + k * 3] for f in mesh for k in range(3)]
    zs = [f[5 + k * 3] for f in mesh for k in range(3)]
    return Bounds(min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def signed_volume(mesh: Mesh) -> float:
    """Positive when facet winding is outward (right-hand rule)."""
    total = 0.0
    for f in mesh:
        ax, ay, az, bx, by, bz, cx, cy, cz = f[3:]
        total += (
            ax * (by * cz - bz * cy)
            - ay * (bx * cz - bz * cx)
            + az * (bx * cy - by * cx)
        )
    return total / 6.0


def translate(mesh: Mesh, dx: float, dy: float, dz: float) -> Mesh:
    return tuple(
        (f[0], f[1], f[2],
         f[3] + dx, f[4] + dy, f[5] + dz,
         f[6] + dx, f[7] + dy, f[8] + dz,
         f[9] + dx, f[10] + dy, f[11] + dz)
        for f in mesh
    )


def rotate_x180(mesh: Mesh) -> Mesh:
    """Rotate 180 degrees about the X axis: (x, y, z) -> (x, -y, -z).

    A proper rotation (det = +1), so winding and outward normals stay valid.
    """
    return tuple(
        (f[0], -f[1], -f[2],
         f[3], -f[4], -f[5],
         f[6], -f[7], -f[8],
         f[9], -f[10], -f[11])
        for f in mesh
    )


def rotate_y180(mesh: Mesh) -> Mesh:
    """Rotate 180 degrees about the Y axis: (x, y, z) -> (-x, y, -z).

    Like rotate_x180 this is a proper rotation and puts the top face down, but it
    mirrors X instead of Y. Which one registers a plate's lattice more cheaply
    depends on which axis its borders are symmetric about.
    """
    return tuple(
        (-f[0], f[1], -f[2],
         -f[3], f[4], -f[5],
         -f[6], f[7], -f[8],
         -f[9], f[10], -f[11])
        for f in mesh
    )


def split_shells(mesh: Mesh, tol: float = 1e-3) -> tuple[Mesh, ...]:
    """Split into connected components by welding vertices onto a `tol` grid.

    Returned largest-first by facet count.
    """
    parent = list(range(len(mesh)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    scale = 1.0 / tol
    seen: dict[tuple[int, int, int], int] = {}
    for i, f in enumerate(mesh):
        for k in range(3):
            key = (round(f[3 + k * 3] * scale),
                   round(f[4 + k * 3] * scale),
                   round(f[5 + k * 3] * scale))
            j = seen.setdefault(key, i)
            if j != i:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[Facet]] = {}
    for i, f in enumerate(mesh):
        groups.setdefault(find(i), []).append(f)
    return tuple(tuple(g) for g in sorted(groups.values(), key=len, reverse=True))


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Mesh:
    """Axis-aligned box as 12 outward-wound triangles."""
    p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = (
        ((0, 3, 2, 1), (0.0, 0.0, -1.0)),
        ((4, 5, 6, 7), (0.0, 0.0, 1.0)),
        ((0, 1, 5, 4), (0.0, -1.0, 0.0)),
        ((2, 3, 7, 6), (0.0, 1.0, 0.0)),
        ((1, 2, 6, 5), (1.0, 0.0, 0.0)),
        ((3, 0, 4, 7), (-1.0, 0.0, 0.0)),
    )
    out: list[Facet] = []
    for (a, b, c, d), n in quads:
        out.append((*n, *p[a], *p[b], *p[c]))
        out.append((*n, *p[a], *p[c], *p[d]))
    return tuple(out)
