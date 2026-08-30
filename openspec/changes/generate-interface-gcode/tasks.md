## 1. Establish the baseline before changing anything

- [ ] 1.1 Slice `out/test-plain-c02.3mf` with `--no-check` and record print time,
      per-filament weight, and the measured clearance below and above the film in
      every gap; this is the number every later step is compared against
      (32m 30s, 11.4 g PLA + 0.66 g support, 0.20/0.20 at last measurement)
- [ ] 1.2 Write a reusable G-code reader that yields (Z, tool, feature, extrusion)
      per move, handling Z carried on travel moves and bare Z-hop moves, and
      verify it reproduces 1.1's numbers from the same file

## 2. Separate the two clearances

- [ ] 2.1 Give the film's placement an independent clearance above and below,
      defaulting the second to the first, and verify a stack asked for 0.2 below
      and 0.1 above places the film's modelled faces at exactly those distances
- [ ] 2.2 Update the gap-too-small error to name both clearances, and verify it
      fires when the gap cannot hold both plus one layer

## 3. Emit the interface as toolpaths

- [ ] 3.1 Replace the per-layer `box()` output of `interface_slabs()` with a
      function returning, per gap and per layer, the Z and the filled region;
      verify the regions are identical to those the mesh film produced for the
      same stack (same bitmask rows, same bridging, same flare)
- [ ] 3.2 Fill each region with a monotonic raster at the extrusion width,
      alternating direction per layer, and verify the emitted paths stay inside
      the region and cover it without gaps
- [ ] 3.3 Synthesise a complete layer block per interface layer -- entry travel
      at the exact Z, tool change to the interface filament, the raster, and the
      return to the stack's filament -- and verify by parsing that each block's
      Z equals the configured height
- [ ] 3.4 Renumber whatever the file counts per layer (layer counters, progress,
      total layer count in the header) and verify the counts are consistent from
      first layer to last

## 4. Give the interface a filament to use

- [ ] 4.1 Restore `decoy_column()` from git history, sized to place a segment at
      every gap level, printed in the interface filament, and verify the sliced
      file contains a tool change to that filament at each gap height
- [ ] 4.2 Restore `full_blocker()` from git history as a support blocker over the
      stack, re-enable support for the decoy's sake, and verify no support
      extrusion appears within the stack's footprint
- [ ] 4.3 Verify the prime tower still lies clear of the stack's footprint with
      both objects on the plate

## 5. Verification that reads the output

- [ ] 5.1 Replace `film_regions()` with a G-code equivalent reporting, per gap,
      the measured clearance below and above and the number of separate interface
      regions; verify it agrees with hand-measurement on one sliced file
- [ ] 5.2 Make a wrong interface height a failure that names the gap and both
      heights, and verify it by deliberately emitting one layer at the wrong Z
- [ ] 5.3 Verify on two stacks of different overall height that the measured
      clearances are identical, which is the property the mesh film could not hold

## 6. Remove the film part

- [ ] 6.1 Drop `--interface`, `--dummy-film` and the film part from `make3mf.py`,
      and stop writing `NAME-interface.stl`; verify the 3mf still opens in Studio
      and slices
- [ ] 6.2 Rebuild `templates/stack-template.3mf` without the film placeholder
      part, keeping the stack part's settings, the filament assignments and the
      plate layout, and verify the rebuilt template is under 20 KB and carries no
      model geometry
- [ ] 6.3 Update the README file table, the generated printing notes and
      `openspec/config.yaml` so none of them describe a film STL, and verify no
      reference to `-interface.stl` remains

## 7. Prove it on the printer

- [ ] 7.1 Generate `models/test-plain.stl` at 0.2 below and 0.1 above, slice,
      emit, and verify from the G-code that the interface sits 0.2 above the
      plate below and 0.1 below the plate above
- [ ] 7.2 Compare print time and material against 1.1 and record the cost of the
      decoy and the extra tool changes
- [ ] 7.3 Test print it, and confirm both that the stack separates and that the
      underside of the upper plate is no longer loose -- this is the only check
      that answers the question the change exists for
- [ ] 7.4 Record the result in an ADR, whichever way it goes
