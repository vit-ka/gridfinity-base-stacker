# gap-film Specification

## Purpose
TBD - created by archiving change bridge-film-over-pillars. Update Purpose after archive.

## Requirements

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
