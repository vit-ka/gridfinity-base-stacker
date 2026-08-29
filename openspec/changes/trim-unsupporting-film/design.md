## Context

`interface_slabs` builds each gap's film from `base` -- the plate below's top
face united with any pillar at that level -- and then flares it upward. `base`
answers "what can this rest on". Nothing answers "does anything rest on this".

The information needed is already computed. `support_regions` returns per-level
pillar occupancy, and the plate above's downward face is one raster away and is
already taken for the pillar calculation.

## Goals / Non-Goals

**Goals:**
- Film only where something above it is carried.
- The flare survives the trim.

**Non-Goals:**
- Changing clearance or thickness. That is `separate-film-from-plates`.

## Decisions

**Film extent becomes `can-rest-on` intersected with `must-carry`.** Where
`must-carry` is the plate above's downward face united with the pillar at the
level above -- the same union, one level up, that `base` uses one level down.

**Trim the carried region, then flare.** Flaring first and trimming after would
cut the overhang back off, and that overhang is load-bearing: it is what lets a
0.3 mm rib of pillar carry a bead over a millimetre wide. So the trim applies to
the film's bottom layer and the flare is grown from the trimmed region.

**Anything trimmed must carry nothing at all.** The failure mode here is the same
one that put plate borders in mid-air: a region that looked unnecessary because
the check asked the wrong question. Film that carries a pillar has no plate
material above it and must survive, which is exactly the case a naive
intersection with the plate's face alone would delete.

## Risks / Trade-offs

- **Trimming too much leaves a pillar segment standing on nothing.** This is the
  serious risk and the reason a pillar's own footprint is part of `must-carry`.
- **The saving is unmeasured.** 22.6 cm3 of film today; how much is waste is not
  known and should be the change's first measurement, because if it is small the
  change is not worth the risk it carries.
- **Interacts with the flare's clipping**, which is currently unbounded sideways;
  a trimmed base flares from a smaller region, so the film's outer edge moves.
