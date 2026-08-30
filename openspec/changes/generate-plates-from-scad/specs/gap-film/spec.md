## MODIFIED Requirements

### Requirement: The film releases at its bottom face

The film SHALL be held clear of the plate below it by at least one printed layer,
and SHALL NOT be held clear of the plate above it. The plate above prints
directly onto the film.

This is the reverse of the previous requirement, and the reasoning that produced
that one was wrong in a specific way. Holding the film clear of *both* plates
gives the first layer of every plate above a gap to bridge with nothing to squish
against, and its underside prints badly -- which
[ADR 0009](../../../../docs/adr/0009-clearance-is-quantised-to-the-layer-height.md)
recorded as the cost of separation and the next thing to fix. It is not the cost
of separation. Separation needs one face to release, not two, and the face that
releases is the bottom one: film laid down onto a cured plate surface is the
direction that grips, and a plate laid down onto film is the direction support
interface material is designed for.

A clearance of zero above therefore means touching, deliberately, and the
material is what separates there. A clearance below of a whole layer means a
layer with no model material in it, which is what stops the slicer treating film
and plate as one body.

#### Scenario: A full layer of clearance below the film

- **WHEN** a stack is generated with the default settings
- **THEN** the lowest film material in every gap is at least one layer height
  above the plate below it, measured in the sliced result

#### Scenario: No clearance above the film

- **WHEN** a stack is generated with the default settings
- **THEN** the plate above prints directly onto the film, with no gap between
  them in the sliced result

#### Scenario: The film's faces are not coincident with a plate's

- **WHEN** the film is generated
- **THEN** no face of it is exactly coincident with a face of a plate, so the
  slicer resolves them as two bodies rather than merging them
- **AND** the distance that keeps them apart is small enough to print as no gap

#### Scenario: Both clearances are configurable

- **WHEN** a clearance below or above the film is requested
- **THEN** it is used
- **AND** the generator does not refuse a zero above, because that is the default

### Requirement: The film is thick enough to peel in one piece

The film SHALL be at least one printed layer thick, and its thickness SHALL be
configurable.

The previous requirement was two layers, on the grounds that a one-layer film
tears at the fringe. That was measured on a film that overhung the rib beneath it
by a flare on every layer, and had a gap above it as well as below. A film that
the plate above is printed onto is held flat by that plate while it is peeled,
and one layer is what a working implementation of this arrangement uses.

Two layers remain available and are the safer choice until a one-layer film has
been peeled off a real print.

#### Scenario: Default gap accommodates clearance and two layers

- **WHEN** a stack is generated with the default gap
- **THEN** the gap holds the clearance below, the clearance above, and at least
  one layer of film
- **AND** two layers of film remain available, and are what the gap holds until a
  one-layer film has been peeled off a real print

#### Scenario: A gap too small for both is rejected or reported

- **WHEN** the requested gap cannot hold the clearance below plus the clearance
  above plus at least one layer of film
- **THEN** the generator fails with a message naming the gap and both clearances,
  rather than emitting a film of zero or negative thickness

### Requirement: The film carries the pillars as well as the plates

Where pillars exist, they occupy the plates' own height bands, so the same gaps
fall between pillar segments. The film SHALL cover them, or each pillar's next
segment stands on nothing.

Pillars are no longer generated for every stack: a decomposition whose plates
nest produces none. This requirement applies wherever they do exist.

#### Scenario: A gap above a pillar is filled

- **WHEN** a pillar stands at some level and another stands at the level above it
- **THEN** film is present in the gap between them

#### Scenario: A stack with no pillars needs no pillar coverage

- **WHEN** every plate rests fully on the one below
- **THEN** the film covers what the plates need and nothing else is required of it
