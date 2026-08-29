## Context

Regions are computed as bitmask grids and then decomposed by `grid_rects` into
maximal horizontal runs merged across identical rows. Each rectangle becomes a
box. Where two boxes touch edge to edge rather than face to face, four triangles
share that edge.

The counts are exact and diagnostic: 165 bad edges in the stack, 5,402 in the
film, every one of them used by four faces and none by three. Nothing is torn or
missing -- there is simply more than one shell meeting along a line.

## Goals / Non-Goals

**Goals:**
- Every edge used by exactly two facets.
- The property is checked by this project, not discovered by the slicer.

**Non-Goals:**
- Reducing pillar volume or changing which regions get support. The regions are
  correct; only their expression as geometry is at fault.

## Decisions

**Trace the outline rather than nudge the boxes.** Two approaches were weighed.

*Overlapping the boxes* by a hair leaves each a closed shell, so no edge is
shared and the count goes to zero. It is a few lines. But the mesh stays a pile
of interpenetrating solids that happens to satisfy the check, and it fixes
neither of the two things already asked for and still outstanding: outlines that
follow the socket's curve instead of stair-stepping it, and one solid per column
instead of hundreds of fragments.

*Tracing the region's outline* -- marching squares to closed contours, then a
prism -- is the honest fix and delivers all three at once. It costs contour
extraction plus cap triangulation with holes, which is real work.

**Decided: take the outline.** Confirmed in review. The nudge is worth keeping in
mind only if the outline work turns out far larger than expected, and would then
be recorded as a stopgap rather than a solution.

**Verification gains a manifold check.** This defect existed for the whole life
of the box decomposition and no test noticed, because nothing looked. The check
is cheap: count facets per edge.

## Risks / Trade-offs

- **Cap triangulation with holes is the hard part.** A region can enclose voids;
  fan triangulation is not sufficient. Ear clipping with hole bridging is the
  standard answer and needs care over degenerate cases.
- **Emitted geometry changes**, so pillar volume and every measurement resting on
  it move. Both need re-measuring rather than assuming the change is neutral.
- **Contours from a raster still carry the sampling step.** Tracing does not by
  itself give true arcs, only a polygon; whether the result looks curved depends
  on the step and on any simplification applied.
