# mesh-output Specification

## Purpose
What the generator guarantees about the geometry it writes: that a slicer will
accept it without repair, and that what was computed is what ends up in the file.

## Requirements

### Requirement: Emitted meshes are manifold

Every edge in a written mesh SHALL be shared by exactly two facets. Meshes that
fail this are reported by slicers as broken and offered for third-party repair,
and a mesh the slicer considers broken cannot be relied on to slice as intended.

#### Scenario: A generated stack is manifold

- **WHEN** any stack, pillar set, or film is written
- **THEN** no edge in the file is used by one facet, or by three, or by four

#### Scenario: Adjacent regions do not create shared edges

- **WHEN** two parts of the same solid meet
- **THEN** they meet along whole shared faces or not at all, so that no edge ends
  up used by four facets -- which is what two boxes touching edge to edge
  produces, and is the sole cause of the 165 bad edges observed in the stack and
  the 5,402 in the film

### Requirement: Emitted meshes are verified before they are written

The verification tooling SHALL check the manifold property, so that a regression
is caught by the project rather than by the slicer.

#### Scenario: Verification rejects a non-manifold mesh

- **WHEN** a mesh with an edge used by other than two facets is checked
- **THEN** verification fails and names the offending edge

### Requirement: Pieces of one region overlap rather than touch

Where a region is written as several solids, they SHALL overlap rather than abut.
Two closed solids that touch share edges between four facets; two that
interpenetrate share none, and every slicer unions overlapping solids as a matter
of course -- it is already doing so with these.

#### Scenario: Adjacent pieces interpenetrate

- **WHEN** a region is written as more than one solid
- **THEN** no facet of one is coincident with a facet of another
- **AND** the overlap is far below the printer's resolution, so the geometry it
  describes is unchanged

### Requirement: A pillar is one solid

A pillar occupying a connected region SHALL be written as a single closed solid,
traced from the region's outline. Pillars are structural and visible: a column
split into hundreds of loose pieces is weaker, slower to slice, and stair-steps
where the socket curves.

The film is exempt. Its regions are lattices of twenty-odd interlocking holes,
and triangulating a two-thousand-point outline with that many holes defeated
three attempts -- proper hole bridging, degenerate-ear handling, and reflex-only
containment -- each leaving the caps partly uncovered. The film is sacrificial and
peeled off, so it is written as overlapping pieces until that is worth another
attempt.

#### Scenario: A column is one solid

- **WHEN** a pillar occupies a connected region across a plate's height
- **THEN** that pillar is one closed solid in the output
- **AND** its outline follows the region's boundary rather than a decomposition
  into rectangles
