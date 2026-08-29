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

- ~~Whether 0.1 mm is the smallest clearance that works, since every 0.1 mm
  costs height across every gap.~~ **The premise was wrong.** Clearance costs no
  height at all:

  | gap | clearance | stack height | film |
  |---|---|---|---|
  | 0.4 | 0.05 | 39.2 mm | 0.30 mm |
  | 0.4 | 0.10 | 39.2 mm | 0.20 mm |
  | 0.6 | 0.05 | 40.8 mm | 0.50 mm |
  | 0.6 | 0.10 | 40.8 mm | 0.40 mm |

  Height follows the gap, and the gap is snapped to a whole layer, so the only
  heights on offer are 39.2 mm and 40.8 mm. Clearance only decides how much of
  the gap is film.

  That makes the real question a different one: whether **0.4 mm gap with 0.05 mm
  clearance** works, since it is a whole layer shorter. Sliced, it keeps two
  interface layers per gap -- 39.2 mm and 7.39 h against 40.8 mm and 8.71 h, and
  224.6 g of PLA against 257.5 g. Worth a third of a kilo of filament and an hour
  and a quarter.

  **0.05 mm welds shut**, and the reason is exact rather than empirical: the
  clearance has to leave one layer with no model material on it. The slicer calls
  a surface a top only where the layer above is empty of *every* region
  (`detect_surfaces_type` diffs against `upper_layer->lslices`), so without that
  layer the plate gets no top surface and the film no bottom, and they are one
  body. Measured on a plate's top layer: 29% top surface at 0.05 mm against 79.5%
  at 0.1 mm, with sparse infill and a floating vertical shell in its place. So the saving is not available, 40.8 mm and 8.71 h stand, and 0.1 mm is
  now bounded on both sides rather than merely chosen -- 0 merges at slicing,
  0.05 welds at printing, 0.1 separates. There is nothing left to tune here.

- ~~The CLI and the GUI disagree, and that is unexplained.~~ **Closed.** They
  disagree only about *coincident surfaces*, and that is the whole of it.

  Reproduced on current geometry: with the film flush against the plates, a CLI
  slice puts 13,151 mm of interface filament on the model, and the GUI merges the
  volumes and puts down none. Same file, same declared parts, opposite answers.
  Which is what an ambiguous input means -- two surfaces occupying the same plane
  give a slicer no fact to decide by, so each resolves it however it happens to,
  and neither is wrong.

  Hold the film clear and the ambiguity is gone: both agree, confirmed in the GUI
  preview. So the clearance is doing a second job beyond stopping the merge --
  it is what makes the geometry decidable at all.

  What this leaves behind is a rule, not a caveat: a CLI slice is evidence only
  where the geometry is unambiguous. It is not a weaker instrument than the GUI,
  and this was never a CLI-versus-GUI difference in general -- coincident
  surfaces are the only case in which they have been seen to differ, and nothing
  the generator now emits contains any.
