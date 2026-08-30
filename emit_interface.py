#!/usr/bin/env python3
"""Write the interface into a sliced file, as toolpaths at heights we choose.

    python3 emit_interface.py --project out/NAME.3mf \
        --gcode out/slice/plate_1.gcode --out out/NAME.gcode

The film used to be a mesh handed to the slicer, and the slicer's sample planes
owned its position: a nominal 0.1 mm clearance printed as 0 mm on one stack and
0.2 mm on another, decided by float rounding that depends on the stack's height
(ADR 0009). Extrusion Z is a number written into the file, so it owes the layer
grid nothing.

What this does *not* do is fabricate a filament change. Bambu's tool changes
carry purge volumes, flush lengths and prime tower moves computed from the
filament pairing, and a hand-written one is wrong in ways that only appear on the
printer. The decoy column beside the stack is supported in the interface filament
at exactly the stack's gap heights, so the slicer produces a real change there of
its own accord; every interface layer is inserted at a seam where that filament
is already loaded and purged, and the emitter refuses to write one where it is
not.

Whole layer blocks, not additions to existing ones: with the film gone there is
no model material in a gap, so at the height the interface wants there is often
no layer at all to add to.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import gcode

PLAN = "Metadata/interface_plan.json"
GCODE = "Metadata/plate_1.gcode"
SLICE_INFO = "Metadata/slice_info.config"


PROFILES = Path("/Applications/BambuStudio.app/Contents/Resources/profiles/BBL/machine")


def machine_id(model: str) -> str:
    """Bambu's internal code for a printer, e.g. "BL-P001" for the X1 Carbon."""
    f = PROFILES / f"{model}.json"
    if f.exists():
        return json.loads(f.read_text()).get("model_id", model)
    return model


def package(project: Path, emitted: Path, out: Path, hdr: gcode.Header,
            cfg: dict[str, str], used: dict[int, tuple[float, float]]) -> None:
    """Wrap the emitted G-code as a sliced 3mf, which is what a printer is sent.

    A bare .gcode cannot be sent with filament mapping. Bambu's send dialog
    builds its mapping table from `Metadata/slice_info.config`, which only a
    sliced package has -- measured: the unmodified output of the slicer, opened
    as a .gcode, reports "not all filaments used in slicing are mapped" exactly
    as our own does. It is the container that is missing, not anything in the
    G-code.

    Bambu's own CLI will not produce one for this project. It refuses
    post-processing outright (`normative_check: postprocess not supported`), and
    the GUI will not slice the project at all, because the stack has no model
    material in its gaps and that trips the empty-layer check. So the package is
    built here, from the project that was sliced plus the G-code that came out.
    """
    text = emitted.read_bytes()
    # Bambu's own code for the machine, not its display name: the X1 Carbon is
    # "BL-P001", from its machine profile's model_id. Read from the installed
    # profiles rather than hardcoded, because this is the one field the printer
    # matches against itself.
    model_id = machine_id(cfg.get("printer_model", ""))
    colours = [c.strip() for c in cfg.get("filament_colour", "").split(";")]
    ids = [c.strip() for c in cfg.get("filament_ids", "").split(";")]
    types = [c.strip() for c in cfg.get("filament_type", "").split(";")]
    rows = "".join(
        f'    <filament id="{t + 1}" tray_info_idx="{ids[t] if t < len(ids) else ""}" '
        f'type="{types[t] if t < len(types) else "PLA"}" '
        f'color="{colours[t] if t < len(colours) else "#FFFFFF"}" '
        f'used_m="{m / 1000:.2f}" used_g="{g:.2f}"/>\n'
        for t, (m, g) in sorted(used.items()))
    info = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
            '  <header>\n'
            '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
            f'    <header_item key="X-BBL-Client-Version" '
            f'value="{cfg.get("BambuStudio", "02.08.02.61")}"/>\n'
            '  </header>\n  <plate>\n'
            '    <metadata key="index" value="1"/>\n'
            f'    <metadata key="printer_model_id" value="{model_id}"/>\n'
            f'    <metadata key="nozzle_diameters" '
            f'value="{cfg.get("nozzle_diameter", "0.4")}"/>\n'
            '    <metadata key="timelapse_type" value="0"/>\n'
            f'    <metadata key="prediction" value="{hdr.seconds}"/>\n'
            f'    <metadata key="weight" value="{sum(g for _, g in used.values()):.2f}"/>\n'
            '    <metadata key="outside" value="false"/>\n'
            '    <metadata key="support_used" value="true"/>\n'
            '    <metadata key="label_object_enabled" value="true"/>\n'
            + rows + '  </plate>\n</config>\n')

    with zipfile.ZipFile(project) as z:
        entries = [(i.filename, z.read(i.filename)) for i in z.infolist()]

    # The plate has to say it is sliced, or Studio never looks at the G-code: it
    # lists the filaments the *model* uses instead, which since the interface
    # stopped being a model part is exactly one -- the stack's. That is the
    # single filament the send dialog offered to map.
    settings = next(d for n, d in entries
                    if n == "Metadata/model_settings.config").decode()
    if "gcode_file" not in settings:
        settings = settings.replace(
            '<metadata key="locked" value="false"/>',
            '<metadata key="locked" value="false"/>\n'
            f'    <metadata key="gcode_file" value="{GCODE}"/>', 1)

    rep = {GCODE: text,
           GCODE + ".md5": hashlib.md5(text).hexdigest().upper().encode(),
           SLICE_INFO: info.encode(),
           "Metadata/model_settings.config": settings.encode()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        for name, data in entries:
            z.writestr(name, rep.get(name, data))
            seen.add(name)
        for name, data in rep.items():
            if name not in seen:
                z.writestr(name, data)


def load_plan(source: Path) -> dict:
    """The plan, from a 3mf that carries it or from the JSON file directly."""
    if source.suffix == ".3mf":
        with zipfile.ZipFile(source) as z:
            if PLAN not in z.namelist():
                raise SystemExit(f"{source}: no {PLAN}. Build the 3mf with "
                                 f"make3mf.py --interface-plan.")
            plan = json.loads(z.read(PLAN))
    else:
        plan = json.loads(source.read_text())
    if plan.get("space") != "bed":
        raise SystemExit(f"{source}: the plan is in {plan.get('space')!r} "
                         f"coordinates; it has to be the one make3mf wrote onto "
                         f"the bed, not stack_plates' model-space one.")
    return plan


def config(lines: tuple[str, ...]) -> dict[str, str]:
    """The CONFIG_BLOCK Bambu writes at the end of the file, as a dict.

    Read rather than assumed: the flow of an extrusion depends on the filament's
    own flow ratio and diameter, and getting it from the file being edited is the
    only way it cannot disagree with the rest of that file.
    """
    out: dict[str, str] = {}
    inside = False
    for ln in lines:
        if ln.startswith("; CONFIG_BLOCK_START"):
            inside = True
        elif ln.startswith("; CONFIG_BLOCK_END"):
            break
        elif inside and ln.startswith("; ") and " = " in ln:
            k, _, v = ln[2:].partition(" = ")
            out[k.strip()] = v.strip()
    return out


def per_filament(cfg: dict[str, str], key: str, tool: int, default: str) -> str:
    """One filament's value out of a comma-separated per-filament setting."""
    vals = [v.strip() for v in cfg.get(key, "").split(",") if v.strip()]
    if not vals:
        return default
    # Some of these are written once for the whole machine and some once per
    # filament; the same key is both, depending on the profile.
    return vals[tool] if tool < len(vals) else vals[0]


def flow(width: float, height: float, diameter: float, ratio: float) -> float:
    """Filament millimetres per millimetre of path.

    An extrusion is a rectangle with semicircular sides, not a rectangle: the
    same formula Slic3r and its descendants use. Checked against the mesh film's
    own sliced G-code, where 0.45 x 0.20 at a flow ratio of 0.95 comes to
    0.03217 mm of filament per mm of path -- the emitter reproduces that to four
    decimal places, which is how the model was confirmed rather than assumed.
    """
    mm3 = ((width - height) * height + math.pi * (height / 2) ** 2) * ratio
    return mm3 / (math.pi * (diameter / 2) ** 2)


def machine_offset(cfg: dict[str, str]) -> tuple[float, float]:
    """Plate coordinates are not machine coordinates.

    A 3mf item's transform places an object on the plate; the G-code is written
    in the machine's own frame, and Bambu subtracts the extruder's offset from
    the plate frame to get there. On an X1C that is `extruder_offset = 0x2`, so
    everything the slicer emits sits 2 mm lower in Y than its item transform
    says.

    This was found the way everything here is found. The interface was placed
    from the item transform alone and landed 2 mm off the stack, and no check
    caught it, because the clearances were being measured inside the interface's
    own footprint -- a plan shifted bodily off the model measures perfectly
    against itself. `verify.py` now checks the interface against the *plate's*
    printed footprint instead, which is what would have caught it.
    """
    first = cfg.get("extruder_offset", "0x0").split(",")[0]
    x, _, y = first.partition("x")
    return -float(x or 0), -float(y or 0)


@dataclass(frozen=True)
class Seam:
    """A point between two of the slicer's layers, where one can be inserted."""
    line: int           # index of the following layer's "; CHANGE_LAYER"
    z: float            # Z of the layer that ends here
    tool: int           # filament loaded at that moment


def obstructed(ms: tuple[gcode.Move, ...], line: int, z0: float,
               box: tuple[float, float, float, float]) -> int:
    """Extrusions already down that an interface layer would print into.

    A layer is inserted between two of the slicer's, so its Z band routinely
    overlaps theirs -- the decoy's support prints at 4.40 and 4.60 while the
    interface occupies 4.20 to 4.70. That is not a collision, because the decoy
    is somewhere else on the plate. What would be a collision is material at the
    interface's own footprint and above the interface's own floor, and with the
    blocker doing its job there is none: the gap is empty.
    """
    x0, y0, x1, y1 = box
    return sum(1 for m in ms
               if m.line < line and m.extruding and m.z > z0 + 1e-6
               and min(m.x0, m.x1) <= x1 and max(m.x0, m.x1) >= x0
               and min(m.y0, m.y1) <= y1 and max(m.y0, m.y1) >= y0)


def seams(lines: tuple[str, ...], ms: tuple[gcode.Move, ...]) -> tuple[Seam, ...]:
    """Every layer boundary in the file, with the Z and tool in force there.

    The tool is the one the *previous* layer left loaded, which is what an
    inserted block would inherit -- not the one the next layer changes to.
    """
    starts = [i for i, ln in enumerate(lines) if ln.startswith("; CHANGE_LAYER")]
    out, k, z, tool = [], 0, None, -1
    for i in starts[1:] + [len(lines)]:
        while k < len(ms) and ms[k].line < i:
            tool = ms[k].tool
            if ms[k].extruding:
                z = round(ms[k].z, 4)
            k += 1
        if z is not None:
            out.append(Seam(i, z, tool))
    return tuple(out)


def block(z1: float, height: float, beads, tool_speed: int, travel: int,
          e_per_mm: float, width: float, label: str,
          retract: float, min_travel: float) -> list[str]:
    """One synthesised layer: travel in at the exact Z, the raster, retract out.

    No tool change of its own. The caller has already established that the
    interface filament is loaded at this seam, because a bare `T` here would
    switch filament with none of the purge that makes the switch clean.

    The layer counters go in as zeroes and `renumber()` fills them: they depend
    on how many blocks end up before this one, which is not known until every
    block has been placed.
    """
    x0, y0 = beads[0][0], beads[0][1]
    out = ["; CHANGE_LAYER",
           f"; Z_HEIGHT: {z1:g}",
           f"; LAYER_HEIGHT: {height:g}",
           "; layer num/total_layer_count: 0/0",
           "; update layer progress",
           "M73 L0",
           "M991 S0 P0 ;notify layer change",
           f"; INTERFACE {label}",
           "M204 S6000",
           # Travel across at whatever height the last layer left the nozzle at,
           # then come down. Everything already printed is below this Z, so the
           # move is clear without a hop.
           f"G1 X{x0:.3f} Y{y0:.3f} F{travel}",
           f"G1 Z{z1:g}",
           f"G1 E{retract:g} F1800",
           # Its own feature name, not "Support interface": the check that no
           # support landed on the stack works by looking for support in the
           # output, and it must not find the interface we put there ourselves.
           "; FEATURE: Interface",
           f"; LINE_WIDTH: {width:g}"]
    at = (x0, y0)
    for bx0, by0, bx1, by1 in beads:
        if math.hypot(bx0 - at[0], by0 - at[1]) > 1e-6:
            hop = math.hypot(bx0 - at[0], by0 - at[1]) > min_travel
            if hop:
                out.append(f"G1 E-{retract:g} F1800")
            out.append(f"G1 X{bx0:.3f} Y{by0:.3f} F{travel}")
            if hop:
                out.append(f"G1 E{retract:g} F1800")
        e = math.hypot(bx1 - bx0, by1 - by0) * e_per_mm
        out.append(f"G1 X{bx1:.3f} Y{by1:.3f} E{e:.4f} F{tool_speed}")
        at = (bx1, by1)
    out.append(f"G1 E-{retract:g} F1800")
    return out


_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def cost(lines: Iterable[str]) -> tuple[float, float]:
    """Seconds and millimetres of filament a synthesised block adds.

    A floor, not a promise: it is the moves at their commanded feed rates, with
    no acceleration and no cooling holds, so the printer will be slower. The
    slicer's own estimate in the header covers the file it produced and knows
    nothing about what was inserted afterwards, and it is not recomputed -- this
    number is what says how far out it now is.
    """
    x = y = f = 0.0
    seconds = filament = 0.0
    for ln in lines:
        if not ln.startswith("G1 "):
            continue
        w = dict(_WORD.findall(ln))
        f = float(w.get("F", f)) or 1.0
        nx, ny = float(w.get("X", x)), float(w.get("Y", y))
        e = float(w.get("E", 0.0))
        d = math.hypot(nx - x, ny - y)
        seconds += (d if d else abs(e)) / f * 60
        # Signed: a retraction gives the filament back, and counting only the
        # pushes charges 0.8 mm for every bead in the raster -- which came to
        # eight times the interface's real weight the first time it was printed.
        filament += e
        x, y = nx, ny
    return seconds, filament


_LAYER_NUM = re.compile(r"^; layer num/total_layer_count: (\d+)/(\d+)")
_M73L = re.compile(r"^M73 L(\d+)")
_M991 = re.compile(r"^M991 S0 P(-?\d+)")
_OBJ_START = re.compile(r"^; object ids of layer (\d+) start:")
_OBJ_END = re.compile(r"^; object ids of this layer(\d+) end:")


def renumber(lines: list[str], total: int) -> list[str]:
    """Make every per-layer counter agree with the layers now in the file.

    These are not comments. `M73 L` drives the printer's layer display, `M991`
    drives the timelapse, and the header's total is what the progress bar is a
    fraction of -- a file whose counters stop at 44 while it prints 46 layers is
    wrong in the machine, not on paper.

    `M73 P`/`R` are left alone: they are the slicer's time estimate, not a count,
    and the interface's own time is not modelled here. They now run slightly
    ahead of the truth by however long the interface takes.
    """
    out, n = [], 0
    for ln in lines:
        if ln.startswith("; CHANGE_LAYER"):
            n += 1
        m = _LAYER_NUM.match(ln)
        if m:
            out.append(f"; layer num/total_layer_count: {n}/{total}")
            continue
        m = _M73L.match(ln)
        if m:
            out.append(f"M73 L{n}")
            continue
        m = _M991.match(ln)
        if m:
            out.append(f"M991 S0 P{n - 1}" + ln[m.end():])
            continue
        m = _OBJ_START.match(ln)
        if m:
            out.append(f"; object ids of layer {n} start:" + ln[m.end():])
            continue
        m = _OBJ_END.match(ln)
        if m:
            out.append(f"; object ids of this layer{n} end:" + ln[m.end():])
            continue
        if ln.startswith("; total layer number:"):
            out.append(f"; total layer number: {total}")
            continue
        out.append(ln)
    return out


def emit(project: Path, src: Path, out: Path, quiet: bool = False,
         pack: Path | None = None) -> int:
    plan = load_plan(project)
    if not quiet:
        print(f"reading {src}")
    hdr, lines, ms = gcode.read(src)
    cfg = config(lines)
    tool = plan["interface_extruder"] - 1
    width = plan["line_width"]

    diameter = float(per_filament(cfg, "filament_diameter", tool, "1.75"))
    ratio = float(per_filament(cfg, "filament_flow_ratio", tool, "1"))
    speed = int(float(per_filament(cfg, "support_interface_speed", tool, "80")) * 60)
    bridge = int(float(cfg.get("bridge_speed", "50")) * 60)
    travel = int(float(cfg.get("travel_speed", "500")) * 60)
    # The first layer of each gap is laid over the clearance below it, with
    # nothing to squish against. That is a bridge, so it gets the bridge's speed
    # and the bridge's flow -- both the slicer's own numbers for this file, which
    # is where a print-tuning parameter belongs.
    bridge_flow = float(cfg.get("bridge_flow", "1"))
    retract = float(per_filament(cfg, "retraction_length", tool, "0.8"))
    min_travel = float(per_filament(cfg, "retraction_minimum_travel", tool, "1"))

    ox, oy = machine_offset(cfg)
    where = seams(lines, ms)
    layers = [dict(l, beads=[[bx0 + ox, by0 + oy, bx1 + ox, by1 + oy]
                             for bx0, by0, bx1, by1 in l["beads"]])
              for l in sorted(plan["layers"], key=lambda l: l["z1"])]
    blocks: dict[int, list[str]] = {}
    fails: list[str] = []
    for lay in layers:
        if not lay["beads"]:
            continue
        z1, z0 = lay["z1"], lay["z0"]
        below = [s for s in where if s.z <= z1 + 1e-6]
        if not below:
            fails.append(f"gap {lay['gap']} layer {lay['index']}: nothing is "
                         f"printed below z={z1:g}, so there is no seam to insert at")
            continue
        seam = below[-1]
        if seam.z >= z1 - 1e-6:
            fails.append(f"gap {lay['gap']} layer {lay['index']} at z={z1:g}: the "
                         f"file already prints at {seam.z:g} there, so the nozzle "
                         f"would not be rising into this layer")
            continue
        box = (min(min(b[0], b[2]) for b in lay["beads"]) - width,
               min(min(b[1], b[3]) for b in lay["beads"]) - width,
               max(max(b[0], b[2]) for b in lay["beads"]) + width,
               max(max(b[1], b[3]) for b in lay["beads"]) + width)
        n = obstructed(ms, seam.line, z0, box)
        if n:
            fails.append(f"gap {lay['gap']} layer {lay['index']} at z={z1:g}: "
                         f"{n} extrusions are already down inside its footprint "
                         f"above z={z0:g} -- the gap is not empty")
        if seam.tool != tool:
            fails.append(f"gap {lay['gap']} layer {lay['index']} at z={z1:g}: the "
                         f"interface filament (T{tool}) is not loaded at that "
                         f"seam, T{seam.tool} is. The decoy is not provoking a "
                         f"filament change there, and writing one here would skip "
                         f"the purge.")
            continue
        first = lay["index"] == 0
        e_per_mm = flow(width, z1 - z0, diameter,
                        ratio * (bridge_flow if first else 1.0))
        blocks.setdefault(seam.line, []).extend(block(
            z1, z1 - z0, lay["beads"],
            bridge if first else speed, travel, e_per_mm, width,
            f"gap {lay['gap']} layer {lay['index']}", retract, min_travel))
    if fails:
        print("cannot emit the interface:", file=sys.stderr)
        for f in fails:
            print("  -", f, file=sys.stderr)
        return 1

    merged: list[str] = []
    for i, ln in enumerate(lines):
        merged.extend(blocks.get(i, ()))
        merged.append(ln)
    total = sum(1 for ln in merged if ln.startswith("; CHANGE_LAYER"))
    out.write_text("\n".join(renumber(merged, total)) + "\n")
    if not quiet:
        added = sum(len(b) for b in blocks.values())
        secs, mm = cost(ln for b in blocks.values() for ln in b)
        grams = mm * math.pi * (diameter / 2) ** 2 * float(
            per_filament(cfg, "filament_density", tool, "1.24")) / 1000
        print(f"wrote {out}")
        print(f"  {len(layers)} interface layers, "
              f"{sum(len(l['beads']) for l in layers)} beads, "
              f"{added} lines into a {len(lines)}-line file")
        print(f"  layers {hdr.layers} -> {total}, interface filament T{tool} "
              f"at {width:g} mm wide")
        print(f"  plate to machine coordinates: ({ox:+g}, {oy:+g}) mm, from "
              f"extruder_offset")
        print(f"  adds at least {secs / 60:.1f} min and {grams:.3f} g on top of "
              f"the slicer's {hdr.seconds / 60:.0f} min; the header's estimate "
              f"is not recomputed")
    if pack is not None:
        _, _, after = gcode.read(out)
        used = {}
        for t, mm in gcode.filament_used(after).items():
            used[t] = (mm, mm * math.pi * (float(per_filament(
                cfg, "filament_diameter", t, "1.75")) / 2) ** 2 * float(
                per_filament(cfg, "filament_density", t, "1.24")) / 1000)
        package(project, out, pack, hdr, cfg, used)
        if not quiet:
            print(f"packaged {pack}  ({', '.join(f'filament {t + 1} {g:.2f} g' for t, (_, g) in sorted(used.items()))})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="As a slicer post-processing script, set Bambu's post_process to "
               "`python3 /abs/emit_interface.py --plan /abs/NAME.interface.json` "
               "-- the slicer appends the G-code path and the file is rewritten "
               "in place, so what Bambu then packages and sends to the printer "
               "already has the interface in it.")
    ap.add_argument("--project", type=Path,
                    help="the 3mf that was sliced; carries the interface plan")
    ap.add_argument("--plan", type=Path,
                    help="the plan as a file, for use as a post-processing "
                         "script where the 3mf is not to hand. It must be the "
                         "one make3mf wrote onto the bed")
    ap.add_argument("--gcode", type=Path, help="the sliced G-code")
    ap.add_argument("--out", type=Path,
                    help="where to write; without it the G-code is rewritten in "
                         "place, which is what a post-processing script must do")
    ap.add_argument("--package", type=Path, metavar="OUT.gcode.3mf",
                    help="also write a sliced 3mf around the result. A bare "
                         ".gcode cannot be sent to the printer with filament "
                         "mapping -- the send dialog builds its table from the "
                         "package's slice_info.config, and reports every "
                         "filament unmapped without it")
    ap.add_argument("target", nargs="?", type=Path,
                    help="the sliced G-code, as the slicer appends it")
    args = ap.parse_args(argv)

    plan = args.project or args.plan
    if plan is None:
        ap.error("give --project or --plan")
    src = args.gcode or args.target
    if src is None:
        ap.error("give the G-code, as --gcode or as the last argument")
    return emit(plan, src, args.out or src, quiet=args.out is None,
                pack=args.package)


if __name__ == "__main__":
    raise SystemExit(main())
