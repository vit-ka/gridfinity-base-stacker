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
PROJECT = "Metadata/project_settings.config"


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


OTHER_PARTS = ("interface", "nosupport", "blocker", "decoy")


def model_part_settings(xml: str) -> list[tuple[str, str]]:
    """Per-part print settings from the template's model part.

    The film is not the only part someone configures. Reading only the part named
    for the film silently dropped everything set on the stack itself -- a bottom
    shell thickness, in the case that found this -- and dropped it on the next
    regeneration, when the template had looked correct in the slicer.
    """
    for block in re.findall(r"<part\b.*?</part>", xml, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', block)
        if not name or any(m in name.group(1).lower() for m in OTHER_PARTS):
            continue
        return [(k, v) for k, v in
                re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', block)
                if k not in STRUCTURAL]
    return []


def part_settings(xml: str, needle: str) -> list[tuple[str, str]]:
    """Per-part print settings from the template's part whose name contains `needle`.

    Bambu keeps these on the <part>, not in project_settings, which is why a
    diff of the project settings shows nothing when someone has configured a
    part. They are what makes the film print as a film: no walls and no shells,
    so it is entirely sparse infill and therefore follows `infill_direction`,
    which alternates 45 and 135 degrees layer to layer on its own.

    Read from the template rather than hardcoded, so changing them is the same
    gesture as changing anything else on the plate: set them on the part in
    Bambu, save the project, point --template at it.
    """
    for block in re.findall(r"<part\b.*?</part>", xml, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', block)
        if not name or needle not in name.group(1).lower():
            continue
        return [(k, v) for k, v in
                re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', block)
                if k not in STRUCTURAL]
    return []


def dummy_film(plates: list[dict], width: float, depth: float,
               clearance: float = 0.1) -> Mesh:
    """A placeholder film: one slab per gap, held clear of the plates.

    The template needs a film part so its print settings have somewhere to live,
    but not the real one -- that is 70,000 facets of lattice and the template is
    meant to stay small enough to commit.
    """
    w, d = width / 2, depth / 2
    return tuple(f for a, b in zip(plates, plates[1:])
                 for f in box(-w, -d, a["z1"] + clearance,
                              w, d, b["z0"] - clearance))


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
    ap.add_argument("--interface", type=Path,
                    help="the gap film STL, added as a part in its own filament. "
                         "With it the plate needs no blocker and no decoy: the "
                         "slicer generates no support at all")
    ap.add_argument("--interface-extruder", type=int, default=5,
                    help="filament slot for the film (default 5)")
    ap.add_argument("--dummy-film", action="store_true",
                    help="with --dummy, also stand in a placeholder film, so the "
                         "template has a part for the film's print settings to "
                         "live on")
    ap.add_argument("--decoy-size", type=float, default=7.0,
                    help="side of the decoy column, mm (default 7)")
    ap.add_argument("--blocker-margin", type=float, default=1.0)
    args = ap.parse_args(argv)

    if (args.model is None) == (args.dummy is None):
        ap.error("give exactly one of --model or --dummy")
    film = None
    doc = json.loads(args.plates.read_text())

    if args.dummy:
        try:
            w, d = (float(v) for v in args.dummy.lower().split("x"))
        except ValueError:
            ap.error(f"--dummy wants WxD in mm, got {args.dummy!r}")
        model = dummy_block(doc["plates"], w, d)
        stem = "stack-placeholder"
        if args.dummy_film:
            film = dummy_film(doc["plates"], w, d)
    else:
        model = read_stl(args.model)
        stem = args.model.stem
    mb = bounds_of(model)

    if args.interface:
        film = read_stl(args.interface)
    if args.dummy_film and not args.dummy:
        ap.error("--dummy-film only makes sense with --dummy")
    decoy = decoy_column(doc["plates"], args.decoy_size)
    blocker = blocker_box(mb, args.blocker_margin)
    db = bounds_of(decoy)

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
        # A template built for the film workflow has no decoy: nothing needs to
        # provoke a filament change, because the film is a part in its own right.
        dec_id = max(pos) + 1
        dx = dy = 0.0

    # Object-local coordinates: centred on the object's own bounding box.
    mcx, mcy, mcz = (mb.x0 + mb.x1) / 2, (mb.y0 + mb.y1) / 2, (mb.z0 + mb.z1) / 2
    dcx, dcy, dcz = (db.x0 + db.x1) / 2, (db.y0 + db.y1) / 2, (db.z0 + db.z1) / 2

    m_xml, m_faces = mesh_xml(centred(model, mcx, mcy, mcz), 1)
    if film is not None:
        # The film replaces the blocker: nothing to block, because support is off.
        b_xml, b_faces = mesh_xml(centred(film, mcx, mcy, mcz), 2)
    else:
        b_xml, b_faces = mesh_xml(centred(blocker, mcx, mcy, mcz), 2)
    d_xml, d_faces = mesh_xml(centred(decoy, dcx, dcy, dcz), 4)

    obj_main = (HDR + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
                ' <resources>\n' + m_xml + b_xml + " </resources>\n</model>\n")
    obj_dec = (HDR + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
               ' <resources>\n' + d_xml + " </resources>\n</model>\n")

    # Keep each object where the template put it; only the height follows the
    # new stack, since the item's z is the object centre.
    mx, my, _ = pos[main_id]
    if dec_id in pos:
        dx, dy, _ = pos[dec_id]

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
                 + ('' if film is not None else
                    f'  <object id="{dec_id}" p:UUID="00000015-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n   <components>\n'
                    + comp("/3D/Objects/object_21.model", 4, "00150000-b206-40ff-9872-83e8017abed1")
                    + '   </components>\n  </object>\n')
                 + ' </resources>\n'
                 + ' <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">\n'
                 + f'  <item objectid="{main_id}" p:UUID="00000003-b1ec-4553-aec9-835e5b724bb4" '
                   f'transform="1 0 0 0 1 0 0 0 1 {mx:.6f} {my:.6f} {mcz:.6f}" printable="1"/>\n'
                 + ('' if film is not None else
                    f'  <item objectid="{dec_id}" p:UUID="00000005-b1ec-4553-aec9-835e5b724bb4" '
                    f'transform="1 0 0 0 1 0 0 0 1 {dx:.6f} {dy:.6f} {dcz:.6f}" printable="1"/>\n')
                 + ' </build>\n</model>\n')

    # Reuse the template's per-object extruder assignments verbatim: they are the
    # user's filament choices, and guessing them here is how a print comes out in
    # the wrong material.
    ext = dict(re.findall(r'<object id="(\d+)">\s*<metadata key="name"[^>]*>\s*'
                          r'<metadata key="extruder" value="(\d+)"', tmpl_set))
    e_main = ext.get(str(main_id), "1")
    e_dec = ext.get(str(dec_id), "1")

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

    film_settings = part_settings(tmpl_set, "interface")
    stack_settings = model_part_settings(tmpl_set)
    second = (part(2, f"{stem}-interface.stl", "normal_part", b_faces,
                   str(args.interface_extruder), mcz, film_settings)
              if film is not None
              else part(2, f"{stem}-noSupport.stl", "support_blocker", b_faces,
                        "0", mcz))
    decoy_block = ("" if film is not None else
                   f'  <object id="{dec_id}">\n'
                   f'    <metadata key="name" value="{stem}-decoy.stl"/>\n'
                   f'    <metadata key="extruder" value="{e_dec}"/>\n'
                   f'    <metadata face_count="{d_faces}"/>\n'
                   + part(4, f"{stem}-decoy.stl", "normal_part", d_faces, None, dcz)
                   + '  </object>\n')
    decoy_inst = ("" if film is not None else
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
               + part(1, f"{stem}.stl", "normal_part", m_faces, None, mcz,
                      stack_settings)
               + second
               + '  </object>\n'
               + '  <plate>\n    <metadata key="plater_id" value="1"/>\n'
               + '    <metadata key="plater_name" value=""/>\n'
               + '    <metadata key="locked" value="false"/>\n'
               + f'    <model_instance>\n'
                 f'      <metadata key="object_id" value="{main_id}"/>\n'
                 f'      <metadata key="instance_id" value="0"/>\n'
                 f'    </model_instance>\n'
               + decoy_inst
               + '  </plate>\n</config>\n')

    replace = {
        MODEL_XML: new_model.encode(),
        SETTINGS: new_set.encode(),
        "3D/Objects/object_20.model": obj_main.encode(),
        "3D/Objects/object_21.model": obj_dec.encode(),
    }
    if film is not None:
        # Nothing left for the slicer to support: every overhang is carried by a
        # pillar and every gap is filled by the film, both of them model parts.
        cfg = json.loads(next(d for i, d in entries
                              if i.filename == PROJECT))
        cfg["enable_support"] = "0"
        replace[PROJECT] = json.dumps(cfg, indent=4).encode()
    # Thumbnails and plate_1.json describe the template's geometry, not ours.
    # Bambu regenerates them on slice; leaving stale ones in is only cosmetic,
    # but plate_1.json carries bounding boxes that would be wrong.
    drop = {"Metadata/plate_1.json"}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in entries:
            if info.filename in drop:
                continue
            z.writestr(info.filename, replace.get(info.filename, data))

    print(f"wrote {args.out}")
    if stack_settings:
        print(f"           stack carries {len(stack_settings)} part settings from "
              f"the template: " + ", ".join(k for k, _ in stack_settings))
    print(f"  model    {stem}  {m_faces} facets, "
          f"{mb.width:.1f} x {mb.depth:.1f} x {mb.height:.1f} mm  "
          f"at ({mx:.1f}, {my:.1f}), extruder {e_main}")
    if film is not None:
        label = args.interface.name if args.interface else "placeholder"
        print(f"  film     {label}  {b_faces} facets, "
              f"extruder {args.interface_extruder}, support disabled")
        if film_settings:
            print(f"           carrying {len(film_settings)} part settings from "
                  f"the template: "
                  + ", ".join(k for k, _ in film_settings[:4]) + ", ...")
        else:
            print("           no part settings found on the template's interface "
                  "part -- the film will print with the object's own settings")
    else:
        print(f"  blocker  whole-stack box, {b_faces} facets, support_blocker part")
        print(f"  decoy    {args.decoy_size:g} mm square, "
              f"{len(doc['plates'])} slabs, at ({dx:.1f}, {dy:.1f}), "
              f"extruder {e_dec}")
        print("  gap layers the decoy forces a filament change at: "
              + ", ".join(f"{p['z0']:.2f}" for p in doc["plates"][1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
