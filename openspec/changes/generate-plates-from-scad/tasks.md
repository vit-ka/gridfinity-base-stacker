## 1. Confirm the premise before building on it

- [x] 1.1 Read the running 0.1/0.1 test print and record whether the stack
      separates and how the upper plate's underside looks; this change assumes a
      full layer below and nothing above, and if the print says a gap above is
      wanted then this change is wrong and `generate-interface-gcode` is right
- [x] 1.2 Print the GridPlates-geometry file already generated
      (`out/gp-style-supportpla.3mf`, 0.2 below / 0.01 above, support PLA) and
      verify by hand that it separates and that the upper plate's underside is
      clean -- this is the geometry every task below assumes

      **Printed 2026-08-30. The bottom face released cleanly; the top face needed
      some cleaning and was not fused.** Measured from the G-code beforehand:
      0.200 below, 0.000 above, zero support on the stack, and it sliced with no
      `--no-check` at exit 0. Recorded in
      [ADR 0010](../../../docs/adr/0010-the-film-releases-at-its-bottom-face.md).
      The premise of this change holds: a full layer below, none above, and gap
      tuning is finished.

      Task 1.1's own file was never printed -- it was blocked on filament
      mapping -- so the 0.1/0.1 arm is untested and now moot.

## 2. Generate a plate

- [ ] 2.1 Invoke OpenSCAD for one plate of a requested cell size, with
      `--export-format binstl` and `--enable=lazy-union`, and verify the written
      STL loads through `stl_io.read_stl` and measures the requested whole number
      of 42 mm cells
- [ ] 2.2 Fail with a message naming what was looked for and where when OpenSCAD
      or the baseplate source is absent, and verify by pointing the tool at a
      path that does not exist
- [ ] 2.3 Surface a generator failure with what was asked of it rather than
      swallowing it, and verify by passing a parameter the SCAD rejects
- [ ] 2.4 Check arriving geometry is manifold and matches the requested cell
      counts before anything is built on it, and verify the check fails on a
      deliberately truncated STL

## 3. Choose a decomposition that nests

- [ ] 3.1 Divide a requested baseplate into identical plates where the area
      divides equally and they fit the bed; verify on a 10x8 request that it
      yields four 5x4 plates rather than the six mixed sizes the built-in bed
      splitter produces
- [ ] 3.2 Fall back to a componentwise decreasing chain of sizes where equal
      division is impossible, and verify every plate's footprint contains the one
      above it
- [ ] 3.3 Fall back to the existing overhang-and-pillar behaviour where neither
      is possible, and verify the tool reports which of the three cases a request
      landed in
- [ ] 3.4 Verify on a set that nests that `support_regions` produces no pillars
      at all, and record pillar volume against the same request built from a
      web-generator STL

## 4. The film

- [ ] 4.1 Build the film from the generated plate's own bottom profile, one layer
      thick, a full layer of clearance below and none above, and verify the
      modelled faces sit at exactly those distances
- [ ] 4.2 Hold the film's top face a hundredth of a millimetre off the plate
      above rather than coincident with it, and verify no face of the film is
      coincident with any face of a plate
- [ ] 4.3 Update the gap-too-small error to name the gap and both clearances, and
      verify it fires when the gap cannot hold both plus one layer
- [ ] 4.4 Keep the film thickness configurable with two layers still available,
      and verify a two-layer film is produced when asked for

## 5. Build the project

- [ ] 5.1 Put the film back into `make3mf.py` as a part in the interface filament
      with the template's per-part print settings, and drop the decoy, the
      blocker and the support routing; verify the written 3mf opens in Studio
- [ ] 5.2 Restore the film part to `templates/stack-template.3mf` so its print
      settings have somewhere to live, and verify the template stays under 20 KB
      and carries no real model geometry
- [ ] 5.3 Slice the generated project **with no `--no-check`** and verify it
      exits 0 with no empty-layer complaint, which is what makes the ordinary
      Studio workflow and its filament mapping work again

## 6. Verification that reads the output

- [ ] 6.1 Report per gap, from the sliced G-code, the measured clearance below
      and above the film, the number of separate film regions, and any support
      found; verify it agrees with hand-measurement on one sliced file
- [ ] 6.2 Verify on two stacks of different overall height that the measured
      clearance is a full layer below and zero above in every gap
- [ ] 6.3 Verify zero support extrusion anywhere on the stack, read from the
      G-code rather than from the settings

## 7. Remove what is no longer needed

- [ ] 7.1 Remove the multi-plate STL input and the inference it required --
      shell splitting, lattice flood-fill, land/rib inference, containment
      ordering, lattice registration -- and verify the suite passes with the
      tests for removed behaviour deleted rather than skipped
- [ ] 7.2 Remove `emit_interface.py`, the decoy, the blocker, the interface plan
      and the sliced-package writer, keeping `gcode.py`; verify nothing
      references them and the suite passes
- [ ] 7.3 Update the README, the generated printing notes and
      `openspec/config.yaml` so none of them describe an STL input, a decoy, a
      blocker or a post-slice step, and verify no reference to any of them
      remains
- [ ] 7.4 Decide what happens to `generate-interface-gcode` -- archived as
      superseded, or kept if the print in 1.1 favoured it -- and record the
      decision

## 8. Prove it on the printer

- [ ] 8.1 Generate a two-plate test set from a requested size, slice, and verify
      from the G-code that the film sits a full layer above the plate below with
      the plate above printed directly onto it
- [ ] 8.2 Compare print time and material against the measured 32.6 min and
      11.36 g PLA + 0.66 g support, and record the cost of generating rather
      than importing
- [ ] 8.3 Test print it and confirm both that the stack separates by hand and
      that the underside of the upper plate is clean -- the only check that
      answers the question this project exists for
- [ ] 8.4 Record in an ADR that the plates are generated rather than imported,
      why the film sits against the plate above, and where the GPL boundary is
