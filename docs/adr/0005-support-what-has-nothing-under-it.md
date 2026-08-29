# 0005. Decide support by what is underneath, not by footprints

Date: 2026-08-29
Status: Accepted

## Context

ADR 0002 fills a ledge -- a plate hanging past the edge of the one below -- with
loose blocks. It finds those ledges by subtracting bounding boxes: where the
upper plate's footprint reaches past the lower one's, raise a column.

That test is too weak, and a nine-plate drawer set showed how. Plate 3 is 134 mm
wide and sits on plate 2 at 168 mm, so by footprint it is comfortably inside and
nothing is reported. But plates in a set have different cell counts -- 3 cells
against 4 -- and once the lattices are registered onto the same 42 mm grid, plate
3's solid west border lands squarely over plate 2's socket opening. Roughly
6.75 x 208 mm of material with nothing beneath it, and since Gridfinity sockets
are through-holes it is unsupported through plate 2 and plate 1 both, all the way
to the bed.

While the slicer still generated its own support this was invisible: the
balconies we spent so long trying to remove were partly doing this job. With the
whole model under a blocker (ADR 0004) nothing catches it, and the plate prints
into air.

## Decision

Ask the question directly: rasterise each plate's downward face, rasterise what
lies beneath, and take the difference.

For each plate, its down-face occupancy is walked down through the levels below
it. At each level the region still unsupported gets a block spanning that level's
z range, and the walk stops as soon as something solid appears underneath. This
subsumes ledges -- a plate hanging past the edge of the one below simply has
nothing beneath it -- so `support_fillers` replaces `ledge_fillers` outright.

Two details are load-bearing:

- These columns stand *inside* sockets, where ledge blocks stood in open air, so
  they must clear the socket taper. The blocking region is the union of a plate's
  material at both faces, which is the complement of the *smaller* opening
  (36.14 mm at the rib face, not 40.5 mm at the land face), then held clear by the
  gap. A column is then clear of the wall through the plate's whole height rather
  than only at the end it was sampled from.
- The region is not dilated outward. Growing it was right for a ledge projection,
  whose thinnest webs the slicer would otherwise drop, but here it walks the
  region off the edge of the plate and a millimetre into every shaft, leaving
  blocks in mid-air with nothing overhead. Measured: 89.2 cm3 of filler with the
  dilation, 50.6 cm3 without, and the difference was all artefact.

## Consequences

Every plate is carried. On the drawer stack: 683 blocks, 50.6 cm3 enclosed,
nothing overlapping plate material. That volume is enclosed, not filament -- the
slicer fills it at the sparse infill density like any other part.

It is still a lot, and it is inherent to the arrangement rather than to the
method: plates with different cell counts put solid border over through-shafts,
and someone has to carry it.

Which makes the plate ordering worth revisiting. `order_plates` minimises ledge
area, which this ADR shows is the wrong quantity -- it should minimise the
unsupported area that is now measurable. Different orders would give very
different filler volumes. Not done.

Analysis cost rose from a handful of small rasters to one per plate face, so the
sampling step defaults to 0.3 mm rather than 0.15 mm. The whole run is about 3 s,
against 38 s before the ray casting was indexed.

`ledges()` and `ledge_regions()` remain, reporting ledges in the printing notes.
They no longer decide geometry.
