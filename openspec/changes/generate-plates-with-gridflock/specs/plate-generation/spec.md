## Purpose

Producing the plates of a stack by driving a vendored OpenSCAD baseplate
generator, so that each plate's cell counts, face types and orientation are known
because they were asked for rather than recovered from an opaque mesh. Also
covers what must remain true when the vendored copy is replaced with a newer one.

## ADDED Requirements

### Requirement: A requested baseplate size is the only model input

The generator SHALL take a requested baseplate size and a bed size and produce
the plates itself. It SHALL NOT require a model file describing the plates.

#### Scenario: A stack is generated from a size alone

- **WHEN** a baseplate size and a bed size are requested
- **THEN** the plates are generated and stacked with no model file supplied

#### Scenario: The requested size need not be a whole number of cells

- **WHEN** a size is requested that is not a whole multiple of the 42 mm cell
- **THEN** the plates are generated with the leftover carried as edge padding
- **AND** the generator does not refuse, because a baseplate is normally sized to
  fit a drawer rather than to fit the lattice

### Requirement: Generated plates measure what was requested

Every generated plate SHALL measure the cell count it was asked for. A plate that
does not is a generator failure and SHALL be reported as one, before anything is
built on it.

#### Scenario: Cell counts are checked, not assumed

- **WHEN** a plate is generated
- **THEN** its width and depth are checked against the requested number of 42 mm
  cells
- **AND** generation fails, naming the plate and both measurements, when they
  disagree

#### Scenario: Arriving geometry is checked before use

- **WHEN** a generated plate is read back
- **THEN** it is checked to be a mesh this project can read and to be manifold
- **AND** a truncated or unreadable result fails with a message naming the file

### Requirement: The plate geometry is read back in a form this project can read

The generator SHALL request binary output. The project's mesh reader rejects
ASCII, and the OpenSCAD default is ASCII, so a generated plate is unreadable
unless binary output is asked for explicitly.

#### Scenario: A generated plate loads

- **WHEN** a plate is generated
- **THEN** it loads through the project's own mesh reader without conversion

### Requirement: A missing or failing generator is reported, not swallowed

The generator SHALL report what it looked for and where when OpenSCAD or the
vendored source is absent, and SHALL surface a generation failure together with
what was asked of it.

#### Scenario: The tool is absent

- **WHEN** OpenSCAD or the vendored source cannot be found
- **THEN** generation fails with a message naming what was looked for and the
  path it was looked for at

#### Scenario: The generator rejects a parameter

- **WHEN** the underlying generator fails on a parameter it does not accept
- **THEN** that failure is surfaced along with the parameters it was given
- **AND** it is not reported as a missing or empty plate

### Requirement: The vendored generator is upstream's file, unmodified

The vendored source SHALL be byte-identical to the upstream release it was taken
from, and the commit it came from SHALL be recorded. This project's own OpenSCAD
SHALL live in its own file, and SHALL NOT be included into the vendored source
nor the vendored source into it.

Upstream carries no tags or releases and the generator this tracks follows its
main branch, so the vendored copy will be replaced repeatedly. Any edit to it is
a conflict to re-resolve on every replacement.

#### Scenario: The vendored copy matches upstream

- **WHEN** the vendored source is compared against the upstream commit recorded
  for it
- **THEN** the files are identical

#### Scenario: Our own geometry lives in our own file

- **WHEN** this project generates the film or the pillars
- **THEN** it does so from a file of its own
- **AND** that file reads back what the vendored generator wrote rather than
  reaching into its internals

### Requirement: The generator is coupled to upstream only by its parameters

The interface to the vendored source SHALL be the parameter names it accepts.
Those names SHALL be checked to exist after the vendored copy is replaced, so
that a rename upstream is caught by this project rather than by producing a
silently wrong plate.

A parameter OpenSCAD does not recognise is not an error: it is ignored, and the
plate is generated with the default instead. That is the failure this guards.

#### Scenario: Parameter names are verified after an upgrade

- **WHEN** the vendored copy is replaced
- **THEN** every parameter this project sets is confirmed to be declared by the
  vendored source
- **AND** the upgrade fails, naming the parameter, when one is not

#### Scenario: An upgrade leaves the checks passing

- **WHEN** the vendored copy is replaced and the plate checks are re-run
- **THEN** the generated plates still measure their requested cell counts and are
  still manifold
- **AND** no file of this project's own had to be edited to achieve it

### Requirement: Third-party material under a different licence is excluded

Vendored material SHALL be limited to what is licensed compatibly with this
project, and the copyright notices of everything vendored SHALL be carried in the
repository.

#### Scenario: Incompatibly licensed model data is not vendored

- **WHEN** the vendored copy is assembled
- **THEN** upstream's third-party model data under a different licence is left
  out
- **AND** the generator still runs, because this project does not use the feature
  that data serves

#### Scenario: Notices are carried

- **WHEN** the repository is distributed
- **THEN** it carries the copyright notice of every vendored work alongside its
  own
