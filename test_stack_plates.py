"""Tests. Run with: python3 -m unittest -v test_stack_plates"""
from __future__ import annotations

import itertools
import math
import tempfile
import unittest
from pathlib import Path

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
    def setUp(self):
        plates = tuple(sp.build_plate(perforated_plate(w, d))
                       for w, d in ((4, 3), (3, 3), (3, 2)))
        self.pl = sp.plan(plates, 0.8, flip=True, register=True)
        every = stl_io.split_shells(sp.make_blockers(self.pl, 0.8))
        # the two bbox pins are not sockets; they exist only to fix placement
        self.solids = tuple(s for s in every
                            if stl_io.bounds_of(s).width > sp.BBOX_PIN + 1e-9)

    def test_one_solid_per_cell(self):
        self.assertEqual(len(self.solids),
                         sum(p.plate.lattice.cells for p in self.pl))

    def test_every_solid_is_closed_and_outward_wound(self):
        for solid in self.solids:
            self.assertGreater(stl_io.signed_volume(solid), 0.0)

    def test_each_solid_fits_inside_one_cell(self):
        pitch = self.pl[0].lattice.pitch
        for solid in self.solids:
            b = stl_io.bounds_of(solid)
            self.assertLessEqual(b.width, pitch)
            self.assertLessEqual(b.depth, pitch)

    def test_each_solid_is_centred_on_a_cell(self):
        holes = {(round(x, 2), round(y, 2)) for pl in self.pl for x, y in pl.holes()}
        for solid in self.solids:
            b = stl_io.bounds_of(solid)
            self.assertIn((round(b.cx, 2), round(b.cy, 2)), holes)

    def test_solids_reach_into_the_gaps(self):
        """A blocker must cross the gap, or support forms in it and bridges the socket."""
        for pl in self.pl:
            here = [stl_io.bounds_of(s) for s in self.solids
                    if pl.z0 - 1e-6 <= (stl_io.bounds_of(s).z0 + stl_io.bounds_of(s).z1) / 2 <= pl.z1 + 1e-6]
            self.assertTrue(here)
            for b in here:
                self.assertLess(b.z0, pl.z0)
                self.assertGreater(b.z1, pl.z1)

    def test_no_blocker_overlaps_plate_material(self):
        """The load-bearing invariant: a blocker never sits inside solid material."""
        meshes = [(pl, pl.placed_mesh()) for pl in self.pl]
        frac = (0.03, 0.19, 0.37, 0.49, 0.63, 0.81, 0.97)
        sampled = 0
        for solid in self.solids:
            b = stl_io.bounds_of(solid)
            for fx in frac:
                for fy in frac:
                    px, py = b.x0 + b.width * fx, b.y0 + b.depth * fy
                    for pl, mesh in meshes:
                        lo, hi = max(b.z0, pl.z0), min(b.z1, pl.z1)
                        if hi - lo <= 1e-9:
                            continue
                        for fz in (0.05, 0.5, 0.95):
                            pz = lo + (hi - lo) * fz
                            if not point_inside(solid, px, py, pz):
                                continue
                            sampled += 1
                            self.assertFalse(
                                point_inside(mesh, px, py, pz),
                                f"blocker point ({px:.2f},{py:.2f},{pz:.2f}) is inside "
                                f"the {pl.plate.label} plate")
        self.assertGreater(sampled, 100)

    def test_bbox_matches_the_model(self):
        """Regression: Bambu centres a loaded part on the object it joins.

        The cell lattice is not centred in the plate outline, so without pinning
        the bbox the blockers land several mm off -- over the ribs, blocking
        nothing, and silently. Measured 3.0 mm in X on the real cabinet set.
        """
        model = stl_io.bounds_of(tuple(f for pl in self.pl for f in pl.placed_mesh()))
        blockers = stl_io.bounds_of(sp.make_blockers(self.pl, 0.8))
        self.assertAlmostEqual(blockers.cx, model.cx, places=6)
        self.assertAlmostEqual(blockers.cy, model.cy, places=6)

    def test_pins_are_small_enough_to_ignore(self):
        pinned = sp.make_blockers(self.pl, 0.8)
        pins = [s for s in stl_io.split_shells(pinned)
                if stl_io.bounds_of(s).width <= sp.BBOX_PIN + 1e-9]
        self.assertEqual(len(pins), 2)
        for pin in pins:
            self.assertLessEqual(stl_io.signed_volume(pin), sp.BBOX_PIN ** 3 + 1e-9)

    def test_blockers_are_opt_in(self):
        """They measured as worth nothing with snug support, so they are off."""
        mesh = perforated_plate(4, 3) + perforated_plate(3, 3, origin=(500.0, 0.0))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.stl"
            stl_io.write_stl(src, mesh)
            for flags, expected in ((["--blockers"], True), ([], False)):
                out = Path(td) / ("on" if expected else "off")
                sp.main([str(src), "-o", str(out), *flags])
                self.assertEqual((out / "gf-stack-2-blockers.stl").exists(), expected)


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
            notes = (out / "PRINTING.md").read_text()
            self.assertIn("land-to-land", notes)
            self.assertIn("Threshold angle", notes)

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
