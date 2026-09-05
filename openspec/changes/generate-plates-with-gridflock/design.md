## Context

See `proposal.md` for why, and the specs for what. The facts that shape the
approach were all measured on this machine and should not be re-derived:

- **OpenSCAD drives headlessly and fast.** A 3x2 plate: **0.97 s**, 2884 facets,
  `manifold`, genus 6, measuring 126.0000 x 84.0000 x 4.6500 mm, centred on the
  origin and sitting on z=0. The whole 478 x 502 set for a 250 x 220 bed: 0.6 s,
  77,286 facets. Local OpenSCAD is 2026.06.12, new enough for the development
  features the vendored source needs.
- **Two flags are not optional.** `--export-format binstl`, because OpenSCAD
  writes ASCII by default and `stl_io.read_stl` refuses it. `--enable=lazy-union`
  where separate shells are wanted rather than one union.
- **The vendored tree is 360 KB and renders.** `gridflock.scad`, the
  `gridfinity-rebuilt-openscad/src/` it includes, and the generated
  `paths/puzzle.scad`. Verified rendering with upstream's `docs/`, `images/`,
  `tests/` and `opengrid/` all absent.
- **`paths/puzzle.scad` is generated** from `puzzle.svg` by upstream's
  `extract_paths.py`, which needs `svgelements`. Committing the generated file
  removes `just`, `uv`, `svgelements` and a git submodule from every path except
  an upgrade.
- **Upstream's stacked mode does not carry overhangs**: 0.4 %-75.5 % of the upper
  plate unsupported across the eight gaps of the real 478 x 502 request. The
  numbers and the reason are in the proposal.
- **The target geometry** is one layer of film, a full layer of clearance below
  it, none above -- 0.200/0.000 read from sliced G-code, exit 0 with no
  `--no-check` (ADR 0010).

## Goals / Non-Goals

**Goals:**

- One model input: a requested baseplate size and a bed size.
- Plate identity known because it was asked for, not measured.
- Film and pillars authored in OpenSCAD, from the real socketed profile.
- Replacing the vendored generator costs a directory swap and a check run.
- The result still judged by parsing the sliced G-code.

**Non-Goals:**

- Authoring baseplate geometry. The socket funnel, corner radii, connectors and
  magnet options stay upstream's.
- Supporting arbitrary third-party STLs. That path is removed, and the inference
  it needed goes with it.
- Reaching clearances off the layer grid. ADR 0010 settled that gap tuning is
  finished.
- Contributing the overhang work upstream. It may be worth doing later; it is not
  what this change is measured on.

## Decisions

### Drive the stock generator over the command line, and read the STL back

Our OpenSCAD is a separate file that `import()`s the plate STL the vendored
generator just wrote, and derives the film and the pillars from it with
`projection(cut = true)`. Upstream's file is never patched, never included, and
never includes ours.

This was chosen against two alternatives that were built and measured:

*Alternative rejected -- patch a library guard into `gridflock.scad`.* Wrapping
its trailing `main()` dispatch in a guard works: one segment renders as 2840
facets, against 32942 when `main()` also runs, and our file can then call
`segment()` directly with full parameter control. It is the most direct
approach and it is why upstream's structure invites it -- `segment()`,
`flip_segment_conditional()` and `main()` are all clean module boundaries. But it
leaves a modified upstream file to re-apply on every upgrade, and upstream has no
releases to diff against. Rejected on the upgrade requirement, not on merit.

*Alternative rejected -- `use <gridflock.scad>`.* It executes no top-level
geometry, which solves the problem the guard was patched in for, with no patch.
But a `use`d file's modules resolve variables in **their own** file's scope, and
`-D` assigns into the root file's scope, so `-D magnets=false` never reaches
`segment()`. Every parameter would silently keep its upstream default. Unusable,
and quietly so, which is worse.

The adopted route costs one extra OpenSCAD invocation and one STL round-trip per
plate. Measured: 0.06 s for a film or a pillar, against 0.97 s for the plate
itself. The round-trip is not where the time goes.

### Take the true footprint by section, not by outer rectangle

`projection(cut = true)` of a plate translated just below the cut plane gives a
horizontal section of the real solid -- sockets, connectors, magnet cutouts and
all. Upstream's own segment extents are outer rectangles in cell counts, and the
sockets are cut later, so a footprint difference computed from them would miss
exactly the case `overhang-support` exists for.

Measured on a 3x2 plate: the sectioned profile is **2793.5 mm2** against
10584 mm2 for the bounding rectangle -- 26.4 %, so the six socket openings are
genuinely cut out of it. The film is that profile extruded one layer. A pillar is
the profile of the plate above minus the profile of the plate below, extruded
through the gap: measured 126 x 84 x 4.000 mm covering **1797.5 mm2** for a 3x2
over a 2x2.

*Alternative rejected:* differencing outer footprints from the cell counts we
asked for. Cheap, and wrong in the normal case -- alternate plates are rotated
180 degrees, so a border sits over a socket even between plates of equal size.
ADR 0006 exists because of this.

*Alternative rejected:* keeping the Python pillar tracing (`support_regions`,
`region_solid`). It already works, is ADR-backed and rasterises the real mesh, so
it gets the socket case right too. Not chosen, because with the film moving into
OpenSCAD, keeping pillars in Python means two mechanisms and two places for the
gap arithmetic to disagree. The Python raster stays in the tree as the
*measurement* of what was generated -- `face_grid` is what produced every number
above -- rather than as the authoring path.

### Choose the decomposition ourselves, and do not use upstream's stacked mode

We ask for each plate separately, one invocation per plate with its own
`plate_size`, and place them ourselves. Upstream's `stacked_print` is not used:
its segmentation is for bed packing and its gap is snapped to the layer grid,
which is the quantisation ADR 0009 was written about.

Where the requested area divides into identical plates that fit the bed, use
them: identical plates carry each other completely, measured at **0.0 %**
unsupported. Where it does not -- and 478 x 502 is 11.38 x 11.95 cells, so it
does not -- accept the overhang and carry it, and report which case the request
landed in.

*Alternative rejected:* changing the requested size to something cell-aligned to
avoid pillars. It works -- 504 x 504 measures 0.5 %-6.7 % -- but the size is a
drawer interior and is fixed.

*Alternative deferred:* rotating plates 90 degrees to rescue a decomposition. The
lattice is square so it is legal, and it would help some requests. It interacts
with the alternating 180 degree flip and with connector orientation, and neither
is worth entangling before the simple case is measured.

### The film's top face is near-coincident with the plate above, not coincident

Zero clearance above means the film's top face and the plate's bottom face are
the same plane, and the slicer merges the volumes rather than seeing two
(ADR 0007). A hundredth of a millimetre keeps them apart in the model, prints as
nothing, and is far from any sample plane so it cannot round either way
(ADR 0009). A constant with a reason, not a fudge factor to tune.

### The upgrade surface is the parameter names, and it is checked

The vendored copy carries the upstream commit it came from. An upgrade replaces
the directory, re-runs `extract_paths.py`, and re-runs the checks. The one thing
that can go wrong silently is a parameter rename: OpenSCAD does not error on an
unrecognised `-D`, it ignores it and uses the default, so a renamed `magnets`
would produce a plate full of magnet cutouts with no complaint. So every
parameter we set is asserted to be declared by the vendored source, and the
upgrade fails naming the parameter if it is not.

`opengrid/` is excluded from the vendored copy: it is CC-BY-4.0 third-party model
data, `import()`ed only by the openGrid adapter branch, and the probe tree
rendered without it.

## Risks / Trade-offs

- **Upstream can change under us, and there is nothing to pin against.** No tags,
  no releases, and the generator tracked follows `main`. → Record the commit,
  assert the parameter names, and re-run the dimensional and manifold checks on
  every upgrade. The specs require all three.
- **`projection()` is the one operation that could get slow on the real set.**
  Measured at 0.06 s on a 3x2, but the full 478 x 502 request is nine plates of
  up to 4.22 x 5.04 cells. → Measure it on the real request early, in task 2,
  before the design depends on it. If it does not hold, the Python raster path is
  still in the tree and still correct.
- **A one-layer film is proven on GridPlates-style geometry, not on ours.** ADR
  0010 printed it and the bottom face released. → Thickness stays configurable,
  and a one-layer film is printed on generated plates before it is trusted.
- **Deleting the inference deletes the tests that cover it.** Lattice detection,
  ordering and registration have real coverage and real ADRs (0001, 0005, 0006).
  → The ADRs stay; the code goes only where the specs say the behaviour goes, and
  the tests are deleted rather than skipped.
- **Two changes are superseded, one of them nearly finished.**
  `generate-interface-gcode` is 19/21 and on the current branch. → ADR 0010
  already recorded that its premise is settled; archive it as superseded rather
  than reverting it, so the measurements it produced stay findable.
- **The film and pillars now depend on an external binary being present.** →
  Absence is a named failure with the path that was searched, required by the
  specs, not a stack trace.

## Migration Plan

1. Vendor the generator and prove one plate: generated, manifold, measuring its
   requested cells, loading through `stl_io`. Nothing else depends on this being
   right, and everything else depends on it being right.
2. Measure `projection()` on the real 478 x 502 request before building on it.
3. Land generation **behind** the existing STL path, so both work, and compare a
   generated set against the equivalent web-generator set on the same request --
   unsupported area, pillar volume, print time, material.
4. Remove the STL path, the inference, and the G-code emitter only once the
   generated path produces a printed, separated stack.
5. Rollback is that removal not having happened yet; before step 4 the old path
   is intact, and after it, `git revert`.

## Open Questions

- Whether connectors between plates are generated by default. They matter for the
  assembled baseplate and not at all for whether the stack prints or separates,
  so this can be answered after the stack is proven.
- Whether the overhang work is worth offering upstream. Deferrable by
  construction: it lives in our own file either way.
