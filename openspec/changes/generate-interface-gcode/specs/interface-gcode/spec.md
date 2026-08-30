## Purpose

The interface that separates the plates, emitted as toolpaths written into a
sliced file rather than as a mesh handed to the slicer, so that its height above
and below each plate is exact instead of quantised to the layer height.

## ADDED Requirements

### Requirement: Interface layers sit at the height they were asked for

Each interface layer SHALL be emitted at the Z it was configured to occupy,
independent of the layer grid the rest of the print uses and independent of the
height of the stack it belongs to.

A mesh film cannot do this. Its faces are resolved against the slicer's sample
planes, and a face landing on a plane is decided by rounding that varies with the
stack's height, so the same requested clearance printed as 0 mm on one stack and
0.2 mm on another.

#### Scenario: Clearance below is exact

- **WHEN** a stack is generated with a clearance below the interface
- **THEN** the lowest interface extrusion in every gap is that clearance above
  the top of the plate below it

#### Scenario: Clearance above is exact and independent of the layer height

- **WHEN** a clearance above the interface is configured that is not a multiple
  of the layer height
- **THEN** the distance from the highest interface extrusion to the first
  extrusion of the plate above equals that clearance
- **AND** it is not rounded to a multiple of the layer height

#### Scenario: The same clearance survives a change of stack height

- **WHEN** two stacks of different overall heights are generated with the same
  clearances
- **THEN** both report the same measured clearance at every gap

### Requirement: The two clearances are configured separately

The clearance below the interface and the clearance above it SHALL be settable
independently. They do different jobs: the gap below is what allows the interface
to release from the plate beneath it, and the gap above is what the first layer
of the plate above has to bridge, so the underside of every plate is as poor as
that number is large.

#### Scenario: Asymmetric clearance

- **WHEN** a clearance of 0.2 mm below and 0.1 mm above is requested
- **THEN** the emitted interface sits 0.2 mm above the plate below and 0.1 mm
  below the plate above

#### Scenario: One value sets both

- **WHEN** only one clearance is configured
- **THEN** it applies to both faces

### Requirement: The interface is emitted in its own filament

Interface extrusions SHALL use the filament configured for the interface, and the
sliced file SHALL already load and purge that filament at every gap where
interface material is emitted.

Without a tool change in the file, an injected extrusion names a filament the
printer never loaded and never purged, and prints in whatever was already in the
nozzle.

#### Scenario: A tool change precedes every interface layer

- **WHEN** interface material is emitted in a gap
- **THEN** the file selects the interface filament before that material and
  restores the stack's filament after it

#### Scenario: No interface filament outside the gaps

- **WHEN** the emitted file is examined
- **THEN** every extrusion in the interface filament lies either inside a gap or
  in the prime tower

### Requirement: The slicer generates no support on the stack

The stack SHALL receive no slicer-generated support, whatever the support
settings have to be for the rest of the plate.

Provoking the filament changes the interface needs requires support to be enabled
for a decoy object, so support being off globally is no longer available as the
mechanism.

#### Scenario: No support on the stack

- **WHEN** the file is sliced and the interface written into it
- **THEN** no support extrusion appears anywhere within the stack's footprint

### Requirement: The result is verified against the emitted file

The emitted file SHALL be checked, not the plan that produced it: the heights of
the interface layers, the filament they use, and the absence of support are
properties of the G-code and SHALL be measured there.

#### Scenario: Verification reads the output

- **WHEN** a stack is emitted
- **THEN** the check parses the emitted file and reports the measured clearance
  at each gap, the filament used for each interface layer, and any support found

#### Scenario: A wrong height is an error, not a warning

- **WHEN** any interface layer is not at its configured height
- **THEN** the check fails and names the gap and both heights
