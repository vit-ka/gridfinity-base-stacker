## Context

See `proposal.md` -- Why. What matters for the approach is how the film's extent
is computed today, in `interface_slabs`:

- Everything is a raster. `face_grid` samples a horizontal slice onto a grid of
  `--filler-step` (0.15 mm) cells and returns one `w`-bit integer per row, so
  every set operation downstream is a bitwise op on a big int and costs a few
  thousand C-level operations instead of fifty million Python ones. `dilate`,
  `erode` and `opened` are all disc-shaped morphology over that representation.
- The film's base for gap *j* is `face_grid(plate j, top) | regions[j]` -- the
  plate below's top face, unioned with the pillars standing at that level.
- The base is then extruded a layer at a time, each layer `dilate`d `flare`
  further out than the last, and each layer's region turned into geometry by
  `contours` + `prism`.

A pillar inside a socket appears in that base as an island: a blob of set cells
with a ring of clear cells around it, a few millimetres wide, then the socket
rim.

The film is written to its own STL under `--interface-part` and consumed by
`make3mf.py --interface`. Nothing about the stack mesh, the plate heights or
`plates.json` is involved.

## Goals / Non-Goals

**Goals:**

- One connected film per gap wherever the spans allow, with the count of
  disconnected regions reported rather than assumed.
- Bridging that is isotropic -- a span 1.5 mm away diagonally is as bridgeable
  as one 1.5 mm away along X.
- A default of 6 mm, with the sweep recording what it costs rather than
  deriving it.

**Non-Goals:**

- Changing the pillars. `support_regions` is not touched; a pillar that is too
  far from anything stays too far.
- Guaranteeing one region per gap. Some pillars stand in the middle of a wide
  socket and there is nothing within reach to bridge to; that is the correct
  outcome, not a failure.
- Bridging between gaps, or vertically. This is one gap at a time, in XY.

## Decisions

### Bridge by morphological closing of the base, not by component labelling

A closing -- `dilate` by `r` then `erode` by `r` with the same disc -- fills
every gap in a region that is too narrow to contain the disc, and leaves
everything else exactly as it was. With `r = span / 2`, the threshold in the
spec falls out directly: spans narrower than `span` close, wider ones do not.

It answers the requirement almost word for word. Each side closes or does not on
its own local width, which is the per-side independence the spec asks for. The
disc makes it isotropic -- the same reason `disc()` exists rather than a square,
already recorded in its docstring. And it reaches whatever material is nearest,
whether that is the socket rim or a neighbouring pillar, without having to ask
which.

It is also two calls into functions that already exist, on the representation
they already use, at big-int speed.

*Alternative: label connected components, dilate each island, intersect with
what it reaches.* More precise -- it can say "this cell was added to join
component A to component B" and add nothing else. Rejected as the first
implementation because it needs a component labeller over bitmask rows, it is
Python-speed over a 1173 × 1387 grid where the closing is C-speed, and its extra
precision buys one thing only: not filling concave notches inside an
already-connected region. That is a measurable quantity, so measure it before
paying for it. See Risks.

### The threshold is a span, not a radius

`--bridge-span MM` is the widest empty span that gets bridged, and the code
halves it to get the disc radius: `r_cells = span / 2 / step`. A user thinks in
"the ring around this pillar is 1.8 mm wide"; nobody thinks in structuring
elements.

The default is 6 mm, so the closing radius is 3 mm: 20 cells at the default
0.15 mm `--filler-step`, making `disc()` a 41 x 41 structuring element. That is
still a handful of big-int shifts per row and not a cost worth designing around.

`0` disables it, and must short-circuit rather than call `dilate(…, 0)`, so that
disabled means byte-identical to today's output rather than merely similar.

Note the rounding trap `disc()` documents: a radius under one cell yields the
centre alone. Here that fails safe -- under-reaching means fewer bridges, never
lost clearance -- so the radius is used as-is and not rounded out to a whole
cell of reach the way clearance radii are.

### Bridging is the last step of forming the base, after any trim

Order within `interface_slabs`:

1. what the film can rest on -- plate below's top face, plus pillars at that
   level
2. *(once `trim-unsupporting-film` lands)* intersect with what needs carrying
3. **bridge**: close by `span / 2`
4. flare, layer by layer, exactly as now

The two changes act on opposite regions, so this is an ordering constraint and
not a conflict. `trim-unsupporting-film` removes film with nothing above it
anywhere up the stack. The film on a pillar top has the plate border that the
pillar exists for sitting directly on it, so it is never a trim target: it is
the carrying case, not the wasted one.

The bridge span is the one place they meet. It lies over a region that carries
nothing at any level, and necessarily so: anywhere above it that needed carrying
would already stand a pillar there, and that pillar would already be in the
base, so there would be nothing to bridge across. Run the trim first and it
never sees a bridge; run it second and it deletes all of them. Hence the order,
and no exemption flag to carry around.

Step 3 after step 2 also gives the second trim scenario for free: a region the
trim deleted is not there to be bridged to, so no bridge is built to film that
will not exist.

Whichever change lands second owns making this sequence true, and the sequence
is written into the spec as behaviour rather than left as a code comment.

### Flare applies to the bridge because the bridge is in the base

Nothing special is needed: step 3 produces a base, and step 4 already flares
whatever base it is given. The bridge is anchored on both ends by construction
-- that is what closing a gap means -- so its bottom layer spans air between two
supported edges, which is a bridge in the printing sense too.

### Island count is a first-class output

The measurement the proposal rests on is "how many disconnected regions of film
are there in this gap". That needs a connected-component count over the base
raster. It is not on the hot path -- once per gap, for reporting and for
`verify.py` -- so a plain flood fill over the rows is fine, and it can be the
same helper `verify.py` uses to check the written film independently.

Report it per gap in the stack report, before and after bridging, so a sweep is
one run per threshold rather than a manual inspection.

## Risks / Trade-offs

- **Closing also fills concave notches inside a region that was already
  connected** -- film that bridges nothing and is pure PETG waste. The chosen
  default should keep this small: the only concave features on a plate's own top
  face are the socket openings, 36 mm across and up, so a 3 mm disc fits inside
  every one of them and fills none, leaving the rings around pillars as the only
  notches in reach -- which is the target. → That is a prediction, not a
  measurement. Task 3.3 checks it: if the volume added at 6 mm is much larger
  than the ring area around the pillars, something else is being filled, and the
  labelled variant above is the answer.

- **A wide threshold adds PETG that carries nothing**, the same kind of waste
  `trim-unsupporting-film` exists to remove, spent deliberately here for the
  peel but spent all the same. The threshold is the only thing holding it in
  check. → Keep the default at the knee of the sweep, and state the film volume
  it costs in the printing notes so it is visible.

- **The bridge is thin and unanchored in Z.** It is one or two layers of PETG
  spanning air, joined to the sheet on both sides. If it tears during the peel
  the island is back, and the peel is the entire point. → Confirm on a sliced
  file that the span prints as bridge or solid infill rather than as sparse air,
  and prefer a threshold where the bridges are short.

- **Both this and `emit-manifold-solids` rewrite `interface_slabs`.** Bridging
  merges regions, which is where degenerate touching geometry appears. → The
  manifold check that change adds is the guard; run it on the bridged film once
  both have landed, and rebase whichever is second.

- **The measurement could say bridging is not worth it.** If the stack turns out
  to have few islands, or all of them are far from anything, the change buys
  little. → Task 1 is to count them before any code is written, exactly as
  `trim-unsupporting-film` was told to measure the waste first.

## Migration Plan

None needed in the usual sense. Bridging changes only `NAME-interface.stl`; the
stack mesh, plate heights and `plates.json` are untouched, so a regenerated film
drops into an existing 3mf and `postprocess.py` is unaffected. `--bridge-span 0`
reproduces today's film exactly.

