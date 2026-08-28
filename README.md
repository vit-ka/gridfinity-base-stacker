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

## Ledges, and why you may want two stacks

A plate that hangs past the one below it forces the slicer to build a tall thin
freestanding wall up to the overhang -- the least printable thing in the whole
arrangement. Ordering cannot always avoid this: containment is a partial order,
and a set with incomparable plates (neither contains the other) has no single
stack without a ledge.

`--split` emits the fewest stacks in which every plate rests fully on the one
below. On the six-plate cabinet set that is two stacks, and it wins on
everything except job count:

| | time | support | ledges |
|---|---|---|---|
| one 6-stack | 4.93 h | 6.7 g | 436 mm2, 8.6 mm tall |
| two 3-stacks | 4.71 h | 4.2 g | none |

Each stack is also a third of the height, so a failure costs a third of the
print.

## Options

```
--gap MM              separation gap, default 0.8 (snapped to layer height)
--layer-height MM     default 0.2
--bed WxDxH           default 256x256x256
--split               emit one stack per nesting group, so nothing overhangs
--no-flip             keep every plate the same way up
--no-register         centre plates instead of aligning their lattices
--no-blockers         skip the blocker file
-o, --out-dir DIR     default out/
--name STEM           output basename
```

## Reusing the slicer settings

The settings that matter are in `settings/`, distilled to the twelve that this
arrangement actually depends on. Everything else is stock.

| profile | for | gap |
|---|---|---|
| `petg-interface` | a support interface that does not bond to PLA (PETG) | 0.2 mm |
| `same-material` | same filament, or one that still grabs (Bambu Support W) | 0.8 mm |

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
