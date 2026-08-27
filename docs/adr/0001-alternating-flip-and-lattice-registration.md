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

Support blockers are emitted as a companion STL, one solid per socket, lofted to
that socket's own measured void and built in the plate's own frame so it goes
through the same rotation and translation as the plate.

## Consequences

The socket surfaces come out untouched, and the stack is exactly the bottom
plate's footprint, so nothing needs support from the bed.

The material saving is smaller than the geometry suggests. Sliced for real, a
six-plate stack takes 23 g of support, against 4 g predicted from contact area
alone -- the slicer puts support into the chimneys regardless, and the support
style matters more than the stacking does (59 g with grid, 23 g with snug).
Treat the flip as protecting the socket surfaces, not as a way to save filament.

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

Registration assumes every plate shares one pitch. Plates from different
generators, or a mix of pitches, would need per-interface registration instead of
one global origin. `--no-flip` and `--no-register` exist to fall back.
