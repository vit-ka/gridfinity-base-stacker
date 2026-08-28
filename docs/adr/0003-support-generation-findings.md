# 3. What controls support in a stacked baseplate

Date: 2026-08-28
Status: Accepted

## Context

Stacked baseplates print with two kinds of support. The **interfaces** in the
gaps are wanted: they carry each plate and are the film that separates them. The
**balconies** are not: ribbons of support hugging the socket walls inside a
plate's own height, which cost material and time and have to be picked out.

Removing the balconies without touching the interfaces took a long investigation
against the BambuStudio source. This records what governs it, so the next attempt
does not repeat the dead ends.

## Decision

Control balconies with **`support_expansion`** (slightly negative) and
**`support_object_xy_distance`** (large). Nothing else reaches them.

Measured on the nine-plate drawer stack, snug, 0.4 mm gap:

| xy | expansion | time | support | balconies | thinnest interface |
|---|---|---|---|---|---|
| 0.35 | 0 | 11.50 h | 45.3 g | 12.2 g | 1.60 |
| 0.8 | -0.25 | 9.18 h | 25.7 g | 5.6 g | 1.44 |
| 1.6 | -0.25 | 9.08 h | 23.4 g | 3.2 g | 1.44 |

Interface coverage tracks expansion only and is flat in xy, so xy can be pushed
freely. Past -0.25 expansion the interfaces start losing the lands they carry.

## Why: the mechanism

`Support/SupportMaterial.cpp`, in order:

```
397  top_contacts = top_contact_layers(...)                     <- blockers and enforcers act ONLY here
422  bottom_contacts = bottom_contact_layers_and_layer_support_areas(object, top_contacts, ...)
457  generate_base_layers(object, bottom_contacts, top_contacts, intermediate_layers, layer_support_areas)
```

A contact is snapped onto a grid before becoming interface:

```cpp
grid_resolution(object_config.support_base_pattern_spacing.value + support_material_flow.spacing())
// Stretch support islands into a grid, trim them.
SupportGridPattern support_grid_pattern(&contact_polygons, &slices_margin.polygons, grid_params);
```

So the interface is whole grid cells (~2.9 mm at stock) covering a rib that is
1.5 mm at a land-to-land gap. The excess hangs past the rib with nothing under
it, and line 229 of `bottom_contact_layers_and_layer_support_areas` projects it
downward unconditionally:

```cpp
polygons_append(overhangs_projection, union_(polygons_new));
```

**That projection is the balcony.** Balconies are not overhangs of the model; they
are the descent of an interface that is wider than what it lands on. Which wall
they appear on is the side the grid cell boundary falls outside the rib, and the
phase is absolute -- `BoundingBox::align_to_grid` snaps `min(0)`/`min(1)` in
absolute coordinates -- so a whole axis behaves alike, and they are never on both
walls of a socket because the rib is never exactly centred in a cell.

Only `support_expansion` and `support_object_xy_distance` change the size of that
excess. Everything else leaves it alone.

## Ruled out, with evidence

- **Support blockers.** `blocker` appears once in 3308 lines, subtracting from
  `diff_polygons` inside `top_contact_layers`. A blocker can delete a contact; it
  cannot delete the descent from one. Blocking inside a plate does nothing;
  blocking a plate's first layer deletes that plate's interface along with the
  balconies. All or nothing per contact.
- **Support enforcers.** `enforcer_polygons = diff(intersection(layer.lslices,
  enforcer), expand(lower_layer_polygons))` -- they only *add* contacts, and only
  where model material has nothing below it. With `normal(manual)` they are the
  only contacts, and the descent still happens: measured 45.2 g with 21.8 g of
  balcony, the same as automatic.
- **Threshold angle.** No effect at all, 1 deg to 30 deg identical. Balconies are
  not angle-detected overhangs.
- **`support_remove_small_overhang`.** Gated on the *bounding box* of a merged
  overhang cluster being under two line widths. Balconies are thin ribbons
  running around a 40 mm socket: thin, never small.
- **Base pattern spacing.** Flat from 2.5 to 20 mm (45.7-48.2 g); finer is worse
  (59.7 g at 0), because a coarse grid discards small contact islands a fine one
  keeps. Aligning the cell to divide the 42 mm pitch changes nothing measurable.
- **Tree support.** Every style either fills the cells with branches (97 g) or
  leaves the gaps unsupported.
- **Negative expansion past -0.25.** Starts deleting the interface: coverage 0.73
  at -0.3, 0.58 at -0.4.
- **OrcaSlicer.** 940 options against Bambu's 738, but the same support engine
  line for line -- identical `expansion_to_propagate`, `grid_resolution`,
  `SupportGridPattern`, unconditional projection. Its one extra support option,
  `support_threshold_overlap`, governs overhang *detection*. Switching gains
  nothing. Its `make_overhang_printable` is interesting for a different reason:
  it edits model geometry to make overhangs printable, the same instinct as the
  ledge fillers here.

## What would actually fix it

Trimming a contact to the part that lands on solid ground, before projecting.
That is a small change in `bottom_contact_layers_and_layer_support_areas` --
intersect the contact with the lower layer's slices -- and it is a slicer patch,
not a setting. Neither slicer has it.

## Consequences

Balconies can be reduced by about 4x but not eliminated. What remains sits inside
open through-shafts, attached at neither end, and falls out with the rest of the
support.

`make_blockers` and `make_enforcers` are kept behind `--blockers` and
`--enforcers`. Neither earns its place on the evidence above, but both are
correct implementations and the harness for testing them exists, so they stay for
future experiments rather than being rediscovered from scratch.

Two traps worth remembering, both cost hours here:

- Bambu centres a loaded modifier part on the object it joins. Any part whose
  bounding box centre differs is silently displaced -- a blocker landed 3 mm out
  in X, an enforcer 2 mm out in Z. `match_bbox` pins all three axes.
- Aggregate support figures hide opposing effects. A blocker that removed 10% of
  an interface while adding the same mass of corner support read as "no change".
  Read the G-code per layer and per feature.
