# gridfinity-base-stacker

Takes a multi-plate Gridfinity baseplate STL and stacks the plates into one
model that prints in a single job. The plates are separated by gaps filled with a
film printed in a second, non-bonding filament -- Bambu Support W against PLA, or
PETG -- which is peeled off afterwards so the stack comes apart into separate
plates.

**The separation works when the film is really held clear of the plates**, which
took three ADRs to establish: at a nominal 0.1 mm clearance every gap printed
with one face fused flat, because a mesh film's height is quantised to the
slicer's layer grid
([ADR 0009](docs/adr/0009-clearance-is-quantised-to-the-layer-height.md)). So the
film stopped being a mesh. It is emitted as **toolpaths written into the sliced
G-code**, at heights chosen to the micron, with the clearance below it and the
clearance above it set separately -- the first is what lets it release, the
second is what the plate above has to bridge.

The stack itself gets **no slicer support**: every overhang on it is carried by
geometry the tool puts there, under a blocker. Support is on only for a small
decoy column beside the stack, whose job is to make the slicer load and purge the
interface filament at the heights the interface needs.

![Nine baseplates stacked into one print, sliced in Bambu Studio](docs/stacked-plates.png)

Nine plates of a drawer set as one job: the brown is the stack and its pillars,
the white is the film filling each gap. The small block at the back is the wipe
tower. The picture is from when the film was still a model part -- it is emitted
into the G-code now, so it no longer appears in Studio's preview at all, which is
what `verify.py` exists to make up for.

Stdlib Python only: no numpy, no trimesh, no mesh kernel. Meshes are flat tuples
of floats and every geometric question is answered by ray casting or by
rasterising to a grid.

## Getting a source model

The plates themselves come from **Gridfinity Extended**, which generates a
multi-plate baseplate STL:

<https://gridfinity.perplexinglabs.com/pr/gridfinity-extended/0/1>

Generate the set you want as a single STL with all plates in it -- this tool
splits them apart itself. It expects extended baseplates: a 42 mm cell lattice
with tapered sockets, no magnet holes required.

## Use

```
python3 stack_plates.py path/to/multiplate.stl

python3 make3mf.py --template templates/stack-template.3mf \
    --model out/NAME.stl --plates out/NAME.plates.json \
    --interface-plan out/NAME.interface.json --out out/NAME.3mf

BambuStudio --no-check --outputdir out/slice --slice 0 out/NAME.3mf

python3 emit_interface.py --project out/NAME.3mf \
    --gcode out/slice/plate_1.gcode --out out/NAME.gcode

python3 verify.py --project out/NAME.3mf --gcode out/NAME.gcode
```

Print `NAME.gcode`. The sliced file has no interface in it at all -- the gaps are
empty until the last step writes them, which is also why `--no-check` is
required: the slicer calls an empty gap an empty layer and refuses to write
G-code without it.

Nothing else to configure: the template carries the plate layout, the filament
assignments, the stack's print settings, and which slot the interface filament
is in.

| file | what it is |
|---|---|
| `NAME.stl` | the stack: plates plus the pillars that carry them |
| `NAME.interface.json` | the interface toolpaths, one region per gap per layer |
| `NAME.plates.json` | where every plate ended up; the 3mf builder reads it |
| `NAME-PRINTING.md` | settings and notes for this particular stack |
| `NAME.3mf` | the project, ready to slice; carries the plan on the bed |
| `NAME.gcode` | the sliced file with the interface written into it |

## How it works

1. Split the STL into shells by welding vertices.
2. Find each plate's 42 mm cell lattice by flood-filling the bottom face.
3. Order the plates so each sits on the one below wherever the containment order
   allows ([ADR 0001](docs/adr/0001-alternating-flip-and-lattice-registration.md)).
4. Rotate every other plate 180 degrees and register the lattices onto a common
   origin, so every interface is land-to-land or rib-to-rib.
5. Raise a **pillar** wherever a plate has material with nothing beneath it
   ([ADR 0005](docs/adr/0005-support-what-has-nothing-under-it.md)).
6. Fill each gap with a **film** that carries the plates and the pillars, bridged
   so it lifts as one sheet
   ([ADR 0007](docs/adr/0007-bridge-the-film-so-it-lifts-as-one-sheet.md)).
7. Write that film into the sliced G-code as toolpaths, at the heights it was
   asked for rather than the ones the layer grid allows
   ([ADR 0009](docs/adr/0009-clearance-is-quantised-to-the-layer-height.md)).

### Pillars

Not just plates hanging past the edge of the one below. The case that bites is a
narrower plate whose solid border lands over a wider plate's socket opening --
those sockets are through-holes, so that border can need carrying to the bed.
Comparing footprints misses it entirely; the tool rasterises each plate's
downward face against what lies beneath and takes the difference.

Pillars are grown outward for stability and held clear of the plates only where
they would meet one. Each connected region is traced into a single solid, so
outlines follow the socket instead of stepping around it
([ADR 0006](docs/adr/0006-one-solid-where-we-can-overlap-where-we-cannot.md)).

### The film

Two layers, each stepping 0.2 mm further out than the one below, so it leans at
45 degrees and bridges unaided. It is trimmed to what it actually carries, then
bridged across short spans so each gap is one sheet rather than islands stranded
on pillar tops.

It is held clear of the plates on both faces, and the two distances are separate
settings because they do different jobs. The gap **below** is what lets the film
release; the gap **above** is what the first layer of the plate above has to
bridge, so the underside of every plate is as poor as that number is large.

That is why the film is no longer a mesh. A mesh gets sliced, and a face landing
on one of the slicer's sample planes is resolved by float rounding that depends
on the height of the stack it belongs to -- so a nominal 0.1 mm clearance printed
as 0.00 on one stack and 0.20 on another, and 0.1 mm was not available at all.
The regions are still worked out the same way; only the output changed, from
facets to extrusions written into the sliced file. Measured on two stacks of
different height, every gap now comes out at exactly the two numbers it was
asked for.

### The decoy and the blocker

Nothing in the model uses the interface filament any more, so nothing makes the
slicer load it -- and an injected extrusion in a filament the file never loaded
prints in whatever was already in the nozzle. A small **decoy** column stands
beside the stack with the stack's own z profile; its gaps get slicer support in
the interface filament, which produces the real tool change, purge and prime
tower volume at exactly the heights the interface needs. The interface then rides
on machinery the slicer built rather than on a hand-written tool change, which
would be wrong in ways that only show up on the printer.

A **blocker** over the whole stack is the other half: support has to be on for
the decoy's sake, so the stack has to be explicitly protected from it
([ADR 0003](docs/adr/0003-support-generation-findings.md) measured that one slab
over everything is the arrangement that does not leak). `verify.py` reads the
emitted file and fails if any support reached the stack.

## Options

`--gap`, `--interface-clearance`, `--interface-clearance-above`, `--bridge-span`
and `--filler-grow` are the ones with measurements behind them; each option's
help text carries the numbers and what was swept to arrive at them.

```
python3 stack_plates.py --help
```

`--split` emits one stack per chain of the containment order, so nothing
overhangs anything -- fewer pillars, at the cost of one print job per stack.

Earlier versions carried machinery for approaches that no longer exist here:
support enforcers, and a G-code pass to delete support the slicer insisted on
generating. What was learned from each is in
[ADR 0003](docs/adr/0003-support-generation-findings.md) and
[ADR 0004](docs/adr/0004-strip-balconies-in-gcode.md), which is the part worth
keeping; git history has the code. That earlier G-code pass had to identify and
delete someone else's extrusions by inference, which is what killed it; this one
writes its own and deletes nothing.

## The template

`templates/stack-template.3mf` carries the plate layout, wipe tower position,
filament assignments, all the project settings, and the per-part print settings
for the stack. A placeholder block stands in for the geometry, so no one's model
lives in this repository.

`support_interface_filament` in that template is what says which slot the
interface prints in -- one number, because the decoy's support and the interface
are the same filament by construction. `--interface-extruder` overrides it.

The template deliberately does *not* park a decoy. Its position is derived, so it
stands beside whatever stack it is built for rather than 80 mm away from a small
one. A part named "decoy" in a template is found and its position kept, for
anyone who wants it somewhere specific.

To change any of it, arrange it in Bambu Studio, save with **File > Save Project
As**, and point `--template` at the result. Note that per-part settings live in
`Metadata/model_settings.config`, not in the project settings -- a diff of the
project settings shows nothing at all when someone has configured a part.

## Verifying

```
python3 -m unittest test_stack_plates test_gcode
```

`verify.py` checks the result independently of the plan that produced it. Given
`--source`/`--stack` it checks the geometry: manifold edges, shell volumes, plate
containment, ledges, lattice registration. Given `--project`/`--gcode` it reads
the file that goes to the printer and reports, per gap, the measured clearance
above and below the interface, how many pieces the film is in, and whether any
support reached the stack. An interface layer that is not at the height it was
asked for is an error there, and it names both numbers.

Verify against a sliced file, not only the test suite. The suite has passed while
the model printed into air. Note also that a CLI slice is evidence only where the
geometry is unambiguous -- ADR 0007 records a case where the CLI and the GUI gave
opposite answers on the same file.

## Licence

MIT -- see [LICENSE](LICENSE).

The baseplate geometry itself is not this project's work: plates come from
[Gridfinity Extended](https://gridfinity.perplexinglabs.com/pr/gridfinity-extended/0/1),
and Gridfinity is Zack Freedman's design.

## Working on this

Changes go through OpenSpec (`openspec/`): `/opsx:propose` to write one,
`/opsx:apply` to implement it, `/opsx:archive` when it lands. The project context
in `openspec/config.yaml` carries the domain facts that are not guessable from
the code.

Decisions live in `docs/adr`. Several exist specifically to stop approaches being
re-tried after they measured as dead ends.
