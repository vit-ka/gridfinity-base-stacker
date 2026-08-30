"""Tests for the G-code reader and the interface emitter.

The reader's tests are on hand-written snippets, because the point of them is the
awkward corners of Bambu's dialect -- relative moves inside the filament-change
macro, Z carried on a travel, bare Z-hops, arcs -- and a real sliced file is a
million lines of the easy case.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import emit_interface as ei
import gcode
import verify


def ms(text: str, tool: int = 2):
    return tuple(gcode.moves(text.splitlines(), tool))


class ReaderState(unittest.TestCase):
    def test_state_is_carried_between_moves(self):
        m = ms("G1 X10 Y20 Z1 E1\nG1 X15 E1\n")
        self.assertEqual((m[1].x0, m[1].y1, m[1].z), (10.0, 20.0, 1.0))

    def test_z_on_a_travel_move_sets_the_layer(self):
        m = ms("G1 X1 Y1 Z4.4 F30000\nG1 X2 E0.5\n")
        self.assertEqual(m[1].z, 4.4)

    def test_a_bare_z_hop_does_not_stick(self):
        """The hop goes up and comes back; extrusion happens at the layer's Z."""
        m = ms("G1 X0 Y0 Z4.4\nG1 Z4.8\nG1 X9 Y9 F30000\nG1 Z4.4\nG1 X10 E1\n")
        self.assertEqual(m[-1].z, 4.4)

    def test_relative_positioning_inside_the_change_macro(self):
        m = ms("G1 X10 Y5\nG91\nG1 X3\nG90\nG1 X20\n")
        self.assertEqual([round(v.x1, 3) for v in m], [10.0, 13.0, 20.0])

    def test_arcs_are_followed(self):
        m = ms("G1 X0 Y0 Z1\nG2 X4 Y0 I2 J0 E0.7\n")
        self.assertEqual((m[1].x1, round(m[1].e, 3)), (4.0, 0.7))

    def test_virtual_tools_are_not_filaments(self):
        """T1000, T1100 and T255 are bookkeeping, not a filament change."""
        m = ms("T1000\nG1 X1 E1\nT4\nG1 X2 E1\nT255\nG1 X3 E1\n")
        self.assertEqual([v.tool for v in m], [2, 4, 4])

    def test_extruding_needs_both_filament_and_motion(self):
        m = ms("G1 X1 Y0 Z1 E1\nG1 E1\nG1 X2 E-0.8\nG1 X3\n")
        self.assertEqual([v.extruding for v in m], [True, False, False, False])

    def test_layer_height_gives_the_bottom_of_a_bead(self):
        m = ms("; LAYER_HEIGHT: 0.2\nG1 X1 Y0 Z4.4 E1\n")
        self.assertAlmostEqual(m[0].z0, 4.2)

    def test_comments_set_feature_layer_and_width(self):
        m = ms("; CHANGE_LAYER\n; Z_HEIGHT: 4.4\n; LAYER_HEIGHT: 0.2\n"
               "; layer num/total_layer_count: 22/44\n"
               "; FEATURE: Sparse infill\n; LINE_WIDTH: 0.42\nG1 X1 Y1 Z4.4 E1\n")
        self.assertEqual((m[0].feature, m[0].layer, m[0].layer_z, m[0].width),
                         ("Sparse infill", 22, 4.4, 0.42))


class HeaderParsing(unittest.TestCase):
    TEXT = ("; HEADER_BLOCK_START\n"
            "; model printing time: 26m 35s; total estimated time: 1h 33m 51s\n"
            "; total layer number: 44\n"
            "; total filament length [mm] : 3358.86,285.19\n"
            "; total filament weight [g] : 10.66,0.87\n"
            "; filament_density: 1.3,1.32,1.32,1.26,1.27\n"
            "; filament_diameter: 1.75,1.75,1.75,1.75,1.75\n"
            "; max_z_height: 8.80\n"
            "; filament: 3,5\n"
            "; HEADER_BLOCK_END\n"
            "; some_setting = 1\n")

    def test_two_fields_on_one_line_do_not_run_together(self):
        h = gcode.read_header(self.TEXT.splitlines())
        self.assertEqual(h.seconds, 3600 + 33 * 60 + 51)

    def test_the_rest_of_the_header(self):
        h = gcode.read_header(self.TEXT.splitlines())
        self.assertEqual((h.layers, h.tools, h.max_z), (44, (3, 5), 8.8))
        self.assertAlmostEqual(h.density(4), 1.27)


class GapMeasurement(unittest.TestCase):
    """Two plates with two interface layers between them, at 0.2 clear of each."""
    TEXT = "".join(
        f"; LAYER_HEIGHT: 0.2\n; FEATURE: {feat}\n"
        f"{'T4' if tool == 4 else 'T2'}\nG1 X0 Y0 Z{z}\nG1 X10 Y0 E1\n"
        for z, tool, feat in [(3.8, 2, "Inner wall"), (4.0, 2, "Top surface"),
                              (4.4, 4, "Sparse infill"), (4.6, 4, "Sparse infill"),
                              (5.0, 2, "Bottom surface"), (5.2, 2, "Inner wall")])

    def test_clearances_are_read_from_what_was_printed(self):
        g, = gcode.measure_gaps(ms(self.TEXT), 4)
        self.assertEqual((g.plate_below, g.film_lo, g.film_hi, g.plate_above),
                         (4.0, 4.2, 4.6, 4.8))
        self.assertAlmostEqual(g.below, 0.2)
        self.assertAlmostEqual(g.above, 0.2)
        self.assertEqual(g.layers, 2)

    def test_a_stray_layer_is_its_own_gap_rather_than_being_absorbed(self):
        text = self.TEXT + ("; LAYER_HEIGHT: 0.2\n; FEATURE: Sparse infill\nT4\n"
                            "G1 X0 Y0 Z6.0\nG1 X10 Y0 E1\n")
        self.assertEqual(len(gcode.measure_gaps(ms(text), 4)), 2)

    def test_the_tower_is_not_the_model(self):
        text = ("; LAYER_HEIGHT: 0.2\n; FEATURE: Prime tower\nT4\n"
                "G1 X0 Y0 Z9.0\nG1 X10 Y0 E1\n") + self.TEXT
        self.assertEqual(len(gcode.measure_gaps(ms(text), 4)), 1)


class Emitter(unittest.TestCase):
    CONFIG = ("; HEADER_BLOCK_START\n; total layer number: 3\n"
              "; HEADER_BLOCK_END\n"
              "; CONFIG_BLOCK_START\n"
              "; filament_diameter = 1.75,1.75,1.75,1.75,1.75\n"
              "; filament_flow_ratio = 1,0.98,0.98,0.98,0.95\n"
              "; retraction_length = 0.8\n"
              "; support_interface_speed = 80\n"
              "; travel_speed = 500\n"
              "; CONFIG_BLOCK_END\n")

    def test_config_block_is_read_and_stops_at_its_end(self):
        cfg = ei.config((self.CONFIG + "; not_a_setting = 9\n").splitlines())
        self.assertEqual(cfg["travel_speed"], "500")
        self.assertNotIn("not_a_setting", cfg)
        self.assertNotIn("total layer number", cfg)

    def test_a_setting_written_once_applies_to_every_filament(self):
        """The same key is per-machine in one profile and per-filament in another."""
        cfg = ei.config(self.CONFIG.splitlines())
        self.assertEqual(ei.per_filament(cfg, "retraction_length", 4, "?"), "0.8")
        self.assertEqual(ei.per_filament(cfg, "filament_flow_ratio", 4, "?"), "0.95")
        self.assertEqual(ei.per_filament(cfg, "nothing_here", 4, "?"), "?")

    def test_flow_matches_what_the_slicer_extruded(self):
        """0.45 x 0.20 at a flow ratio of 0.95 measured 0.03217 mm/mm in the
        mesh film's own sliced G-code."""
        self.assertAlmostEqual(ei.flow(0.45, 0.2, 1.75, 0.95), 0.03217, places=4)

    def test_seams_carry_the_tool_the_previous_layer_left_loaded(self):
        text = ("; CHANGE_LAYER\nT2\n; FEATURE: Inner wall\nG1 X0 Y0 Z4.0\n"
                "G1 X9 Y0 E1\n"
                "; CHANGE_LAYER\nT4\n; FEATURE: Support interface\n"
                "G1 X0 Y0 Z4.4\nG1 X9 Y0 E1\n"
                "; CHANGE_LAYER\n; FEATURE: Inner wall\nG1 X0 Y0 Z4.6\n"
                "G1 X9 Y0 E1\n")
        lines = tuple(text.splitlines())
        got = ei.seams(lines, ms(text))
        self.assertEqual([(s.z, s.tool) for s in got], [(4.0, 2), (4.4, 4), (4.6, 4)])

    def test_a_block_prints_at_the_z_it_was_given(self):
        out = ei.block(4.45, 0.25, [(1.0, 2.0, 5.0, 2.0)], 4800, 30000,
                       0.03914, 0.45, "gap 0 layer 0", 0.8, 1.0)
        self.assertNotIn("; FEATURE: Support interface", out)
        m = [v for v in ms("\n".join(out)) if v.extruding]
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].z, 4.45)
        self.assertAlmostEqual(m[0].e, 4.0 * 0.03914, places=4)
        self.assertIn("; Z_HEIGHT: 4.45", out)

    def test_renumber_makes_every_counter_agree(self):
        lines = []
        for i in (1, 2, 3):
            lines += ["; CHANGE_LAYER", f"; Z_HEIGHT: {i * 0.2:g}",
                      f"; layer num/total_layer_count: {i}/9",
                      f"M73 L{i}", f"M991 S0 P{i - 1} ;notify layer change",
                      f"; object ids of layer {i} start: 12",
                      f"; object ids of this layer{i} end: 12"]
        lines = ["; total layer number: 9"] + lines
        out = ei.renumber(lines, 3)
        self.assertIn("; total layer number: 3", out)
        self.assertEqual([v for v in out if v.startswith("M73 L")],
                         ["M73 L1", "M73 L2", "M73 L3"])
        self.assertEqual([v for v in out if v.startswith("; layer num")],
                         [f"; layer num/total_layer_count: {i}/3" for i in (1, 2, 3)])
        self.assertEqual([v for v in out if v.startswith("M991")],
                         [f"M991 S0 P{i} ;notify layer change" for i in (0, 1, 2)])
        self.assertTrue(out[-1].startswith("; object ids of this layer3 end:"))

    def test_plate_coordinates_are_not_machine_coordinates(self):
        """Bambu subtracts the extruder offset when it writes G-code, so an
        object's item transform is 2 mm above where it prints on an X1C."""
        self.assertEqual(ei.machine_offset({"extruder_offset": "0x2"}), (0.0, -2.0))
        self.assertEqual(ei.machine_offset({"extruder_offset": "0x2,0x2"}),
                         (0.0, -2.0))
        self.assertEqual(ei.machine_offset({}), (0.0, 0.0))

    def test_obstruction_ignores_what_is_elsewhere_on_the_plate(self):
        text = ("; FEATURE: Support interface\nT4\nG1 X100 Y100 Z4.4\n"
                "G1 X110 Y100 E1\n")
        got = ms(text)
        self.assertEqual(ei.obstructed(got, 99, 4.2, (120, 90, 180, 160)), 0)
        self.assertEqual(ei.obstructed(got, 99, 4.2, (90, 90, 180, 160)), 1)
        # Below the layer's own floor is what it is meant to be printed onto.
        self.assertEqual(ei.obstructed(got, 99, 4.5, (90, 90, 180, 160)), 0)


# The pipeline's own artefacts, from
#   python3 stack_plates.py models/test-plain.stl --name test-plain --out-dir out \
#       --gap 0.8 --interface-clearance 0.2 --interface-clearance-above 0.1
#   python3 make3mf.py --template templates/stack-template.3mf \
#       --model out/test-plain.stl --plates out/test-plain.plates.json \
#       --interface-plan out/test-plain.interface.json --out out/test-plain.3mf
#   BambuStudio --no-check --outputdir out/slice --slice 0 out/test-plain.3mf
#   python3 emit_interface.py --project out/test-plain.3mf \
#       --gcode out/slice/plate_1.gcode --out out/test-plain.gcode
PROJECT = Path("out/test-plain.3mf")
SLICED = Path("out/slice/plate_1.gcode")
RESULT = Path("out/slice/result.json")
EMITTED = Path("out/test-plain.gcode")


@unittest.skipUnless(SLICED.exists() and RESULT.exists(), f"{SLICED} not sliced")
class AgainstASlicedFile(unittest.TestCase):
    """The reader against a file the slicer wrote, before anything was added.

    Bambu's own header totals exclude part of what its machine start macro
    extrudes and include the rest, so whole-file filament is not reproducible and
    is not asserted. What is asserted is what describes the print: the layer
    count, and the model material in the interface filament, which result.json
    reports separately as `main_used_g` and which the macro does not touch.
    """
    @classmethod
    def setUpClass(cls):
        cls.hdr, _, cls.ms = gcode.read(SLICED)
        cls.plate = json.loads(RESULT.read_text())["sliced_plates"][0]

    def test_layer_count_matches_the_header(self):
        self.assertEqual(max(m.layer for m in self.ms), self.hdr.layers)

    def test_model_material_matches_result_json(self):
        want = {f["id"] - 1: f["main_used_g"] for f in self.plate["filaments"]}
        got = gcode.grams(gcode.model_moves(self.ms), self.hdr)
        tool = max(want)          # the interface filament, the second slot in use
        self.assertAlmostEqual(got[tool], want[tool], places=2)

    def test_the_gaps_are_empty_before_the_interface_is_written(self):
        """No film part any more: what is in a gap is the decoy's support, and it
        is somewhere else on the plate."""
        self.assertTrue(gcode.support_moves(self.ms))


@unittest.skipUnless(EMITTED.exists() and PROJECT.exists(), f"{EMITTED} not emitted")
class AgainstAnEmittedFile(unittest.TestCase):
    """The interface written into a sliced file, read back out of it.

    Generated by `stack_plates.py models/test-plain.stl --gap 0.8
    --interface-clearance 0.2 --interface-clearance-above 0.1`, built with
    `make3mf.py --interface-plan`, sliced, and emitted.
    """
    @classmethod
    def setUpClass(cls):
        cls.plan = ei.load_plan(PROJECT)
        cls.hdr, cls.lines, cls.ms = gcode.read(EMITTED)
        beads = [b for l in cls.plan["layers"] for b in l["beads"]]
        cls.box = (min(min(b[0], b[2]) for b in beads),
                   min(min(b[1], b[3]) for b in beads),
                   max(max(b[0], b[2]) for b in beads),
                   max(max(b[1], b[3]) for b in beads))

    def test_every_interface_layer_is_at_the_z_it_was_planned_for(self):
        want = sorted(round(l["z1"], 4) for l in self.plan["layers"])
        got = sorted({round(m.z, 4) for m in
                      gcode.model_moves(self.ms, self.box)
                      if m.tool == self.plan["interface_extruder"] - 1})
        self.assertEqual(got, want)

    def test_the_clearances_are_the_ones_that_were_asked_for(self):
        g, = gcode.measure_gaps(self.ms, self.plan["interface_extruder"] - 1,
                                box=self.box)
        self.assertAlmostEqual(g.below, 0.2, places=6)
        self.assertAlmostEqual(g.above, 0.1, places=6)

    def test_the_clearance_above_is_not_a_multiple_of_the_layer_height(self):
        """Which is the whole reason the interface stopped being a mesh."""
        g, = gcode.measure_gaps(self.ms, self.plan["interface_extruder"] - 1,
                                box=self.box)
        self.assertNotAlmostEqual(g.above % 0.2, 0.0, places=6)

    def test_the_interface_filament_is_loaded_before_every_interface_layer(self):
        tool = self.plan["interface_extruder"] - 1
        for lay in self.plan["layers"]:
            before = [m for m in self.ms if m.extruding and m.z < lay["z1"] - 1e-6]
            self.assertEqual(before[-1].tool, tool,
                             f"layer at {lay['z1']} follows T{before[-1].tool}")

    def test_the_layer_counters_are_consistent_end_to_end(self):
        n = sum(1 for ln in self.lines if ln.startswith("; CHANGE_LAYER"))
        self.assertEqual(self.hdr.layers, n)
        self.assertEqual([ln for ln in self.lines if ln.startswith("; layer num")],
                         [f"; layer num/total_layer_count: {i}/{n}"
                          for i in range(1, n + 1)])
        self.assertEqual(max(int(ln[5:]) for ln in self.lines
                             if ln.startswith("M73 L")), n)

    def test_z_never_goes_backwards(self):
        zs = [round(m.z, 4) for m in self.ms if m.extruding
              and m.feature not in ("Custom", "")]
        self.assertEqual(zs, sorted(zs), "an inserted layer prints below one already down")

    def test_no_support_lands_on_the_stack(self):
        inside = gcode.model_moves(gcode.support_moves(self.ms), self.box)
        self.assertEqual(inside, ())


@unittest.skipUnless(EMITTED.exists() and PROJECT.exists(), f"{EMITTED} not emitted")
class WrongHeightIsAnError(unittest.TestCase):
    """A layer at the wrong Z is the failure this change exists to prevent.

    Verified by putting one there: the emitted file is copied with a single
    interface layer moved off its planned height, and the check has to catch it
    and say which gap and which two numbers.
    """
    def corrupt(self, shift: float) -> tuple[list[str], list[str]]:
        plan = ei.load_plan(PROJECT)
        target = max(l["z1"] for l in plan["layers"])
        out = []
        for ln in EMITTED.read_text().splitlines():
            if ln.startswith(f"; Z_HEIGHT: {target:g}"):
                out.append(f"; Z_HEIGHT: {target + shift:g}")
            elif ln.strip() == f"G1 Z{target:g}":
                out.append(f"G1 Z{target + shift:g}")
            else:
                out.append(ln)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.gcode"
            bad.write_text("\n".join(out) + "\n")
            return verify.check_interface(PROJECT, bad)

    def test_the_good_file_passes(self):
        _, fails = verify.check_interface(PROJECT, EMITTED)
        self.assertEqual(fails, [])

    def test_a_layer_off_by_fifty_microns_is_caught(self):
        plan = ei.load_plan(PROJECT)
        target = max(l["z1"] for l in plan["layers"])
        _, fails = self.corrupt(0.05)
        self.assertEqual(len(fails), 1)
        self.assertIn(f"z={target:g}", fails[0])           # what was asked for
        self.assertIn(f"z={target + 0.05:g}", fails[0])    # what is in the file
        self.assertIn("gap 1", fails[0])                   # and where

    def test_a_layer_off_by_one_micron_is_caught(self):
        """The clearance is claimed to the micron, so the check holds to it."""
        _, fails = self.corrupt(0.001)
        self.assertEqual(len(fails), 1)


@unittest.skipUnless(EMITTED.exists() and PROJECT.exists(), f"{EMITTED} not emitted")
class InterfaceOffTheStackIsAnError(unittest.TestCase):
    """An interface that misses the model is the failure no clearance check sees.

    Every clearance is measured inside the interface's own footprint, so an
    interface shifted bodily off the stack measures perfectly against itself.
    That is not hypothetical: the plate-to-machine offset was missed and the
    first real file had the interface 2 mm off the plates, passing every check
    that existed. This one reads the plates' own printed footprint.
    """
    def shifted(self, dy: float):
        """The emitted file with only the interface's own beads moved in Y."""
        out, inside = [], False
        for ln in EMITTED.read_text().splitlines():
            if ln.startswith("; FEATURE: Interface"):
                inside = True
            elif ln.startswith("; CHANGE_LAYER"):
                inside = False
            if inside and ln.startswith("G1 ") and " Y" in ln:
                ln = re.sub(r"Y(-?\d*\.?\d+)",
                            lambda m: f"Y{float(m.group(1)) + dy:.3f}", ln)
            out.append(ln)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.gcode"
            bad.write_text("\n".join(out) + "\n")
            return verify.check_interface(PROJECT, bad)

    def test_the_good_file_lands_on_the_plates(self):
        lines, fails = verify.check_interface(PROJECT, EMITTED)
        self.assertEqual(fails, [])
        self.assertTrue(any("on the plates" in ln for ln in lines))

    def test_two_millimetres_off_is_caught(self):
        """The exact miss that shipped, in the exact direction."""
        _, fails = self.shifted(2.0)
        self.assertTrue(fails)
        self.assertIn("not on the stack", " ".join(fails))

    def test_a_shift_inside_the_flare_is_not_flagged(self):
        """The film leans outward on purpose; that is not a miss."""
        _, fails = self.shifted(0.1)
        self.assertEqual(fails, [])


if __name__ == "__main__":
    unittest.main()
