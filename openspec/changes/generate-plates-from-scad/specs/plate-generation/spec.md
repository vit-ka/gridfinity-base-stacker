## Purpose

Producing the plates of a stack from the Gridfinity Extended OpenSCAD source
rather than recovering them from an STL somebody else generated: what the tool is
asked for, what it therefore knows about each plate without measuring it, and how
a requested baseplate is divided into pieces that stack on one another.

## ADDED Requirements

### Requirement: The tool is asked for a baseplate, not handed one

The tool SHALL take the size of the baseplate wanted, in cells or in millimetres,
and produce the plates itself. It SHALL NOT require a multi-plate STL as input.

Everything that made a plate what it is -- its cell counts, its size, which face
is land and which is rib, where its lattice origin sits -- is then known because
it was requested, rather than recovered by splitting shells and flood-filling a
face. Recovery is inference, and inference has been wrong here before.

#### Scenario: A baseplate is generated from a requested size

- **WHEN** a baseplate of a given width and depth is requested
- **THEN** the tool produces the plates of a stack covering exactly that area
- **AND** it needs no model file as input

#### Scenario: Every plate is a whole number of cells

- **WHEN** the plates for a request are generated
- **THEN** each plate's width and depth is a whole number of 42 mm cells, to
  within the tolerance of the format they are written in

#### Scenario: What is known is carried, not re-derived

- **WHEN** a plate is generated
- **THEN** its cell counts, its lattice origin and the type of each of its faces
  are available to everything downstream without measuring the mesh

### Requirement: The decomposition is chosen so the plates stack

Where the requested baseplate can be divided into pieces that each rest fully on
the piece below, the tool SHALL choose such a division.

A tiling cut for bed packing does not nest: a narrower plate's solid border lands
over a wider plate's socket opening, those sockets are through-holes, and that
border has to be carried to the bed. Pillars exist for that case. Choosing the
division ourselves removes the case rather than supporting it.

#### Scenario: A nesting division is preferred

- **WHEN** a requested baseplate admits a division whose pieces nest
- **THEN** the tool produces that division
- **AND** no pillar is generated, because nothing hangs over a socket

#### Scenario: A request that cannot nest is still served

- **WHEN** no nesting division exists for the requested size
- **THEN** the tool still produces a stack, carrying whatever overhangs with
  pillars as before
- **AND** it reports that it could not nest, and what that costs

#### Scenario: The division fits the bed

- **WHEN** plates are generated for a printer of a given build volume
- **THEN** every plate fits within it

### Requirement: The generator is invoked, not absorbed

The upstream OpenSCAD source SHALL be invoked as an external tool from a location
the user supplies. It SHALL NOT be copied into this project.

Upstream is licensed GPL-3.0 and this project is MIT. Invoking a separate program
is aggregation; copying its source in is not, and would change this project's
licence.

#### Scenario: The generator is missing

- **WHEN** OpenSCAD or the baseplate source cannot be found
- **THEN** the tool fails with a message naming what it looked for and where,
  rather than producing a stack from nothing

#### Scenario: The generator reports a problem

- **WHEN** the generator fails or produces geometry the tool cannot read
- **THEN** the failure is surfaced with what was asked of it, not swallowed

### Requirement: Generated geometry is checked before it is used

Geometry that arrives from the generator SHALL be checked for the properties the
rest of the pipeline depends on before anything is built on it.

This tool no longer authors the plates, but it is still what writes the file that
gets sliced. A mesh the slicer rejects is this tool's problem whoever made it.

#### Scenario: A plate that is not manifold is refused

- **WHEN** generated geometry has edges not shared by exactly two facets
- **THEN** the tool reports it and does not write a stack built on it

#### Scenario: The generated plate is the size that was asked for

- **WHEN** a plate arrives from the generator
- **THEN** its measured bounding box matches the requested cell counts
- **AND** a mismatch is an error naming both
