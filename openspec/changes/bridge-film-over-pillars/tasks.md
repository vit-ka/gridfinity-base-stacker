## 1. Count the islands before writing any bridging

- [x] 1.1 Add a connected-component count over a bitmask raster (flood fill on
      the row integers), with a unit test covering: one blob is 1, two separated
      blobs are 2, two blobs touching only corner to corner are counted the way
      the film would print them, and an empty grid is 0
- [x] 1.2 Report disconnected film regions per gap in the stack report, and
      verify by running the generator on the nine-plate drawer stack: record the
      count for each of the eight gaps and the current film volume against the
      recorded 22.6 cm3
- [x] 1.3 Record, for each island, the shortest span from it to any other base
      material in the same gap. This is what says whether the 6 mm default
      reaches anything; verify by producing the list, and stop here if there are
      few islands or every span is wider than 6 mm -- as with
      `trim-unsupporting-film`, if there is little to win the change should not
      be built

## 2. Bridge the base

- [x] 2.1 Add a closing step to `interface_slabs`: `dilate` then `erode` by
      `span / 2 / step` on the film's base, applied after the base is formed and
      before the flare. Verify with a unit test on a synthetic base -- an island
      1.0 mm from a wall joins at `--bridge-span 1.5` and stays separate at 0.5,
      measured by the region count from 1.1
- [x] 2.2 Verify the per-side rule with a unit test: an island near a wall on
      one side and far from anything on the other bridges on the near side only,
      and the far side's clear cells stay clear
- [x] 2.3 Verify isotropy with a unit test: an island the same distance away
      diagonally as along X bridges in both directions
- [x] 2.4 Add `--bridge-span MM` and thread it through to `interface_slabs`.
      Verify `--bridge-span 0` writes a film byte-identical to the same run
      without the flag, by comparing the two STLs
- [x] 2.5 Verify the flare still holds over a bridge: a unit test that no layer
      of a bridged film extends beyond the layer below it by more than the layer
      height

## 3. Choose the default from a sweep

- [x] 3.1 Sweep `--bridge-span` around the 6 mm default -- a spread either side
      of it, informed by the span distribution from 1.3 -- recording islands per
      gap and film volume at each value. Verify by producing the table
- [x] 3.2 Set the default to 6 mm and record what it costs in the option's help
      text, along with whether the sweep puts 6 mm at the knee -- where islands
      stop falling and volume keeps rising -- or past it. If past it, say so in
      the help text rather than quietly changing the number. Verify the
      generator's default run reproduces the 6 mm row of the table
- [x] 3.3 Quantify the closing's side effect: how much of the added volume joins
      two regions and how much only fills a concave notch inside one. design.md
      predicts the notch share is near zero at 6 mm, since a 3 mm disc fits
      inside every socket opening; if it is not, switch to the labelled variant
      there. Verify by re-running 3.1 and comparing the volumes

## 4. Verify against a sliced file, not only the tests

- [x] 4.1 Generate the stack and film at the chosen default, build the 3mf with
      `make3mf.py --interface`, and slice it with the Bambu CLI. Verify the film
      is present in all eight gaps and that the total interface-filament
      extrusion moved by the volume the sweep predicted
- [x] 4.2 Parse the G-code for the bridge spans specifically: verify they print
      as bridge or solid infill and not as air, and record which line types
      appear over the pillar tops
- [x] 4.3 Verify no slicer support appeared anywhere on the model, the check the
      whole arrangement depends on
- [ ] 4.4 Open the sliced result in Bambu Studio and confirm by eye in Preview
      (Colour Scheme: Line Type) that the film in each gap reads as one sheet.
      The CLI and the GUI have already disagreed once on this model, so a CLI
      slice alone is not evidence

## 5. Keep it from regressing

- [x] 5.1 Add the film region count to `verify.py`, derived from the written STL
      rather than from the plan, and verify it reports the same per-gap counts
      as the generator did in 3.1
- [x] 5.2 Run the existing suite and `verify.py` end to end on the generated
      files, and verify the film still clears both plates by the configured
      clearance
- [x] 5.3 Write the trim ordering into `trim-unsupporting-film`'s tasks -- trim
      before bridging, never after -- so whichever change lands second does not
      delete the other's bridges. Verify the note is present in that change's
      artifacts, and that it says why: pillar-top film is carrying film and is
      never trimmed, but a bridge span carries nothing by construction
