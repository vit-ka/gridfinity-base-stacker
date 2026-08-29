## Why

The gap film is currently generated flush against the plates above and below it,
on the reasoning that distinct parts of one object stay distinct volumes and that
PETG will not bond to PLA anyway. In Bambu Studio the two are merged and no
interface filament is laid down at all: the failure happens at slicing, not at
printing, and there is nothing to peel because there is nothing there.

That is a different failure from thermal fusing and it matters, because bonding
is irrelevant to it. Coincident surfaces stop the slicer seeing two volumes. The
film has to be physically separated, not merely assigned another filament.

## What Changes

- The film is inset vertically by a clearance at both faces, so it touches
  nothing. `interface_slabs` already takes `clearance`; the change is to make a
  non-zero value the default and to size the gap so the film still gets two
  layers.
- **BREAKING** for existing stacks: the default gap goes from 0.4 mm to 0.6 mm --
  0.1 mm clear, 0.4 mm of film, 0.1 mm clear. A nine-plate stack grows 1.6 mm
  taller across its eight gaps.
- A 0.4 mm gap with clearance is still available and yields a one-layer film. It
  is not the default because a single-layer film tears at the fringe teeth, which
  is the failure the two-layer film exists to prevent.

## Capabilities

### New Capabilities
- `gap-film`: the solid that fills each gap between plates, printed in a
  non-bonding filament so the stack separates after printing. Covers its extent,
  its clearance from the plates, and the 45 degree flare that lets it overhang
  what carries it.

## Impact

- `stack_plates.py`: `interface_slabs` clearance default, gap default, and the
  printing notes that quote the gap.
- `settings/petg-interface.json`: `_gap_mm` follows the new default.
- Regenerating any stack changes its height, so a saved 3mf built from an older
  stack will not match a newly generated `plates.json`.

## Open questions

- Whether 0.1 mm is the smallest clearance that works. It was chosen because it
  is what the merge prompted, not because anything smaller was measured. Worth
  one slice at 0.05 mm before the height cost is accepted permanently -- and a
  slice answers it, since the failure is visible without printing.

- **The CLI and the GUI disagree, and that is unexplained.** Slicing the very
  same 3mf from the command line produces 6,940 mm of interface filament, present
  in all eight gaps; the GUI merges the parts and produces none. The file itself
  is not at fault: it declares the film as its own part with its own extruder,
  and the copy Bambu re-saved still does. Until this is understood, a CLI slice
  is not evidence that the GUI will behave, which undercuts how the rest of this
  project has been verified. Clearance is likely to fix it either way, since
  coincident geometry is the obvious cause, but the change should confirm the fix
  in the GUI and not only in the harness.
