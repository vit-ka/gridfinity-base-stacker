## 1. Make the clearance real

- [ ] 1.1 Default `interface_slabs` clearance to 0.1 mm and the CLI gap to 0.6 mm
- [ ] 1.2 Raise a clear error when the gap cannot hold clearance on both faces
      plus one layer, naming both numbers
- [ ] 1.3 Update `settings/petg-interface.json` `_gap_mm` and the printing notes,
      including the height cost

## 2. Verify the film touches nothing

- [ ] 2.1 Test: no film facet is coincident with a plate facet, and the vertical
      distance from film to plate is at least the clearance at both faces
- [ ] 2.2 Test: a gap too small for clearance plus one layer is rejected
- [ ] 2.3 Test: clearance of zero still produces a film that fills the gap

## 3. Confirm against a sliced file

- [ ] 3.1 Generate the nine-plate drawer stack and its 3mf, slice it, and confirm
      the film still prints two layers per gap and all interface filament on the
      model lies inside a gap
- [ ] 3.2 Record the new height and print time against the current 39.2 mm /
      7.39 h
- [ ] 3.3 Test print, and confirm the stack separates -- this is the only check
      that answers the question the change exists for
