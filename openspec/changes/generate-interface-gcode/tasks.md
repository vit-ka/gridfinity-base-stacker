## 1. Establish the baseline before changing anything

- [x] 1.1 Slice `out/test-plain-c02.3mf` with `--no-check` and record print time,
      per-filament weight, and the measured clearance below and above the film in
      every gap; this is the number every later step is compared against
      (32m 30s, 11.4 g PLA + 0.66 g support, 0.20/0.20 at last measurement)

      **Measured 2026-08-30**, `models/test-plain.stl` at gap 0.8 / clearance 0.2,
      2 plates, 44 layers: **33m 51s** (2031 s), **10.66 g PLA + 0.87 g support**,
      2 filament changes, 0 support extrusions. Model material alone is 8.24 g PLA
      + 0.156 g support; the rest is the prime tower and the flush. One gap, film
      printed 4.20-4.60 against a plate top of 4.00 and a plate bottom of 4.80:
      **0.20 below, 0.20 above**, as ADR 0009 predicts.
- [x] 1.2 Write a reusable G-code reader that yields (Z, tool, feature, extrusion)
      per move, handling Z carried on travel moves and bare Z-hop moves, and
      verify it reproduces 1.1's numbers from the same file

## 2. Separate the two clearances

- [x] 2.1 Give the film's placement an independent clearance above and below,
      defaulting the second to the first, and verify a stack asked for 0.2 below
      and 0.1 above places the film's modelled faces at exactly those distances
- [x] 2.2 Update the gap-too-small error to name both clearances, and verify it
      fires when the gap cannot hold both plus one layer

## 3. Emit the interface as toolpaths

- [x] 3.1 Replace the per-layer `box()` output of `interface_slabs()` with a
      function returning, per gap and per layer, the Z and the filled region;
      verify the regions are identical to those the mesh film produced for the
      same stack (same bitmask rows, same bridging, same flare)
- [x] 3.2 Fill each region with a monotonic raster at the extrusion width,
      alternating direction per layer, and verify the emitted paths stay inside
      the region and cover it without gaps
- [x] 3.3 Synthesise a complete layer block per interface layer -- entry travel
      at the exact Z, tool change to the interface filament, the raster, and the
      return to the stack's filament -- and verify by parsing that each block's
      Z equals the configured height
- [x] 3.4 Renumber whatever the file counts per layer (layer counters, progress,
      total layer count in the header) and verify the counts are consistent from
      first layer to last

## 4. Give the interface a filament to use

- [x] 4.1 Restore `decoy_column()` from git history, sized to place a segment at
      every gap level, printed in the interface filament, and verify the sliced
      file contains a tool change to that filament at each gap height
- [x] 4.2 Restore `full_blocker()` from git history as a support blocker over the
      stack, re-enable support for the decoy's sake, and verify no support
      extrusion appears within the stack's footprint
- [x] 4.3 Verify the prime tower still lies clear of the stack's footprint with
      both objects on the plate

      Measured on the sliced decoy file: tower x 14.6-51.9, y 194.4-231.7;
      stack x 124.9-181.8, y 97.6-157.6; decoy x 107.9-114.9, y 124.1-131.1.
      The decoy's support reaches x 115.1, ten millimetres clear of the stack.

## 5. Verification that reads the output

- [x] 5.1 Replace `film_regions()` with a G-code equivalent reporting, per gap,
      the measured clearance below and above and the number of separate interface
      regions; verify it agrees with hand-measurement on one sliced file
- [x] 5.2 Make a wrong interface height a failure that names the gap and both
      heights, and verify it by deliberately emitting one layer at the wrong Z
- [x] 5.3 Verify on two stacks of different overall height that the measured
      clearances are identical, which is the property the mesh film could not hold

      Two stacks, 8.8 mm and 18.4 mm tall, both asked for 0.2 below and 0.1
      above. Every gap in both measures **0.200 below, 0.100 above**. The mesh
      film gave 0.20/0.00 on one height and 0.00/0.20 on another for the same
      request (ADR 0009). The taller stack is the two test models concatenated
      -- `test-plain.stl` plus `test-connectors.stl` moved 200 mm in X -- which
      also gives a rib-to-rib interface the pair does not have on its own.

      Gap 3 of that stack reports 3 regions rather than 1. That is the film's
      bridging, unchanged by this change and the same on the mesh film; it is the
      region count doing its job on a model assembled for a height test.

## 6. Remove the film part

- [x] 6.1 Drop `--interface`, `--dummy-film` and the film part from `make3mf.py`,
      and stop writing `NAME-interface.stl`; verify the 3mf still opens in Studio
      and slices
- [x] 6.2 Rebuild `templates/stack-template.3mf` without the film placeholder
      part, keeping the stack part's settings, the filament assignments and the
      plate layout, and verify the rebuilt template is under 20 KB and carries no
      model geometry

      Rebuilt with `make3mf.py --dummy 192x232` against the old template: 40,120
      bytes down to **18,077**, one placeholder block and a blocker, no film
      part. Kept: the stack part's `bottom_shell_thickness`, extruders 2/3, and
      the build item at (153.33, 127.64). Gained: `support_interface_filament=5`,
      which is now where the interface filament's slot is recorded. It also
      carries no decoy -- the decoy's position is derived from the stack it is
      built for, so baking one in would put it 80 mm from a small stack.
- [x] 6.3 Update the README file table, the generated printing notes and
      `openspec/config.yaml` so none of them describe a film STL, and verify no
      reference to `-interface.stl` remains

## 7. Prove it on the printer

- [x] 7.1 Generate `models/test-plain.stl` at 0.2 below and 0.1 above, slice,
      emit, and verify from the G-code that the interface sits 0.2 above the
      plate below and 0.1 below the plate above

      `out/test-plain.gcode`, 46 layers. Interface printed **4.200-4.700**
      against a plate top of 4.000 and a plate bottom of 4.800: **0.200 below,
      0.100 above**, one region, no support inside the stack's footprint. The
      0.1 mm above is not a multiple of the layer height, which is the thing no
      mesh film could express.
- [x] 7.2 Compare print time and material against 1.1 and record the cost of the
      decoy and the extra tool changes

      | | baseline (mesh film) | now (decoy + emitted interface) |
      |---|---|---|
      | time | 33.9 min | 34.6 min sliced + 1.3 min interface = **35.9 min** (+6.0%) |
      | PLA | 10.66 g | **11.05 g** (+0.39 g, all of it the decoy) |
      | interface filament | 0.87 g | 0.73 g sliced + 0.148 g emitted = **0.88 g** |
      | filament changes | 2 | **2** |
      | support on the stack | none | none |

      Both inside the 10% the proposal asked for. The tool changes did not go up:
      the decoy prints in the stack's own filament and only its *support* is the
      interface filament, so the file still changes twice. Getting that wrong --
      giving the decoy the interface filament -- cost 42 changes and 2h 4m on a
      34-minute plate, which is why it is now taken from the stack's part
      extruder rather than its object's.

      The 1.3 min is a floor: commanded feed rates with no acceleration. About
      half of it is the 1,068 retractions a monotonic raster needs, one pair per
      bead. The emitted file's own header estimate is the slicer's and is not
      recomputed; `emit_interface.py` prints how far short it now is.
- [x] 7.3 Test print it, and confirm both that the stack separates and that the
      underside of the upper plate is no longer loose -- this is the only check
      that answers the question the change exists for

      **Answered, but not by this change's own file.** The question was settled by
      printing `out/gp-style-supportpla.3mf` instead: the same two-plate test at
      0.2 mm below the film and 0.01 above (printing as 0.000), film in Bambu
      Support W, generated through the mesh path this change replaces. The bottom
      face released cleanly and the top face needed some cleaning and was not
      fused, so the loose underside ADR 0009 left open is fixed -- by removing the
      gap above rather than by shrinking it.

      This change's own file (`out/t-petg-01-01.gcode.3mf`, 0.100/0.100, interface
      injected as G-code) was generated and verified but never printed. It was
      blocked on filament mapping: a bare `.gcode` carries no
      `Metadata/slice_info.config`, and the packaged `.gcode.3mf` built here still
      offered only one filament in the send dialog. That is unresolved.

      **Awaiting the printer.** File generated: `out/t-petg-01-01.gcode`, gap
      0.6, **0.1 mm below and 0.1 mm above**, interface in PETG (slot 5, 255 C).
      Measured back out of the emitted file: film 4.100-4.500 against a plate
      top of 4.000 and a plate bottom of 4.600, one region, no support inside
      the stack. 45 layers, ~36 min, 11.0 g PLA + 0.86 g PETG.

      0.1 mm on both faces is the case ADR 0009 could not produce at all: as a
      mesh it printed 0.00 on one stack and 0.20 on another, per gap, decided by
      float rounding. This is the first file in which it is actually 0.1.

      **Two real bugs, both found by looking rather than by testing.**

      *The interface was 2 mm off the stack.* The plan was placed from the 3mf
      item transform alone, but a 3mf item is in plate coordinates and the G-code
      is in machine coordinates -- Bambu subtracts `extruder_offset`, which is
      `0x2` on an X1C. Every check passed, because the clearances were measured
      inside the interface's own footprint and a plan shifted bodily off the
      model measures perfectly against itself. `verify.py` now checks the
      interface against the *plates'* printed footprint, and a test shifts the
      emitted beads by 2 mm to prove it catches it.

      *A bare .gcode cannot be sent to the printer with filament mapping.* The
      send dialog builds its table from `Metadata/slice_info.config`, which only
      a sliced package has. Confirmed on the *unmodified* slicer output, so it is
      the container and not anything injected. `emit_interface.py --package`
      writes a `.gcode.3mf` around the result.

      Neither of Bambu's own routes closes that gap: the CLI refuses
      post-processing outright (`normative_check: postprocess not supported`),
      and the GUI will not slice the project at all -- "Object can't be printed
      for empty layer between 4 and 4.8", because the stack has no model material
      in its gaps. `--no-check` is therefore not orthogonal to this change, as
      the design assumed; it is the only way to slice, which is what forces the
      packaging step.

      Found while generating it: Bambu's default 0.2 mm support Z distance at
      each face left only one supported layer in a 0.6 mm gap, so the interface
      filament was not loaded at the seam the first interface layer needed and
      the emitter refused to write it -- correctly. The decoy is scrap, so its
      support now fills each gap completely (`support_top_z_distance` and
      `support_bottom_z_distance` set to 0 for the whole plate; the stack is
      protected by the blocker, not by those distances).
- [x] 7.4 Record the result in an ADR, whichever way it goes

      [ADR 0010](../../../docs/adr/0010-the-film-releases-at-its-bottom-face.md):
      the film releases at its bottom face alone. A full printed layer of
      clearance below, none above, the plate above printed directly onto the
      film. It went the way that leaves this change's central mechanism
      unnecessary -- with every face in the gap back on the layer grid, nothing
      wants a height the slicer cannot place, so the emitter, the decoy, the
      blocker and the packaging step buy nothing. Kept finished by the user's
      decision rather than archived as superseded.
