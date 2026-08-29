# 0006. One solid per region where tracing works, overlapping pieces where it does not

Date: 2026-08-29
Status: Accepted

## Context

Bambu reported 165 non-manifold edges in the stack and 5,225 in the film, and
offered the model for third-party repair. Every one was used by exactly four
facets and none by one: nothing torn, simply too many closed skins.

Regions are rasterised, cut into rectangles, and written as boxes. Any two boxes
that touch put four facets on the shared edge, and no arrangement escapes it --
sharing a whole face is worse than sharing a corner, five bad edges against one.

## Decision

Every mesh this project writes has each edge shared by exactly two facets, and
`verify.py` checks it. The check distinguishes the causes and that distinction is
what makes it useful: one facet on an edge is a hole in the surface, four is two
skins meeting.

Pillars are traced. `contours()` walks a region's cell boundaries into closed
loops -- outer counter-clockwise, holes clockwise -- and `prism()` builds one
closed solid, bridging each hole into the outer loop so the caps can be
ear-clipped. 193 loose boxes become 28 solids, with outlines that follow the
socket instead of stepping around it.

Tracing is verified rather than trusted. A region touching itself corner to
corner pinches, and the two loops meeting there reintroduce the defect, so
`region_solid()` checks the traced solid and falls back to boxes overlapping by a
micron. Overlapping solids share no edge and slicers union them regardless, so
the guarantee holds for any shape rather than only shapes the tracer manages.

The film uses that fallback wholesale. It is sacrificial and peeled off, so
rectangles are acceptable there.

The weld goes between pieces and never on a face anything is measured against:
sideways always, upward into the band above, never past the film's own top. A
micron of overhang there would make the clearance from the plates a lie.

## Consequences

Slicers accept the output. Pillars gained the two properties asked for much
earlier and not delivered until now: one solid per column, and curved outlines.

The film is still hundreds of overlapping rectangles, which is why the spec
requires "a pillar is one solid" rather than "a region is one solid". Anyone
tempted to extend tracing to the film should read the next section first.

Verification is now this project's job. This defect existed for the entire life
of the box decomposition and no test looked for it; the slicer found it.

## What was tried and does not work

Triangulating the film's regions. Its largest is one outline of 118 points
containing 20 holes, bridged into a loop of 2,174, and the clip finishes about 89
triangles short of 2,172 -- the uncovered cap being exactly the edges reported as
used by one facet. Three attempts:

- **Eberly mutual-visibility hole bridging**, replacing a nearest-vertex rule:
  5,225 bad edges down to 2,571. An improvement, not a fix.
- **Degenerate-ear fallback**, dropping zero-area vertices at bridge seams when
  no convex ear exists: no change at all. The stalled polygon has no zero-area
  vertex either.
- **Reflex-only containment testing**, the textbook optimisation: recovered 17
  triangles of 89 and took one ring from under a second to 64 seconds, the reflex
  lookup having made the clip cubic.

The remaining fault is a self-intersection in the bridged loop that a correct
implementation would not produce and that I did not find.
