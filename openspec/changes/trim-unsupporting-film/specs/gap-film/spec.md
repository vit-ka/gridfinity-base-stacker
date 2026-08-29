## MODIFIED Requirements

### Requirement: The film exists only where something rests on it

The film SHALL be generated only where it carries something: the downward face of
the plate above, or a pillar standing at the level above. Film beneath an empty
socket carries nothing and is printed, paid for, and thrown away.

This narrows the previous behaviour, which generated film wherever there was
something to rest on without regard to whether anything rested on it.

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
