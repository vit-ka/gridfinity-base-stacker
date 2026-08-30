## Why

The tool is handed an opaque multi-plate STL and reverse-engineers everything it
needs from it: split the shells, flood-fill the bottom face to find the 42 mm
lattice, infer which face is land and which is rib, order the plates by
containment, register the lattices onto a common origin, and raise a pillar
wherever a plate has material with nothing beneath it. Every one of those steps
exists because the input is a black box, and the black box was cut for **bed
packing**, not for stacking -- pieces that tile a large baseplate do not nest, so
a narrower plate's solid border lands over a wider plate's socket opening and has
to be carried to the bed.

The generator behind that STL is OpenSCAD, and it drives headlessly. Measured on
this machine: `gridfinity_baseplate.scad` renders a 3x2 plate in **0.36 s**, and
with `--enable=lazy-union` and `build_plate_enabled="enabled"` it splits a 10x8
baseplate into **6 separate shells in 0.5 s**, at exact multiples of 42 mm
(168.00, 210.00, 126.00, 84.00). If we drive it ourselves we know the lattice,
the cell counts and the face types by construction rather than by inference, we
choose a decomposition that nests, and we can fix the geometry when it is wrong
instead of working around it.

The same move closes the separation question. [GridPlates](https://github.com/pfa230/GridPlates)
stacks plates by putting the spacer **in the model**: one layer thick, 0.2 mm
below it and 0.01 mm above. Reproduced here through this repository's own mesh
path and measured from the sliced G-code: **0.200 mm below, 0.000 mm above, zero
support extrusion**, and -- because the gaps are full of model material -- it
slices with **return code 0 and no `--no-check`**, which the current design
cannot do. A spacer generated alongside the plates needs no G-code emitter, no
decoy, no support blocker and no packaging step.

## What Changes

- **BREAKING** The multi-plate STL input is removed. The tool takes the size of
  the baseplate wanted and generates the plates itself.
- **BREAKING** OpenSCAD becomes a required external tool. It is invoked, never
  vendored: upstream is GPL-3.0 and this repository is MIT.
- **BREAKING** The separating film stops being something this tool emits. It is
  generated with the plates as part of one solid, so the slicer sees it as an
  ordinary part in its own filament.
- **BREAKING** The plate decomposition is chosen so that each plate rests on the
  one below wherever the requested size allows it, rather than accepting a tiling
  cut for bed packing.
- Removed, because the geometry is known rather than recovered: shell splitting,
  lattice flood-fill, land/rib inference, containment ordering and lattice
  registration.
- Removed, because there is no longer an empty gap to fill or a filament change
  to provoke: the G-code interface emitter, the decoy column, the whole-stack
  support blocker, the interface plan, and the sliced-package writer.
- Pillars are kept but become the exception. A nesting decomposition removes the
  case that forces them; a request that cannot nest still needs them.
- Verification against a sliced file is kept and is still the acceptance test.

## Capabilities

### New Capabilities

- `plate-generation`: producing the plates from the Gridfinity Extended OpenSCAD
  source -- how the tool is invoked, what is known about each plate without
  measuring it, how a requested baseplate is decomposed so the pieces stack, and
  what happens when it cannot be.

### Modified Capabilities

- `gap-film`: the film is currently a body this tool builds and holds clear of
  both plates, at least two layers thick. It becomes a spacer generated with the
  plates, one layer thick, with a full layer of clearance below it and **none
  above** -- the plate above prints directly onto it and releases at the film's
  bottom face. The two-layer minimum and the "clear of both plates" requirement
  both change.
- `mesh-output`: the requirements about emitted meshes being manifold, verified
  before writing, and about a pillar being one traced solid, apply to geometry
  this tool no longer builds itself. What survives is the check on what OpenSCAD
  produced.

## Impact

- `stack_plates.py`: loses `build_plate`, `split_shells` usage, `nesting_groups`,
  `order_plates`, `registration_error`, `flipped_lattice` and the section
  sampling behind `steepest_overhang`; gains the OpenSCAD invocation and the
  decomposition. `support_regions` and the pillar tracing stay.
- `gridfinity.py`: the lattice detection it exists for is no longer needed.
- `make3mf.py`: loses the decoy, the blocker, the interface plan and the support
  routing; goes back to a stack part and a film part in their own filaments.
- `emit_interface.py`, `gcode.py`: the emitter goes. The G-code reader stays --
  it is how the result is measured.
- `verify.py`: keeps the sliced-file checks, loses the mesh-vs-source comparison
  that assumed an input STL.
- `templates/stack-template.3mf`: needs the film part back.
- `models/`: the committed test STLs become generated rather than stored.
- **Supersedes most of `generate-interface-gcode`.** That change exists to reach
  a clearance the layer grid cannot express; if the spacer belongs in the model
  at 0.2/0.0, nothing off-grid is wanted and its emitter, decoy, blocker and
  packaging step are all unnecessary. It is currently 19/21 with a test print in
  progress, and that print decides which of the two survives.
- `docs/adr/0009` stands. Its constraint is real; this change avoids it rather
  than removing it, by choosing values nowhere near a sample plane.

## How this will be measured

- The generated plates are dimensionally exact: every plate's width and depth is
  a whole number of 42 mm cells, checked against what was requested, not
  eyeballed.
- Generation cost stays in the same order as the measurements above -- under a
  second for a set that fits one bed, and reported rather than assumed for the
  nine-plate drawer set.
- **Pillar volume falls.** Measured on the same requested baseplate, mm3 of
  pillar under the nesting decomposition against mm3 under the tiling the web
  generator produces. The claim is that it goes to zero for a set that can nest.
- The project slices with **no `--no-check`**, exit 0, as the GridPlates geometry
  already does and the current design does not.
- Zero support extrusion anywhere on the stack, read from the G-code.
- Printed clearance read from the G-code: a full layer below the film, none
  above, on two stacks of different overall height.
- Print time and material against the measured 32.6 min, 11.36 g PLA + 0.66 g
  support for the two-plate test.
- The stack separates by hand and the underside of the upper plate is clean --
  the question every previous attempt has failed.

## What was ruled out

- **Vendoring the SCAD.** Upstream is GPL-3.0 and 19 MB; this repository is MIT.
  It is invoked from a checkout the user supplies, which is aggregation rather
  than derivation. Relicensing this project is the alternative and is not
  proposed here.
- **Reimplementing the baseplate geometry in Python.** The socket funnel, the
  corner radii, the connector tabs and the magnet options are a large surface
  that upstream already maintains and that this project has no interest in
  owning. Rasterising and ray casting exist here to *measure* geometry, not to
  author it.
- **Keeping the STL input as a second path.** Rejected deliberately: two input
  paths means the inference machinery has to keep working, which is most of what
  this change exists to delete.
- **Emitting the interface as G-code** (`generate-interface-gcode`). It reaches
  clearances the layer grid cannot express, at the cost of a decoy, a blocker, a
  post-slice pass and a hand-built print package -- and it cannot produce a zero
  gap above at all, because the interface then lands on the layer grid and there
  is nothing to insert between. Superseded if the running print confirms that
  zero above is what is wanted.
