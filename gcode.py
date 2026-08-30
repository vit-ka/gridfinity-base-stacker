"""Reading a sliced Bambu file: what was actually printed, not what was planned.

Every measurement in this project that was made against the plan and not against
the output has been wrong at least once, so the interface's heights, its filament
and the absence of support are all read back from here.

Three things about Bambu's dialect shape the parser:

- **Layer Z appears twice and neither is authoritative on its own.** There is a
  `; CHANGE_LAYER` / `; Z_HEIGHT:` comment pair, and there is the actual Z of the
  travel move that enters the layer (`G1 X.. Y.. Z4.4 F30000`). The comment is
  what the slicer meant; the move is what the printer does. We track both, and
  `Move.z` is always the machine's.
- **Z-hop moves are bare `G1 Z..`** with no X or Y, and they bracket travel. A
  reader that took the last Z it saw as the layer's would read the hop height.
  Nothing here needs to special-case them, because extrusions happen at the
  un-hopped Z by construction -- but only if Z is tracked per move rather than
  per layer.
- **Extrusion is relative** (`M83`) and `G91` appears inside the filament-change
  macro, so both modes have to be honoured or the purge lands in the wrong place
  and the totals come out nonsense.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# Bambu's virtual tools: 1000/1100 mark prime-tower bookkeeping and 255 is the
# unload at the end of the print. None of them is a filament.
PHYSICAL_TOOLS = 32


@dataclass(frozen=True)
class Header:
    """The slicer's own summary, from the block before the config dump."""
    layers: int
    tools: tuple[int, ...]
    diameters: tuple[float, ...]
    densities: tuple[float, ...]
    lengths: tuple[float, ...]
    weights: tuple[float, ...]
    max_z: float
    seconds: int

    def area(self, tool: int) -> float:
        """Cross-section of the filament in tool `tool`, mm2."""
        d = self.diameters[tool] if tool < len(self.diameters) else 1.75
        return math.pi * (d / 2) ** 2

    def density(self, tool: int) -> float:
        return self.densities[tool] if tool < len(self.densities) else 1.24


@dataclass(frozen=True)
class Move:
    """One motion command, with everything the file said about it at the time."""
    line: int           # index into the file's lines, so an editor can find it
    x0: float
    y0: float
    x1: float
    y1: float
    z: float            # machine Z at the end of the move
    e: float            # filament delta in mm; negative is a retraction
    tool: int           # -1 before the first tool change
    feature: str        # last "; FEATURE:", "" before any
    layer: int          # layer counter from the file, 0 before the first
    layer_z: float      # the layer's declared Z_HEIGHT, -1.0 before the first
    width: float        # last "; LINE_WIDTH:", 0.0 before any
    height: float       # last "; LAYER_HEIGHT:", 0.0 before any
    obj: int            # last "; OBJECT_ID:", -1 for none

    @property
    def z0(self) -> float:
        """Where this extrusion's material starts, not where the nozzle is.

        Z in the file is the top of the bead. The distance that matters for a
        clearance is to the *bottom* of the first interface layer, which is a
        layer height below it.
        """
        return self.z - self.height

    @property
    def extruding(self) -> bool:
        """Laying material: pushing filament while the head moves."""
        return self.e > 0 and (self.x1 != self.x0 or self.y1 != self.y0)

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


_TIME = re.compile(r"(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?\s*$")


def _floats(s: str) -> tuple[float, ...]:
    return tuple(float(v) for v in s.split(",") if v.strip())


def _seconds(s: str) -> int:
    m = _TIME.search(s.strip())
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def read_header(lines: Iterable[str]) -> Header:
    """Parse the HEADER_BLOCK. Stops there: the config dump below it is 800 lines."""
    got: dict[str, str] = {}
    for ln in lines:
        if ln.startswith("; HEADER_BLOCK_END"):
            break
        if not ln.startswith(";") or ":" not in ln:
            continue
        # One line carries two fields: "model printing time: A; total estimated
        # time: B". Split on ";" first so the second does not end up inside the
        # first's value, which is how the total once got read as the model time.
        for field in ln[1:].split(";"):
            k, sep, v = field.partition(":")
            if sep:
                got[k.strip()] = v.strip()
    return Header(
        layers=int(got.get("total layer number", 0)),
        tools=tuple(int(v) for v in got.get("filament", "").split(",") if v.strip()),
        diameters=_floats(got.get("filament_diameter", "")),
        densities=_floats(got.get("filament_density", "")),
        lengths=_floats(got.get("total filament length [mm]", "")),
        weights=_floats(got.get("total filament weight [g]", "")),
        max_z=float(got.get("max_z_height", 0.0)),
        seconds=_seconds(got.get("total estimated time", "")),
    )


_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def moves(lines: Iterable[str], tool: int = -1) -> Iterator[Move]:
    """Every move in the file, with the state the file had established for it.

    State is carried, not inferred: a G1 that names only X keeps the Y, Z and
    tool of the one before it, which is how Bambu writes most of a raster.

    `tool` seeds the active filament. It has to be given, because the first
    filament is selected inside the machine start G-code by a placeholder the
    slicer expands, and a file with only one tool change in it then attributes
    nineteen thousand lines to no tool at all -- which is how the totals first
    came out 10% short.

    Arcs (G2/G3) are followed for their endpoint and their extrusion, which is
    what every measurement here needs; their `length` is the chord, not the arc.
    Bambu emits five thousand of them in a two-plate print, so skipping them is
    not an option.
    """
    x = y = z = 0.0
    absolute, rel_e = True, True
    feature, obj, width, height = "", -1, 0.0, 0.0
    layer, layer_z = 0, -1.0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if s[0] == ";":
            body = s[1:].strip()
            if body.startswith("FEATURE:"):
                feature = body[8:].strip()
            elif body.startswith("Z_HEIGHT:"):
                layer_z = float(body[9:])
            elif body.startswith("LINE_WIDTH:"):
                width = float(body[11:])
            elif body.startswith("LAYER_HEIGHT:"):
                height = float(body[13:])
            elif body.startswith("OBJECT_ID:"):
                obj = int(body[10:])
            elif body.startswith("layer num/total_layer_count:"):
                layer = int(body.split(":")[1].split("/")[0])
            continue
        code = s.split(None, 1)[0].upper()
        if code == "G90":
            absolute = True
        elif code == "G91":
            absolute = False
        elif code == "M82":
            rel_e = False
        elif code == "M83":
            rel_e = True
        elif code[0] == "T" and code[1:].isdigit():
            n = int(code[1:])
            if n < PHYSICAL_TOOLS:
                tool = n
        elif code == "G92":
            pass        # only ever E0 here, and E is tracked per move
        elif code in ("G0", "G1", "G2", "G3"):
            w = dict(_WORD.findall(s.split(";")[0]))
            nx = float(w["X"]) if "X" in w else None
            ny = float(w["Y"]) if "Y" in w else None
            nz = float(w["Z"]) if "Z" in w else None
            e = float(w["E"]) if "E" in w else 0.0
            x1 = x if nx is None else (nx if absolute else x + nx)
            y1 = y if ny is None else (ny if absolute else y + ny)
            z1 = z if nz is None else (nz if absolute else z + nz)
            yield Move(i, x, y, x1, y1, z1, e if rel_e else e, tool, feature,
                       layer, layer_z, width, height, obj)
            x, y, z = x1, y1, z1


def read(path: Path) -> tuple[Header, tuple[str, ...], tuple[Move, ...]]:
    """Header, raw lines, and every move -- the lines so an editor can splice."""
    lines = tuple(path.read_text().splitlines())
    hdr = read_header(lines)
    # Tool numbers in the body are zero-based; the header's filament list is the
    # slicer's one-based slots.
    first = hdr.tools[0] - 1 if hdr.tools else -1
    return hdr, lines, tuple(moves(lines, first))


def filament_used(ms: Iterable[Move]) -> dict[int, float]:
    """Millimetres of filament pushed per tool, retractions netted off.

    Reproduces the header's `total filament length`, which is the check that the
    parser is following the file's modes rather than guessing them.
    """
    out: dict[int, float] = {}
    for m in ms:
        if m.tool >= 0 and m.e:
            out[m.tool] = out.get(m.tool, 0.0) + m.e
    return out


def grams(ms: Iterable[Move], hdr: Header) -> dict[int, float]:
    return {t: mm * hdr.area(t) * hdr.density(t) / 1000
            for t, mm in filament_used(ms).items()}


def extrusion_by_feature(ms: Iterable[Move]) -> dict[str, float]:
    """Millimetres of filament per feature type, extrusion only."""
    out: dict[str, float] = {}
    for m in ms:
        if m.extruding:
            out[m.feature] = out.get(m.feature, 0.0) + m.e
    return out


def layer_zs(ms: Iterable[Move]) -> tuple[float, ...]:
    """Distinct Z heights at which material was laid, in order.

    The machine's Z, not the declared one, and only where something was
    extruded -- so a Z-hop and an empty layer both drop out.
    """
    seen: dict[float, None] = {}
    for m in ms:
        if m.extruding:
            seen.setdefault(round(m.z, 4), None)
    return tuple(sorted(seen))


# Features that are not the model: the tower and whatever the machine's own
# macros lay down. Everything else is an object being printed.
OFF_MODEL = frozenset({"Prime tower", "Custom", ""})


def model_moves(ms: Iterable[Move],
                box: tuple[float, float, float, float] | None = None
                ) -> tuple[Move, ...]:
    """Extrusions that belong to an object, not to the tower or the start macro.

    `box` narrows that to one object's footprint. The plate carries more than the
    stack -- the decoy stands beside it and is supported in the same filament the
    interface uses -- so anything measuring the interface has to say which
    footprint it means, or it measures the decoy's support as well.
    """
    out = (m for m in ms if m.extruding and m.feature not in OFF_MODEL)
    if box is None:
        return tuple(out)
    x0, y0, x1, y1 = box
    return tuple(m for m in out
                 if min(m.x0, m.x1) <= x1 and max(m.x0, m.x1) >= x0
                 and min(m.y0, m.y1) <= y1 and max(m.y0, m.y1) >= y0)


@dataclass(frozen=True)
class Gap:
    """What one gap in the stack actually printed as."""
    index: int          # 1-based, counting up the stack
    plate_below: float  # top of the last plate extrusion below the gap
    plate_above: float  # bottom of the first plate extrusion above it
    film_lo: float      # bottom of the lowest interface extrusion
    film_hi: float      # top of the highest
    layers: int         # interface layers found in the gap
    regions: int        # connected regions of interface material, in plan

    @property
    def below(self) -> float:
        """Clearance between the plate below and the interface."""
        return self.film_lo - self.plate_below

    @property
    def above(self) -> float:
        """Clearance between the interface and the plate above."""
        return self.plate_above - self.film_hi


def measure_gaps(ms: Iterable[Move], interface_tool: int, regions=None,
                 box: tuple[float, float, float, float] | None = None
                 ) -> tuple[Gap, ...]:
    """Read every gap's clearances back out of the file.

    A gap is a run of interface layers with plate material below and above it.
    Nothing here is told where the gaps are meant to be -- they are found from
    what was printed, so a film emitted at the wrong height is measured at the
    wrong height rather than being quietly assigned to the gap it was meant for.

    `regions(lo, hi)` optionally counts the connected regions between two
    heights; without it the count is reported as zero. `box` restricts the
    measurement to one footprint on the plate.
    """
    ms = tuple(ms)
    model = model_moves(ms, box)
    plate = sorted({(round(m.z0, 4), round(m.z, 4))
                    for m in model if m.tool != interface_tool})
    film = sorted({(round(m.z0, 4), round(m.z, 4))
                   for m in model if m.tool == interface_tool})
    if not film or not plate:
        return ()
    # Interface layers grouped into runs: consecutive when one ends where the
    # next begins. A wrongly-placed layer therefore falls out as its own run and
    # is measured against whatever plate material surrounds it.
    runs: list[list[tuple[float, float]]] = [[film[0]]]
    for lo, hi in film[1:]:
        if abs(lo - runs[-1][-1][1]) < 1e-6:
            runs[-1].append((lo, hi))
        else:
            runs.append([(lo, hi)])
    out = []
    for n, run in enumerate(runs, 1):
        lo, hi = run[0][0], run[-1][1]
        below = max((b for _, b in plate if b <= lo + 1e-6), default=float("nan"))
        above = min((a for a, _ in plate if a >= hi - 1e-6), default=float("nan"))
        out.append(Gap(n, below, above, lo, hi, len(run),
                       regions(lo, hi) if regions else 0))
    return tuple(out)


def support_moves(ms: Iterable[Move]) -> tuple[Move, ...]:
    """Anything the slicer generated as support, which should be nothing."""
    return tuple(m for m in ms if m.extruding and "upport" in m.feature)
