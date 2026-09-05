## 1. Vendor the generator

- [x] 1.1 Vendor `gridflock.scad`, the `gridfinity-rebuilt-openscad/src/` tree it
      includes, and the generated `paths/puzzle.scad` under `vendor/`, excluding
      `opengrid/`, `docs/`, `images/` and `tests/`; record the upstream commit,
      and verify the tree is byte-identical to upstream at that commit and that
      it renders a plate with `opengrid/` absent
- [x] 1.2 Carry the copyright notices of both vendored works alongside this
      project's own, and verify each vendored work's notice is present
- [x] 1.3 Document the upgrade procedure -- replace the directory, re-run
      upstream's `extract_paths.py` for `paths/puzzle.scad`, re-run the checks --
      and verify the documented steps reproduce the vendored tree from a fresh
      clone

## 2. Generate a plate

- [x] 2.1 Invoke OpenSCAD for one plate of a requested cell size with
      `--export-format binstl` and `--enable=lazy-union`, and verify the written
      STL loads through `stl_io.read_stl` and measures the requested whole number
      of 42 mm cells -- baseline 126.0000 x 84.0000 x 4.6500 for a 3x2
- [x] 2.2 Check arriving geometry is manifold and matches the requested cell
      counts before anything is built on it, and verify the check fails on a
      deliberately truncated STL
- [x] 2.3 Fail with a message naming what was looked for and where when OpenSCAD
      or the vendored source is absent, and verify by pointing the tool at a path
      that does not exist
- [x] 2.4 Surface a generator failure with what was asked of it rather than
      swallowing it, and verify by passing a parameter the SCAD rejects
- [x] 2.5 Assert every parameter this project sets is declared by the vendored
      source, and verify the check fails when a parameter is renamed -- OpenSCAD
      ignores an unrecognised `-D` silently, so this is the failure mode an
      upgrade actually has
- [x] 2.6 **Measure `projection(cut = true)` on the real 478 x 502 / 250 x 220
      request before building on it.** Baseline is 0.06 s on a 3x2; the real
      plates reach 4.22 x 5.04 cells. Record the time per plate, and if it does
      not hold, stop and re-decide -- the Python raster path is still in the tree

## 3. Decompose the request

- [x] 3.1 Divide a requested baseplate into identical plates where the area
      divides equally and they fit the bed, and verify a 504 x 504 request on a
      250 x 220 bed yields nine identical 4x4-cell plates
- [x] 3.2 Fall back to accepting overhang where equal division is impossible, and
      verify a 478 x 502 request on a 250 x 220 bed is generated and reports that
      it landed in that case
- [x] 3.3 Report per gap the unsupported area and the generated support volume,
      and verify against the measured baseline for upstream's own stacked output
      on 478 x 502: eight gaps at 0.4 %-75.5 %, worst gap 3034.5 mm2

      **Done: `support_report` / `format_support`.** On the real request the
      generated stack currently measures 0.0, 0.0, 64.5, 25.3, 12.2, 0.0, 0.2,
      0.0 % against upstream's 0.4-75.5 %. Five of eight gaps are already at
      zero; the three that are not are task 5.5 below.

## 4. The film

- [x] 4.1 Build the film in our own `.scad` from the plate's real profile via
      `projection(cut = true)` of the imported plate STL, one layer thick, and
      verify a 3x2 film measures 126.000 x 84.000 x 0.200 mm covering 2793.5 mm2
      -- 26.4 % of the 10584 mm2 bounding rectangle, i.e. the sockets are cut out
- [x] 4.2 Place the film a full layer of clearance above the plate below and
      against the plate above, and verify the modelled faces sit at exactly those
      distances
- [x] 4.3 Hold the film's top face a hundredth of a millimetre off the plate
      above rather than coincident with it, and verify no face of the film is
      coincident with any face of a plate
- [x] 4.4 Keep the film thickness configurable with two layers still available,
      and verify a two-layer film is produced when asked for
- [x] 4.5 Update the gap-too-small error to name the gap and both clearances, and
      verify it fires when the gap cannot hold the clearance below plus one layer

## 5. Overhang support

- [x] 5.1 Generate a pillar in our own `.scad` as the profile of the plate above
      minus the profile of the plate below, extruded through the gap, and verify
      a 3x2 over a 2x2 measures 126 x 84 x 4.000 mm covering **937.5 mm2**
      rib-to-rib and registered. (The proposal's 1797.5 mm2 was measured by a
      probe that sectioned the plate below at its *bottom* and applied neither
      the flip nor the registration; the plate below's upward face is the one
      that carries, so 937.5 supersedes it.)
- [x] 5.2 Verify a pillar is generated where a plate's solid border sits over a
      socket through-hole in the plate below even though the upper plate is
      within the lower plate's outline -- the case an outer-footprint difference
      misses, and the normal case once alternate plates are flipped
- [x] 5.3 Verify no pillar is generated where a socket opening lies over a socket
      opening, and that a pillar protrudes into neither plate nor any other gap
- [x] 5.4 Verify on a decomposition into identical plates that no pillar is
      generated at all, against the measured 0.0 % baseline
- [ ] 5.5 Verify on the real 478 x 502 request that every gap reports zero
      unsupported area after pillars, down from 0.4 %-75.5 %

      **BLOCKED on a diagnosed defect: pillars do not carry pillars.** A plate's
      land face is almost entirely open -- a 4x4 measures 7473.5 mm2 at its rib
      face and 244.0 mm2 at its land face -- so at a land-to-land interface
      almost everything standing in the upper plate's band is *the next gap's
      pillar*, not plate material. Measured at gap 3: of 3793.0 mm2 present,
      3613.0 mm2 is the gap-3 pillar and only 180 mm2 is plate 3.

      So support has to accumulate top-down: what needs carrying at a level is
      the plate's downward face **union the pillars standing in that plate's
      band**, and `generate_pillar` currently differences plate against plate
      only. The gap-film spec already requires this ("The film carries the
      pillars as well as the plates"); task 5.1 as written does not. Fix is to
      walk the stack from the top and feed each level's accumulated support
      region into the next.

## 6. Build the project

- [x] 6.1 Put the film back into `make3mf.py` as a part in the interface filament
      with the template's per-part print settings, and drop the decoy, the
      blocker and the support routing; verify the written 3mf opens in Studio
- [ ] 6.2 Restore the film part to `templates/stack-template.3mf` so its print
      settings have somewhere to live, and verify the template stays under 20 KB
      and carries no real model geometry
- [x] 6.3 Slice the generated project **with no `--no-check`** and verify it exits
      0 with no empty-layer complaint, which is what makes the ordinary Studio
      workflow and its filament mapping work again

## 7. Verification that reads the sliced file

- [x] 7.1 Report per gap, from the sliced G-code, the measured clearance below and
      above the film, the number of separate film regions, and any support found;
      verify it agrees with hand-measurement on one sliced file
- [x] 7.2 Verify from the G-code, on two stacks of different overall height, that
      the measured clearance is a full layer below the film and zero above in
      every gap
- [x] 7.3 Verify zero support extrusion anywhere on the stack, read from the
      G-code rather than from the settings
- [x] 7.4 Compare print time and material from the sliced file against the
      measured 32.6 min and 11.36 g PLA + 0.66 g support for the two-plate test,
      and record the cost of generating rather than importing

      Measured on the generated test set (84 x 84 mm, 2x2 cells):
      t-identical 38.2 min / 14.70 g + 0.52 g; t-pillar 26.6 min / 8.82 g +
      0.52 g; t-tall (3 plates) 59.1 min / 22.35 g + 1.56 g. Not directly
      comparable to the 32.6 min baseline, which was a different footprint --
      recorded as the new baseline rather than as a win or a loss.

## 8. Remove what is no longer needed

- [ ] 8.1 Remove the multi-plate STL input and the inference it required -- shell
      splitting, lattice flood-fill, land/rib inference, containment ordering,
      lattice registration -- and verify the suite passes with the tests for
      removed behaviour deleted rather than skipped
- [ ] 8.2 Remove `emit_interface.py`, the decoy, the blocker, the interface plan
      and the sliced-package writer, keeping `gcode.py`; verify nothing
      references them and the suite passes
- [ ] 8.3 Remove the Python pillar authoring (`support_regions`, `region_solid`
      and the tracing behind them) while keeping `face_grid` and the rasterising
      used to *measure* generated geometry, and verify the measurements in tasks
      3.3 and 5.5 still run
- [ ] 8.4 Remove every reference in the repository to the previous web generator
      -- `README.md`, `models/README.md`, `openspec/config.yaml`,
      `test_stack_plates.py` -- leaving `docs/adr/0001` alone as an accepted ADR,
      and verify no other reference remains
- [ ] 8.5 Update the README and the generated printing notes so none of them
      describe an STL input, a decoy, a blocker or a post-slice step, and verify
      no reference to any of them remains
- [ ] 8.6 Archive `generate-interface-gcode` and `generate-plates-from-scad` as
      superseded, and verify the archive keeps their measurements findable

## 9. Prove it on the printer

- [x] 9.1 Generate a small two-plate test set from a requested size, slice it, and
      verify from the G-code that the film sits a full layer above the plate below
      with the plate above printed directly onto it -- small and fast-printing,
      because two full-size stacks were lost partway and answered nothing
- [ ] 9.2 Test print it and confirm both that the stack separates by hand and that
      the underside of the upper plate is clean -- the only check that answers the
      question this project exists for
- [ ] 9.3 Verify an upgrade is cheap: replace the vendored copy with a newer
      upstream commit, re-run the path extraction and the checks, and confirm
      every check above still passes with no file of this project's own edited
- [ ] 9.4 Record in an ADR that the plates are generated rather than imported,
      that the film and pillars are authored in OpenSCAD against the real
      sectioned profile, why upstream's own stacked mode was not used, and where
      the vendoring boundary sits


## 10. Connectors, segmentation, and top contact (added during apply)

The original per-plate approach could not produce connectors -- a lone segment
has no neighbour to join -- which was the whole reason to adopt GridFlock.
Corrected here; the artifacts (proposal, design) still describe the per-plate
premise and need revising to match.

- [x] 10.1 Generate a whole baseplate with GridFlock's own segmentation and
      connectors on (`scad.generate_baseplate`); verify the connector geometry
      has strictly more facets than the connectorless (746 more on a 2-segment
      168x84 plate)
- [x] 10.2 Plan placement on a connectorless pass, because the puzzle tab breaks
      lattice detection (a 2-cell segment reads as 3) and is non-manifold; apply
      that transform to the connector geometry, whose cells sit identically
      (`stack_plates.build_segmented_stack`); verify detection is clean on the
      connectorless shells and miscounts on the connector ones
- [x] 10.3 Shave `top_slice` off each plate's land face so the plate above lands
      on a flat contact rather than a one-perimeter knife edge; verify contact
      area rises from 53.2 to 354.2 mm2 at 0.33 mm on a 2x2 (TOP_SLICE = 0.33)
- [x] 10.4 Build and slice a 2-segment connector test with no --no-check; verify
      exit 0, connectors present, 0.200 below / 0.000 above, zero support
      (out/t-connectors-suppla.3mf: 39.0 min, 15.13 g + 0.44 g)
- [ ] 10.5 The connector geometry is non-manifold upstream (8-17 edges); Bambu
      slices it anyway. Decide whether the mesh-output manifold check should
      exempt vendored connector geometry, and record it
- [ ] 10.6 Revise proposal.md and design.md: the "drive per plate, choose our own
      decomposition" decision is wrong -- connectors require GridFlock's
      segmentation. The nesting/identical-plates argument goes with it.
