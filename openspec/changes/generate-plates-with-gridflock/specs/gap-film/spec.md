## MODIFIED Requirements

### Requirement: The film is physically separated from the plates

The film SHALL be held clear of the plate **below** it by a configured clearance,
and SHALL sit against the plate **above** it with no clearance at all. A
different filament is not sufficient separation at the bottom face: printed flush
against the plate below, the film fuses to it and the stack does not come apart.

The two faces are not symmetric and are not one number. ADR 0010 measured a
printed stack whose film had a full layer below it and none above: the bottom
face released cleanly, and the top face needed some cleaning but was not fused.
The clearance below is what lets the film release; the clearance above is what
the plate above would have to bridge, and it is not wanted.

#### Scenario: Film sits clear of both plates

- **WHEN** a stack is generated with the default settings
- **THEN** every part of the film is at least the configured clearance above the
  plate below it
- **AND** the plate above prints directly onto the film's top face, the clearance
  above being zero by default rather than the same number as the one below

#### Scenario: The film's top face is not coincident with the plate above

- **WHEN** the film is generated
- **THEN** its top face is held a small distance below the plate above rather
  than in the same plane
- **AND** no facet of the film is coincident with any facet of a plate, so the
  two are separate volumes to the slicer rather than one merged solid

#### Scenario: Clearance is configurable and can be disabled

- **WHEN** a clearance of zero is requested below the film
- **THEN** the film fills the gap exactly and touches both plates
- **AND** the generator does not refuse, because the behaviour is a deliberate
  choice and was the previous default

### Requirement: The film is thick enough to peel in one piece

The film SHALL be one printed layer thick by default, and its thickness SHALL
remain configurable. A one-layer film was measured to release cleanly at its
bottom face and lift as a sheet (ADR 0010); the earlier two-layer minimum was
measured on a film that was held clear of both plates and tore at its fringe.

#### Scenario: Default gap accommodates clearance and two layers

- **WHEN** a stack is generated with the default gap
- **THEN** the film is at least one layer height thick after the clearance below
  it is subtracted
- **AND** the gap is not required to hold two layers, the default film being one

#### Scenario: A thicker film can still be asked for

- **WHEN** a two-layer film is requested
- **THEN** a two-layer film is generated

#### Scenario: A gap too small for both is rejected or reported

- **WHEN** the requested gap cannot hold the clearance below plus at least one
  layer of film
- **THEN** the generator fails with a message naming the gap, the clearance below
  and the clearance above, rather than emitting a film of zero or negative
  thickness

## ADDED Requirements

### Requirement: The film follows the plate's real profile

The film SHALL take the shape of the profile of the plate it carries, sockets
cut out of it, rather than the plate's bounding rectangle. Film under an open
socket carries nothing and is printed, paid for and thrown away.

#### Scenario: Sockets are absent from the film

- **WHEN** the film for a plate is generated
- **THEN** its area is that of the plate's own profile
- **AND** the socket openings of that plate are absent from it

#### Scenario: The film matches the plate it carries in extent

- **WHEN** the film for a plate is generated
- **THEN** it covers the plate's downward face and does not extend beyond it

## REMOVED Requirements

### Requirement: The film overhangs what carries it

**Reason**: This described a film of several layers, each stepping outward by up
to a layer height so that a rib of pillar narrower than an extrusion could carry
a much wider bead. The film is now a single layer taking the shape of the plate
it carries, so there is no layer below it to step outward from, and its lowest
layer deliberately does *not* cover only what is directly beneath it -- covering
the plate above's footprint rather than the plate below's is the whole point.

**Migration**: None required. The behaviour this requirement guarded against --
film overhanging its own support at more than 45 degrees -- cannot arise in a
one-layer film. Where a thicker film is configured, the layers are identical in
extent rather than flared, so no layer overhangs the one below it at all.

### Requirement: A bridge obeys the same flare as the rest of the film

**Reason**: A consequence of the flare requirement removed above. With a single
layer there is no per-layer step for a bridge to obey.

**Migration**: None required. Bridging itself is unchanged and its remaining
requirements stand.
