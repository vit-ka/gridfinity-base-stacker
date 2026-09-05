## Purpose

Carrying plate material that has nothing beneath it. Plates in a stack differ in
size and in origin, so a plate's solid border routinely lands over the socket
opening of the plate below it, and anything not carried prints into air.

## ADDED Requirements

### Requirement: Every part of a plate with nothing beneath it is carried

Where a plate has material that no material of the plate below stands under, the
generator SHALL place geometry carrying it to the plate below. Nothing on the
stack is supported by the slicer, so anything the generator misses prints into
air.

#### Scenario: An overhanging border is carried

- **WHEN** a plate's solid border extends past the footprint of the plate below
- **THEN** geometry is generated under that border, reaching the plate below

#### Scenario: No gap is left with material hanging over nothing

- **WHEN** a generated stack is measured gap by gap
- **THEN** no part of any plate's downward face is over empty space

### Requirement: Support is decided against real profiles, not bounding boxes

What stands under a plate SHALL be determined from the actual profile of the
plate below, sockets included. Gridfinity sockets are through-holes, so a plate
whose outline is entirely within the outline of the plate below can still have
material over a hole.

This is the case that a comparison of outer footprints misses, and it is the
normal case rather than the exception: alternate plates are rotated 180 degrees,
so a border can sit squarely over a socket even between plates of equal size.

#### Scenario: A border over a socket opening is carried

- **WHEN** a plate has solid material directly above a socket opening in the
  plate below
- **THEN** that material is carried, even though the plate above is within the
  outline of the plate below

#### Scenario: A socket over a socket needs nothing

- **WHEN** a plate's socket opening lies over a socket opening in the plate below
- **THEN** no supporting geometry is generated there, because there is nothing to
  carry

### Requirement: Supporting geometry spans exactly the gap it stands in

Supporting geometry SHALL reach from the top face of the plate below to the
downward face of the plate it carries, and SHALL NOT extend into either plate or
into any other gap.

#### Scenario: A pillar meets both plates

- **WHEN** supporting geometry is generated in a gap
- **THEN** it reaches the plate below and the plate above it carries
- **AND** it does not protrude past either

### Requirement: How much was carried is reported

The generator SHALL report, per gap, how much plate material had nothing beneath
it and how much supporting geometry was generated for it. A silent generator
cannot be told from one that found nothing to do.

#### Scenario: A stack reports its support

- **WHEN** a stack is generated
- **THEN** each gap's unsupported area and generated support volume are reported

#### Scenario: A nesting stack reports none

- **WHEN** every plate in a stack is carried entirely by the plate below it
- **THEN** the report shows no unsupported area and no supporting geometry
- **AND** the stack is generated without any
