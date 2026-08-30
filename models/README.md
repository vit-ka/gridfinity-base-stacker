# Test models

Small multi-plate baseplates, here so a test run needs nothing from outside the
repository. They print fast enough to answer one question per print, which is
the point: two full-size stacks were lost partway through and answered nothing
([ADR 0008](../docs/adr/0008-what-the-first-test-prints-showed.md)).

| file | generated as | connectors |
|---|---|---|
| `test-plain.stl` | `gf-extended-baseplate-(3,60)x(3,113)-magfalse-ccfalse-ecb03` | no |
| `test-connectors.stl` | `gf-extended-baseplate-(3,60)x(3,113)-magfalse-cctrue-3c575` | yes |

Two plates, 56.9 x 60.0 x 8.6 mm stacked, one land-to-land interface: about 33
minutes and 12 g a print. Small enough to answer one question per print, and
small enough that the answer arrives the same hour it is asked.

They do **not** cover a rib-to-rib interface, which is the one with several
times the contact area and the one expected to hold hardest. A stack that comes
apart here has not yet been shown to come apart there.

The pair differs only in the `cc` flag, so anything that behaves differently
between them is the connectors and nothing else.

Both come from [Gridfinity
Extended](https://gridfinity.perplexinglabs.com/pr/gridfinity-extended/0/1);
the geometry is not this project's work. The generator's own filenames are kept
above because they carry the settings each was made with.

    python3 stack_plates.py models/test-plain.stl --name test-plain --out-dir out
