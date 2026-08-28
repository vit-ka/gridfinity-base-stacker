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

Use **`support_object_xy_distance` 0.8 mm** with **`support_expansion` -0.25**,
which is what the settings profile carries.

In practice the XY distance stops helping past about 0.8 mm: beyond that the only
thing left in the gap band is the interface itself, which the trim cannot touch
(see below), so raising it further changes nothing visible.

Measured on the nine-plate drawer stack, snug, 0.4 mm gap:

| xy | expansion | time | support | balconies | thinnest interface |
|---|---|---|---|---|---|
| 0.35 | 0 | 11.50 h | 45.3 g | 12.2 g | 1.60 |
| 1.6 | -0.25 | 9.07 h | 23.4 g | 3.2 g | 1.44 |
| 6.0 | -0.25 | 8.90 h | 19.9 g | 0.1 g | 1.44 |
| 10.0 | 0 | 9.01 h | 22.5 g | 0.0 g | 1.65 |

**These last rows are disputed.** The CLI sweep above shows balconies still
falling from 0.8 mm to 6 mm, but in Bambu Studio the effect saturates at about
0.8 mm and larger values change nothing visible. The setting is not clamped
(`def->max = 10`), so that is not the explanation. Unresolved; the GUI is the
authority, so the profile stays at 0.8 mm and this is recorded as a discrepancy
rather than a result. Anyone revisiting should reproduce the sweep in the GUI
before trusting the numbers.

What is not in doubt is the last surviving layer. A 0.4 mm gap holds two layers,
and with one top and one bottom interface layer *both* of them are interface.
Neither overlaps an object layer in Z, so neither can ever be trimmed, whatever
the XY distance. What looks like a final thin balcony is that interface spreading
past the rib, because the contact was snapped to a ~2.9 mm grid. The only way to
narrow it is to shrink the contact before the snap with negative
`support_expansion`, which costs interface coverage -- 1.44 against 1.65.

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

`support_expansion` shrinks that excess before the snap. But the setting that
actually clears the balconies works differently and later:
`trim_support_layers_by_object` is applied to contacts, bottom contacts *and*
intermediate layers, and erases support that lies within `gap_xy` of the object:

```cpp
bool is_overlap = is_layers_overlap(support_layer, object_layer);
coordf_t trimming_offset = is_sharptail ? sharp_tail_xy_gap :
                           is_overlap   ? gap_xy_scaled :
                                          no_overlap_xy_gap;
polygons_append(polygons_trimming, offset({expoly}, trimming_offset, ...));
```

The `is_overlap` test is the whole story. Balconies sit at layers where the plate
exists, so they overlap an object layer in Z and are trimmed by `gap_xy`.
Interfaces sit in the gap layers, where nothing overlaps them in Z, so they get
`no_overlap_xy_gap` -- a small constant -- whatever `gap_xy` is set to. That is
why interface coverage is flat in XY while balconies fall away, and why the
setting can be pushed far past any sane "clearance" value without harm.

`support_object_xy_distance` is therefore not a clearance here. It is a trim
radius, and it needs to reach from the socket wall to the middle of the shaft.

## Interface continuity

The interface is peeled off by hand, so it wants to come away as one sheet rather
than tear at every travel move. The pattern decides that, and the default is the
worst of the five. Measured on the nine-plate drawer stack, counting separate
extrusion paths across all interfaces:

| interface pattern | separate paths | material | longest unbroken run |
|---|---|---|---|
| rectilinear_interlaced (Bambu default) | 38,686 | 18.8 g | 50 mm |
| grid | 33,963 | 19.1 g | 40 mm |
| rectilinear | 26,317 | 18.7 g | 1001 mm |
| **concentric** | **6,214** | **14.8 g** | 487 mm |
| concentric + loop pattern | 11,255 | 15.9 g | 366 mm |

Use **concentric**: six times fewer places to tear, and 4 g less material. The
interface is a lattice of narrow ribs, and concentric traces each rib as one
continuous loop where rectilinear zig-zags across it with a travel at every turn.
Interlaced is worse still because it deliberately offsets alternate layers -- the
Bambu wiki notes it "may be harder to remove than standard rectilinear", and
recommends concentric with support material and zero interface spacing, which is
exactly this arrangement.

Leave `support_interface_loop_pattern` off: it nearly doubled the path count on
concentric, and the slice failed outright with interlaced.

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

## Open

At `xy 10` the balconies go to zero but legitimate support disappears too --
support under the ledge overhangs. The obvious explanation, that the ledge
fillers leave some overhang uncovered, does not hold: measured, the fillers place
101-125% of the material needing support directly beneath every ledge. So what is
being lost there is not yet identified. If it were, pushing the XY distance high
would become safe and the balconies would go with it.

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
