## Purpose

What the generator guarantees about the geometry it writes: that a slicer will
accept it without repair, and that what was computed is what ends up in the file.

## ADDED Requirements

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

### Requirement: One connected region is one solid

A region of support or film that is connected SHALL be written as a single closed
solid rather than as a collection of touching pieces. Fragmentation is what
creates the shared edges, and a column split into hundreds of loose pieces is
also weaker and slower to slice.

#### Scenario: A column is one solid

- **WHEN** a pillar occupies a connected region across a plate's height
- **THEN** that pillar is one closed solid in the output
