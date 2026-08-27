#!/usr/bin/env python3
"""Check a Bambu Studio project against the support settings this stack needs.

    python3 check_settings.py mystack.3mf
    python3 check_settings.py mystack.3mf --profile same-material

Takes a .3mf (reads Metadata/project_settings.config from it) or that file
directly. Exits non-zero if anything differs, so it can gate a print.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

PROFILES = Path(__file__).parent / "settings"


def load_project(path: Path) -> dict:
    if path.suffix == ".3mf":
        with zipfile.ZipFile(path) as z:
            name = "Metadata/project_settings.config"
            if name not in z.namelist():
                raise SystemExit(f"{path}: no {name} inside")
            return json.loads(z.read(name))
    return json.loads(path.read_text())


def scalar(v):
    """Bambu stores most values as one-element lists."""
    return v[0] if isinstance(v, list) and v else v


def compare(project: dict, wanted: dict) -> list[tuple[str, str, str]]:
    """(key, wanted, found) for every setting that does not match."""
    out = []
    for key, want in wanted.items():
        if key.startswith("_"):
            continue
        found = scalar(project.get(key))
        if found is None:
            out.append((key, want, "missing"))
        elif str(found) != str(want):
            out.append((key, want, str(found)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path, help="a .3mf, or a project_settings.config")
    ap.add_argument("--profile", default="petg-interface",
                    help="name under settings/ (default: petg-interface)")
    args = ap.parse_args(argv)

    wanted = json.loads((PROFILES / f"{args.profile}.json").read_text())
    project = load_project(args.project)
    bad = compare(project, wanted)

    print(f"{args.project.name} against {args.profile}")
    if gap := wanted.get("_gap_mm"):
        print(f"  (expects a stack built with --gap {gap})")
    if not bad:
        print(f"\nall {len([k for k in wanted if not k.startswith('_')])} settings match")
        return 0
    print(f"\n{len(bad)} setting(s) differ:")
    for key, want, found in bad:
        print(f"  {key:34} want {want:<8} found {found}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
