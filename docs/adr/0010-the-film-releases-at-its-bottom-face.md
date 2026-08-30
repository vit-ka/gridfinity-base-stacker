# 10. The film releases at its bottom face

Date: 2026-08-30
Status: Accepted

## Context

[ADR 0009](0009-clearance-is-quantised-to-the-layer-height.md) got a stack to come
apart and left one problem behind: with a real gap under it, the first layer of
each plate above bridges over the film with nothing to squish against, and its
underside prints badly. That ADR called it "the next thing to fix" and noted the
clearance could not simply be raised, because every 0.2 mm of clearance is 0.2 mm
the plate above has to bridge.

The assumption underneath that, held since [ADR 0007](0007-bridge-the-film-so-it-lifts-as-one-sheet.md),
was that the film must be held clear of **both** plates. It was never tested. It
came from a slicing observation -- coincident faces make the slicer merge the two
volumes into one body -- which is a statement about *meshes*, not about adhesion,
and it was generalised into a statement about separation.

[GridPlates](https://github.com/pfa230/GridPlates) stacks plates the other way
round: 0.2 mm below the spacer and 0.01 mm above it, one layer thick. Reproduced
here on the two-plate test, through this repository's own mesh film, in Bambu
Support W:

| | |
|---|---|
| modelled | 0.2 below, 0.01 above, gap 0.6, two layers of film |
| printed, read from G-code | film 4.20-4.60, plate below 4.00, plate above 4.60 |
| measured clearance | **0.200 below, 0.000 above** |
| slices with **no `--no-check`** | yes, exit 0 |
| time / material | 32.6 min, 11.36 g PLA + 0.66 g Support W, 2 filament changes |
| support extrusion on the stack | 0 |

Test printed: **the bottom face released cleanly. The top face needed some
cleaning and was not fused.** The underside of the upper plate is no longer the
loose surface a 0.2 mm bridge produced.

Two faces of the film are not the same problem, and the reason is directional.
Film laid down onto a cured plate surface is support-printed-onto-model, which is
the direction that grips -- that is what ADR 0008 was fighting when Support W had
to be pried and cut away, and ADR 0009's table shows those prints had the film
fused to the plate *below*. A plate laid down onto film is
model-printed-onto-support, which is the direction breakaway interface material
is designed for.

The 0.01 mm is not a gap. It is the smallest value that keeps the film's top face
off the plate's bottom face so the slicer resolves them as two bodies rather than
merging them, and it is far from any sample plane, so ADR 0009's rounding cannot
decide it either way. It prints as nothing.

## Decision

**The film is held clear of the plate below it by a full printed layer, and is
not held clear of the plate above it.** The plate above prints directly onto the
film and the stack releases at the film's bottom face alone.

The defaults become 0.2 mm below and 0 above, modelled as 0.01 so the faces are
not coincident. ADR 0009's decision stands unchanged -- the clearance that exists
is still a whole number of layers, and the film's material still prints at its
own specification.

**Gap tuning stops here.** The values are settled by this print, not by further
sweeps.

## Consequences

The problem ADR 0009 left open is closed, and the mechanism this project exists
for now works end to end: a stack that comes apart, with a usable surface on both
sides of every interface.

The film's height no longer needs to land anywhere the slicer's layer grid cannot
put it. Every face in the gap sits on a layer boundary or harmlessly inside one,
which is what ADR 0009 said was the only unambiguous arrangement. The machinery
built to reach off-grid heights -- writing the interface into sliced G-code, the
decoy column that provoked its filament changes, the blocker that protected the
stack from the decoy's support, and the hand-built print package -- buys nothing
this geometry wants. It is finished and kept, but it is not the path.

Because the gaps are full of model material again, the project slices without
`--no-check` and goes to the printer through the ordinary Bambu Studio workflow.
That removes the empty-layer refusal, and with it the filament-mapping failure
that a bare `.gcode` cannot avoid.

What is given up is the ability to choose the clearance above freely. It is zero,
and the material is what separates there -- so the interface filament's release
behaviour now matters more than it did, and a filament that grips in the
model-onto-support direction will fail with no geometry left to compensate.

A one-layer film is what the arrangement this came from uses; the print above
used two and is what is measured. One layer is not yet proven here.
