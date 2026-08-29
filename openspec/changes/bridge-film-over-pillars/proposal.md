## Why

The film in a gap is built from what it can rest on: the plate below's top face,
plus any pillar standing at that level. A pillar inside a socket shaft is ringed
by the socket opening, so the film it carries comes out as an island, floating
in the middle of the hole with the sheet on the surrounding lattice a few
millimetres away and not touching it.

The film is peeled by hand and is meant to come off as one sheet -- that is why
it prints as a solid part, no walls and no shells and 100% zig-zag infill
crossed layer to layer, rather than as support interface, and why it is two
layers rather than one. Islands defeat that. They stay behind on the
pillar tops after the sheet lifts and have to be picked out of the sockets one
at a time, and each is a small unanchored patch printed over a rib as narrow as
0.3 mm.

Where the ring is narrow, closing it costs almost nothing and the island stops
being an island.

## What Changes

- The film's base gains a bridging step. For each region of the base that is
  disconnected from the rest -- in practice, a pillar top -- the empty span to
  the nearest other base material in the same gap is measured outward, and the
  span is filled where it is shorter than a threshold.
- The threshold is judged per side, independently. A pillar in a socket can end
  up bridged on the two near sides and left open on the two far ones; nothing
  requires all four to qualify.
- A bridge reaches to the nearest film base in that gap, whatever it is: the
  plate below's top face, or a neighbouring pillar's top. It is a geometric
  question about real material, not about the nominal 42 mm lattice.
- New option `--bridge-span MM` sets the threshold, default 6; `0` disables
  bridging and restores today's behaviour.
- The bridge is part of the film's base, so the existing flare applies to it
  unchanged and the film still leans out at no more than 45 degrees.
- Bridging is applied after any trim of film that carries nothing, never before.
  `trim-unsupporting-film` acts on the opposite regions to this change: it
  removes film with nothing above it anywhere up the stack, and the film on a
  pillar top is carrying the plate border the pillar exists for, so it is never
  a trim target. The two touch in one place only -- the bridge span itself lies
  over a region that carries nothing, by construction, since anywhere above it
  that needed carrying would already stand a pillar there and already be part of
  the base. Ordering is the whole of the fix; no exemption flag is needed.

## Capabilities

### Modified Capabilities
- `gap-film`: the film's base is bridged across short spans between otherwise
  disconnected regions, so the film in a gap is one sheet wherever the spans
  allow. Fixes bridging as the last step of forming the base, after any trim.

## Impact

- `stack_plates.py`: `interface_slabs` and its CLI. `support_regions` is
  untouched -- bridging changes the film, not the pillars.
- `verify.py`: gains a film connectivity count, so the number of islands per gap
  is reported rather than guessed at.
- Interacts with two open changes. `trim-unsupporting-film` imposes an ordering
  constraint rather than a conflict (above). `emit-manifold-solids` reduces each
  region to one traced outline, and bridging merges regions, so the two pull the
  same way -- but both edit `interface_slabs`, and whichever lands second
  rebases onto the other.
- More PETG, by the area of the bridges. The film is currently 22.6 cm3 across
  eight gaps on the nine-plate drawer stack.

## How this is measured

- **Islands per gap, before and after.** The claim is that the film comes off as
  one sheet, so the number is the whole point: count connected regions of the
  film's base in each gap today, and again at each candidate threshold. The
  target is one per gap wherever spans allow; a gap that keeps islands should be
  explainable by a span wider than the threshold.
- **PETG volume, before and after**, against the current 22.6 cm3, at 6 mm and
  at a spread either side of it. The threshold is the trade, so the sweep is
  what puts on record whether 6 mm sits at the knee -- where islands stop
  falling and volume keeps rising -- or past it. Note the floor: the flare
  already widens the film 0.2 mm a side per layer, so spans under about 0.4 mm
  close themselves whatever the threshold says.
- **A sliced file, not only the test suite.** Confirm in the G-code that the
  bridged film is present in every gap, that the bridge spans print as bridge or
  solid infill rather than as air, and that no slicer support appeared anywhere
  on the model. The suite has passed while the model printed into air.
- **Manifoldness** of the emitted film, once `emit-manifold-solids` has landed
  -- merging regions is exactly where degenerate touching shows up.

## What was ruled out

- **Bridging to the nominal 42 mm lattice lines.** A bridge needs material at
  both ends. A grid line is a coordinate, not an anchor, and the plate under a
  given gap may have no material on it at all.
- **Filling the socket ring outright, with no threshold.** That is not bridging,
  it is filling the socket with film -- the waste that `trim-unsupporting-film`
  exists to remove, reintroduced under another name.
- **Widening the pillars until their tops reach the socket wall.** A pillar is
  already dilated by `--filler-grow` and then held off the plate by the gap
  clearance, precisely so it does not weld to the socket wall it stands in. It
  cannot reach the wall and remain separable, and it would spend PLA through a
  plate's whole height to fix something 0.4 mm thick.
- **Raising `flare` so the film closes the ring by itself.** Flare is capped at
  the layer height because 0.2 mm out per 0.2 mm up is the 45 degrees a printer
  bridges unaided. Raising it to span millimetres would steepen every film edge
  in the model to fix a few of them.

## Open questions

- Whether a bridge should be measured as an axis-aligned span or as a true
  nearest-material distance. The rasterised base makes the second no harder --
  it is a dilation of the island intersected with what it reaches -- and it does
  not privilege the X and Y axes. Design decides.
