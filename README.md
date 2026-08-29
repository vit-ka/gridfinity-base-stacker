# gridfinity-base-stacker

Takes a multi-plate Gridfinity baseplate STL and stacks the plates into one
printable model, separated by gaps that print as support and split apart
afterwards.

Stdlib Python only -- no numpy, no trimesh.

```
python3 stack_plates.py path/to/multiplate.stl
```

Writes to `out/`:

| file | what it is |
|---|---|
| `gf-stack-N.stl` | the stacked model, one object |
| `gf-stack-N-blockers.stl` | support blockers, one solid per socket |
| `gf-stack-N-PRINTING.md` | Bambu Studio settings for the model it was generated from |

## What it does

1. Splits the STL into shells by welding vertices.
2. Finds each plate's 42 mm cell lattice by flood-filling the bottom face.
3. Orders plates largest footprint first.
4. Rotates every other plate 180 degrees, picking the axis that keeps it centred.
5. Translates each plate so all the lattices share one origin.

Alternating the orientation makes every interface land-to-land or rib-to-rib, so
contact faces match and the socket funnels never fill with support. See
[ADR 0001](docs/adr/0001-alternating-flip-and-lattice-registration.md) for why.

## Ledges

A plate that hangs past the one below it leaves the slicer to raise a tall thin
freestanding fin up to the overhang -- the least printable thing in the whole
arrangement. Ordering cannot always avoid this: containment is a partial order,
and a set with incomparable plates has no single stack without a ledge. On the
six-plate cabinet set, `216x126` must sit second because only `216x144` is wide
enough to hold it, which forces a 144-deep plate above a 126-deep one later.

By default the tool fills the void with **loose blocks instead**, one at each
plate level the ledge spans. Each is the overhanging plate's own footprint,
projected from the face directly above it and inset by the gap in XY. The stack's
gaps already separate the levels, so every block ends up clear on all six sides,
welded to nothing, and lifts out with the support.

| | time | support | ledge |
|---|---|---|---|
| `--no-fillers` | 4.93 h | 6.7 g | a 174 mm long fin, 8.6 mm tall |
| fillers (default) | 5.19 h | 5.3 g | braced blocks, 6.0 cm3 |
| `--split` (two jobs) | 4.71 h | 4.2 g | none at all |

Fillers cost 16 minutes and buy a structure that is not a thin wall. `--split`
avoids the ledge outright by emitting one stack per chain of the containment
order, and is better on every axis except that it is two print jobs.

**Project the near face, grown a little.** `--filler-grow` defaults to 0.5 mm,
about two perimeters. A faithful projection reproduces the plate's thinnest webs
exactly, and the slicer then drops the thinnest of them as unprintable, leaving
holes in the filler. Much beyond 0.5 mm the webs start doubling and the rounded
socket corners fill in; the dilation uses a disc for that reason, since a square
element offsets corners on both axes at once and squares them off.

Project the *near* face, not the plate's widest section. The socket tapers, so
the wide end gives a filler broader than both the face it carries and the face it
stands on, and its own footprint then needs bridging support underneath --
measured 12.5 cm3 and 6.0 g of support, against 5.4 cm3 and 5.3 g for the near
face.

## Options

```
--gap MM              separation gap, default 0.8 (snapped to layer height)
--layer-height MM     default 0.2
--bed WxDxH           default 256x256x256
--split               emit one stack per nesting group, so nothing overhangs
--no-fillers          leave ledges to the slicer instead of filling them
--filler-grow MM      dilate the filler footprint (default 0.5, see above)
--filler-step MM      resolution the filler outline is traced at (default 0.15)
--no-flip             keep every plate the same way up
--no-register         centre plates instead of aligning their lattices
--no-blockers         skip the blocker file
-o, --out-dir DIR     default out/
--name STEM           output basename
```

## Tuning support

Two settings do all the work, and they are model-dependent -- sweep them rather
than copying a number. See [ADR 0003](docs/adr/0003-support-generation-findings.md)
for why nothing else reaches the problem.

**Use `support_object_xy_distance` 0.8 mm with `support_expansion` -0.25.**

That setting is not a clearance despite the name. `trim_support_layers_by_object`
erases support lying within it of the object, but only where the support layer
overlaps an object layer in Z. Unwanted support sits inside a plate's own height,
so it is erased; interfaces sit in the gaps, where nothing overlaps them, so they
are untouched whatever the value. In practice it stops helping past about 0.8 mm -- beyond that the only thing left
in the gap band is the interface, which the trim can never reach. A CLI sweep
disagrees and shows it still helping to 6 mm; that discrepancy is unresolved and
recorded in the ADR. Trust the slicer.

The last thin layer that survives is not unwanted support at all: a 0.4 mm gap
holds two layers and both are interface, neither overlaps an object layer in Z,
so no XY distance can trim them. It looks wider than the rib because contacts are
snapped to a ~2.9 mm grid.

The unwanted support -- ribbons hugging the socket walls inside a plate's own
height -- is the downward projection of interface that is wider than the rib
beneath it, because contacts are snapped to a ~2.9 mm grid and a land is 1.5 mm.
It cannot be removed entirely, and what remains sits loose inside open
through-shafts.

To see it in Bambu Studio: Preview, Colour Scheme **Line Type**. Anything drawn
as `Support` rather than `Support interface`, inside a plate's own z range, is
the unwanted kind -- everything legitimate lives in the gaps.

`--blockers` and `--enforcers` emit modifier parts to experiment with. Neither
helps, for reasons the ADR records; they are kept because they are correct and
the next idea in that direction can start from them.

### Interface pattern

Set `support_interface_pattern` to **Concentric**, not the Bambu default. The
interface is peeled by hand and wants to come off as one sheet; concentric traces
each rib as a continuous loop where the default zig-zags across it with a travel
at every turn. Measured across all interfaces of a nine-plate stack: 6,214
separate extrusion paths against 38,686, and 4 g less material. Leave
`support_interface_loop_pattern` off.

## Support pillars

Nothing the slicer generates lands on the model, so every overhang has to be
carried by geometry we put there. `support_fillers` rasterises each plate's
downward face against what lies beneath it and raises a block wherever material
has nothing under it, walking down level by level until something solid appears.

This is not the same as finding ledges. A plate hanging past the edge of the one
below is the easy case; the one that bites is a narrower plate whose solid border
lands over a wider plate's socket opening, which happens whenever two plates have
different cell counts. Those shafts are through-holes, so that border can need
carrying all the way to the bed. See ADR 0005.

## Printing it: the 3mf

The interface is a second filament, and the slicer only changes filament for
something it knows about. So the plate carries three things, and `make3mf.py`
assembles them:

```
python3 make3mf.py --template templates/stack-template.3mf \
    --model out/NAME.stl --plates out/NAME.plates.json --out out/NAME.3mf
```

- **the stack**, in the object filament
- **a support blocker** over the whole stack, so the slicer generates nothing on
  it -- every gap gets its interface from us instead
- **a decoy column** beside it, with the stack's exact z profile. It exists to be
  supported: the slicer fills its gaps with interface, which is what puts the
  interface filament in the nozzle at each of the stack's gap layers.

The decoy and blocker are derived from `plates.json`, so they cannot drift from
the stack they belong to. Everything else -- plate layout, wipe tower position,
per-object filament assignments, all 581 settings keys -- comes from the template
verbatim, so a different stack never means re-arranging the plate by hand.

`templates/stack-template.3mf` ships a placeholder block where the stack goes,
sized to the space a stack gets, so no one's model lives in this repository. Save
your own from Bambu Studio with File > Save Project As and point `--template` at
it to change the layout or the filaments.

Verified on the nine-plate drawer stack: 0.0 mm of slicer support anywhere on the
model, and PETG present at all 8 gap layers.

**One trap.** Bambu refuses to slice a 3mf whose `3D/3dmodel.model` is missing the
`<metadata>` block after the `<model>` element -- it fails with a bare "slicing or
export error" naming nothing. An otherwise byte-identical file carrying only
`3mfVersion` fails; restoring the block fixes it. `make3mf.py` copies the
template's header verbatim for this reason.

## Removing the balconies

The support ribbon along the socket walls is the one defect no setting reaches
(ADR 0003 has the source and the measurements). `postprocess.py` deletes it from
the sliced G-code, where the slicer gets no vote. Install it into the project once:

```
python3 postprocess.py out/NAME.plates.json --install PROJECT.3mf
```

That writes the script's own source, plus the plate boxes, into the project's
`post_process` setting as a single self-contained command -- so the 3mf carries
everything, nothing has to remain on disk, and no path points anywhere
machine-specific. Re-run it whenever the stack is regenerated. `--embed` prints
the same line for pasting by hand, and passing a `.gcode` path instead runs it
directly on an exported file.

It strips support inside each plate's own footprint and height, which is exactly
the balconies; the ledge columns stand outside those footprints and survive, and
`Support interface` is never touched. On the nine-plate drawer stack: 26,329 moves, about
6 g, in 1.3 s, with the interface measurably unchanged. It refuses to run if the
plate heights do not match the sliced object, which is what a stale `plates.json`
looks like. See ADR 0004.

## Reusing the slicer settings

The settings that matter are in `settings/`, distilled to the twelve that this
arrangement actually depends on. Everything else is stock.

| profile | for | gap |
|---|---|---|
| `petg-interface` | a support interface that does not bond to PLA (PETG) | 0.4 mm |
| `same-material` | same filament, or one that still grabs (Bambu Support W) | 0.8 mm |

The gap is part of the profile, not a free choice: `check_settings.py` prints the
gap each one expects. A 0.4 mm gap with a non-bonding interface gives two
interface layers, so the film lifts off in one piece; at 0.2 mm it is a single
layer and tends to tear on the way out.

Set them once in Bambu Studio and save the project as a template, then swap the
model for each new stack. To confirm a template still says what you think it
says:

```
python3 check_settings.py mystack.3mf
python3 check_settings.py mystack.3mf --profile same-material
```

It reads `Metadata/project_settings.config` out of the 3mf, reports any setting
that differs, and exits non-zero, so it can gate a print. Worth running after
touching anything in the support panel: several of these look harmless and are
not. Setting the top Z distance to 0 with a bonding interface costs an hour;
support style `grid` costs 1.4 h and 35 g.

Keep the 3mf itself outside the repo -- it carries the mesh.

## Checking a result

`verify.py` re-derives the plan and checks the written files independently:
facet and shell counts, per-shell volume and winding, plate containment, gap
sizes, interface matching and registration, and -- by point sampling -- that no
blocker sits inside plate material.

```
python3 verify.py source.stl out/gf-stack-6.stl out/gf-stack-6-blockers.stl
```

Takes a couple of minutes; the sampling is the slow part.

## Tests

```
python3 -m unittest test_stack_plates
```
