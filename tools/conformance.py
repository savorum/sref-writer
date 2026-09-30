#!/usr/bin/env python3
"""Run the published SREF conformance corpus against this writer.

The corpus lives in the SREF repository, and the reader used to check what this
writer produced lives in its own. Neither is vendored here.

    python3 tools/conformance.py --sref ../sref --reader ../sref-reader
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CLAIMED = {"json-writer", "package-writer", "bundle-writer", "round-trip"}

LOGGER = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sref", default="../sref", help="path to a savorum/sref checkout")
    parser.add_argument(
        "--reader",
        default="../sref-reader",
        help="path to a savorum/sref-reader checkout",
    )
    parser.add_argument("--capability", action="append")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()

    reader = pathlib.Path(arguments.reader).resolve()
    if not (reader / "sref_reader").is_dir():
        parser.error(f"no sref_reader package under {reader}; pass --reader")
    sys.path.insert(0, str(reader))
    from sref_writer import conformance

    root = pathlib.Path(arguments.sref).resolve()
    if not (root / "conformance" / "manifest.json").is_file():
        parser.error(f"no conformance manifest under {root}; pass --sref")
    manifest = json.loads((root / "conformance" / "manifest.json").read_text())
    wanted = set(arguments.capability) if arguments.capability else CLAIMED

    outcomes: collections.Counter[str] = collections.Counter()
    failures: list[str] = []
    for case in manifest["cases"]:
        if not set(case["capabilities"]) & wanted:
            continue
        outcome, detail = conformance.run_case(root, case)
        outcomes[outcome] += 1
        if outcome != "pass":
            failures.append(f"  {case['id']:44s} {outcome:18s} {detail}")

    total = sum(outcomes.values())
    LOGGER.info(
        "SREF %s / registry %s",
        manifest["sref_version"],
        manifest["unit_registry_version"],
    )
    LOGGER.info("capabilities: %s", ", ".join(sorted(wanted)))
    LOGGER.info("%s/%s cases pass", outcomes["pass"], total)
    for outcome, count in sorted(outcomes.items()):
        if outcome != "pass":
            LOGGER.info("  %s: %s", outcome, count)
    if failures and (arguments.verbose or len(failures) <= 40):
        LOGGER.info("")
        LOGGER.info("\n".join(failures))
    return 0 if outcomes["pass"] == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
