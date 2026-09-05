## MODIFIED Requirements

### Requirement: Emitted meshes are manifold

Every edge in a written mesh SHALL be shared by exactly two facets. Meshes that
fail this are reported by slicers as broken and offered for third-party repair,
and a mesh the slicer considers broken cannot be relied on to slice as intended.

This now applies to geometry produced by the external generator as well as to
geometry this project writes. The guarantee is unchanged; what changed is that
the project is checking someone else's output rather than only its own, and an
upgrade of the vendored generator can break it without any change here.

#### Scenario: A generated stack is manifold

- **WHEN** any stack, pillar set, or film is written
- **THEN** no edge in the file is used by one facet, or by three, or by four

#### Scenario: Geometry arriving from the generator is checked

- **WHEN** a plate, film or pillar produced by the external generator is read
  back
- **THEN** it is checked to be manifold before anything is built on it
- **AND** a non-manifold result fails, naming the file, rather than being written
  into the stack

#### Scenario: Adjacent regions do not create shared edges

- **WHEN** two parts of the same solid meet
- **THEN** they meet along whole shared faces or not at all, so that no edge ends
  up used by four facets -- which is what two boxes touching edge to edge
  produces, and is the sole cause of the 165 bad edges observed in the stack and
  the 5,402 in the film

### Requirement: A pillar is one solid

A pillar occupying a connected region SHALL be written as a single closed solid
whose outline follows the region's boundary. Pillars are structural and visible:
a column split into hundreds of loose pieces is weaker, slower to slice, and
stair-steps where the socket curves.

The film's former exemption is withdrawn. It was granted because triangulating a
two-thousand-point outline with twenty-odd interlocking holes defeated three
attempts in this project's own tracing code, so the film was written as
overlapping pieces. The film is no longer traced here -- it is a solid produced
by the external generator from the plate's profile -- so the same rule applies to
it.

#### Scenario: A column is one solid

- **WHEN** a pillar occupies a connected region across a plate's height
- **THEN** that pillar is one closed solid in the output
- **AND** its outline follows the region's boundary rather than a decomposition
  into rectangles

#### Scenario: A film region is one solid

- **WHEN** the film in a gap occupies a connected region
- **THEN** that region is one closed solid in the output
- **AND** its socket openings are holes in that solid rather than gaps between
  separate pieces

## REMOVED Requirements

### Requirement: Pieces of one region overlap rather than touch

**Reason**: This existed because this project wrote a region as many small
solids -- rectangles from a rasterised grid -- and two closed solids that abut
share edges between four facets, which is non-manifold. Overlapping them by far
below the printer's resolution was the fix. The film and the pillars are now
each produced as a single solid by the external generator, so a region is never
decomposed into pieces and there is nothing to overlap.

**Migration**: None required. The manifold requirement above is unchanged and
still forbids the four-facet edges this rule was avoiding; it is now satisfied by
construction rather than by deliberate interpenetration. Should any region again
be written as several solids, that requirement alone already rules out abutting
them.
