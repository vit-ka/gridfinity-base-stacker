#!/usr/bin/env python3
"""Strip support balconies out of a sliced G-code file.

A balcony is the ribbon of support that hugs a socket wall *inside* a plate's own
height. It is the downward projection of interface that overhangs the rib it
lands on: contacts get snapped to a grid of roughly base-pattern-spacing plus one
extrusion width, about 2.9 mm, while the land it must sit on is 1.5 mm wide. The
fringe left over has nothing beneath it, so the slicer props it up all the way
down through the plate. See docs/adr/0003 for the settings that cannot reach it --
which is every one of them, because the propagation in SupportMaterial.cpp is
unconditional.

So we delete the toolpaths instead. The slicer gets no vote at this stage.

  Put it inside the project, so the 3mf carries everything it needs:

      python3 postprocess.py out/NAME.plates.json --install PROJECT.3mf

  That writes this file's own source, plus the plate boxes, into the project's
  `post_process` setting as one self-contained command. Nothing has to exist on
  disk afterwards and no path points anywhere machine-specific, so the 3mf still
  works when it is moved or shared. `--embed` prints the same line to paste into
  Others -> Post-processing Scripts by hand.

  Or run it directly on an exported G-code file:

      python3 postprocess.py out/NAME.plates.json exported.gcode

  Embedding is one line because run_post_process_scripts splits the field on
  newlines and runs each piece as its own command; the source is deflated and
  base64'd to get there, which also keeps it to a single shell word.

  Note that the *CLI* refuses to open a 3mf whose post_process is set --
  `normative_check: postprocess not supported` -- unless given
  --normative-check=0. That guard lives in the CLI entry point and is gated on a
  CLI-only option; the GUI, which is what actually runs these scripts, does not
  apply it.

Two facts make the edit safe. Bambu emits M83, so extrusion is relative and
dropping an E word is a purely local change -- nothing downstream needs its
numbers rewritten. And support is labelled: `; FEATURE: Support` is distinct from
`; FEATURE: Support interface`, so the interface film in the gaps, the thing the
whole stack exists to preserve, is never touched.

We strip the E word rather than delete the line. A deleted move would leave the
nozzle at the wrong place and the next extrusion would draw a stripe across the
plate to catch up. Pure-E moves (retractions) are left alone so retract and
unretract stay balanced.

Known limitation: tool changes are not removed. If support base is a different
filament from the object, a layer whose only base extrusion was balcony still
pays for its change and flush. The report counts those layers.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

FEATURE = re.compile(r"^; FEATURE: *(.+?) *$")
Z_HEIGHT = re.compile(r"^; Z_HEIGHT: *([-\d.]+)")
MOVE = re.compile(r"^G([0123])(?![0-9])")
WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")

# What counts as balcony. "Support interface" is deliberately absent.
STRIP_FEATURES = frozenset({"Support", "Support transition"})
# What defines the object's true footprint on the bed.
WALL_FEATURES = frozenset({"Outer wall"})

EPS = 1e-6


@dataclass(frozen=True)
class Box:
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float

    def shifted(self, dx: float, dy: float) -> Box:
        return Box(self.x0 + dx, self.x1 + dx, self.y0 + dy, self.y1 + dy,
                   self.z0, self.z1)

    def grown(self, m: float) -> Box:
        return Box(self.x0 - m, self.x1 + m, self.y0 - m, self.y1 + m,
                   self.z0, self.z1)

    def holds_xy(self, x: float, y: float) -> bool:
        return self.x0 - EPS <= x <= self.x1 + EPS and self.y0 - EPS <= y <= self.y1 + EPS

    def spans_z(self, z: float) -> bool:
        """A plate's own height, bottom face exclusive, top face inclusive.

        Support below z0 is what carries the plate; support at z0 exactly is that
        contact, not a balcony. Above z1 is the gap, which is interface.
        """
        return self.z0 + EPS < z <= self.z1 + EPS


def boxes_from(doc: dict) -> tuple[Box, tuple[Box, ...]]:
    if doc.get("version") != 1:
        raise SystemExit(f"unknown plates.json version {doc.get('version')!r}")
    as_box = lambda d: Box(d["x0"], d["x1"], d["y0"], d["y1"], d["z0"], d["z1"])
    return as_box(doc["bbox"]), tuple(as_box(p) for p in doc["plates"])


def load_plates(path: Path) -> tuple[Box, tuple[Box, ...], dict]:
    doc = json.loads(path.read_text())
    try:
        model, boxes = boxes_from(doc)
    except SystemExit as e:
        raise SystemExit(f"{path}: {e}") from None
    return model, boxes, doc


def check_z(lines, boxes: tuple[Box, ...], layer: float) -> None:
    """Verify the plate heights describe the object that was actually sliced.

    The footprint check cannot see this. Two builds of the same plates at
    different gaps have identical outlines and differ only in height, so a
    mismatched pair passes it while every box drifts by one gap per plate --
    which deletes support that carries a plate and leaves the balconies it was
    supposed to remove.

    Every plate above the first rests on a gap filled with interface, so there
    must be interface extrusion at each plate's z0. That is a direct test of the
    z registration, not a proxy for it.
    """
    seen = set()
    for _, _, _, z, _, _, _, _, e in scan(lines, frozenset({"Support interface"})):
        if e > 0:
            seen.add(round(z, 3))
    if not seen:
        return                      # no interface at all: nothing to check against
    missing = [b.z0 for b in boxes[1:]
               if not any(abs(z - b.z0) <= layer / 2 for z in seen)]
    if missing:
        raise SystemExit(
            f"the plate heights do not line up with this G-code.\n"
            f"  expected support interface at each plate's base; found none at "
            f"{', '.join(f'{z:.2f}' for z in missing[:5])} mm\n"
            f"  sliced object is {max(seen):.2f} mm at its topmost interface, "
            f"this plates.json describes plates up to {boxes[-1].z1:.2f} mm\n"
            f"The STL that was sliced and this plates.json are from different "
            f"runs -- most likely a different gap, which changes every height "
            f"but no outline. Re-slice the STL that was generated alongside "
            f"this file, or regenerate both.")


def apply(doc: dict, gcode: Path, margin: float = 0.0, tolerance: float = 1.0,
          dry_run: bool = False) -> dict:
    """Strip the balconies from one G-code file. The whole job, given the data."""
    model, boxes = boxes_from(doc)
    lines = gcode.read_text(errors="surrogateescape").splitlines(keepends=True)
    dx, dy = bed_offset(lines, model, tolerance)
    check_z(lines, boxes, doc.get("layer_height", 0.2))
    out, stat = process(lines, boxes, dx, dy, margin)
    if not dry_run:
        gcode.write_text("".join(out), errors="surrogateescape")
    return {**stat, "dx": dx, "dy": dy, "plates": len(boxes),
            "gap_mm": doc.get("gap_mm")}


def report(stat: dict) -> str:
    mm3, g = grams(stat["filament_mm"])
    return (f"balconies: {stat['plates']} plates, gap {stat['gap_mm']} mm, "
            f"object placed at {stat['dx']:+.2f}, {stat['dy']:+.2f} mm\n"
            f"  stripped {stat['moves']} moves across {stat['layers']} layers\n"
            f"  removed {stat['filament_mm']:.1f} mm filament "
            f"= {mm3:.0f} mm3 = {g:.1f} g")


def run_embedded(doc: dict) -> None:
    """Entry point for the copy of this file embedded in a 3mf.

    The slicer appends the G-code path to the command line, so it arrives as
    argv[1]. Anything printed on stdout is discarded; only a non-zero exit is
    surfaced, as a dialog, which is why failures raise.
    """
    print(report(apply(doc, Path(sys.argv[1]))))


def scan(lines, want: frozenset[str]):
    """Yield (index, line, feature, z, x0, y0, x1, y1, extruding) for moves.

    Position is tracked across every move, not just interesting ones, or the
    start point of the next interesting one would be wrong.

    It starts *unknown* rather than at the origin: the nozzle's position before
    the first move that sets it is genuinely not known, and assuming (0, 0) --
    a real point on the bed -- would drag a bounding box to the corner and make
    a containment test answer for a place the nozzle never was. Callers get
    None for a start point and must decide; both here refuse to use it.
    """
    feature, z, x, y = "", 0.0, None, None
    for i, line in enumerate(lines):
        if line.startswith(";"):
            if m := FEATURE.match(line):
                feature = m.group(1)
            elif m := Z_HEIGHT.match(line):
                z = float(m.group(1))
            continue
        if not (m := MOVE.match(line)):
            continue
        words = dict(WORD.findall(line))
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        if nx is None or ny is None:
            x, y = nx, ny
            continue
        if feature in want and ("X" in words or "Y" in words):
            e = float(words["E"]) if "E" in words else 0.0
            yield i, line, feature, z, x, y, nx, ny, e
        x, y = nx, ny


def bed_offset(lines, model: Box, tol: float) -> tuple[float, float]:
    """Recover where the slicer put the object, by matching outer walls to the model.

    Nothing in the G-code says where the object was placed -- Bambu emits an
    OBJECT_ID but no polygon or centre -- so it has to be inferred. Outer walls
    are the object's true silhouette: support can sit outside them and brim
    always does, so anything wider would measure the wrong thing.

    Matching centres cancels the half-extrusion-width the wall adds on each side.
    Matching *dimensions* is then free verification: a rotated, scaled or simply
    different object will not match, and we stop rather than deface the file.
    """
    lo_x = lo_y = float("inf")
    hi_x = hi_y = float("-inf")
    for _, _, _, _, px, py, nx, ny, e in scan(lines, WALL_FEATURES):
        if e <= 0:
            continue
        xs = (nx,) if px is None else (px, nx)
        ys = (ny,) if py is None else (py, ny)
        lo_x, hi_x = min(lo_x, *xs), max(hi_x, *xs)
        lo_y, hi_y = min(lo_y, *ys), max(hi_y, *ys)
    if lo_x == float("inf"):
        raise SystemExit("no outer wall extrusions found: is this a Bambu G-code "
                         "with verbose comments enabled?")

    got_w, got_d = hi_x - lo_x, hi_y - lo_y
    want_w, want_d = model.x1 - model.x0, model.y1 - model.y0
    if abs(got_w - want_w) > tol or abs(got_d - want_d) > tol:
        raise SystemExit(
            f"object does not match the stack this plates.json describes:\n"
            f"  sliced outer walls span {got_w:.2f} x {got_d:.2f} mm\n"
            f"  the stack is           {want_w:.2f} x {want_d:.2f} mm\n"
            f"Rotated or scaled on the bed, or a different model. Refusing to "
            f"edit, since the boxes would land in the wrong place.")

    return ((lo_x + hi_x) / 2 - (model.x0 + model.x1) / 2,
            (lo_y + hi_y) / 2 - (model.y0 + model.y1) / 2)


def strip_e(line: str) -> str:
    """Turn an extrusion into a travel, leaving every other word alone."""
    return re.sub(r" *\bE-?\d*\.?\d+", "", line, count=1)


def process(lines: list[str], boxes: tuple[Box, ...], dx: float, dy: float,
            margin: float) -> tuple[list[str], dict]:
    placed = tuple(b.shifted(dx, dy).grown(margin) for b in boxes)
    out = list(lines)
    stripped, e_total, layers = 0, 0.0, set()
    for i, line, _, z, px, py, nx, ny, e in scan(lines, STRIP_FEATURES):
        if e <= 0 or px is None or py is None:
            continue
        if not any(b.spans_z(z) and b.holds_xy(px, py) and b.holds_xy(nx, ny)
                   for b in placed):
            continue
        out[i] = strip_e(line)
        stripped += 1
        e_total += e
        layers.add(round(z, 3))
    return out, {"moves": stripped, "filament_mm": e_total, "layers": len(layers)}


def grams(mm: float) -> tuple[float, float]:
    """Filament length to volume and mass, using the slicer's own settings if it set them."""
    dia = float(os.environ.get("SLIC3R_filament_diameter", "1.75").split(",")[0])
    den = float(os.environ.get("SLIC3R_filament_density", "1.24").split(",")[0])
    mm3 = mm * 3.141592653589793 * (dia / 2) ** 2
    return mm3, mm3 * den / 1000.0


SETTINGS = "Metadata/project_settings.config"


def embedded_command(doc: dict, python: str = "/usr/bin/python3") -> str:
    """This whole file, plus the plate data, as one shell command line.

    Pointing the slicer at a script on disk does not survive being shared: the
    working directory is the app's, so the path must be absolute, and
    `post_process` is a process setting stored inside the 3mf -- so a machine's
    own paths would travel with every project saved from that profile. Carrying
    the source instead makes the 3mf self-contained.

    One line, because run_post_process_scripts splits the field on newlines and
    runs each piece as a separate command. Deflated and base64'd, so it is a
    single shell word with no quoting hazards: the alphabet is [A-Za-z0-9+/=],
    none of which mean anything to sh or fish inside double quotes.
    """
    future = "from __future__ import annotations\n"
    # Anchored to the start of a line: the same text appears just below as a
    # string literal, and a positional replace would eventually strip the wrong
    # one if this function ever moved above the import.
    src, n = re.subn(r"^from __future__ import annotations\n", "",
                     Path(__file__).read_text(), count=1, flags=re.M)
    if n != 1:
        raise SystemExit("cannot find the __future__ import to hoist")
    # The future import has to be the first statement in the payload, and the
    # plate data has to precede the source so the __main__ guard can see the
    # sentinel. Only one order satisfies both.
    payload = (f"{future}"
               # repr, not json.dumps: this is Python source, and JSON writes
               # false/true/null, which are not Python names. The doc holds only
               # scalars, lists and dicts, so its repr is a valid literal.
               f"EMBEDDED_PLATES = {doc!r}\n"
               f"{src}\n"
               f"run_embedded(EMBEDDED_PLATES)\n")
    blob = base64.b64encode(zlib.compress(payload.encode(), 9)).decode()
    return (f'{python} -c "import base64,zlib;'
            f"exec(zlib.decompress(base64.b64decode('{blob}')))\"")


def install(mf: Path, line: str) -> None:
    """Put the command into a 3mf's process settings, in place.

    A 3mf is a zip, and a zip cannot be edited in place, so it is rebuilt --
    every other entry byte-for-byte with its original compression, so nothing
    but the one setting changes.
    """
    with zipfile.ZipFile(mf) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    if not any(i.filename == SETTINGS for i, _ in items):
        raise SystemExit(f"{mf}: no {SETTINGS} inside. Is this a Bambu project "
                         f"3mf saved from File > Save Project, rather than a "
                         f"plain model export?")

    tmp = mf.with_suffix(mf.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        for info, data in items:
            if info.filename == SETTINGS:
                cfg = json.loads(data)
                cfg["post_process"] = [line]
                data = json.dumps(cfg, indent=4).encode()
            z.writestr(info, data, compress_type=info.compress_type)
    tmp.replace(mf)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plates", type=Path, help="the NAME.plates.json beside the STL")
    ap.add_argument("gcode", type=Path, nargs="?",
                    help="sliced G-code; edited in place. Omit with --embed "
                         "or --install")
    ap.add_argument("--embed", action="store_true",
                    help="print this script and the plate data as one "
                         "self-contained post-processing command line")
    ap.add_argument("--install", type=Path, metavar="PROJECT.3mf",
                    help="write that command into a project 3mf's post-processing "
                         "setting, so the 3mf carries it and needs nothing on disk")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="grow each plate box outward, mm. Positive eats into the "
                         "legitimate support beside a plate, negative leaves a rim "
                         "of balcony (default 0)")
    ap.add_argument("--tolerance", type=float, default=1.0,
                    help="how far the sliced object may differ from the stack's "
                         "footprint before we refuse to edit, mm (default 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be stripped, write nothing")
    args = ap.parse_args(argv)

    if args.embed or args.install:
        doc = json.loads(args.plates.read_text())
        boxes_from(doc)                      # fail now, not at export time
        line = embedded_command(doc)
        if args.install:
            install(args.install, line)
            print(f"{args.install}: post-processing set, {len(line)} chars, "
                  f"{len(doc['plates'])} plates. The 3mf now carries the script; "
                  f"nothing else has to be installed.")
        else:
            print(line)
        return 0

    if args.gcode is None:
        ap.error("a G-code file is required unless --embed or --install is given")

    for f in (args.plates, args.gcode):
        if not f.exists():
            raise SystemExit(
                f"{f}: no such file.\n"
                "If that path is relative, that is the cause. Bambu Studio runs "
                "post-processing scripts through $SHELL -c inheriting the app's "
                "own working directory, which is / when it was launched from "
                "Finder. Every path in the box must be absolute, the interpreter "
                "included.")

    model, boxes, doc = load_plates(args.plates)
    stat = apply(doc, args.gcode, args.margin, args.tolerance, args.dry_run)
    print(report(stat))
    if not stat["moves"]:
        print("  nothing matched -- check the stack is the object being sliced")

    return 0


if __name__ == "__main__" and "EMBEDDED_PLATES" not in globals():
    # Under `python3 -c`, __name__ is "__main__" too. The sentinel is defined
    # only by the embedded payload, which calls run_embedded itself.
    raise SystemExit(main())
