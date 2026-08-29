## 1. Measure before changing

- [ ] 1.1 Report how much of the current 22.6 cm3 of film has nothing above it,
      per gap. If the answer is small, stop and say so rather than proceeding

## 2. Trim

- [ ] 2.1 Compute `must-carry` per gap: the plate above's downward face united
      with the pillar occupancy at the level above
- [ ] 2.2 Intersect the film's bottom layer with it, then flare from the result
- [ ] 2.3 **Trim before bridging, never after.** `bridge-film-over-pillars` has
      landed and closes the film's base across short spans, and that closing is
      the last step of forming the base. Run this trim after it and it deletes
      every bridge: a bridge span carries nothing by construction -- anywhere
      above it that needed carrying would already stand a pillar, and that pillar
      would already be in the base, so there would have been nothing to bridge.
      Film on a pillar *top* is the opposite case and must survive: it carries
      the plate border the pillar exists for, so it is never a trim target.
      Verify by asserting the film's region count per gap is unchanged by the
      trim -- 1 per gap on the nine-plate drawer stack

## 3. Verify nothing lost its footing

- [ ] 3.1 Test: film that carries only a pillar is kept
- [ ] 3.2 Test: film under an empty socket is dropped
- [ ] 3.3 Test: every pillar segment has film beneath it wherever the level below
      does not already support it
- [ ] 3.4 Slice the generated 3mf; confirm no support is generated, all interface
      filament on the model is inside a gap, and record the PETG saved
