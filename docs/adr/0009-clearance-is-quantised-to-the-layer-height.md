# 9. Clearance is quantised to the layer height

Date: 2026-08-30
Status: Accepted

## Context

[ADR 0008](0008-what-the-first-test-prints-showed.md) left both film materials
failing in opposite directions and no explanation that covered both. The
explanation turned out to be that the clearance was never printed at all.

The film is modelled 0.1 mm clear of each plate, so with 0.2 mm layers its faces
land at 4.10 and 4.50 -- **exactly on the slicer's sample planes**. Measured in one
sliced file, gf-300:

| gap | model film | printed | below | above |
|---|---|---|---|---|
| 4.0-4.6 | 4.10-4.50 | 4.00-4.40 | **0.00** | 0.20 |
| 8.6-9.2 | 8.70-9.10 | 8.80-9.20 | 0.20 | **0.00** |
| 13.2-13.8 | 13.30-13.70 | 13.40-13.80 | 0.20 | **0.00** |

Every interface printed with **one face fused flat against a plate**, and which
face was not consistent between gaps of the same print, nor between prints. A
nominal 0.1 mm clearance is not a small clearance; it is an undefined one.

Three things in the BambuStudio source combine to produce this, and it is worth
recording because the first explanation written here was wrong:

1. `PrintObjectSlice.cpp:36` -- `slice_z = 0.5 * (lo + hi)`. The sample plane is
   the middle of the layer, so with 0.2 mm layers the planes sit at odd multiples
   of 0.1, exactly where a 0.1 mm clearance puts the film's faces.
2. `TriangleMeshSlicer.cpp:191` -- `slice_facet()` has a special branch for a face
   lying *exactly* on the plane, and it is deliberately asymmetric: "the bottom
   most edge resp. vertex of a triangle is not owned by the triangle, but the top
   most edge resp. vertex is part of the triangle". A solid's **top** face on the
   plane yields material; its **bottom** face does not.
3. That branch tests exact float equality, and the mesh makes a round trip through
   float32 -- the geometry is stored centred on the object and restored by the
   instance transform. The error is a few times 1e-7 mm and it depends on the
   stack's own height, which is what `Layer(id, obj, hi - lo, hi + zmin, slice_z)`
   then measures everything against.

Reproduced arithmetically, the three cases give: plane inside the film (material),
plane outside it (nothing), or exact coincidence (decided by rule 2). For gf-300
(17.8 mm tall, zmin +3.8e-7) the film's faces land just *below* both planes, so
the bottom plane cuts inside and the top plane misses -- film printed 4.00-4.40,
welded below. For test-plain (8.6 mm tall, zmin -1.9e-7) both faces land exactly
on their planes, so rule 2 decides -- bottom face disowned, top face owned, film
printed 4.20-4.60, welded above. Both predictions match the sliced G-code.

The practical statement is that a face nominally on a sample plane is resolved by
sub-micron rounding that depends on the height of the stack it belongs to.

Setting the clearance to a whole layer removes the ambiguity, because the film's
faces then land on layer boundaries, between sample planes, where there is
nothing to round:

| gap | clearance | model film | printed | below | above |
|---|---|---|---|---|---|
| 0.6 | 0.2 | 4.20-4.40 | 4.20-4.40 | 0.20 | 0.20 |
| 0.8 | 0.2 | 4.20-4.60 | 4.20-4.60 | 0.20 | 0.20 |
| 1.2 | 0.4 | 4.40-4.80 | 4.40-4.80 | 0.40 | 0.40 |

Test printed at gap 0.8 / clearance 0.2, on a two-plate 34-minute plate:

- **Bambu Support W: separated cleanly**, no prying.
- **PETG at 255 C: separated cleanly**, and the film layers stayed intact.

Both had failed at nominal 0.1. Neither material was the problem; the fused face
was. The PETG print also settles the ambiguity ADR 0008 left open: PETG works,
and the earlier peel was a film printed 35 C below its own specification.

## Decision

**The clearance is a whole number of layers.** It is not a continuous setting.
At 0.2 mm layers the available values are 0 and 0.2 and 0.4; there is no 0.1,
and asking for one yields whichever of 0 or 0.2 the floating-point noise picks,
per gap.

This is forced from both sides. Plate positions have to stay on layer boundaries
or the plates themselves slice ambiguously, and the film's faces have to stay off
the sample planes, so every face in the gap sits on a 0.2 mm boundary and every
distance between them is a multiple of 0.2.

**Print the film's material at its own specification.** PETG runs at 255 C, not
at the 220 C it inherits from sharing a project with PLA.

## Consequences

The mechanism now works: a stack comes apart. That was unproven through eight
ADRs.

Asking for a clearance below one layer is not available. Anyone wanting a tighter
interface than 0.2 mm has to lower the layer height where it matters -- Bambu
supports a height range modifier per object, stored as
`Metadata/layer_config_ranges.xml` -- rather than lowering the clearance.

**A new problem replaces the old one.** With a real gap under it, the first layer
of each plate above now bridges 0.2 mm over the film with nothing to squish
against, and its underside prints badly: loose perimeters over Support W, and
worse over PETG, which the bridged PLA does not grip at all. This is the ordinary
cost of a supported surface, and it is the next thing to fix. It is also the
reason the clearance cannot simply be raised: every 0.2 mm of clearance is 0.2 mm
the plate above has to bridge.

The defaults are unchanged pending that: the tool still ships gap 0.6 and
clearance 0.1, which this ADR says is undefined. They should move to 0.8 and 0.2
once the underside question is settled, so the change lands once.
