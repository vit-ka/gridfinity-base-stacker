# 0007. Bridge the film across short spans so it lifts as one sheet

Date: 2026-08-29
Status: Accepted

## Context

A pillar stands inside a socket shaft, so the film on its top came out ringed by
the opening: an island, floating in the hole with the sheet on the surrounding
lattice millimetres away. The film is peeled by hand and is meant to come off
whole -- that is why it prints as a solid part with no walls or shells and 100%
crossed infill rather than as support interface. Islands defeat that. They stay
behind in the sockets to be picked out one at a time.

Measured before anything was written: 36 regions across eight gaps, so 28
islands, every one either 0.90 mm or 3.15 mm from the sheet and none beyond.

## Decision

The film's base is closed -- dilated then eroded by half the span -- as the last
step of forming it. A span narrower than the threshold closes and a wider one is
untouched, each side judging its own local width, and the disc gives the same
answer whichever way a gap is turned.

`--bridge-span` defaults to 6 mm. The sweep on the nine-plate drawer stack puts
the knee at 3 mm:

| span | islands | film cm3 |
|---|---|---|
| 0 | 28 | 22.40 |
| 1-2 | 11 | 22.70 |
| 3 | 0 | 23.22 |
| 6 | 0 | 23.59 |
| 8 | 0 | 23.77 |

6 mm buys no fewer islands than 3 mm, only 0.37 cm3 more filament. The default is
past the knee deliberately and the help text says so; 3 mm is the value to use if
that matters.

**Trimming film that carries nothing must run before bridging, never after.** A
bridge span carries nothing by construction -- anywhere above it that needed
carrying would already stand a pillar, and that pillar would already be in the
base, so there would have been nothing to bridge. Run a trim afterwards and it
deletes every bridge. Film on a pillar *top* is the opposite case and must
survive: it carries the plate border the pillar exists for.

The raster grid carries an 8 mm margin, and every result is clipped back to the
stack's extent. Both are needed, and clipping the *result* rather than the
operands is what gives both: sized to the stack exactly, a dilation is cut off at
the model's edge, and a closing cut off on the way out erodes back from that
straight edge instead of the real rounded one -- squaring the plates' rounded
corners and leaving film in the wedge outside them. Given room but no clip,
pillar dilation runs past the plate into open air and the stack grows from
176.0 x 208.0 to 177.0 x 208.5.

The film's flare is exempt from that clip. It is meant to overhang by a layer's
worth on every side, all the way around, and the lip past the outer edge is the
point of it.

## Consequences

One film region per gap across all eight, counted back from the written STL
rather than from the plan. `verify.py` reports it, rasterised rather than split
into shells -- the film's pieces overlap deliberately (ADR 0006) and overlapping
boxes share no vertices, so shell-splitting would count every box and say nothing
about the sheet.

Costs 1.2 cm3 of interface filament at the default, 0.8 at the knee.

## A CLI slice is evidence only where the geometry is unambiguous

Recorded here because nearly everything in this project is verified by slicing
from the command line, and for a while that looked unsafe.

With the film flush against the plates, a CLI slice put 13,151 mm of interface
filament on the model and the GUI merged the volumes and put down none. Same
file, same declared parts, opposite answers. That is what an ambiguous input
means: two surfaces in the same plane give a slicer no fact to decide by, so each
resolves it however it happens to.

Hold the film clear and both agree. So the clearance in ADR 0006's sibling change
does a second job beyond stopping the merge -- it makes the geometry decidable.

This was never a CLI-versus-GUI difference in general. Coincident surfaces are
the only case in which the two have been seen to differ, and the generator no
longer emits any: the film is held clear of the plates, and pieces of one region
overlap rather than touch.

## The clearance must leave an empty layer, not merely a distance

0.1 mm per face, and the criterion behind it is exact rather than empirical: the
clearance has to be enough that at least one layer ends up with no model material
on it at all.

`PrintObject::detect_surfaces_type` says why. Its own comment states the rule --

    // stTop - Part of a region, which is not covered by any upper layer.
    //         This surface will be filled with a top solid infill.

-- and the implementation compares against the layer above:

    // comparison happens against the *full* slices (considering all regions)
    // unless internal shells are requested
    ExPolygons upper_slices = interface_shells ?
        diff_ex(layerm_slices_surfaces, upper_layer->m_regions[region_id]->slices.surfaces, ...) :
        diff_ex(layerm_slices_surfaces, upper_layer->lslices, ...);
    surfaces_append(top, opening_ex(upper_slices, offset), stTop);

`upper_layer->lslices` is the layer's slices across *all* regions. The film is a
different region -- its own extruder -- but the same object, so it is in there and
it subtracts. Where film sits on the layer directly above a plate, that plate's
top is not a top: it is `stInternal`, and gets sparse infill.

Which is what a weld looks like from outside, and it was spotted in the preview
before any of it was measured: infill on what should have been the top layer of a
base. Measured on that layer:

| | 0.05 mm clearance | 0.1 mm clearance |
|---|---|---|
| Top surface | 160 mm (29%) | 640 mm (79.5%) |
| Sparse infill | 68 mm | none |
| Floating vertical shell | 115 mm | none |

At 0.05 mm the layers run consecutively -- plate, film, film, plate -- with
material on every one, so the slicer reads a single continuous body: no top on
the plate, no bottom on the film. At 0.1 mm the layer between carries nothing on
the model (57.7 mm of extrusion, all wipe tower), and that emptiness is the whole
mechanism.

So the number is a consequence and does not travel: at a 0.2 mm layer height
0.05 mm cannot leave an empty layer and 0.1 mm just does. At a 0.1 mm layer
height, 0.1 mm of clearance would weld for the same reason.

Shaving it buys nothing anyway. Height follows the gap, the gap snaps to a whole
layer, so the only stack heights on offer are 39.2 mm and 40.8 mm whatever the
clearance; a smaller value buys a thicker film, not a shorter stack. The 0.4 mm
gap at 0.05 mm clearance would have saved an hour and a quarter and a third of a
kilo of filament, and it is the value that welds.

`interface_shells` would defeat the mechanism directly -- it switches that diff to
the region's own slices, so every part gets its own top and bottom shells even
where parts touch, and the slicer's comment gives the intent as "useful if one of
the parts is to be dissolved", which is this arrangement almost word for word.
Recorded and not used: it is a whole-object setting touching every part boundary
in the model, where an empty layer costs nothing and configures nothing. If the
clearance ever has to go, that is where to look, and it wants measuring.

## What was measured wrong first

- **An island does not bridge at the same distance diagonally as along an axis,
  and should not.** A closing fills by channel width, not nearest-point distance,
  and two blobs meeting corner to corner have a wedge wider than the corner gap:
  radius 7 against 4, for nearest points 5.66 and 5.00 cells apart. Isotropy is a
  property of the structuring element, so the test asserts that the same gap
  turned ninety degrees closes at the same span.
- **The join-versus-notch split first measured 0% joining**, which was the
  metric's fault: a bridge's interior cells touch no original region, only its
  ends do. Per connected component of the added material it is 92.1% joining and
  7.9% notch, so the simple closing stands and the labelled variant is not
  needed.
