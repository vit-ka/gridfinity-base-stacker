## Context

See `proposal.md` for why. The facts that shape the approach, all measured here
and not to be re-derived:

- **OpenSCAD drives headlessly and fast.** `gridfinity_baseplate.scad` renders a
  3x2 plate in 0.36 s. A 10x8 baseplate with `build_plate_enabled="enabled"` and
  `--enable=lazy-union` comes out as 6 separate shells in 0.5 s, 42,792 facets.
- **Two flags are not optional.** OpenSCAD writes ASCII STL by default and
  `stl_io.read_stl` refuses it, so `--export-format binstl` is required.
  Without `--enable=lazy-union` the result is one unioned shell, not several.
- **The parameters we need are already exposed**: `Width` and `Depth` as
  `[units, mm]` pairs, `build_plate_enabled`, `build_plate_size`,
  `Enable_Magnets`, and the frame connectors.
- **Upstream is GPL-3.0 and 19 MB. This project is MIT.** Invoking a separate
  binary is aggregation. Writing a `.scad` of our own that `use`s or `include`s
  upstream's modules is a derivative work and is not available to us.
- **The measured target geometry** is one layer of film, a full layer of
  clearance below it, none above -- 0.200/0.000 read from sliced G-code, exit 0
  with no `--no-check`.
- **The bed-packing split does not nest.** That 10x8 came out as sizes (4,5),
  (4,3), (2,5), (2,3) in cells. No ordering of those is componentwise
  decreasing, so some plate always overhangs another.

## Goals / Non-Goals

**Goals:**

- One input: a requested baseplate size. No model file.
- Plate identity known because it was asked for, not measured.
- A decomposition that nests, so pillars stop being the normal case.
- The film as model geometry the slicer handles, in its own filament.
- The result still judged by parsing the sliced G-code.

**Non-Goals:**

- Authoring baseplate geometry. The socket funnel, corner radii, connectors and
  magnet options stay upstream's.
- Supporting arbitrary third-party STLs. That path is being removed, so the
  inference it needed goes with it.
- Reaching clearances off the layer grid. That is what the change this one
  supersedes exists for, and the target geometry does not want it.

## Decisions

### Drive OpenSCAD per plate, not the built-in bed splitter

`build_plate_enabled` splits for bed packing, and its output is exactly the
non-nesting set above. We ask for each plate separately -- one invocation per
plate with its own `Width`/`Depth` -- and choose the sizes ourselves.

*Alternative rejected:* using the built-in splitter and keeping the pillar
machinery for what it produces. That is the current situation with extra steps;
the whole point is to choose a division the splitter will not.

### Prefer a division into identical plates

Where the requested area divides into equal pieces that fit the bed, use them.
Identical plates nest trivially, every interface is the same, and it is the
arrangement the working implementation of this stacking method relies on.

Where it does not divide equally, fall back to a division whose sizes form a
componentwise decreasing chain; where that fails too, fall back to the current
behaviour -- accept overhang and carry it with pillars, and say so.

*Alternative rejected:* rotating plates 90 degrees to make a chain. The 42 mm
lattice is square so it is geometrically legal, and it would rescue some sets.
Deferred rather than rejected outright: it interacts with the alternating 180
degree flip and with connector orientation, and neither is worth entangling
before the simple case is measured.

### Build the film here, from the generated plate

The film is one layer, the shape of the plate's own bottom profile. We already
compute exactly that -- `face_grid` against the plate's down face is what the
current trim uses -- and `slab_mesh` already writes it. So the film is generated
from the plate geometry after it arrives, and goes into the 3mf as a part in the
interface filament.

*Alternative rejected:* generating the film inside the SCAD, which is how
GridPlates does it and is what a first reading of this change asks for. It would
mean a `.scad` of ours that includes upstream's modules to get the plate profile,
and that is a derivative of GPL-3.0 source. The output is identical either way --
a solid in the model, sliced normally -- and the licence boundary is not worth
crossing for it. This is a mechanism change from the proposal's framing, not a
scope change; the film is still model geometry rather than emitted toolpaths.

### Keep the G-code reader, drop the emitter

`gcode.py` and the sliced-file checks in `verify.py` are how anything here is
believed. They stay and are unaffected by where the geometry came from.
`emit_interface.py`, the decoy, the blocker and the package writer all exist to
put material into a gap the slicer left empty; with the film back in the model
there is no empty gap.

### The film's faces are near-coincident, not coincident

Zero clearance above means the film's top face and the plate's bottom face are
the same plane, and the slicer merges the volumes rather than seeing two
(ADR 0007). A hundredth of a millimetre keeps them apart in the model and prints
as nothing, and is far from any sample plane so it cannot go either way
(ADR 0009). That number is a constant with a reason, not a fudge factor to tune.

## Risks / Trade-offs

- **The running test print decides whether this is right at all.** It is testing
  0.1/0.1 with the interface as G-code; this change assumes a full layer below
  and nothing above → do not start implementing until that print is read.
- **OpenSCAD becomes a hard requirement, and upstream can change under us.** →
  pin nothing, but check what arrives: cell counts against what was asked for,
  and manifold edges, both already required by the specs.
- **A licence boundary that is easy to cross by accident.** Someone will
  reasonably want to add a small `.scad` for the film or a tweak → the decision
  above says why not, and it belongs in an ADR when this lands.
- **Deleting the inference deletes the tests that cover it.** Lattice detection,
  ordering and registration have real coverage and real ADRs (0001, 0005, 0006)
  → the ADRs stay; the code goes only where the specs say the behaviour goes.
- **Not every request will nest**, and the fallback is the machinery we are
  trying to stop needing → keep it working, and report which case a given
  request landed in rather than hiding it.
- **One-layer film is unproven here.** Two layers was measured, on different
  geometry → keep the thickness configurable and print a one-layer film before
  making it the default.

## Migration Plan

1. Read the running print first. If it says a gap above is wanted after all,
   this change is wrong and `generate-interface-gcode` is right.
2. Land generation behind the existing STL path, so both work, and compare a
   generated set against the equivalent web-generator set on the same request --
   pillar volume, print time, material.
3. Remove the STL path and the inference only once the generated path produces a
   printed, separated stack.
4. Rollback is that removal not having happened yet; before step 3 the old path
   is intact, and after it, `git revert`.

## Open Questions

- Whether the film is one layer or two by default. A constant, decided by peeling
  one off a real print.
- Whether 90 degree rotation is allowed when it would rescue a decomposition.
  Deferrable: it adds sets that nest, it does not change how the ones that
  already nest are handled.
- Whether connectors between plates are generated by default. They matter for the
  assembled baseplate and not at all for whether the stack prints or separates.
