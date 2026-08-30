#!/usr/bin/env python3
"""Build a Bambu project 3mf for a stack, from a template project.

The template is a project saved out of Bambu Studio with the plate already
arranged -- wipe tower placed, decoy parked, filaments assigned. We keep all of
that and swap in new geometry, so a different stack does not mean re-arranging
anything by hand, and so the 581 keys in project_settings.config stay exactly as
Bambu wrote them rather than being synthesised here.

Three solids go in:

  model    the stack itself, printed in the object filament
  blocker  one box over the whole stack, as a support_blocker part. Nothing the
           slicer generates lands on the model; every gap gets its interface
           from us instead.
  decoy    a small column beside it, with the stack's exact z profile. It is
           there to be supported: the slicer fills its gaps with interface, and
           that is what puts the interface filament in the nozzle at each of the
           stack's gap layers. Without it the slicer has no reason to change
           filament at all.

The decoy and the blocker are derived from the model's plates.json, not passed
in, so they cannot drift out of step with the stack they belong to.

The interface itself is not a solid and is not here. It used to be a fourth --
a film mesh in its own filament -- until the slicer's sample planes turned out
to own its height (ADR 0009). It is written into the sliced G-code afterwards by
emit_interface.py, and what travels in the 3mf is its toolpath plan, moved onto
the bed here because this is the only place the bed transform is known.

3mf layout, as Bambu writes it and as we reproduce it:

  3D/3dmodel.model            resources: one <object> per plate item, each a
                              <components> list referencing a per-object file;
                              <build> carries the bed position in each <item>
  3D/Objects/object_N.model   the actual meshes, stored object-centred
  Metadata/model_settings.config   part subtypes, names, per-part extruder

Meshes are stored centred on their own bounding box and the build item's
transform carries the position, so the translation in the item is the object
centre, not its corner.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import zipfile
from pathlib import Path

from stl_io import Mesh, bounds_of, box, read_stl, translate

HDR = ('<?xml version="1.0" encoding="UTF-8"?>\n'
       '<model unit="millimeter" xml:lang="en-US" '
       'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
       'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
       'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
       'requiredextensions="p">\n')

MODEL_XML = "3D/3dmodel.model"
SETTINGS = "Metadata/model_settings.config"
OBJ_RELS = "3D/_rels/3dmodel.model.rels"
PROJECT = "Metadata/project_settings.config"
PLAN = "Metadata/interface_plan.json"


def mesh_xml(mesh: Mesh, oid: int) -> str:
    """One <object> holding a mesh, with vertices welded.

    3mf indexes triangles into a vertex list, so duplicates have to go or the
    file is three times the size it needs to be and Bambu reports the mesh as
    having unshared edges.
    """
    verts: list[tuple[float, float, float]] = []
    index: dict[tuple[float, float, float], int] = {}
    tris = []
    for f in mesh:
        idx = []
        for k in range(3):
            v = (round(f[3 + k * 3], 6), round(f[4 + k * 3], 6), round(f[5 + k * 3], 6))
            if v not in index:
                index[v] = len(verts)
                verts.append(v)
            idx.append(index[v])
        tris.append(idx)
    out = [f' <object id="{oid}" p:UUID="{oid:08d}-81cb-4c03-9d28-80fed5dfa1dc" '
           f'type="model">\n  <mesh>\n   <vertices>\n']
    out += [f'    <vertex x="{v[0]}" y="{v[1]}" z="{v[2]}"/>\n' for v in verts]
    out.append("   </vertices>\n   <triangles>\n")
    out += [f'    <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>\n' for t in tris]
    out.append("   </triangles>\n  </mesh>\n </object>\n")
    return "".join(out), len(tris)


def centred(mesh: Mesh, cx: float, cy: float, cz: float) -> Mesh:
    return translate(mesh, -cx, -cy, -cz)


def decoy_column(plates: list[dict], size: float) -> Mesh:
    """A miniature of the stack's z profile: one slab per plate, same heights.

    Flat, fully overhanging faces at every level, so there is nothing subtle for
    overhang detection to decide.
    """
    h = size / 2
    return tuple(f for p in plates
                 for f in box(-h, -h, p["z0"], h, h, p["z1"]))


def blocker_box(b, margin: float = 1.0) -> Mesh:
    """One box around whatever is actually being printed, not around what the
    plates.json describes -- so it still covers the model when that model is a
    placeholder of a different size."""
    return box(b.x0 - margin, b.y0 - margin, b.z0 - margin,
               b.x1 + margin, b.y1 + margin, b.z1 + margin)


def dummy_block(plates: list[dict], width: float, depth: float) -> Mesh:
    """A placeholder with the stack's z profile, filling the space a stack gets.

    The repo's template ships one of these instead of a real baseplate: it keeps
    the plate layout, filament assignments and settings that make the template
    useful, without committing somebody's model into the repository. It is built
    like the decoy -- one slab per level, same heights, same gaps -- so the
    template previews as the thing it stands in for.
    """
    w, d = width / 2, depth / 2
    return tuple(f for p in plates
                 for f in box(-w, -d, p["z0"], w, d, p["z1"]))


STRUCTURAL = {"name", "matrix", "source_file", "source_object_id", "source_volume_id",
              "source_offset_x", "source_offset_y", "source_offset_z", "extruder"}


# Names of the parts that are not the stack. "interface" is kept although this
# tool no longer writes one: a template saved before the interface became G-code
# still has that part, and its settings are not the stack's.
OTHER_PARTS = ("interface", "nosupport", "blocker", "decoy")


def model_part_settings(xml: str) -> list[tuple[str, str]]:
    """Per-part print settings from the template's model part.

    The stack is not the only part in a template, and it is not the first: a
    reader that took the part named for the interface silently dropped everything
    set on the stack itself -- a bottom shell thickness, in the case that found
    this -- and dropped it on the next regeneration, when the template had looked
    correct in the slicer.
    """
    for block in re.findall(r"<part\b.*?</part>", xml, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', block)
        if not name or any(m in name.group(1).lower() for m in OTHER_PARTS):
            continue
        return [(k, v) for k, v in
                re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', block)
                if k not in STRUCTURAL]
    return []


def stack_extruder(xml: str) -> str | None:
    """The filament slot the template's model part is assigned to.

    Structural, so it is not copied along with the rest of a part's settings --
    but it is still the user's filament choice, and it is not stable across
    templates. Rearranging the slots in Bambu renumbers every part; a template
    that puts the support filament first moves the stack from 2 to 3, and a
    hardcoded number would print it in whatever now sits in the old slot.
    """
    for block in re.findall(r"<part\b.*?</part>", xml, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', block)
        if not name or any(m in name.group(1).lower() for m in OTHER_PARTS):
            continue
        e = re.search(r'<metadata key="extruder" value="(\d+)"', block)
        return e.group(1) if e else None
    return None


def interface_slot(cfg: dict, kind: str) -> str:
    """Which filament slot an interface material is in, from the template itself.

    Named rather than numbered, because the slot is not stable: rearranging the
    AMS in Bambu renumbers everything, and a hardcoded 5 would print the
    interface in whatever now sits there.

    Only two materials qualify and they qualify for different reasons. PETG does
    not bond to PLA at all but has to run at its own 255 C to lay down properly
    (ADR 0008 lost a print to PETG at 220). "PLA" means the breakaway support
    PLA -- ordinary PLA against PLA welds, so the only PLA that can be an
    interface is the one flagged as a support material.
    """
    types = [t.strip() for t in cfg.get("filament_type", [])]
    is_support = [str(v) for v in cfg.get("filament_is_support", [])]
    if kind == "petg":
        slots = [i for i, t in enumerate(types) if t.upper() == "PETG"]
        what = "a PETG filament"
    else:
        slots = [i for i, v in enumerate(is_support) if v == "1"
                 and types[i:i + 1] != ["PETG"]]
        what = "a PLA filament flagged as support material"
    if not slots:
        raise SystemExit(f"no slot holds {what}. Loaded: "
                         + ", ".join(f"{i + 1}={t}" for i, t in enumerate(types))
                         + ". Load one in Bambu Studio, or pass "
                           "--interface-extruder.")
    return str(slots[0] + 1)


def build_items(xml: str) -> dict[int, tuple[float, float, float]]:
    """Bed positions from the template's <build>, keyed by object id."""
    out = {}
    for m in re.finditer(r'<item objectid="(\d+)"[^>]*transform="([^"]+)"', xml):
        nums = [float(v) for v in m.group(2).split()]
        out[int(m.group(1))] = (nums[9], nums[10], nums[11])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--template", type=Path, required=True,
                    help="a project 3mf saved from Bambu Studio, whose plate "
                         "layout and settings are reused verbatim")
    ap.add_argument("--model", type=Path, help="the stack STL")
    ap.add_argument("--dummy", metavar="WxD",
                    help="instead of a model, a placeholder block of this size "
                         "with the stack's z profile, for building a committable "
                         "template (e.g. 192x232)")
    ap.add_argument("--plates", type=Path, required=True,
                    help="the stack's plates.json; the decoy and blocker are "
                         "derived from it")
    ap.add_argument("--out", type=Path, required=True, help="3mf to write")
    ap.add_argument("--interface-filament", choices=("pla", "petg"), default=None,
                    help="which material the interface prints in, resolved to a "
                         "slot from the template's own filament list. petg is "
                         "the PETG slot; pla is the slot flagged as support "
                         "material, because ordinary PLA welds to PLA. The two "
                         "want different clearances, which is why this is a "
                         "parameter and not a constant")
    ap.add_argument("--interface-extruder", type=int, default=None,
                    help="filament slot for the interface, by number. Overrides "
                         "--interface-filament; without either, the template's "
                         "support_interface_filament, which is the same thing -- "
                         "the decoy's support is what loads it")
    ap.add_argument("--interface-plan", type=Path,
                    help="the interface toolpath plan from stack_plates, in model "
                         "coordinates. Stored inside the 3mf, moved onto the bed, "
                         "so the file that gets sliced carries the plan for what "
                         "goes into the sliced result and the two cannot drift")
    ap.add_argument("--decoy-size", type=float, default=7.0,
                    help="side of the decoy column, mm (default 7)")
    ap.add_argument("--decoy-gap", type=float, default=10.0,
                    help="clear space between the stack and the decoy, mm "
                         "(default 10). Only used when the template does not "
                         "already have a decoy parked somewhere")
    ap.add_argument("--blocker-margin", type=float, default=1.0)
    args = ap.parse_args(argv)

    if (args.model is None) == (args.dummy is None):
        ap.error("give exactly one of --model or --dummy")
    doc = json.loads(args.plates.read_text())

    if args.dummy:
        try:
            w, d = (float(v) for v in args.dummy.lower().split("x"))
        except ValueError:
            ap.error(f"--dummy wants WxD in mm, got {args.dummy!r}")
        model = dummy_block(doc["plates"], w, d)
        stem = "stack-placeholder"
    else:
        model = read_stl(args.model)
        stem = args.model.stem
    mb = bounds_of(model)

    # A template is a layout, and the decoy's place in it is derived rather than
    # arranged: it stands beside whatever stack it is built for, and a position
    # baked in for a 232 mm stack is 80 mm of travel away from a 60 mm one.
    # Someone who wants it somewhere specific parks it in Bambu Studio and saves;
    # a part named "decoy" in the template is found and its position kept.
    decoy = () if args.dummy else decoy_column(doc["plates"], args.decoy_size)
    blocker = blocker_box(mb, args.blocker_margin)
    db = bounds_of(decoy) if decoy else mb

    with zipfile.ZipFile(args.template) as z:
        entries = [(i, z.read(i.filename)) for i in z.infolist()]
    names = {i.filename for i, _ in entries}
    for need in (MODEL_XML, SETTINGS):
        if need not in names:
            raise SystemExit(f"{args.template}: no {need}. Save it from Bambu "
                             f"Studio with File > Save Project As.")

    tmpl_model = next(d for i, d in entries if i.filename == MODEL_XML).decode()
    tmpl_set = next(d for i, d in entries if i.filename == SETTINGS).decode()
    pos = build_items(tmpl_model)

    # Which template object is the decoy? Named, not guessed by size.
    dec_id = None
    for m in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"',
                         tmpl_set):
        if "decoy" in m.group(2).lower():
            dec_id = int(m.group(1))
    main_id = next((i for i in pos if i != dec_id), None)
    if main_id is None:
        raise SystemExit(f"{args.template}: no object found on the plate; "
                         f"found {sorted(pos)}.")
    if dec_id is None:
        # A template with no decoy parked on it. Stand one beside the stack
        # rather than at the origin, which is off the printable area and which
        # the slicer accepts silently by moving nothing.
        dec_id = max(pos) + 1
        dx = dy = None

    # Object-local coordinates: centred on the object's own bounding box.
    mcx, mcy, mcz = (mb.x0 + mb.x1) / 2, (mb.y0 + mb.y1) / 2, (mb.z0 + mb.z1) / 2
    dcx, dcy, dcz = (db.x0 + db.x1) / 2, (db.y0 + db.y1) / 2, (db.z0 + db.z1) / 2

    m_xml, m_faces = mesh_xml(centred(model, mcx, mcy, mcz), 1)
    b_xml, b_faces = mesh_xml(centred(blocker, mcx, mcy, mcz), 2)
    d_xml, d_faces = (mesh_xml(centred(decoy, dcx, dcy, dcz), 4) if decoy
                      else ("", 0))

    obj_main = (HDR + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
                ' <resources>\n' + m_xml + b_xml + " </resources>\n</model>\n")
    obj_dec = (HDR + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
               ' <resources>\n' + d_xml + " </resources>\n</model>\n")

    # Keep each object where the template put it; only the height follows the
    # new stack, since the item's z is the object centre.
    mx, my, _ = pos[main_id]
    if dec_id in pos:
        dx, dy, _ = pos[dec_id]
    elif dx is None:
        # Clear of the stack by more than support_object_xy_distance, and on the
        # side the wipe tower is not on.
        dx, dy = mx - mb.width / 2 - args.decoy_gap - args.decoy_size / 2, my

    def comp(path, oid, uid):
        return (f'    <component p:path="{path}" objectid="{oid}" p:UUID="{uid}" '
                f'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n')

    # The template's header verbatim. Bambu refuses to slice without the
    # <metadata> block that follows the <model> element -- measured: an
    # otherwise byte-identical file carrying only 3mfVersion fails, and
    # restoring the block makes it slice. Reusing it also keeps the thumbnail
    # references pointing at entries that are still in the archive.
    head = tmpl_model[:tmpl_model.index(" <resources>")]
    new_model = (head
                 + ' <resources>\n'
                 + f'  <object id="{main_id}" p:UUID="00000014-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n   <components>\n'
                 + comp("/3D/Objects/object_20.model", 1, "00140000-b206-40ff-9872-83e8017abed1")
                 + comp("/3D/Objects/object_20.model", 2, "00140001-b206-40ff-9872-83e8017abed1")
                 + '   </components>\n  </object>\n'
                 + ('' if not decoy else
                    f'  <object id="{dec_id}" p:UUID="00000015-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n   <components>\n'
                    + comp("/3D/Objects/object_21.model", 4, "00150000-b206-40ff-9872-83e8017abed1")
                    + '   </components>\n  </object>\n')
                 + ' </resources>\n'
                 + ' <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">\n'
                 + f'  <item objectid="{main_id}" p:UUID="00000003-b1ec-4553-aec9-835e5b724bb4" '
                   f'transform="1 0 0 0 1 0 0 0 1 {mx:.6f} {my:.6f} {mcz:.6f}" printable="1"/>\n'
                 + ('' if not decoy else
                    f'  <item objectid="{dec_id}" p:UUID="00000005-b1ec-4553-aec9-835e5b724bb4" '
                    f'transform="1 0 0 0 1 0 0 0 1 {dx:.6f} {dy:.6f} {dcz:.6f}" printable="1"/>\n')
                 + ' </build>\n</model>\n')

    # Reuse the template's per-object extruder assignments verbatim: they are the
    # user's filament choices, and guessing them here is how a print comes out in
    # the wrong material.
    ext = dict(re.findall(r'<object id="(\d+)">\s*<metadata key="name"[^>]*>\s*'
                          r'<metadata key="extruder" value="(\d+)"', tmpl_set))
    e_main = ext.get(str(main_id), "1")

    def part(pid, name, subtype, faces, extruder, off, extra=()):
        e = (f'      <metadata key="extruder" value="{extruder}"/>\n'
             if extruder is not None else "")
        e += "".join(f'      <metadata key="{k}" value="{v}"/>\n' for k, v in extra)
        return (f'    <part id="{pid}" subtype="{subtype}">\n'
                f'      <metadata key="name" value="{name}"/>\n'
                f'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                f'      <metadata key="source_file" value="{name}"/>\n'
                f'      <metadata key="source_object_id" value="0"/>\n'
                f'      <metadata key="source_volume_id" value="{pid - 1}"/>\n'
                f'      <metadata key="source_offset_x" value="0"/>\n'
                f'      <metadata key="source_offset_y" value="0"/>\n'
                f'      <metadata key="source_offset_z" value="{off}"/>\n'
                + e +
                f'      <mesh_stat face_count="{faces}" edges_fixed="0" '
                f'degenerate_facets="0" facets_removed="0" facets_reversed="0" '
                f'backwards_edges="0"/>\n    </part>\n')

    stack_settings = model_part_settings(tmpl_set)
    # The interface filament is the support-interface filament: the decoy's
    # support is the whole mechanism that loads it, so there is one number and it
    # lives where the slicer already keeps it.
    tmpl_cfg = json.loads(next(d for i, d in entries if i.filename == PROJECT))
    e_iface = (str(args.interface_extruder) if args.interface_extruder is not None
               else interface_slot(tmpl_cfg, args.interface_filament)
               if args.interface_filament
               else str(tmpl_cfg.get("support_interface_filament") or "5"))
    if e_iface == "0":
        raise SystemExit(f"{args.template}: support_interface_filament is 0 "
                         f'("default"), so there is no interface filament to '
                         f"use. Set it in Bambu Studio, or pass "
                         f"--interface-extruder.")
    e_stack = stack_extruder(tmpl_set)
    n_fil = max(1, len(tmpl_cfg.get("filament_settings_id", [])))
    # The decoy is not the thing that needs the interface filament -- its
    # *support* is. Printed in anything but the filament the stack itself uses,
    # it costs two tool changes on every layer of the print: measured at 42
    # changes and two hours on a two-plate test that takes 34 minutes. Note that
    # is the stack's *part* extruder, not its object's: they differ in this
    # repository's own template, 3 against 2.
    e_dec = ext.get(str(dec_id), e_stack or e_main)
    second = part(2, f"{stem}-noSupport.stl", "support_blocker", b_faces, "0", mcz)
    decoy_block = ("" if not decoy else
                   f'  <object id="{dec_id}">\n'
                   f'    <metadata key="name" value="{stem}-decoy.stl"/>\n'
                   f'    <metadata key="extruder" value="{e_dec}"/>\n'
                   f'    <metadata face_count="{d_faces}"/>\n'
                   + part(4, f"{stem}-decoy.stl", "normal_part", d_faces, None, dcz)
                   + '  </object>\n')
    decoy_inst = ("" if not decoy else
                  f'    <model_instance>\n'
                  f'      <metadata key="object_id" value="{dec_id}"/>\n'
                  f'      <metadata key="instance_id" value="0"/>\n'
                  f'    </model_instance>\n')

    new_set = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
               + decoy_block
               + f'  <object id="{main_id}">\n'
               + f'    <metadata key="name" value="{stem}.stl"/>\n'
               + f'    <metadata key="extruder" value="{e_main}"/>\n'
               + f'    <metadata face_count="{m_faces}"/>\n'
               + part(1, f"{stem}.stl", "normal_part", m_faces, e_stack, mcz,
                      stack_settings)
               + second
               + '  </object>\n'
               + '  <plate>\n    <metadata key="plater_id" value="1"/>\n'
               + '    <metadata key="plater_name" value=""/>\n'
               + '    <metadata key="locked" value="false"/>\n'
               # One entry per filament, mapping each to the single extruder.
               # Dropped once when this writer was first used to rebuild the
               # template, and the template had carried them since it was saved
               # out of Studio; they are the user's filament arrangement.
               + f'    <metadata key="filament_map_mode" value="Auto For Flush"/>\n'
               + f'    <metadata key="filament_maps" value="{" ".join("1" * n_fil)}"/>\n'
               + f'    <metadata key="filament_volume_maps" value="{" ".join("0" * n_fil)}"/>\n'
               + '    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>\n'
               + '    <metadata key="thumbnail_no_light_file" '
                 'value="Metadata/plate_no_light_1.png"/>\n'
               + '    <metadata key="top_file" value="Metadata/top_1.png"/>\n'
               + '    <metadata key="pick_file" value="Metadata/pick_1.png"/>\n'
               + f'    <model_instance>\n'
                 f'      <metadata key="object_id" value="{main_id}"/>\n'
                 f'      <metadata key="instance_id" value="0"/>\n'
                 f'    </model_instance>\n'
               + decoy_inst
               + '  </plate>\n</config>\n')

    if args.interface_plan:
        # Into bed coordinates, which is the only place the transform is known:
        # stack_plates has no idea where on the plate the template puts things.
        plan = json.loads(args.interface_plan.read_text())
        ox, oy = mx - mcx, my - mcy
        for lay in plan["layers"]:
            lay["beads"] = [[round(bx0 + ox, 4), round(by0 + oy, 4),
                             round(bx1 + ox, 4), round(by1 + oy, 4)]
                            for bx0, by0, bx1, by1 in lay["beads"]]
        plan["space"] = "bed"
        plan["interface_extruder"] = int(e_iface)

    replace = {
        MODEL_XML: new_model.encode(),
        SETTINGS: new_set.encode(),
        "3D/Objects/object_20.model": obj_main.encode(),
    }
    if decoy:
        replace["3D/Objects/object_21.model"] = obj_dec.encode()
    # The decoy exists to be supported, and its support is what loads the
    # interface filament at the stack's gap heights -- so support has to be on,
    # and it has to be routed to that filament rather than to whichever one
    # Bambu's "Default" resolves to. The stack itself is protected by the
    # blocker; ADR 0003 measured that a single slab over everything is the one
    # arrangement that does not leak.
    #
    # normal/snug rather than tree: the decoy is a stack of flat slabs with fully
    # overhanging faces, which is what tree support is worst at and what the old
    # profiles used normal for.
    cfg = dict(tmpl_cfg)
    cfg["enable_support"] = "1"
    cfg["support_type"] = "normal(auto)"
    cfg["support_style"] = "snug"
    # The decoy's support fills its gaps completely, top and bottom. Bambu's
    # default 0.2 mm at each face is there so support releases from the model,
    # and the decoy is scrap -- what it costs instead is layers. A 0.6 mm gap
    # with 0.2 clear at both ends leaves one supported layer in the middle, and
    # an interface layer wanting a seam anywhere else in that gap finds the
    # object filament loaded and cannot be written.
    cfg["support_top_z_distance"] = "0"
    cfg["support_bottom_z_distance"] = "0"
    cfg["support_filament"] = e_iface
    cfg["support_interface_filament"] = e_iface
    replace[PROJECT] = json.dumps(cfg, indent=4).encode()
    # The part relationships name the object files by path, and we just renamed
    # them. A relationship pointing at a part that is not in the archive does not
    # degrade gracefully: Bambu fails to parse the whole 3mf.
    replace[OBJ_RELS] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">\n'
        + "".join(f' <Relationship Target="/{n}" Id="rel-{i}" '
                  f'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/'
                  f'3dmodel"/>\n'
                  for i, n in enumerate(sorted(n for n in replace
                                               if n.startswith("3D/Objects/")), 1))
        + '</Relationships>\n').encode()
    if args.interface_plan:
        replace[PLAN] = (json.dumps(plan) + "\n").encode()
    # Thumbnails and plate_1.json describe the template's geometry, not ours.
    # Bambu regenerates them on slice; leaving stale ones in is only cosmetic,
    # but plate_1.json carries bounding boxes that would be wrong.
    drop = {"Metadata/plate_1.json"}
    # So does the template's own object geometry, and it does not ride along:
    # we write object_20 and reference it by name from the model XML. A template
    # whose geometry is called something else (Bambu numbers these per project)
    # would otherwise contribute an unreferenced mesh -- 10 MB of one, in the
    # case that found this -- while our own object went missing entirely,
    # because the archive was written by walking the template's entries.
    drop |= {i.filename for i, _ in entries
             if i.filename.startswith("3D/Objects/") and i.filename not in replace}
    # A thumbnail is a picture of the template author's model. Blanked rather
    # than dropped, so anything referencing it still resolves.
    blank = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    for i, _ in entries:
        if i.filename.startswith("Metadata/") and i.filename.endswith(".png"):
            replace[i.filename] = blank

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        for info, data in entries:
            if info.filename in drop:
                continue
            z.writestr(info.filename, replace.get(info.filename, data))
            seen.add(info.filename)
        # Entries the template did not have. Walking only its entries silently
        # dropped our geometry when the template numbered its objects otherwise.
        for name, data in replace.items():
            if name not in seen:
                z.writestr(name, data)

    print(f"wrote {args.out}")
    if stack_settings:
        print(f"           stack carries {len(stack_settings)} part settings from "
              f"the template: " + ", ".join(k for k, _ in stack_settings))
    kinds = [t.strip() for t in tmpl_cfg.get("filament_type", [])]
    kind = kinds[int(e_iface) - 1] if int(e_iface) <= len(kinds) else "?"
    print(f"  model    {stem}  {m_faces} facets, "
          f"{mb.width:.1f} x {mb.depth:.1f} x {mb.height:.1f} mm  "
          f"at ({mx:.1f}, {my:.1f}), extruder {e_stack or e_main}")
    print(f"  blocker  whole-stack box, {b_faces} facets, support_blocker part")
    if decoy:
        print(f"  decoy    {args.decoy_size:g} mm square, "
              f"{len(doc['plates'])} slabs, at ({dx:.1f}, {dy:.1f}), "
              f"extruder {e_dec}, supported in {e_iface} ({kind})")
        print("  gap layers the decoy forces a filament change at: "
              + ", ".join(f"{p['z0']:.2f}" for p in doc["plates"][1:]))
    else:
        print("  decoy    none: a template carries the layout, not the decoy")
    if args.interface_plan:
        print(f"  plan     {len(plan['layers'])} interface layers in extruder "
              f"{e_iface}, moved onto the bed by "
              f"({mx - mcx:+.1f}, {my - mcy:+.1f}) mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
