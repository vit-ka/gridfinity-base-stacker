"""Tests for the balcony stripper.

The G-code fragments here are shaped like Bambu's real output: relative E,
`; FEATURE:` and `; Z_HEIGHT:` comments, arcs, and E words written without a
leading zero.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import base64
import zipfile
import zlib

from postprocess import (SETTINGS, Box, bed_offset, check_z, embedded_command,
                         install, load_plates, main, process, strip_e)


def gcode(*lines: str) -> list[str]:
    return [l + "\n" for l in lines]


# One plate: 10x10 at the origin, 0 to 4 mm tall.
PLATE = Box(-5.0, 5.0, -5.0, 5.0, 0.0, 4.0)


class StripE(unittest.TestCase):
    def test_removes_only_the_e_word(self):
        self.assertEqual(strip_e("G1 X78.71 Y59.988 E1.26551"), "G1 X78.71 Y59.988")

    def test_handles_no_leading_zero(self):
        self.assertEqual(strip_e("G3 X78.7 Y26 I-.086 J.717 E.03903"),
                         "G3 X78.7 Y26 I-.086 J.717")

    def test_keeps_feedrate(self):
        self.assertEqual(strip_e("G1 X1 Y2 E.5 F3000"), "G1 X1 Y2 F3000")

    def test_leaves_a_line_without_e_alone(self):
        self.assertEqual(strip_e("G1 X1 Y2"), "G1 X1 Y2")


class SpansZ(unittest.TestCase):
    def test_bottom_face_is_excluded(self):
        # Support at the plate's own bottom face is the contact carrying it,
        # not a balcony.
        self.assertFalse(PLATE.spans_z(0.0))

    def test_inside_and_top_face_are_included(self):
        self.assertTrue(PLATE.spans_z(2.0))
        self.assertTrue(PLATE.spans_z(4.0))

    def test_the_gap_above_is_excluded(self):
        self.assertFalse(PLATE.spans_z(4.2))


class BedOffset(unittest.TestCase):
    def test_recovers_the_translation_from_outer_walls(self):
        # Model spans -5..5; walls printed at 123..133 means it moved +128.
        lines = gcode("; FEATURE: Outer wall",
                      "G1 X123 Y123 E1", "G1 X133 Y133 E1")
        dx, dy = bed_offset(lines, PLATE, 1.0)
        self.assertAlmostEqual(dx, 128.0)
        self.assertAlmostEqual(dy, 128.0)

    def test_ignores_support_outside_the_object(self):
        # Support sits outside the walls; measuring it would inflate the bbox.
        lines = gcode("; FEATURE: Outer wall", "G1 X123 Y123 E1", "G1 X133 Y133 E1",
                      "; FEATURE: Support", "G1 X200 Y200 E1")
        self.assertAlmostEqual(bed_offset(lines, PLATE, 1.0)[0], 128.0)

    def test_refuses_when_the_footprint_disagrees(self):
        # A 20 mm span cannot be this 10 mm model: rotated, scaled, or not ours.
        lines = gcode("; FEATURE: Outer wall", "G1 X120 Y120 E1", "G1 X140 Y140 E1")
        with self.assertRaises(SystemExit) as e:
            bed_offset(lines, PLATE, 1.0)
        self.assertIn("does not match", str(e.exception))

    def test_refuses_when_there_are_no_walls(self):
        with self.assertRaises(SystemExit):
            bed_offset(gcode("; FEATURE: Support", "G1 X1 Y1 E1"), PLATE, 1.0)


class CheckZ(unittest.TestCase):
    """The footprint check cannot see a height mismatch; this one has to."""

    BOXES = (Box(-5, 5, -5, 5, 0.0, 4.0), Box(-5, 5, -5, 5, 4.2, 8.2))

    def interface_at(self, *zs):
        out = []
        for z in zs:
            out += gcode(f"; Z_HEIGHT: {z}", "; FEATURE: Support interface",
                         "G1 X0 Y0", "G1 X1 Y1 E.5")
        return out

    def test_passes_when_the_gap_layers_line_up(self):
        check_z(self.interface_at(4.2), self.BOXES, 0.2)

    def test_refuses_when_every_height_has_drifted(self):
        # What a 0.4 mm-gap stack looks like against a 0.2 mm plates.json.
        with self.assertRaises(SystemExit) as e:
            check_z(self.interface_at(4.4), self.BOXES, 0.2)
        self.assertIn("different runs", str(e.exception))

    def test_skips_when_there_is_no_interface_to_check(self):
        # Support may legitimately be off; that is not evidence of a mismatch.
        check_z(gcode("; FEATURE: Support", "G1 X0 Y0"), self.BOXES, 0.2)

    def test_tolerates_half_a_layer(self):
        check_z(self.interface_at(4.29), self.BOXES, 0.2)


class Process(unittest.TestCase):
    def run_on(self, lines, margin=0.0):
        return process(lines, (PLATE,), 0.0, 0.0, margin)

    def test_strips_support_inside_the_plate(self):
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G1 X1 Y1 E.5"))
        self.assertEqual(out[-1], "G1 X1 Y1\n")
        self.assertEqual(stat["moves"], 1)
        self.assertAlmostEqual(stat["filament_mm"], 0.5)

    def test_never_touches_the_interface(self):
        # The whole stack exists to keep this film intact.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support interface",
                                      "G1 X0 Y0", "G1 X1 Y1 E.5"))
        self.assertEqual(stat["moves"], 0)
        self.assertEqual(out[-1], "G1 X1 Y1 E.5\n")

    def test_keeps_support_beside_the_plate(self):
        # A ledge column stands outside the footprint and must survive.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X20 Y20", "G1 X21 Y21 E.5"))
        self.assertEqual(stat["moves"], 0)

    def test_keeps_support_below_the_plate(self):
        out, stat = self.run_on(gcode("; Z_HEIGHT: -1.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G1 X1 Y1 E.5"))
        self.assertEqual(stat["moves"], 0)

    def test_keeps_a_move_that_leaves_the_plate(self):
        # Both ends must be inside, or we would cut support we cannot see.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G1 X20 Y20 E.5"))
        self.assertEqual(stat["moves"], 0)

    def test_strips_arcs(self):
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G3 X1 Y1 I-.086 J.717 E.039"))
        self.assertEqual(out[-1], "G3 X1 Y1 I-.086 J.717\n")
        self.assertEqual(stat["moves"], 1)

    def test_leaves_retractions_alone(self):
        # Pure-E moves keep retract and unretract balanced under M83.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G1 E-.8 F1800"))
        self.assertEqual(out[-1], "G1 E-.8 F1800\n")
        self.assertEqual(stat["moves"], 0)

    def test_tracks_position_through_an_omitted_axis(self):
        # "G1 X1 E.5" inherits Y from the previous move; if we lost it the
        # containment test would run against a stale coordinate.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X0 Y0", "G1 X1 E.5"))
        self.assertEqual(stat["moves"], 1)

    def test_tracks_position_through_other_features(self):
        # The nozzle moves during the wall; the next support move starts there.
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0",
                                      "; FEATURE: Outer wall", "G1 X20 Y20 E1",
                                      "; FEATURE: Support", "G1 X21 Y21 E.5"))
        self.assertEqual(stat["moves"], 0)

    def test_margin_reaches_outside_the_plate(self):
        out, stat = self.run_on(gcode("; Z_HEIGHT: 2.0", "; FEATURE: Support",
                                      "G1 X5.5 Y5.5", "G1 X5.6 Y5.6 E.5"), margin=1.0)
        self.assertEqual(stat["moves"], 1)

    def test_counts_distinct_layers(self):
        out, stat = self.run_on(gcode("; FEATURE: Support",
                                      "; Z_HEIGHT: 1.0", "G1 X0 Y0", "G1 X1 Y1 E.5",
                                      "; Z_HEIGHT: 2.0", "G1 X1 Y1", "G1 X2 Y2 E.5"))
        self.assertEqual(stat["layers"], 2)


class EndToEnd(unittest.TestCase):
    def test_edits_the_file_in_place(self):
        doc = {"version": 1, "gap_mm": 0.2, "layer_height": 0.2,
               "bbox": {"x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4},
               "plates": [{"index": 1, "size": "10x10", "flipped": False,
                           "x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4}]}
        with TemporaryDirectory() as d:
            plates = Path(d) / "s.plates.json"
            plates.write_text(json.dumps(doc))
            g = Path(d) / "p.gcode"
            g.write_text("".join(gcode(
                "M83",
                "; FEATURE: Outer wall", "; Z_HEIGHT: 0.2",
                "G1 X123 Y123 E1", "G1 X133 Y133 E1",
                "; FEATURE: Support", "; Z_HEIGHT: 2.0",
                "G1 X128 Y128", "G1 X129 Y129 E.5")))
            self.assertEqual(main([str(plates), str(g)]), 0)
            self.assertIn("G1 X129 Y129\n", g.read_text())

    def test_dry_run_writes_nothing(self):
        doc = {"version": 1, "gap_mm": 0.2, "layer_height": 0.2,
               "bbox": {"x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4},
               "plates": [{"index": 1, "size": "10x10", "flipped": False,
                           "x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4}]}
        with TemporaryDirectory() as d:
            plates = Path(d) / "s.plates.json"
            plates.write_text(json.dumps(doc))
            g = Path(d) / "p.gcode"
            text = "".join(gcode("; FEATURE: Outer wall", "G1 X123 Y123 E1",
                                 "G1 X133 Y133 E1", "; FEATURE: Support",
                                 "; Z_HEIGHT: 2.0", "G1 X128 Y128", "G1 X129 Y129 E.5"))
            g.write_text(text)
            main([str(plates), str(g), "--dry-run"])
            self.assertEqual(g.read_text(), text)

    def test_names_the_cause_when_a_path_does_not_resolve(self):
        # The slicer runs the line from its own working directory (/ when
        # launched from Finder), so a relative path in the settings box is the
        # overwhelmingly likely reason a file is not found.
        with TemporaryDirectory() as d:
            g = Path(d) / "p.gcode"
            g.write_text("")
            with self.assertRaises(SystemExit) as e:
                main(["nope.plates.json", str(g)])
        self.assertIn("absolute", str(e.exception))

    def test_rejects_an_unknown_version(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "s.plates.json"
            p.write_text(json.dumps({"version": 99}))
            with self.assertRaises(SystemExit):
                load_plates(p)


DOC = {"version": 1, "gap_mm": 0.2, "layer_height": 0.2,
       "bbox": {"x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4},
       "plates": [{"index": 1, "size": "10x10", "flipped": False,
                   "x0": -5, "x1": 5, "y0": -5, "y1": 5, "z0": 0, "z1": 4}]}


class Embedding(unittest.TestCase):
    def payload(self) -> str:
        line = embedded_command(DOC)
        blob = line[line.index("('") + 2:line.index("')")]
        return zlib.decompress(base64.b64decode(blob)).decode()

    def test_is_a_single_line(self):
        # run_post_process_scripts splits the field on newlines and runs each
        # piece separately, so a payload with one would be torn into fragments.
        self.assertNotIn("\n", embedded_command(DOC))
        self.assertNotIn("\r", embedded_command(DOC))

    def test_uses_only_shell_safe_characters(self):
        line = embedded_command(DOC)
        blob = line[line.index("('") + 2:line.index("')")]
        self.assertRegex(blob, r"^[A-Za-z0-9+/=]+$")

    def test_payload_compiles(self):
        compile(self.payload(), "<embedded>", "exec")

    def test_future_import_comes_first(self):
        # It is a syntax error anywhere else, and the plate data has to precede
        # the source, so the ordering is load-bearing.
        body = self.payload().lstrip()
        self.assertTrue(body.startswith("from __future__ import annotations"))
        # Counting statements, not mentions: the source also contains the text
        # as a string literal inside embedded_command.
        statements = [l for l in self.payload().splitlines()
                      if l.startswith("from __future__ import annotations")]
        self.assertEqual(len(statements), 1)

    def test_carries_the_plate_data_as_python_not_json(self):
        # json.dumps would write false/true/null, which are not Python names.
        payload = self.payload()
        self.assertIn("EMBEDDED_PLATES = {", payload)
        self.assertNotIn("'flipped': false", payload)

    def test_does_not_re_enter_the_cli(self):
        # Under python3 -c, __name__ is "__main__" as well; the sentinel is what
        # stops argparse running against the slicer's single argument.
        self.assertIn('"EMBEDDED_PLATES" not in globals()', self.payload())


class Install(unittest.TestCase):
    def make_3mf(self, path: Path, settings=b'{"post_process": []}') -> None:
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("3D/3dmodel.model", "<model/>")
            z.writestr(SETTINGS, settings)

    def test_sets_the_setting(self):
        with TemporaryDirectory() as d:
            mf = Path(d) / "p.3mf"
            self.make_3mf(mf)
            install(mf, "the command")
            with zipfile.ZipFile(mf) as z:
                self.assertEqual(json.loads(z.read(SETTINGS))["post_process"],
                                 ["the command"])

    def test_keeps_every_other_entry(self):
        with TemporaryDirectory() as d:
            mf = Path(d) / "p.3mf"
            self.make_3mf(mf)
            install(mf, "x")
            with zipfile.ZipFile(mf) as z:
                self.assertEqual(z.read("3D/3dmodel.model"), b"<model/>")
                self.assertEqual(len(z.infolist()), 2)

    def test_refuses_a_model_only_3mf(self):
        with TemporaryDirectory() as d:
            mf = Path(d) / "p.3mf"
            with zipfile.ZipFile(mf, "w") as z:
                z.writestr("3D/3dmodel.model", "<model/>")
            with self.assertRaises(SystemExit) as e:
                install(mf, "x")
            self.assertIn("Save Project", str(e.exception))

    def test_leaves_no_temp_file_behind(self):
        with TemporaryDirectory() as d:
            mf = Path(d) / "p.3mf"
            self.make_3mf(mf)
            install(mf, "x")
            self.assertEqual([f.name for f in Path(d).iterdir()], ["p.3mf"])


if __name__ == "__main__":
    unittest.main()
