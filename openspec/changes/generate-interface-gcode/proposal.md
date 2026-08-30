## Why

The film works as a separator and fails as a model part. Printed with a real gap
on both faces it releases cleanly -- both Bambu Support W and PETG at 255 C came
apart by hand on a two-plate test -- but its position is quantised to the layer
height, and that quantisation is now the binding constraint.

A film that is a mesh gets sliced, and the slicer samples at the middle of each
layer (`PrintObjectSlice.cpp:36`, `slice_z = 0.5 * (lo + hi)`). Every face of the
film therefore lands either between two sample planes or exactly on one, and a
face exactly on a plane is resolved by an asymmetric rule in `slice_facet()`
(`TriangleMeshSlicer.cpp:191`: a solid's top face is owned, its bottom face is
not) decided by float32 rounding of a few times 1e-7 mm that depends on the
height of the stack. Measured consequences, all from sliced G-code:

| requested clearance | printed below | printed above |
|---|---|---|
| 0.1 (gf-300, 17.8 mm tall) | **0.00** | 0.20 |
| 0.1 (test-plain, 8.6 mm tall) | 0.20 | **0.00** |
| 0.2 | 0.20 | 0.20 |
| 0.4 | 0.40 | 0.40 |

So the clearance above the film can be 0 or 0.2 mm and nothing else. At 0
everything fuses -- that is the drawer stack, which had to be pried and cut
apart. At 0.2 the stack separates cleanly but the first layer of every plate
bridges 0.2 mm over the film with nothing to squish against, and its underside
prints badly: loose perimeters over Support W, worse over PETG. **Both available
values are wrong, and the value that is wanted -- 0.1 mm above -- is not
expressible as a mesh.**

Emitting the interface as G-code removes the constraint rather than working
around it. Extrusion Z is a number we write; it owes nothing to the layer grid.

## What Changes

- **BREAKING** The generator stops emitting a film mesh. `NAME-interface.stl` is
  no longer produced and `make3mf` no longer adds a film part.
- **BREAKING** `templates/stack-template.3mf` loses its placeholder film part and
  the ten per-part print settings that lived on it. The template keeps the stack
  part, the filament assignments, and the plate layout.
- A new step takes the sliced G-code and writes the interface into it: whole
  layer blocks synthesised at chosen Z heights, in the second filament, with the
  tool changes that implies.
- The film's *shape* logic is kept as it stands -- regions, trim to what is
  carried, bridging, and the per-layer flare all still decide where interface
  material goes. Only the output medium changes, from facets to extrusions.
- Clearance becomes two independent numbers, below and above, because they do
  different jobs: the gap below is what lets the film release, and the gap above
  is what the plate above has to bridge.
- **The decoy column and the whole-stack support blocker come back.** With no
  film part in the model, nothing makes the slicer touch the second filament: no
  tool change, no prime tower, no flush volumes, and the injected interface would
  reference a filament the file never loads. A decoy printed in the interface
  filament, with a segment at every gap level, provokes exactly the tool changes
  the interface needs. The blocker keeps the slicer from generating support on
  the stack itself once support is enabled for the decoy's sake. Both existed
  before and were deleted when the film part made them unnecessary; git history
  has the code.

## Capabilities

### New Capabilities

- `interface-gcode`: the interface as emitted toolpaths -- where its layers sit
  in Z, what filament they use, how they are inserted into a sliced file, and how
  the result is verified.

### Modified Capabilities

- `gap-film`: the separation requirement currently reasons about facets ("no
  facet of the film is coincident with any facet of a plate") and about a film
  that fills the gap when clearance is zero. Both are statements about a mesh.
  They become statements about printed Z, and the clearance stops being one
  number for both faces.

## Impact

- `stack_plates.py`: `interface_slabs()` stops returning a mesh; the region,
  trim, bridge and flare logic feeding it is reused by the new emitter.
- `make3mf.py`: the film part, `--interface`, `--dummy-film`, and the film's
  per-part settings go. `decoy_column()`, `full_blocker()` and their flags come
  back from git history, along with `enable_support` no longer being forced off.
  `part_extruder()` still resolves the interface filament, now for the decoy and
  the G-code writer rather than for a film part.
- `verify.py`: `film_regions()` currently rasterises a mesh; it needs a G-code
  equivalent, and gains the check that matters most -- that every interface
  extrusion sits at the Z it was asked for.
- `templates/stack-template.3mf`: rebuilt without the film part.
- A new post-slice step in the workflow, between Bambu Studio and the printer.
- `docs/adr/0007` (bridge the film so it lifts as one sheet) still holds;
  `docs/adr/0009` (clearance is quantised) is the reason this change exists and
  its constraint is what the change removes.

## How this will be measured

- Interface Z is exact: for two stacks of different heights, every interface
  extrusion is at `plate_below_top + clearance_below`, and the distance from the
  top interface layer to the first layer of the plate above equals the configured
  clearance above -- to the micron, not to the layer.
- The configured 0.1 mm above is delivered as 0.1 mm, which no mesh film has
  managed.
- Zero support features in the output, as now.
- The sliced file loads the interface filament at every gap, so the injected
  extrusions have a tool change and a purge behind them rather than assuming one.
- Print time and material within 10% of the current 34-minute two-plate test
  (32m 30s, 11.4 g PLA + 0.66 g support), so the tool-change cost of writing the
  interface is known rather than assumed.
- The test print separates by hand, as the 0.2/0.2 mesh film already does, and
  the underside of the upper plate is no longer loose.

## What was ruled out

- **Raising or lowering the mesh film's clearance.** Measured above: the printed
  value is quantised to the layer height regardless of what is asked for.
- **Printing the film flush against the plate above.** That is the 0 case, and it
  is what the drawer stack did in seven of its eight gaps. It fused.
- **Height range modifiers** (`Metadata/layer_config_ranges.xml`, which Bambu
  supports) to print 0.1 mm layers across each gap. This would deliver 0.1 mm and
  is the closest alternative. Rejected because it only moves the quantum from
  0.2 to 0.1 rather than removing it, doubles the layer count across every gap,
  and leaves the interface's position still owned by the slicer.
- **A G-code pass over support the slicer generated** was tried and abandoned
  ([ADR 0004](../../../docs/adr/0004-strip-balconies-in-gcode.md)). That pass had
  to identify and delete someone else's extrusions by inference. This change
  writes its own and deletes nothing, so the failure mode that killed it -- not
  being able to tell wanted support from unwanted -- does not arise.
