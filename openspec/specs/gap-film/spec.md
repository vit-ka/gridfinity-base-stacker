# gap-film Specification

## Purpose
TBD - created by archiving change bridge-film-over-pillars. Update Purpose after archive.

## Requirements

### Requirement: The film is physically separated from the plates

The film SHALL NOT touch the plate above it or the plate below it. A different
filament is not sufficient separation: printed flush, the film fuses to the
plates and the stack does not come apart.

#### Scenario: Film sits clear of both plates

- **WHEN** a stack is generated with the default settings
- **THEN** every part of the film is at least the configured clearance below the
  plate above it and above the plate below it
- **AND** no facet of the film is coincident with any facet of a plate

#### Scenario: Clearance is configurable and can be disabled

- **WHEN** a clearance of zero is requested
- **THEN** the film fills the gap exactly and touches both plates
- **AND** the generator does not refuse, because the behaviour is a deliberate
  choice and was the previous default

### Requirement: The film is thick enough to peel in one piece

The film SHALL be at least two printed layers thick by default. A one-layer film
tears at the fringe -- the teeth where the film overhangs the rib beneath it --
and comes off in fragments rather than as a sheet.

#### Scenario: Default gap accommodates clearance and two layers

- **WHEN** a stack is generated with the default gap
- **THEN** the film is at least two layer heights thick after clearance is
  subtracted from both faces

#### Scenario: A gap too small for both is rejected or reported

- **WHEN** the requested gap cannot hold the requested clearance on both faces
  plus at least one layer of film
- **THEN** the generator fails with a message naming the gap and the clearance,
  rather than emitting a film of zero or negative thickness

### Requirement: The film carries the pillars as well as the plates

Pillars occupy the plates' own height bands, so the same gaps fall between pillar
segments. The film SHALL cover the pillars, or each pillar's next segment stands
on nothing.

#### Scenario: A gap above a pillar is filled

- **WHEN** a pillar stands at some level and another stands at the level above it
- **THEN** film is present in the gap between them

### Requirement: The film overhangs what carries it

Each layer of the film SHALL extend beyond the layer below it by no more than the
layer height, so the film leans outward at no more than 45 degrees and prints
without support. This overhang is what allows a rib of pillar narrower than an
extrusion to carry a much wider bead.

#### Scenario: Bottom layer matches its support

- **WHEN** the film is generated
- **THEN** its lowest layer covers only what is directly beneath it
- **AND** each layer above extends past the one below by at most the layer height

### Requirement: The film bridges short spans so that it lifts as one sheet

Where the film's base falls into regions that do not touch each other, the film
SHALL bridge the empty span between them wherever that span is shorter than a
configured threshold. The film is removed by hand and is meant to come off
whole; a region left disconnected -- in practice a pillar top ringed by a socket
opening -- stays behind in the socket when the sheet lifts.

The span is measured to the nearest other base material in the same gap,
whatever that material is: the plate below's top face, or another pillar's top.
It is a question about material that is actually there, not about the nominal
42 mm lattice.

#### Scenario: A pillar close to the socket wall is bridged

- **WHEN** a pillar's top is separated from the rest of the film's base by a
  span shorter than the threshold
- **THEN** the film covers that span
- **AND** the pillar's film is continuous with the film around it

#### Scenario: A pillar far from anything is left alone

- **WHEN** every span from a pillar's top to other base material is longer than
  the threshold
- **THEN** no bridge is generated and the film over that pillar remains its own
  region

#### Scenario: Each side is judged on its own

- **WHEN** a pillar's top is close to base material in one direction and far
  from it in another
- **THEN** the near side is bridged and the far side is not
- **AND** the decision on one side does not depend on the spans on the others

#### Scenario: A bridge reaches a neighbouring pillar

- **WHEN** two pillars stand at the same level within the threshold of each
  other, with nothing else within reach
- **THEN** the film bridges between them

### Requirement: Bridging is configurable and can be turned off

The threshold SHALL be settable, and setting it to zero SHALL disable bridging
entirely, leaving the film exactly as it would be with no bridge at all.

#### Scenario: Bridging disabled

- **WHEN** a stack is generated with a bridge threshold of zero
- **THEN** the film's extent is the same as it would be with no bridging
  behaviour at all

#### Scenario: A wider threshold never removes film

- **WHEN** the same stack is generated at two thresholds
- **THEN** the film generated at the wider threshold covers everything the
  narrower one covered

### Requirement: Bridging comes after any trim of film that carries nothing

Where the film is restricted to the regions that carry something, that
restriction SHALL be applied before bridging and never after.

The film on a pillar top is not at stake: it carries the plate material the
pillar exists for, and is never a trim target. A bridge span is. By construction
it lies over a region that carries nothing at any level, because anywhere above
it that needed carrying would already stand a pillar there and would already be
part of the film's base. A trim run after bridging would therefore delete every
bridge.

#### Scenario: Bridges survive a trimmed film

- **WHEN** the film is trimmed to the regions that carry something and then
  bridged
- **THEN** the bridges are present in the generated film

#### Scenario: No bridge is built to film that will not exist

- **WHEN** a region of film would be trimmed away entirely because it carries
  nothing
- **THEN** no bridge is generated to reach it, since bridging to film that will
  not exist connects nothing

### Requirement: A bridge obeys the same flare as the rest of the film

A bridge SHALL be part of the film's base and SHALL be subject to the same
outward step per layer as the rest of it, so that no part of a bridged film
overhangs its own lower layer by more than the layer height.

#### Scenario: Bridged film still leans at 45 degrees

- **WHEN** the film in a gap includes a bridge
- **THEN** each layer of that film extends beyond the layer below it by at most
  the layer height, bridge included

### Requirement: The film exists only where something rests on it

The film SHALL be generated only where it carries something: the downward face of
the plate above, or a pillar standing at the level above. Film beneath an empty
socket carries nothing and is printed, paid for, and thrown away.

This narrows the behaviour the film had before, which generated it wherever
there was something to rest on without regard to whether anything rested on it.
That behaviour was never written down as a requirement, so this adds one rather
than modifying one.

#### Scenario: Film under an empty socket is not generated

- **WHEN** a region of a gap has the plate below beneath it but neither plate
  material nor a pillar above it
- **THEN** no film is generated in that region

#### Scenario: Film that carries a pillar is kept

- **WHEN** a region of a gap has a pillar standing on it at the level above
- **THEN** film is generated there, even though no plate material is directly
  above it

#### Scenario: The flare is not trimmed away

- **WHEN** the film is trimmed to what it carries
- **THEN** each layer still extends beyond the one below it by up to the layer
  height, so the overhang that lets a narrow pillar carry a wide bead survives
