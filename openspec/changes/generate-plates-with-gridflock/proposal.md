## Why

The tool is handed an opaque multi-plate STL from a web generator and
reverse-engineers everything from it: split the shells, flood-fill the bottom
face to find the 42 mm lattice, infer which face is land and which is rib, order
the plates by containment, register the lattices onto a common origin, and raise
a pillar wherever a plate has material with nothing beneath it. Every one of
those steps exists because the input is a black box.

[GridFlock](https://github.com/yawkat/GridFlock) is that black box's source, and
it is **MIT** -- as is its own dependency,
[Gridfinity Rebuilt](https://github.com/kennetek/gridfinity-rebuilt-openscad).
This repository is MIT. That removes the constraint that shaped every previous
attempt at this: we may vendor it, and we may build geometry against what it
produces, without a licence boundary to work around.

Measured on this machine, driving it headlessly:

- A 3x2 plate renders in **0.97 s**: 2884 facets, `manifold`, genus 6, measuring
  **126.0000 x 84.0000 x 4.6500 mm** -- an exact whole number of 42 mm cells, and
  it loads through `stl_io.read_stl` unmodified.
- The full **478 x 502** baseplate for a **250 x 220** bed renders in **0.6 s**,
  77,286 facets.

So the plates can be generated rather than recovered, and their cell counts and
face types are known by construction.

## What Changes

- **BREAKING** The multi-plate STL input is removed. The tool takes a requested
  baseplate size and a bed size, and generates the plates itself.
- **BREAKING** GridFlock is **vendored** into the repository under `vendor/`,
  byte-identical to upstream, alongside a recorded commit. OpenSCAD becomes a
  required external tool.
- **BREAKING** The separating film stops being toolpaths written into sliced
  G-code. It is generated as model geometry in OpenSCAD from the plate's real
  socketed profile, so the slicer sees an ordinary part in its own filament.
- **BREAKING** Pillars stop being traced in Python from a rasterised mesh. They
  are generated in OpenSCAD as the part of a plate's profile with nothing under
  the plate below it.
- Our OpenSCAD lives in **our own file**. Nothing upstream is patched, and
  nothing of ours is `include`d into it or it into ours.
- Removed, because the geometry is known rather than recovered: shell splitting,
  lattice flood-fill, land/rib inference, containment ordering, lattice
  registration.
- Removed, because there is no longer an empty gap to fill or a filament change
  to provoke: the G-code interface emitter, the decoy column, the whole-stack
  support blocker, the interface plan, and the sliced-package writer.
- Verification against a sliced file is kept and is still the acceptance test.
- Every reference in the repository to the previous web generator is removed,
  excepting `docs/adr/0001`, which records a historical measurement and is
  accepted.

### The upgrade path is a requirement, not a nicety

Upstream has **no tags and no releases**, and the generator this change tracks
is the perplexinglabs one, which follows upstream `main`. So the vendored copy
will need replacing, repeatedly, and the design is judged partly on how cheaply
that can be done.

The coupling to upstream is therefore **command-line parameter names only** --
`plate_size`, `bed_size`, `magnets`, `connector_intersection_puzzle` -- and never
its source. This was tested, not assumed:

- Patching a guard into `gridflock.scad` so it can be `include`d as a library
  works (2840 facets for one segment, against 32942 when its `main()` also runs)
  but leaves a modified upstream file to re-apply on every upgrade. **Rejected on
  the upgrade requirement.**
- `use <gridflock.scad>` executes no top-level geometry, but a `use`d file's
  modules read *its* globals, so `-D magnets=false` never reaches `segment()`.
  **Unusable.**
- Driving the stock file per plate over the command line, then reading the
  written STL back into our own `.scad`, needs no patch at all. **Adopted.**

## Capabilities

### New Capabilities

- `plate-generation`: producing plates by driving the vendored OpenSCAD source --
  how it is invoked, what is known about each plate without measuring it, how a
  requested baseplate is decomposed, what is checked about what arrives, and what
  an upgrade of the vendored copy has to satisfy.
- `overhang-support`: carrying plate material that has nothing beneath it, now
  generated in OpenSCAD from the real socketed profiles of the plate above and
  the plate below rather than traced from a rasterised mesh.

### Modified Capabilities

- `gap-film`: the film is currently at least two layers thick, held clear of the
  plate above *and* the plate below, built in Python and flared outward one layer
  height at a time. It becomes **one layer**, generated in OpenSCAD from the
  plate's own profile, with a full layer of clearance **below** and **none
  above** -- ADR 0010 measured that it releases at its bottom face. The
  two-layer minimum, the clear-of-both-plates requirement, and every requirement
  about the per-layer flare all change or fall away with the single layer.
- `mesh-output`: the manifold guarantee, the verify-before-write rule and the
  one-pillar-one-solid rule apply to geometry this tool no longer builds itself.
  What survives is the check on what OpenSCAD produced.

## Impact

- `vendor/gridflock/`: new. `gridflock.scad` (90 KB), `gridfinity-rebuilt-openscad/src/`
  (188 KB), and the generated `paths/puzzle.scad` (1.7 KB). Measured: **360 KB**
  and it renders. `opengrid/` is **excluded** -- it is CC-BY-4.0 third-party
  model data and only feeds an adapter this project does not use.
- `LICENSE` / `NOTICE`: three copyright notices to carry -- this project's,
  GridFlock's, Gridfinity Rebuilt's.
- New `.scad` of ours: imports a written plate STL and emits the film and the
  pillars from it.
- `stack_plates.py`: loses `build_plate`, `split_shells` usage, `nesting_groups`,
  `order_plates`, `registration_error`, `flipped_lattice`, `support_regions`,
  the pillar tracing and the section sampling behind `steepest_overhang`; gains
  the OpenSCAD invocation and the decomposition.
- `gridfinity.py`: the lattice detection it exists for is no longer needed.
- `make3mf.py`: loses the decoy, the blocker, the interface plan and the support
  routing; goes back to a stack part and a film part in their own filaments.
- `emit_interface.py`, `gcode.py`: the emitter goes. The G-code reader stays --
  it is how the result is measured.
- `verify.py`: keeps the sliced-file checks, loses the mesh-vs-source comparison
  that assumed an input STL.
- `templates/stack-template.3mf`: needs the film part back.
- `models/`: the committed test STLs become generated rather than stored.
- **Supersedes `generate-interface-gcode`** (19/21, on the current branch): its
  emitter, decoy, blocker and packaging step all exist to reach a clearance off
  the layer grid, and ADR 0010 recorded that gap tuning is finished.
- **Supersedes `generate-plates-from-scad`**, which planned the same move against
  a GPL-3.0 generator and is unimplemented past task 1.
- `docs/adr/0009` stands. Its constraint is real; this change avoids it rather
  than removing it.

## How this will be measured

- **Dimensional exactness.** Every generated plate's width and depth is a whole
  number of 42 mm cells against what was requested. Baseline already taken:
  126.0000 x 84.0000 for a 3x2.
- **Generation cost**, reported rather than assumed. Baseline: 0.97 s for one
  3x2 plate, 0.6 s for the whole 478 x 502 set, 0.06 s for a film or a pillar.
- **Unsupported area per gap**, read off the mesh by sampling the plate above and
  the plate below and differencing the occupancy. The baseline this change must
  beat is GridFlock's own stacked output on 478 x 502 / 250 x 220:
  **0.4 % to 75.5 % unsupported across eight gaps**, worst gap 3034.5 mm2. The
  claim is that generated pillars take every gap to **zero**.
- **Pillar correctness against sockets, not bounding boxes.** A pillar is checked
  where a narrower plate's solid border sits over a wider plate's socket
  through-hole. Measured baseline for a 3x2 over a 2x2: the pillar is
  126 x 84 x 4.000 mm covering **1797.5 mm2**.
- **Film follows the real profile.** Measured baseline for a 3x2: the film is
  126.000 x 84.000 x 0.200 mm covering **2793.5 mm2**, against 10584 mm2 for the
  plate's bounding rectangle -- 26.4 %, i.e. the sockets are cut out of it.
- **The project slices with no `--no-check`, exit 0**, which the current design
  cannot do because an empty gap is an empty layer.
- **Zero support extrusion** anywhere on the stack, read from the G-code rather
  than from the settings.
- **Printed clearance** read from the G-code: a full layer below the film, none
  above, on two stacks of different overall height.
- **Print time and material** against the measured 32.6 min and 11.36 g PLA +
  0.66 g support for the two-plate test.
- **An upgrade is cheap.** Replacing the vendored copy and re-running the path
  extraction leaves every check above passing, with no file of ours edited.
- **The stack separates by hand** and the underside of the upper plate is clean.
  The question this project exists for, and the only one the others stand in for.

## What was ruled out

- **Using GridFlock's own `stacked_print` mode.** It stacks segments with a
  measured 0.250 mm gap, flips alternate ones and adds a top slice for contact
  area -- but it leaves the gap empty and **does not carry overhangs**. Measured
  on 478 x 502 / 250 x 220: eight gaps, seven of them between **20 % and 76 %**
  unsupported. The cause is its segmentation, which is for bed packing: nine
  segments of six distinct sizes at two different x origins. Nesting needs a
  componentwise-decreasing chain, and a grid of three widths by three depths is a
  lattice, not a chain -- `(4.22, 2.74)` and `(3.04, 5.04)` are incomparable, so
  something always overhangs.
- **Parameter combinations that might have rescued it.** Defeating the y stagger
  with `y_row_count_first=[4,4]` leaves 0.2 %-75.5 %. `separate_edge_padding`
  makes it worse: 21 pieces, and eleven gaps at **100 %** unsupported.
- **Requesting a cell-aligned baseplate to avoid pillars.** At 504 x 504, a whole
  12 x 12 cells, unsupported area collapses to **0.5 %-6.7 %**; three identical
  1x1 plates measure **0.0 %**. But 478 x 502 is **11.38 x 11.95 cells** and is
  fixed -- it is a drawer interior, and the filler padding that fills it is
  exactly what makes the pieces unequal. Pillars are therefore unavoidable here,
  which is why `overhang-support` is a capability rather than an edge case.
- **Patching `gridflock.scad`, and `use <gridflock.scad>`.** Both covered above:
  the first loses the upgrade path, the second loses the parameters.
- **Reimplementing the baseplate geometry in Python.** The socket funnel, corner
  radii, connector tabs and magnet options are a large surface upstream already
  maintains. Rasterising and ray casting exist here to *measure* geometry, not to
  author it.
- **Keeping the STL input as a second path.** Two input paths means the inference
  machinery has to keep working, which is most of what this change deletes.
- **Emitting the interface as G-code.** It reaches clearances the layer grid
  cannot express, at the cost of a decoy, a blocker, a post-slice pass and a
  hand-built print package -- and it cannot produce a zero gap above at all,
  because the interface then lands on the layer grid with nothing to insert
  between. ADR 0010 measured that zero above is what is wanted.
