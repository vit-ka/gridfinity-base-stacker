"""Tests. Run with: python3 -m unittest -v test_stack_plates"""
from __future__ import annotations

import itertools
import json
import math
import re
import zipfile
import tempfile
import unittest
from pathlib import Path

import check_settings as cs
import gridfinity as gf
import stack_plates as sp
import stl_io


def perforated_plate(w_cells: int, d_cells: int, pitch: float = 42.0,
                     thickness: float = 4.0, bottom_open: float = 36.0,
                     top_open: float = 40.0, taper: float = 1.4,
                     origin: tuple[float, float] = (0.0, 0.0)) -> stl_io.Mesh:
    """A slab with a grid of tapered square through-holes.

    Not a real Gridfinity socket, but it shares the properties the code relies on:
    flat top and bottom faces, a regular hole lattice, and a funnel near the top.
    """
    ox, oy = origin
    w, d = w_cells * pitch, d_cells * pitch
    mesh: list = []
    # Outer slab as a box, then subtract nothing -- instead build the plate as a
    # union of boxes forming the rib lattice, which keeps the mesh trivially valid
    # for the flat-face and lattice queries under test.
    for i in range(w_cells):
        for j in range(d_cells):
            cx = ox + (i + 0.5) * pitch
            cy = oy + (j + 0.5) * pitch
            for z0, z1, opening in ((0.0, thickness - taper, bottom_open),
                                    (thickness - taper, thickness, top_open)):
                h = opening / 2
                half = pitch / 2
                # four rib segments around this cell's hole
                mesh += stl_io.box(cx - half, cy - half, z0, cx + half, cy - h, z1)
                mesh += stl_io.box(cx - half, cy + h, z0, cx + half, cy + half, z1)
                mesh += stl_io.box(cx - half, cy - h, z0, cx - h, cy + h, z1)
                mesh += stl_io.box(cx + h, cy - h, z0, cx + half, cy + h, z1)
    return tuple(mesh)


def point_inside(mesh: stl_io.Mesh, px: float, py: float, pz: float) -> bool:
    """Majority of three jittered rays.

    A single ray on a cell's plane of symmetry passes through shared vertices of
    the tessellation and double-counts, reading solid where the cell is open.
    """
    votes = sum(
        sum(1 for x in gf._ray_crossings(mesh, py + dy, pz) if x > px) % 2
        for dy in (-0.017, 0.0, 0.019)
    )
    return votes >= 2


class TestStlIo(unittest.TestCase):
    def test_box_winding_is_outward(self):
        self.assertAlmostEqual(stl_io.signed_volume(stl_io.box(0, 0, 0, 2, 3, 4)), 24.0)

    def test_roundtrip_preserves_facets(self):
        mesh = stl_io.box(0, 0, 0, 1, 1, 1)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.stl"
            stl_io.write_stl(p, mesh, "test")
            self.assertEqual(stl_io.read_stl(p), mesh)

    def test_rotate_x180_preserves_volume_and_winding(self):
        mesh = stl_io.box(1, 2, 3, 5, 7, 11)
        rotated = stl_io.rotate_x180(mesh)
        self.assertAlmostEqual(stl_io.signed_volume(mesh),
                               stl_io.signed_volume(rotated))
        self.assertGreater(stl_io.signed_volume(rotated), 0)

    def test_rotate_x180_negates_y_and_z(self):
        b = stl_io.bounds_of(stl_io.rotate_x180(stl_io.box(1, 2, 3, 5, 7, 11)))
        self.assertEqual((b.x0, b.x1), (1, 5))
        self.assertEqual((b.y0, b.y1), (-7, -2))
        self.assertEqual((b.z0, b.z1), (-11, -3))

    def test_split_shells_separates_and_orders_by_size(self):
        small = stl_io.box(0, 0, 0, 1, 1, 1)
        big = stl_io.box(10, 0, 0, 12, 2, 2) + stl_io.box(12, 0, 0, 14, 2, 2)
        shells = stl_io.split_shells(small + big)
        self.assertEqual(len(shells), 2)
        self.assertEqual(len(shells[0]), len(big))
        self.assertEqual(len(shells[1]), len(small))

    def test_rejects_ascii_stl(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.stl"
            p.write_text("solid x\n facet normal 0 0 1\n" + "x" * 200)
            with self.assertRaises(ValueError):
                stl_io.read_stl(p)


class TestLattice(unittest.TestCase):
    def setUp(self):
        self.mesh = perforated_plate(4, 3)
        self.lat = gf.detect_lattice(self.mesh)

    def test_finds_every_cell(self):
        self.assertIsNotNone(self.lat)
        self.assertEqual(self.lat.pitch, 42.0)
        self.assertEqual((len(self.lat.xs), len(self.lat.ys)), (4, 3))

    def test_measures_both_openings(self):
        self.assertAlmostEqual(self.lat.bottom_opening, 36.0, places=2)
        self.assertAlmostEqual(self.lat.top_opening, 40.0, places=2)

    def test_z_levels(self):
        self.assertEqual(gf.z_levels(self.mesh), (0.0, 2.6, 4.0))

    def test_phase_ignores_an_off_lattice_partial_row(self):
        lat = gf.Lattice(42.0, (21.0, 63.0, 105.0), (21.0, 63.0, 105.0, 135.0), 36.0, 40.0)
        self.assertEqual(lat.phase_y, 21.0)   # not 135 % 42 == 9

    def test_mirrored_y_negates_rows(self):
        lat = gf.Lattice(42.0, (21.0,), (21.0, 63.0), 36.0, 40.0)
        self.assertEqual(lat.mirrored_y().ys, (-63.0, -21.0))

    def test_returns_none_without_a_lattice(self):
        self.assertIsNone(gf.detect_lattice(stl_io.box(0, 0, 0, 50, 50, 4)))


# Levels and mid-cell radii measured off the real extended baseplate.
REAL_SOCKET = ((0.0, 0.79, 2.59, 4.0),
               (18.07, 18.84, 18.85, 18.85, 18.86, 20.25))


class TestOverhang(unittest.TestCase):
    def test_vertical_wall(self):
        self.assertAlmostEqual(sp.overhang_from_sections((0.0, 4.0), (18.0, 18.0)), 90.0)

    def test_45_degree_taper(self):
        self.assertAlmostEqual(sp.overhang_from_sections((0.0, 1.4), (18.0, 19.4), skin=0.0), 45.0)

    def test_shallow_taper(self):
        self.assertAlmostEqual(sp.overhang_from_sections((0.0, 1.0), (18.0, 20.0), skin=0.0),
                               math.degrees(math.atan2(1.0, 2.0)), places=6)

    def test_a_step_is_a_flat_ledge(self):
        """A widening step is a horizontal overhang once the plate is flipped."""
        self.assertAlmostEqual(
            sp.overhang_from_sections((0.0, 2.6, 4.0), (18.0, 18.0, 19.4, 19.4)), 0.0)

    def test_takes_the_shallowest_of_several_walls(self):
        self.assertAlmostEqual(
            sp.overhang_from_sections((0.0, 1.4, 4.0), (18.0, 19.4, 19.4, 20.0), skin=0.0),
            45.0, places=6)

    def test_matches_the_real_gridfinity_socket(self):
        """Levels and radii as measured off the baseplate: two 45 degree chamfers."""
        self.assertAlmostEqual(sp.overhang_from_sections(*REAL_SOCKET), 45.0, places=1)

    def test_is_not_measured_against_the_bottom_face(self):
        """Regression: using the bottom-face rib understates the funnel angle."""
        naive = math.degrees(math.atan2(1.41, 20.25 - 18.07))
        self.assertGreater(sp.overhang_from_sections(*REAL_SOCKET), naive + 10)

    def test_a_taper_crossing_a_level_is_not_a_ledge(self):
        """Regression: sampling inset from each level makes a taper drift by `skin`.

        Read literally that drift is a step, and a clean 45 degree socket reports
        as a 0 degree overhang -- which would send the threshold advice backwards.
        """
        self.assertGreater(sp.overhang_from_sections(*REAL_SOCKET), 40.0)

    def test_reads_the_synthetic_plate_as_a_stepped_socket(self):
        plate = sp.build_plate(perforated_plate(3, 3))
        self.assertAlmostEqual(sp.steepest_overhang(plate), 0.0)


class TestSnap(unittest.TestCase):
    def test_exact_when_already_on_lattice(self):
        self.assertAlmostEqual(sp.snap(-10.0, 31.0, 21.0, 42.0), -10.0)

    def test_takes_the_shorter_direction(self):
        self.assertAlmostEqual(sp.snap(0.0, 22.0, 21.0, 42.0), -1.0)
        self.assertAlmostEqual(sp.snap(0.0, 20.0, 21.0, 42.0), 1.0)

    def test_never_shifts_more_than_half_a_pitch(self):
        for coord in range(0, 84):
            self.assertLessEqual(abs(sp.snap(0.0, float(coord), 21.0, 42.0)), 21.0)

    def test_result_lands_on_the_lattice(self):
        for coord in range(0, 84):
            shift = sp.snap(-7.5, float(coord), 21.0, 42.0)
            self.assertAlmostEqual((coord + shift - 21.0) % 42.0, 0.0, places=9)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.plates = tuple(sp.build_plate(perforated_plate(w, d, origin=(o, o)))
                            for w, d, o in ((4, 3, 0.0), (3, 3, 5.0), (3, 2, 11.0)))

    def test_orders_largest_footprint_first(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        areas = [p.plate.bounds.footprint for p in pl]
        self.assertEqual(areas, sorted(areas, reverse=True))

    def test_alternates_orientation_starting_upright(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        self.assertEqual([p.flipped for p in pl], [False, True, False])

    def test_no_flip_keeps_every_plate_upright(self):
        pl = sp.plan(self.plates, 0.8, flip=False, register=True)
        self.assertEqual([p.flipped for p in pl], [False, False, False])

    def test_z_offsets_stack_by_height_plus_gap(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        self.assertAlmostEqual(pl[0].z0, 0.0)
        self.assertAlmostEqual(pl[1].z0, 4.8)
        self.assertAlmostEqual(pl[2].z0, 9.6)

    def test_interfaces_are_matched_faces(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        for lower, upper in sp.interfaces(pl):
            self.assertEqual(lower.up_face, upper.down_face)

    def test_no_flip_gives_mismatched_faces(self):
        pl = sp.plan(self.plates, 0.8, flip=False, register=True)
        lower, upper = sp.interfaces(pl)[0]
        self.assertNotEqual(lower.up_face, upper.down_face)

    def test_registration_aligns_every_lattice(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        for lower, upper in sp.interfaces(pl):
            ex, ey = sp.registration_error(lower, upper)
            self.assertAlmostEqual(ex, 0.0, places=6)
            self.assertAlmostEqual(ey, 0.0, places=6)

    def test_no_register_leaves_lattices_offset(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=False)
        errors = [max(sp.registration_error(lo, up)) for lo, up in sp.interfaces(pl)]
        self.assertGreater(max(errors), 0.05)

    def test_placed_mesh_sits_at_its_z_offset(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        for p in pl:
            b = stl_io.bounds_of(p.placed_mesh())
            self.assertAlmostEqual(b.z0, p.z0, places=6)
            self.assertAlmostEqual(b.z1, p.z1, places=6)

    def test_flipped_plate_keeps_outward_winding(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        flipped = next(p for p in pl if p.flipped)
        self.assertGreater(stl_io.signed_volume(flipped.placed_mesh()), 0)

    def test_flipping_preserves_volume(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        for p in pl:
            self.assertAlmostEqual(stl_io.signed_volume(p.placed_mesh()),
                                   stl_io.signed_volume(p.plate.mesh), places=3)

    def test_holes_of_a_flipped_plate_track_the_mesh(self):
        pl = sp.plan(self.plates, 0.8, flip=True, register=True)
        p = next(q for q in pl if q.flipped)
        b = stl_io.bounds_of(p.placed_mesh())
        for hx, hy in p.holes():
            self.assertGreater(hx, b.x0)
            self.assertLess(hx, b.x1)
            self.assertGreater(hy, b.y0)
            self.assertLess(hy, b.y1)


class TestOpeningAt(unittest.TestCase):
    def test_none_inside_solid_material(self):
        self.assertIsNone(gf._opening_at(stl_io.box(0, 0, 0, 100, 100, 4), 50, 37.3, 1.7))

    def test_measures_a_void(self):
        mesh = stl_io.box(0, 0, 0, 30, 100, 4) + stl_io.box(70, 0, 0, 100, 100, 4)
        self.assertAlmostEqual(gf._opening_at(mesh, 50, 37.3, 1.7), 40.0)

    def test_a_closed_rim_is_not_an_opening(self):
        """Regression: a solid span must never be reported as a cell opening.

        Scanning every consecutive crossing pair rather than the odd-indexed ones
        reads a plate-wide rim as a plate-wide hole, and the blocker built from it
        covers the whole plate.
        """
        plate = perforated_plate(2, 2)                       # spans 0..84 in x and y
        rim = stl_io.box(0.0, 84.0, 0.0, 84.0, 88.0, 4.0)    # closes the top edge
        self.assertIsNone(gf._opening_at(plate + rim, 21.0, 85.3, 1.7))

    def test_still_finds_the_cell_below_the_rim(self):
        plate = perforated_plate(2, 2)
        rim = stl_io.box(0.0, 84.0, 0.0, 84.0, 88.0, 4.0)
        self.assertAlmostEqual(gf._opening_at(plate + rim, 21.0, 22.3, 1.7), 36.0, places=2)


class TestOrdering(unittest.TestCase):

    def test_no_total_containment_order_exists_for_this_set(self):
        """216x126 and 174x144 are incomparable, so some ledge is unavoidable."""
        def contains(a, b):
            return a[0] >= b[0] and a[1] >= b[1]
        pair = ((216, 126), (174, 144))
        self.assertFalse(contains(*pair))
        self.assertFalse(contains(*reversed(pair)))

    def test_picks_the_least_ledge_order(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((5, 4), (5, 3), (4, 4), (4, 3)))
        order = sp.order_plates(plates)
        cost = sum(sp.uncovered(lo, up) for lo, up in zip(order, order[1:]))
        best = min(sum(sp.uncovered(lo, up) for lo, up in zip(perm, perm[1:]))
                   for perm in itertools.permutations(plates))
        self.assertAlmostEqual(cost, best)

    def test_largest_plate_goes_on_the_bed(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((4, 3), (5, 4), (4, 4)))
        order = sp.order_plates(plates)
        self.assertEqual(order[0].bounds.footprint,
                         max(p.bounds.footprint for p in plates))

    def test_beats_plain_area_order_on_the_real_set(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((5, 4), (5, 3), (4, 4), (4, 3)))
        by_area = tuple(sorted(plates, key=lambda p: p.bounds.footprint, reverse=True))
        nested = sp.order_plates(plates)
        cost = lambda o: sum(sp.uncovered(lo, up) for lo, up in zip(o, o[1:]))
        self.assertLessEqual(cost(nested), cost(by_area))


class TestNestingGroups(unittest.TestCase):
    def plates(self, sizes):
        return tuple(sp.build_plate(perforated_plate(w, d)) for w, d in sizes)

    def test_a_fully_nested_set_stays_one_stack(self):
        groups = sp.nesting_groups(self.plates(((5, 4), (4, 4), (4, 3))))
        self.assertEqual(len(groups), 1)

    def test_incomparable_plates_are_split_apart(self):
        """5x3 and 4x4 contain neither one another, so they cannot share a stack."""
        groups = sp.nesting_groups(self.plates(((5, 4), (5, 3), (4, 4), (4, 3))))
        self.assertEqual(len(groups), 2)

    def test_every_plate_appears_exactly_once(self):
        plates = self.plates(((5, 4), (5, 3), (4, 4), (4, 3), (3, 3)))
        got = [p for g in sp.nesting_groups(plates) for p in g]
        self.assertEqual(len(got), len(plates))
        self.assertEqual({id(p) for p in got}, {id(p) for p in plates})

    def test_each_group_nests_with_no_ledge(self):
        plates = self.plates(((5, 4), (5, 3), (4, 4), (4, 3), (3, 3)))
        for group in sp.nesting_groups(plates):
            for lo, up in zip(group, group[1:]):
                self.assertTrue(sp.contains(lo, up))
            self.assertEqual(sp.ledges(sp.plan(group, 0.2, True, True), 0.2), ())

    def test_uses_the_fewest_stacks_possible(self):
        """Dilworth: the fewest chains equals the largest set of mutually
        incomparable plates. Here 5x3 and 4x4 are the only such pair."""
        plates = self.plates(((5, 4), (5, 3), (4, 4), (4, 3)))
        self.assertEqual(len(sp.nesting_groups(plates)), 2)

    def test_largest_plate_leads_its_stack(self):
        plates = self.plates(((4, 3), (5, 4), (4, 4)))
        for group in sp.nesting_groups(plates):
            self.assertEqual(group[0].bounds.footprint,
                             max(p.bounds.footprint for p in group))


class TestLedgeFillers(unittest.TestCase):
    def stack(self):
        # 5x3 and 4x4 are incomparable, so this set always leaves a ledge
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((5, 4), (5, 3), (4, 4), (4, 3)))
        return sp.plan(plates, 0.2, flip=True, register=True)

    def test_a_ledge_produces_fillers(self):
        pl = self.stack()
        self.assertTrue(sp.ledges(pl, 0.2))
        self.assertTrue(sp.ledge_fillers(pl, 0.2))

    def test_no_ledge_produces_none(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((5, 4), (4, 4), (4, 3)))
        pl = sp.plan(plates, 0.2, flip=True, register=True)
        self.assertEqual(sp.ledges(pl, 0.2), ())
        self.assertEqual(sp.ledge_fillers(pl, 0.2), ())

    def test_fillers_clear_every_plate(self):
        """The whole point: nothing may fuse to a plate."""
        pl = self.stack()
        mesh = sp.ledge_fillers(pl, 0.2)
        boxes = [mesh[i:i + 12] for i in range(0, len(mesh), 12)]
        plates = [stl_io.bounds_of(p.placed_mesh()) for p in pl]
        for b in (stl_io.bounds_of(x) for x in boxes):
            for pb in plates:
                overlap = (min(b.z1, pb.z1) - max(b.z0, pb.z0) > 1e-6
                           and min(b.x1, pb.x1) - max(b.x0, pb.x0) > 1e-6
                           and min(b.y1, pb.y1) - max(b.y0, pb.y0) > 1e-6)
                self.assertFalse(overlap, "a filler intersects a plate")

    def test_fillers_take_whole_plate_levels(self):
        """Each block occupies one plate's z range, so the stack gaps clear it."""
        pl = self.stack()
        levels = {(round(p.z0, 6), round(p.z1, 6)) for p in pl}
        mesh = sp.ledge_fillers(pl, 0.2)
        for i in range(0, len(mesh), 12):
            b = stl_io.bounds_of(mesh[i:i + 12])
            self.assertIn((round(b.z0, 6), round(b.z1, 6)), levels)

    def test_projects_the_face_directly_above_the_filler(self):
        """The near face, not the plate's widest section.

        The socket tapers, so projecting the wide end makes a filler broader than
        both the face it carries and the one it stands on, and its own footprint
        then needs bridging support: measured 6.0 g of support against 5.6 g.
        """
        for p in self.stack():
            b = stl_io.bounds_of(p.placed_mesh())
            spans = sp.solid_spans(p, b.x0, b.y0, b.x1, b.y1, step=1.0)
            covered = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in spans)
            self.assertLess(abs(covered - p.down_area), abs(covered - p.up_area),
                            f"{p.plate.label} did not follow its down face")

    def test_grown_slightly_by_default(self):
        """A faithful projection reproduces webs the slicer drops as too thin.

        0.5 mm, about two perimeters, keeps them. Much more and the webs double
        and the rounded socket corners fill in.
        """
        pl = self.stack()
        faithful = sp.ledge_fillers(pl, 0.2, grow=0.0)
        default = sp.ledge_fillers(pl, 0.2)
        self.assertGreater(stl_io.signed_volume(default),
                           stl_io.signed_volume(faithful))

    def test_dilation_is_a_disc_not_a_square(self):
        """A square element offsets corners by r on both axes at once, squaring
        off the sockets; a disc offsets every direction equally."""
        pl = self.stack()
        grown = sp.ledge_fillers(pl, 0.2, grow=1.0)
        square_area = stl_io.signed_volume(sp.ledge_fillers(pl, 0.2, grow=0.0))
        # a disc adds less than the square of the same radius would
        self.assertLess(stl_io.signed_volume(grown), square_area * 4)

    def test_fillers_are_absent_when_asked(self):
        mesh = (perforated_plate(5, 4) + perforated_plate(5, 3, origin=(500.0, 0.0))
                + perforated_plate(4, 4, origin=(0.0, 500.0)))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            a, b = Path(td) / "a", Path(td) / "b"
            sp.main([str(src), "-o", str(a)])
            sp.main([str(src), "-o", str(b), "--no-fillers"])
            self.assertGreater((a / "gf-stack-3.stl").stat().st_size,
                               (b / "gf-stack-3.stl").stat().st_size)


class TestUnsupported(unittest.TestCase):
    def test_fully_covered_is_free(self):
        rect = (0.0, 0.0, 10.0, 10.0)
        below = (((0.0, 0.0, 20.0, 20.0), 4.0),)
        self.assertEqual(sp.unsupported(rect, 4.8, below, 0.8), (0.0, 0.0, 0.0))

    def test_measures_the_strip_that_hangs(self):
        rect = (0.0, 0.0, 10.0, 10.0)
        below = (((0.0, 0.0, 10.0, 6.0), 4.0),)     # 4 mm shallower
        area, volume, drop = sp.unsupported(rect, 4.8, below, 0.8)
        self.assertAlmostEqual(area, 40.0)          # 10 x 4
        self.assertAlmostEqual(drop, 4.8)           # all the way to the bed
        self.assertAlmostEqual(volume, 40.0 * 4.8)

    def test_reaches_only_to_the_highest_plate_underneath(self):
        rect = (0.0, 0.0, 10.0, 10.0)
        below = (((0.0, 0.0, 20.0, 20.0), 4.0), ((0.0, 0.0, 10.0, 6.0), 8.8))
        area, volume, drop = sp.unsupported(rect, 9.6, below, 0.8)
        self.assertAlmostEqual(area, 40.0)
        self.assertAlmostEqual(drop, 5.6)           # down to the 4.0 plate, not the bed

    def test_a_normal_gap_is_not_a_ledge(self):
        rect = (0.0, 0.0, 10.0, 10.0)
        below = (((0.0, 0.0, 10.0, 10.0), 4.0),)
        self.assertEqual(sp.unsupported(rect, 4.8, below, 0.8)[0], 0.0)


class TestSupportEstimate(unittest.TestCase):
    def test_gap_is_costed_as_solid_not_sparse(self):
        """Regression: the gap prints as solid interface.

        Costing it at a sparse density understated support several times over.
        """
        plates = tuple(sp.build_plate(perforated_plate(w, d)) for w, d in ((4, 3), (3, 3)))
        pl = sp.plan(plates, 0.8, flip=True, register=True)
        interface, _, _ = sp.support_estimate(pl, 0.8)
        contact = min(pl[0].up_area, pl[1].down_area)
        self.assertAlmostEqual(interface, contact * 0.8)

    def test_grams_include_the_columns(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((5, 4), (5, 3), (4, 4)))
        pl = sp.plan(plates, 0.8, flip=True, register=True)
        iface, cols, grams = sp.support_estimate(pl, 0.8)
        self.assertAlmostEqual(grams, (iface + cols * sp.SPARSE) * sp.PLA_DENSITY)


class TestBlockers(unittest.TestCase):
    """Blockers are one slab per plate, not one solid per socket.

    Tracing sockets was abandoned: support collects in the four-way rib junctions
    between cells, outside any socket outline however finely traced. A slab
    spanning a plate's thickness covers everything inside it at once.
    """

    def setUp(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((4, 3), (3, 3), (3, 2)))
        self.pl = sp.plan(plates, 0.4, flip=True, register=True)
        self.slabs = stl_io.split_shells(sp.make_blockers(self.pl, 0.4, layer=0.2))

    def test_one_slab_per_plate(self):
        big = [s for s in self.slabs if stl_io.bounds_of(s).width > 1]
        self.assertEqual(len(big), len(self.pl))

    def test_each_slab_starts_one_layer_above_its_plate(self):
        """The offset at the bottom is required, not slop.

        Blockers are subtracted from the *overhang* polygons at the layer where
        the overhang is found, and the support for an overhang is printed below
        it. A plate's interface comes from the overhang at its first layer, so a
        slab covering that layer deletes the interface.
        """
        big = sorted((stl_io.bounds_of(s) for s in self.slabs
                      if stl_io.bounds_of(s).width > 1), key=lambda b: b.z0)
        for b, pl in zip(big, self.pl):
            self.assertAlmostEqual(b.z0, pl.z0 + 0.2, places=6)
            self.assertAlmostEqual(b.z1, pl.z1, places=6)

    def test_slabs_leave_the_gaps_open(self):
        big = sorted((stl_io.bounds_of(s) for s in self.slabs
                      if stl_io.bounds_of(s).width > 1), key=lambda b: b.z0)
        for a, b in zip(big, big[1:]):
            self.assertGreater(b.z0 - a.z1, 0.0)

    def test_a_negative_inset_is_refused(self):
        """At or below zero the slabs meet across the gaps and take the interface."""
        with self.assertRaises(ValueError):
            sp.make_blockers(self.pl, 0.4, layer=-0.1)

    def test_bbox_matches_the_model_on_all_three_axes(self):
        """Bambu centres a loaded part on the object; a mismatch silently
        displaces it. Pinning only X and Y left blockers 2 mm out in Z."""
        model = stl_io.bounds_of(tuple(f for pl in self.pl for f in pl.placed_mesh()))
        b = stl_io.bounds_of(sp.make_blockers(self.pl, 0.4, layer=0.2))
        self.assertAlmostEqual(b.cx, model.cx, places=6)
        self.assertAlmostEqual(b.cy, model.cy, places=6)
        self.assertAlmostEqual((b.z0 + b.z1) / 2, (model.z0 + model.z1) / 2, places=6)


class TestEnforcers(unittest.TestCase):
    def setUp(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((4, 3), (3, 3), (3, 2)))
        self.pl = sp.plan(plates, 0.4, flip=True, register=True)

    def test_slabs_bracket_each_plate_first_layer(self):
        """An enforcer's contact is model material at a layer minus material
        below it, so it must sit on the plate's first layer to mean anything."""
        mesh = sp.make_enforcers(self.pl, layer=0.2)
        big = sorted((stl_io.bounds_of(mesh[i:i+12])
                      for i in range(0, len(mesh), 12)
                      if stl_io.bounds_of(mesh[i:i+12]).width > 1), key=lambda b: b.z0)
        self.assertTrue(big)
        for b in big:
            self.assertTrue(any(b.z0 < pl.z0 < b.z1 for pl in self.pl))

    def test_nothing_for_the_bottom_plate(self):
        """It sits on the bed and needs no support."""
        mesh = sp.make_enforcers(self.pl, layer=0.2)
        for i in range(0, len(mesh), 12):
            b = stl_io.bounds_of(mesh[i:i+12])
            if b.width > 1:
                self.assertGreater(b.z1, self.pl[0].z1)

    def test_bbox_matches_the_model_on_all_three_axes(self):
        model = stl_io.bounds_of(tuple(f for pl in self.pl for f in pl.placed_mesh()))
        b = stl_io.bounds_of(sp.make_enforcers(self.pl, layer=0.2))
        self.assertAlmostEqual(b.cx, model.cx, places=6)
        self.assertAlmostEqual(b.cy, model.cy, places=6)
        self.assertAlmostEqual((b.z0 + b.z1) / 2, (model.z0 + model.z1) / 2, places=6)


class TestCli(unittest.TestCase):
    def test_end_to_end_writes_all_three_outputs(self):
        mesh = (perforated_plate(4, 3, origin=(0.0, 0.0))
                + perforated_plate(3, 3, origin=(500.0, 0.0)))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            out = Path(td) / "out"
            self.assertEqual(sp.main([str(src), "-o", str(out), "--blockers"]), 0)
            self.assertTrue((out / "gf-stack-2.stl").exists())
            self.assertTrue((out / "gf-stack-2-blockers.stl").exists())
            notes = (out / "gf-stack-2-PRINTING.md").read_text()
            self.assertIn("land-to-land", notes)
            self.assertIn("Threshold angle", notes)

    def test_notes_have_no_unrendered_placeholders(self):
        """Regression: a section substituted as a value keeps its own braces.

        PETG_FILAMENT is inserted into the template as a value, so its {layer}
        never went through .format() and shipped literally.
        """
        mesh = perforated_plate(4, 3) + perforated_plate(3, 3, origin=(500.0, 0.0))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            for i, iface in enumerate(("same", "petg")):
                out = Path(td) / f"o{i}"
                sp.main([str(src), "-o", str(out), "--interface", iface,
                         "--gap", "0.2" if iface == "petg" else "0.8"])
                notes = (out / "gf-stack-2-PRINTING.md").read_text()
                leftover = re.findall(r"\{[a-z_]+(?::[^}]*)?\}", notes)
                self.assertEqual(leftover, [], f"{iface}: unrendered {leftover}")

    def test_petg_gets_zero_z_distance_and_same_material_does_not(self):
        mesh = perforated_plate(4, 3) + perforated_plate(3, 3, origin=(500.0, 0.0))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            sp.main([str(src), "-o", str(Path(td) / "p"), "--interface", "petg", "--gap", "0.2"])
            sp.main([str(src), "-o", str(Path(td) / "s"), "--interface", "same", "--gap", "0.8"])
            petg = (Path(td) / "p/gf-stack-2-PRINTING.md").read_text()
            same = (Path(td) / "s/gf-stack-2-PRINTING.md").read_text()
            self.assertIn("| Top Z distance | **0** |", petg)
            self.assertIn("| Top Z distance | **0.2 mm** |", same)

    def test_interface_layers_fit_the_gap(self):
        # 0.2 mm gap at 0.2 mm layers holds exactly one layer, not two
        self.assertEqual(sp.iface_layers(0.2, 0.2, petg=True), 1)
        self.assertEqual(sp.support_layers(0.2, 0.2, petg=True), 1)
        # same-material loses a layer to each clearance
        self.assertEqual(sp.support_layers(0.8, 0.2, petg=False), 2)
        self.assertEqual(sp.support_layers(0.6, 0.2, petg=False), 1)

    def test_variants_keep_their_own_notes(self):
        """Regression: a fixed notes filename leaves instructions for the wrong STL."""
        mesh = perforated_plate(4, 3) + perforated_plate(3, 3, origin=(500.0, 0.0))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            out = Path(td) / "out"
            sp.main([str(src), "-o", str(out), "--gap", "0.8"])
            sp.main([str(src), "-o", str(out), "--gap", "0.6", "--name", "tight"])
            wide = (out / "gf-stack-2-PRINTING.md").read_text()
            tight = (out / "tight-PRINTING.md").read_text()
            self.assertIn("gf-stack-2.stl", wide)
            self.assertIn("tight.stl", tight)
            self.assertIn("0.8 mm", wide)
            self.assertIn("0.6 mm", tight)

    def test_split_writes_one_stack_per_group(self):
        mesh = (perforated_plate(5, 4) + perforated_plate(5, 3, origin=(500.0, 0.0))
                + perforated_plate(4, 4, origin=(0.0, 500.0))
                + perforated_plate(4, 3, origin=(500.0, 500.0)))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            out = Path(td) / "out"
            self.assertEqual(sp.main([str(src), "-o", str(out), "--split"]), 0)
            for n in (1, 2):
                self.assertTrue((out / f"gf-stack-4-{n}of2.stl").exists())
                self.assertTrue((out / f"gf-stack-4-{n}of2-PRINTING.md").exists())
            self.assertFalse((out / "gf-stack-4.stl").exists())

    def test_without_split_it_stays_one_file(self):
        mesh = perforated_plate(5, 4) + perforated_plate(5, 3, origin=(500.0, 0.0))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            out = Path(td) / "out"
            sp.main([str(src), "-o", str(out)])
            self.assertTrue((out / "gf-stack-2.stl").exists())
            self.assertFalse((out / "gf-stack-2-1of2.stl").exists())

    def test_gap_snaps_to_layer_height(self):
        mesh = (perforated_plate(4, 3) + perforated_plate(3, 3, origin=(500.0, 0.0)))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            out = Path(td) / "out"
            sp.main([str(src), "-o", str(out), "--gap", "0.75", "--layer-height", "0.2"])
            gaps = stl_io.bounds_of(stl_io.read_stl(out / "gf-stack-2.stl"))
            self.assertAlmostEqual(gaps.height, 4.0 + 0.8 + 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
