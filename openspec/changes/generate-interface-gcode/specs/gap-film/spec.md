## MODIFIED Requirements

### Requirement: The film is physically separated from the plates

The film SHALL NOT touch the plate above it or the plate below it. A different
filament is not sufficient separation: printed flush, the film fuses to the
plates and the stack does not come apart.

Separation is a property of the printed result, not of the geometry that was
requested. A film held clear in the model was still printed flush against one
plate in every gap, because a mesh is resolved against the slicer's sample
planes; measured clearance is therefore what this requirement is about.

The clearance below the film and the clearance above it are separate settings.

#### Scenario: Film sits clear of both plates

- **WHEN** a stack is generated with the default settings
- **THEN** every part of the film is at least the configured clearance below the
  plate above it and above the plate below it, measured in the emitted file

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
- **THEN** the film is at least two layer heights thick after the clearance below
  and the clearance above are subtracted

#### Scenario: A gap too small for both is rejected or reported

- **WHEN** the requested gap cannot hold the clearance below plus the clearance
  above plus at least one layer of film
- **THEN** the generator fails with a message naming the gap and both clearances,
  rather than emitting a film of zero or negative thickness
