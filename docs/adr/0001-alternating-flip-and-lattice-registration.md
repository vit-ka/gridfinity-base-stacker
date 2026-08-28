# 1. Stack plates with alternating flips on a shared cell lattice

Date: 2026-08-27
Status: Accepted

## Context

A multi-plate Gridfinity baseplate STL holds several plates side by side, more
than fit on one bed. To print them in a single run they have to be stacked with a
gap that support material fills, then split apart afterwards.

Measuring the extended baseplate settled how it had to be done. The cell
interiors are through-holes, so only 26-31% of a plate's footprint is solid. The
socket profile is a 5.86 mm rib lattice at the bottom face tapering to a 1.50 mm
land at the top face over a 1.41 mm funnel, on a 42 mm pitch.

Stacking every plate the same way up therefore lands a 5.86 mm rib on a 1.50 mm
land. That leaves a 2.18 mm unsupported ledge around all 91 sockets and forces
the slicer to fill every socket funnel with support -- support that presses into
the bin-seating surface and has to be picked out of 91 pockets by hand.

Blocking the funnels does not fix it. Support must be continuous from below, so a
blocker cannot leave a floating shelf under the ledge; the ledge becomes a
2.18 mm cantilever on the *first layer* of every plate, over open air. That layer
is the plate's mating face, and droop there means it will not sit flat.

## Decision

Order plates by footprint area descending, rotate every other one 180 degrees,
and translate each so all plates' 42 mm cell lattices share one origin.

Alternating the orientation makes every interface **land-to-land** (1.50 mm
against 1.50 mm) or **rib-to-rib** (5.86 mm against 5.86 mm). Contact faces
match exactly, so no ledge overhangs and no funnel is ever filled. Registering
the lattices lines the through-holes up into continuous open chimneys, which the
slicer leaves empty because nothing above needs holding up.

The rotation axis is chosen per plate, not fixed. Rotating about X mirrors Y and
about Y mirrors X; both put the top face down, and both are proper rotations, so
winding stays valid. A plate whose borders are asymmetric on the mirrored axis
has to shift up to half a pitch to re-register. The 144 mm-deep plates have a
partial cell row (21 mm border one end, 9 mm the other) and shift 18 mm if
rotated about X, hanging off the stack over open air. We take whichever axis
leaves the plate closest to centred.

Support blockers are available as a companion STL (`--blockers`), one solid per
socket, lofted to that socket's own measured void and built in the plate's own
frame. They are **off by default**: sliced both ways they make no measurable
difference, so they are not part of the design, only an escape hatch.

## Consequences

The socket surfaces come out untouched, and the stack is exactly the bottom
plate's footprint, so nothing needs support from the bed.

The material saving is smaller than the geometry suggests, and most of what we
can control is not in the geometry at all. Sliced for real on a six-plate stack:

| | time | support |
|---|---|---|
| flat, six separate jobs | 4.2 h | none |
| stacked, first working slice | 10.6 h | 88 g |
| ... support style snug, not grid | 8.7 h | 24 g |
| ... top Z distance 0.2, not 0 | 6.5 h | 23 g |
| ... gap 0.6 mm, not 0.8 | **6.2 h** | 18 g |
| stacked, blockers loaded | no change | no change |

Every one of those wins is a slicer setting. None is geometry. Support **style**
dominates -- grid packs the socket chimneys, snug does not -- and a top Z
distance of 0 makes support fill the whole gap rather than the part left between
the clearances. The blockers, the most intricate code here, change nothing.

Treat the flip as protecting the socket surfaces, not as a way to save filament
or time. And measure: these numbers came from slicing with the Bambu Studio CLI,
and each of them contradicted a plausible-sounding prediction made from the
geometry alone.

Three of the six plates print their top land face-down against support. That
surface is the flat rim, not the socket walls, so a PETG interface leaves it
clean; the functional taper is never touched.

The flipped plates print their socket walls as 45 degree overhangs. That is
self-supporting, but it puts a ceiling on the slicer's support threshold: set it
above 45 degrees and the funnels fill after all. `PRINTING.md` states the
measured angle for the model it was generated from.

Stacking costs print time rather than saving it. Measured on this set: 4.2 h to
print the six plates flat with no support at all, against 5.7 h stacked. It buys
one unattended job instead of six, and nothing else.

A ledge cannot always be ordered away, and where it survives it is the worst
thing in the print: a tall thin freestanding support wall. `--split` avoids it by
emitting one stack per chain of the containment order, which for the cabinet set
is two stacks and is faster and lighter than the single stack (4.71 h and 4.2 g
against 4.93 h and 6.7 g) at the cost of a second job. Prefer it unless one job
matters more than the wall does.

Registration assumes every plate shares one pitch. Plates from different
generators, or a mix of pitches, would need per-interface registration instead of
one global origin. `--no-flip` and `--no-register` exist to fall back.
