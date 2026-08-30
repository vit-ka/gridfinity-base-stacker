## MODIFIED Requirements

### Requirement: Emitted meshes are manifold

Every closed solid this project writes SHALL have each edge shared by exactly two
facets, whether this project authored it or a generator produced it.

The requirement is unchanged; what changed is that most of the geometry now
arrives from OpenSCAD rather than being built here. That does not move the
obligation. This project writes the file that gets sliced, and a slicer that
rejects the mesh offers it for third-party repair regardless of who made it.

#### Scenario: A generated stack is manifold

- **WHEN** any stack, pillar set, or film is written
- **THEN** no edge in the file is used by one facet, or by three, or by four

#### Scenario: Adjacent regions do not create shared edges

- **WHEN** two parts of the same solid meet
- **THEN** they meet along whole shared faces or not at all, so that no edge ends
  up used by four facets -- which is what two boxes touching edge to edge
  produces, and is the sole cause of the 165 bad edges observed in the stack and
  the 5,402 in the film

#### Scenario: Geometry from the generator is checked on arrival

- **WHEN** geometry arrives from the generator rather than being built here
- **THEN** it is checked before anything is built on it
- **AND** a boundary edge and an edge shared by four facets are distinguished, so
  a hole in a surface is not reported as two shells meeting

### Requirement: A pillar is one solid

A pillar occupying a connected region SHALL be written as a single closed solid,
traced from the region's outline. Pillars are structural and visible: a column
split into hundreds of loose pieces is weaker, slower to slice, and stair-steps
where the socket curves.

The film is exempt, and now for a second reason as well as the original one. The
original: its regions are lattices of twenty-odd interlocking holes, and
triangulating a two-thousand-point outline with that many holes defeated three
attempts. The second: the film is generated with the plates rather than traced
here, so its shape is not this project's to decide.

#### Scenario: A column is one solid

- **WHEN** a pillar occupies a connected region across a plate's height
- **THEN** that pillar is one closed solid in the output
- **AND** its outline follows the region's boundary rather than a decomposition

#### Scenario: A stack with no pillars satisfies this trivially

- **WHEN** every plate rests fully on the one below and no pillar is generated
- **THEN** the requirement is met with nothing to check
