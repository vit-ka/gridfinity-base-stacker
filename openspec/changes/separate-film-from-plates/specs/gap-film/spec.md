## Purpose

The gap film is the solid that fills the space between two stacked plates. It is
printed in a filament that does not bond to the plates, so that after printing
the stack comes apart into separate plates and the film peels off as waste.

## ADDED Requirements

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
