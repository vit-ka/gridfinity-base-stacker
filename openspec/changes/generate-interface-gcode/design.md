## Context

See `proposal.md` for why. The facts that shape the approach, all measured in
this repository and not to be re-derived:

- **The slicer emits nothing in an empty gap.** Layers with no model material are
  not merely thin, they are absent, and the slicer treats them as an error
  ("Object can't be printed for empty layer between 4.4 and 4.8"). With the film
  part gone, the Z range of every gap has no layer block at all, so the interface
  cannot be injected into existing layers -- whole layer blocks have to be
  synthesised.
- **The check is bypassable.** `--no-check` on the BambuStudio CLI produces
  G-code despite the empty-layer complaint; the plain slice exits 156 with no
  output. This is already how every file in this project is sliced.
- **Layer Z is written explicitly.** Bambu's output carries no `;LAYER_CHANGE`
  marker; the Z of a layer appears on the travel move that enters it
  (`G1 X.. Y.. Z4.4 F30000`), and there are separate bare `G1 Z..` moves for
  Z-hop. Anything reading or writing layer positions has to account for both.
- **Tool changes are expensive and are already in the file.** The current film
  costs two filament changes per gap; the two-plate test spends 0.66 g of support
  filament on the film and a comparable amount again in the prime tower.
- **Support W and PETG both release** at a real 0.2 mm clearance on both faces.
  Material is not the open question. Height is.
- **The pillars are model geometry and stay that way.** They are PLA, part of the
  stack, and this change does not touch them.

## Goals / Non-Goals

**Goals:**

- Interface material at an exact Z, chosen per face, independent of the layer grid.
- The film's existing shape logic reused unchanged: regions, trim to what is
  carried, bridging, per-layer flare.
- A sliced file that already loads and purges the interface filament wherever the
  interface needs it.
- Verification that reads the emitted file.

**Non-Goals:**

- Replacing the slicer for anything but the interface. The plates and the pillars
  are sliced normally.
- Choosing the film material, or revisiting bridging thresholds. Both are settled
  by measurement already.
- Making the file printable without `--no-check`; the empty-layer complaint is
  pre-existing and orthogonal.

## Decisions

### Emit into the sliced file, rather than emitting a whole file

The tool writes the plates and pillars as it does now, slices normally, and then
inserts interface layer blocks into the result at the gap heights.

*Alternative rejected:* generating the entire G-code. That means owning
perimeters, infill, cooling, flow, acceleration and the printer's start/end
sequences for the plates too -- an enormous surface for no benefit, since the
plates slice correctly today.

### Synthesise layer blocks, do not rewrite existing ones

There is nothing in the gap to rewrite. Each interface layer is a new block: a
travel to the layer's Z, a tool change, extrusions, and a return to the stack's
filament.

*Alternative rejected:* keeping a vestigial film mesh so the slicer emits layers
there to be overwritten. It reintroduces exactly the quantisation this change
exists to remove -- the vestigial part's layers would sit on the slicer's grid.

### Provoke the filament changes with a decoy, do not fabricate them

The decoy is a small column beside the stack, printed in the interface filament,
with a segment at every gap level. Its purpose is that the slicer, of its own
accord, produces the tool change, the prime tower volume and the flush handling
at those heights. The interface then rides on machinery the slicer built.

*Alternative rejected:* writing the tool change ourselves. Bambu's tool changes
carry purge volumes, flush parameters and prime tower moves computed from the
filament pairing; hand-written ones would be wrong in ways that only show up as a
colour-contaminated or under-purged interface on the printer.

The blocker exists as the other half of this: enabling support so the decoy gets
it means the stack must be explicitly protected from it.

### Keep the geometry code, replace only the emitter

`interface_slabs()` currently walks each gap, builds a bitmask region per layer,
and turns it into boxes. The walk and the region logic stay; the per-layer output
becomes extrusion paths instead of `box()` calls. This keeps ADR 0006's tracing
and ADR 0007's bridging intact and testable as they are.

### Fill the region with a single monotonic raster

The film prints today as 100% sparse infill with no walls -- that is what the ten
per-part settings on the template's film part were configuring. The emitter
reproduces that directly: parallel beads at the extrusion width, alternating
direction per layer, clipped to the region. There is no perimeter to emit because
the film deliberately has none.

## Risks / Trade-offs

- **A synthesised layer block is wrong in a way the CLI cannot see** → verify by
  parsing the emitted file, and print the two-plate test before anything larger.
  Every check in this project that was not made against the output has been wrong
  at least once.
- **The printer's own layer accounting is confused by inserted layers** (progress,
  timelapse, `;LAYER:` counters, the `total_layer_count` in the header) → treat
  the counters as part of the output that has to be renumbered, not as comments.
- **Flow for a bead laid over air is not the flow for one laid on a plate.** The
  interface's first layer bridges the clearance below it → start from the bridge
  flow the slicer would have used, and keep it configurable; this is a print-tuning
  parameter, not a geometric one.
- **Two more objects on the plate** (decoy, blocker) → both existed before and
  their removal is in git history; restore rather than reinvent.
- **The interface no longer appears in Studio's preview as a part.** Nobody can
  eyeball it before printing → the verification output is the substitute, so it
  has to report per-gap numbers a person can read.
- **Print time grows with tool changes.** The decoy adds its own → measure against
  the 32m 30s / 11.4 g + 0.66 g baseline rather than assuming it is negligible.

## Migration Plan

1. The film part is removed from `templates/stack-template.3mf` in the same step
   that the emitter lands, not before. Removing it earlier breaks `make3mf`,
   which reads the film's print settings from that part.
2. `NAME-interface.stl` disappears from the output set; anything referring to it
   (the printing notes, the README's file table, the 3mf builder's flags) is
   updated in the same change.
3. Rollback is `git revert`: the mesh film at 0.2/0.2 separates cleanly and
   remains a working configuration, just with a poor plate underside.

## Open Questions

- Whether the interface wants one raster direction per layer or a crosshatch
  between the two. The current film alternates 45/135 because `infill_direction`
  does it automatically; the emitter has to choose deliberately. Deferrable: it
  changes a constant, not the approach.
- Whether the decoy needs a segment at every gap or whether one filament change
  per gap can be shared with the interface's own. Deferrable: measurable on the
  first sliced file.
