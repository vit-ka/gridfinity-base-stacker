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

**Cost is accepted explicitly.** Eight gaps at +0.2 mm is 1.6 mm of height and
eight further layers of print time on the nine-plate drawer stack. Worth stating
in the printing notes rather than discovering from the slicer.

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
