## Why

The film is generated wherever there is something to rest *on* -- the plate below
plus any pillar at that level -- and then flared outward. It is not checked
against whether anything rests *on it*. Film under an empty socket carries
nothing: it is printed, paid for in PETG and in time, and peeled off as waste.

## What Changes

- Restrict the film to where something above needs carrying: the plate above's
  downward face, plus any pillar standing at the level above.
- Keep the flare. The film must still overhang what it rests on, since that
  overhang is what lets a 0.3 mm rib of pillar carry a bead a millimetre wide, so
  the trim applies to the carried region and not to the flare that serves it.
- Anything trimmed must be film that carries nothing at all. Trimming film that
  carries a pillar leaves that pillar's next segment standing on nothing, which
  is the same class of bug as the plate borders that printed into air.

## Capabilities

### Modified Capabilities
- `gap-film`: the film's extent becomes the intersection of what it can rest on
  and what needs carrying, rather than only the former.

## Impact

- `stack_plates.py`: `interface_slabs`.
- Reduces PETG. Current film is 22.6 cm3 across eight gaps on the nine-plate
  drawer stack; the saving is unmeasured and is the first thing the change should
  establish.
- Depends on `separate-film-from-plates` only in that both edit the same
  function; the two are otherwise independent.

**Approved in review.** The measure-first task stands regardless: if the waste
turns out to be small, this change carries real risk -- stranding a pillar -- for
little return, and that is worth knowing before the code is written rather than
after.
