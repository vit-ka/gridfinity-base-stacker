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
| `PRINTING.md` | Bambu Studio settings for the model it was generated from |

## What it does

1. Splits the STL into shells by welding vertices.
2. Finds each plate's 42 mm cell lattice by flood-filling the bottom face.
3. Orders plates largest footprint first.
4. Rotates every other plate 180 degrees, picking the axis that keeps it centred.
5. Translates each plate so all the lattices share one origin.

Alternating the orientation makes every interface land-to-land or rib-to-rib, so
contact faces match and the socket funnels never fill with support. See
[ADR 0001](docs/adr/0001-alternating-flip-and-lattice-registration.md) for why.

## Options

```
--gap MM              separation gap, default 0.8 (snapped to layer height)
--layer-height MM     default 0.2
--bed WxDxH           default 256x256x256
--no-flip             keep every plate the same way up
--no-register         centre plates instead of aligning their lattices
--no-blockers         skip the blocker file
-o, --out-dir DIR     default out/
--name STEM           output basename
```

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
