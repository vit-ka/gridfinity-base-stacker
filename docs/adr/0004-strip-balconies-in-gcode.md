# 0004. Strip balconies in G-code post-processing

Date: 2026-08-28
Status: Accepted

## Context

A balcony is the ribbon of support that hugs a socket wall inside a plate's own
height. It is the downward projection of interface that overhangs the rib it
lands on: contacts are snapped to a grid of roughly `support_base_pattern_spacing`
plus one extrusion width, about 2.9 mm, while the land beneath is 1.50 mm. The
fringe left over has nothing under it, so it is propped all the way down.

ADR 0003 records the search for a setting that stops this. There is none.
`SupportMaterial.cpp:229` appends the projection unconditionally --

    polygons_append(overhangs_projection, union_(polygons_new));

-- with no config gate, and blockers and enforcers act only on `top_contact_layers`
at line 397, upstream of the descent. Measured and rejected: support blockers,
support enforcers, threshold angle, remove-small-overhangs, base pattern spacing,
grid alignment, snug and tree styles, and OrcaSlicer.

Raising `support_object_xy_distance` does clear them, but takes the legitimate
support under the ledge overhangs with it, so it trades one defect for another.

The balconies are otherwise harmless -- they fall out of an open cell -- but they
cost about 6 g and their share of print time on a nine-plate stack.

## Decision

We delete the toolpaths after slicing, with a script run by hand on the exported
G-code.

Through Bambu's own post-processing hook, but carrying the script rather than
pointing at it. `run_script` builds `$SHELL -c "<line> '<gcode>'"` and the child
inherits the app's working directory -- `/` for an app launched from Finder -- so
any path in the box must be absolute. And `post_process` is a *process* setting,
stored in `Metadata/project_settings.config` inside the 3mf, so those absolute
paths would travel with every project saved from that profile and break on any
other machine.

So the line carries the source instead: this file plus the plate boxes, deflated
and base64'd into a single `python3 -c` command. `--install` writes it into a
project 3mf directly. The project is then self-contained -- nothing on disk, no
machine-specific path -- which is what makes it survive being moved or shared.

It is one line because `run_post_process_scripts` splits the field on newlines
and runs each piece as a separate command, so a multi-line payload would be torn
into fragments. Base64 also keeps it a single shell word: the alphabet is
`[A-Za-z0-9+/=]`, none of which mean anything to sh or fish.

The interpreter is `/usr/bin/python3` -- an absolute path, but a stable system one
present on every Mac rather than a machine's own layout. A bare `python3` would
depend on a PATH a GUI app may not have. This is the one part that is not
portable off macOS.

## Consequences

Balconies go and nothing else does. Measured on the nine-plate drawer stack:
support inside the plate boxes 1998.8 mm -> 0.0 mm, `Support interface` 3852 mm
unchanged, and the 162.2 mm of base support outside those boxes unchanged. That
162.2 mm is *not* ledge columns, as first assumed: it sits entirely at the gap
layers, one per plate base, and is the base support that shares those layers with
the interface. On this stack the ledge overhangs are carried by the fillers
(ADR 0002), so there is almost no ledge support to preserve -- which means this
measurement does not by itself demonstrate that ledge columns survive.
26,329 moves edited in 1.3 s, about 6.0 g, which independently reproduces the
~5.6 g of balcony measured from the geometry.

The project is self-contained: it can be moved, archived, or shared and still
strips its own balconies, with nothing installed alongside it.

The plate boxes are baked into the line, so the install has to be repeated
whenever the stack is regenerated. A stale copy is caught only if the footprint
changed, not if only the internal z-levels did.

`check_settings.py` will now see a non-empty `post_process`, which is a real
setting difference and should be expected rather than treated as drift.

The preview never reflects it: post-processing runs at export, so Bambu still
draws the balconies and quotes their time. The exported G-code is the artifact to
inspect, and Bambu Studio opens `.gcode` directly.

The Bambu **CLI** refuses to open a 3mf whose `post_process` is set --
`normative_check: postprocess not supported, array size 1`, exiting
`CLI_POSTPROCESS_NOT_SUPPORTED` -- unless given `--normative-check=0`. That check
sits in the CLI entry point (`BambuStudio.cpp:1950`) behind a CLI-only option, so
it does not affect the GUI, but it does mean the measurement harness in this repo
must pass that flag when slicing an installed project.

The two halves can drift. `plates.json` is generated with the STL and must be
regenerated with it; a stale one is caught by the footprint check only if the
outline changed, not if only the internal z-levels did.

Tool changes are not removed. If support base is a different filament from the
object, a layer whose only base extrusion was balcony still pays for its change
and its flush. Recovering that is possible but not done.

We now depend on a Bambu implementation detail -- feature comments, `M83`, and
arc output -- rather than on documented configuration. That is the price of
reaching something no configuration reaches.
