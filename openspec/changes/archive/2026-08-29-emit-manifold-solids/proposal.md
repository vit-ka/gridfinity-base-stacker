## Why

Bambu Studio reports 165 non-manifold edges in the generated stack and refuses to
call the mesh valid, offering to send it to a repair service. The gap film is
worse: 5,402. The models slice today, but a mesh the slicer calls broken is one
misjudgement away from slicing wrongly, and it is not something to hand to
someone else.

Every bad edge is used by exactly four faces. The full distribution is the
evidence, and it is unambiguous:

| edges used by | stack | film | meaning |
|---|---|---|---|
| 1 face | 0 | 0 | a hole in the surface |
| 2 faces | 112,674 | 103,982 | normal, closed |
| 3 faces | 0 | 0 | one extra sheet |
| 4 faces | 165 | 5,402 | two closed shells sharing an edge |

Torn geometry shows up as edges used by a single face -- a boundary, the rim of a
hole. There are none, in either mesh. Every anomaly is exactly four, which is two
closed boxes meeting along an edge, corner to corner or edge to edge. Pillars and film are emitted as many
axis-aligned boxes from a rasterised region, and wherever two of them touch
without sharing a whole face, four triangles meet on one edge.

## What Changes

- Emit each connected region as one closed solid rather than as a heap of boxes.
- Two approaches, to be weighed in design:
  - Trace the region's outline (marching squares) and build a prism from the
    resulting polygon. Correct, and it also yields the two things asked for
    earlier and not yet delivered: curved outlines where the socket curves, and
    one solid per column instead of hundreds of fragments.
  - Overlap the boxes by a hair instead of butting them, so each stays a closed
    shell and no edge is shared. Cheap, and it satisfies the edge count, but it
    leaves the mesh a pile of interpenetrating solids and fixes neither the
    faceting nor the fragmentation.
- Whichever is chosen, the emitted mesh must satisfy: every edge used by exactly
  two faces.

## Capabilities

### New Capabilities
- `mesh-output`: what the generator guarantees about the geometry it writes --
  manifold edges, consistent winding, and enclosed volume matching what was
  computed.

## Impact

- `stack_plates.py`: `grid_rects` and its callers `support_fillers` and
  `interface_slabs`.
- `verify.py` gains a manifold check, so this cannot regress silently. It is not
  checked anywhere today, which is why the slicer found it first.
- The outline approach changes the emitted geometry, so pillar volume and the
  slicer's reading of it both move; both need measuring against the current file.
