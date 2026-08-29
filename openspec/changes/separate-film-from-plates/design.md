## Context

`interface_slabs` already accepts a `clearance` and subtracts it from both faces
of the gap; the default is zero. What blocks simply raising it is arithmetic: at
the current 0.4 mm gap, 0.1 mm at each face leaves 0.2 mm, which is one layer,
and a one-layer film is the failure the two-layer film was introduced to fix.

Layer boundaries sit at multiples of the layer height from the bed. A film inset
by 0.1 mm therefore does not begin on a boundary. This is not a problem to solve:
the inset exists to break the coincidence between the film's surface and the
plate's, and the slicer still resolves the solid into whole layers.

## Goals / Non-Goals

**Goals:**
- The film touches nothing.
- It keeps two printed layers.
- The failure is loud if a gap cannot hold both.

**Non-Goals:**
- Finding the minimum workable clearance. 0.1 mm is what the fusing prompted;
  establishing that 0.05 mm also works is a separate measurement, noted in the
  proposal.
- Changing how the film's footprint is computed. That is `trim-unsupporting-film`.

## Decisions

**Gap goes to 0.6 mm; clearance 0.1 mm at each face.** 0.1 + 0.4 + 0.1. The film
keeps two layers. The alternative of holding 0.4 mm and accepting one layer was
rejected because the tearing it causes is documented and visible; the alternative
of 0.05 mm clearance was rejected for now because nothing has measured whether it
is enough, and guessing at the number that just failed is how this bug arrived.

**Cost is accepted explicitly, and it is larger than it first looked.** Measured
on the nine-plate drawer stack: 39.2 mm and 7.39 h before, 40.8 mm and 10.42 h
after.

Three hours, not the eight layers this design first estimated. The estimate was
wrong because the 7.39 h baseline was itself measured on a slice where the film
was partly merged into the plates -- the very defect being fixed -- so a good deal
of the film was never being printed. Interface filament on the model goes from
6,940 mm to 10,011 mm, and that increase is the film appearing, not the film
growing. No earlier timing taken against a zero-clearance film is a valid
comparison.

**Layer alignment is not worth chasing.** Offset by 0.1 mm from the layer grid,
the film falls across three printed layers rather than two. Aligning it -- a
0.8 mm gap with 0.2 mm of clearance, so the film sits exactly on two layer
boundaries -- was measured and is worse on both counts: 42.4 mm and 12.52 h, for
the same 9,915 mm of filament. The offset costs nothing that alignment recovers.

**Clearance stays a parameter with zero permitted.** Zero was the previous
default and is a legitimate configuration; the generator should not refuse it.

## Risks / Trade-offs

- **The stack gets taller**, which matters for a set that only just fits the bed.
  Nine plates at 39.2 mm becomes 40.8 mm, so there is room, but a taller set
  could stop fitting.
- **0.1 mm may be more than needed.** Every extra 0.1 mm of clearance costs
  height across every gap. Flagged for a later measurement rather than tuned
  blind.
- **`plates.json` heights change**, so any saved 3mf built from an older stack no
  longer matches a freshly generated one. The film is part of the same 3mf, so
  the mismatch is caught by rebuilding rather than silently printed.
