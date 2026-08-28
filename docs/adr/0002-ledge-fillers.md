# 2. Fill ledge voids with loose blocks copied from the plate above

Date: 2026-08-27
Status: Accepted

## Context

A plate that overhangs the one below leaves the slicer to raise a freestanding
support fin from whatever is beneath it. On the six-plate cabinet set that fin is
174 mm long, 8.6 mm tall and under a millimetre thick. It is the least printable
structure in the arrangement, and no support setting fixes it: it is not an
overhang-detection artefact but the only way support can reach the ledge.

Ordering does not avoid it. `216x126` must sit second because only `216x144` is
wide enough to hold it, which forces a 144-deep plate above a 126-deep one later.
Containment is a partial order and this set has incomparable members.

## Decision

Fill the void with loose blocks, one at each plate level the ledge spans, and
include them in the model rather than as slicer modifier parts.

Each block is the overhanging plate's own footprint, **projected from the face
directly above it** and inset by the gap in XY. The stack's gaps already give the
vertical clearance, so a block is clear on all six sides: welded to nothing, and
loose once the support is out.

Three things were tried and rejected:

- **Support enforcers.** Rejected on the user's instruction -- they want one file
  to load, not a model plus modifier parts.
- **A solid slab.** Stable, but 27 cm3 against 6 cm3, and it pays for top and
  bottom shells over the whole area.
- **Projecting the plate's widest section.** The socket tapers, so the wide end
  gives a filler broader than both the face it carries and the face it stands on,
  and its own footprint then needs bridging support under itself: 12.5 cm3 and
  6.0 g of support, against 5.4 cm3 and 5.3 g for the near face.
- **Heavy dilation.** 0.8 mm doubles every web and swallows the rounded corners.
  A small outward offset is still needed, though: a faithful projection
  reproduces the plate's thinnest webs exactly and the slicer drops the thinnest
  of them, leaving holes. The default is 0.5 mm, about two perimeters, dilated
  with a disc rather than a square so corners stay round.

`--split` remains as an alternative that avoids ledges entirely by emitting one
stack per chain of the containment order. It is better on every measure -- 4.71 h
and 4.2 g against 5.19 h and 5.3 g -- except that it is two print jobs, which was
the whole point of stacking.

## Consequences

The fin is gone and support drops, because the slicer no longer builds it. The
cost is 16 minutes and 6 cm3 of blocks to pick out of the ledge area.

The projection is sampled on a raster, not sliced as polygons, so the outline is
stepped at `--filler-step` (0.15 mm by default, below what a 0.4 mm nozzle can
render). True arc geometry would need polygon offsetting and triangulation with
holes; the raster reproduces the plate cell for cell at this resolution, verified
by comparing footprint maps character for character.

Every filler is checked against every plate for overlap. Nothing may fuse: that
is the property the whole idea rests on.
