#!/usr/bin/env python3
"""Exercise sref-writer as an installed distribution rather than a checkout.

A checkout resolves every path whether or not it was packaged, so run this
against an installed sref-writer, from a directory that is not the checkout:

    cd "$(mktemp -d)"
    python3 /path/to/sref-writer/tools/installed.py --sref /path/to/sref --reader /path/to/sref-reader

The reader comes from a checkout, as it only checks the output. This refuses to
run if it imports the writer from the checkout.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

#: One valid case per capability is enough here. This is a smoke test that the
#: installed artifact works at all; `tools/conformance.py` runs the corpus.
_SMOKE = ("json-writer", "package-writer", "bundle-writer", "round-trip")

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
    arguments = parser.parse_args()

    root = pathlib.Path(arguments.sref).resolve()
    if not (root / "conformance" / "manifest.json").is_file():
        parser.error(f"no conformance manifest under {root}; pass --sref")
    reader = pathlib.Path(arguments.reader).resolve()
    if not (reader / "sref_reader").is_dir():
        parser.error(f"no sref_reader package under {reader}; pass --reader")
    sys.path.insert(0, str(reader))

    import sref_writer
    from sref_writer import conformance, snapshot
    from sref_writer.errors import WriteRefusedError

    # The source tree itself, not merely anything under the checkout: CI
    # builds its virtual environment inside the working copy, and a package
    # installed into it is a real installed distribution.
    source = pathlib.Path(__file__).resolve().parent.parent / "sref_writer"
    installed = pathlib.Path(sref_writer.__file__).resolve()
    if installed.parent == source:
        LOGGER.error("imported the checkout at %s, not an installed distribution", installed)
        return 2
    LOGGER.info("sref_writer %s from %s", sref_writer.__version__, installed.parent)

    # The snapshot is the part a wheel loses, so read all of it, not just the
    # file the write path happens to need. Each load verifies its own digest.
    for name in snapshot.manifest()["files"]:
        snapshot.load(name)
    version = snapshot.manifest()["unit_registry_version"]
    LOGGER.info(
        "snapshot %s / registry %s: %s files",
        snapshot.manifest()["sref_version"],
        version,
        len(snapshot.manifest()["files"]),
    )
    if version != sref_writer.SUPPORTED_REGISTRY:
        LOGGER.error(
            "the shipped registry is %s, but this build writes %s",
            version,
            sref_writer.SUPPORTED_REGISTRY,
        )
        return 1

    document = sref_writer.prepare(_RECIPE)
    written = sref_writer.write_recipe(document)
    package = sref_writer.write_package(document, {})
    bundle = sref_writer.write_bundle([(document, None)])
    LOGGER.info(
        f"wrote {len(written)} B of JSON, a {len(package)} B package and a {len(bundle)} B bundle",
    )

    # Refusing an unregistered unit proves the shipped registry loaded.
    unregistered = json.loads(json.dumps(_RECIPE))
    unregistered["ingredient_sections"][0]["ingredients"][0]["quantity"]["unit"] = "mass.smidgen"
    try:
        sref_writer.write_recipe(unregistered)
    except WriteRefusedError as refused:
        LOGGER.exception("refused an unregistered unit as %s", refused.code)
        if refused.code != "unknown-unit":
            return 1
    else:
        LOGGER.error("wrote a unit the pinned registry does not define")
        return 1

    manifest = json.loads((root / "conformance" / "manifest.json").read_text())
    failures = 0
    for capability in _SMOKE:
        case = _first_valid(manifest, capability)
        if case is None:
            LOGGER.error("  %-16s no valid case in the corpus", capability)
            failures += 1
            continue
        outcome, detail = conformance.run_case(root, case)
        LOGGER.info("  %-16s %-44s %s %s", capability, case["id"], outcome, detail.rstrip())
        failures += outcome != "pass"
    if failures:
        LOGGER.error("%s smoke case(s) did not pass from the installed artifact", failures)
        return 1
    LOGGER.info("the installed writer writes SREF")
    return 0


def _first_valid(manifest: dict, capability: str) -> dict | None:
    for case in manifest["cases"]:
        if case["expected"] == "valid" and capability in case["capabilities"]:
            return case
    return None


#: Small enough to read, wide enough to touch a quantity, a package size and a
#: temperature — the three places the registry is consulted.
_RECIPE = {
    "id": "installed-check",
    "title": "Installed check",
    "ingredient_sections": [
        {
            "id": "tin",
            "ingredients": [
                {
                    "id": "tomatoes",
                    "name": "chopped tomatoes",
                    "quantity": {
                        "kind": "simple",
                        "amount": {"value": "2"},
                        "unit": "container.can",
                    },
                    "package_size": {
                        "kind": "simple",
                        "amount": {"value": "400"},
                        "unit": "mass.gram",
                    },
                },
            ],
        },
    ],
    "instruction_sections": [
        {
            "id": "method",
            "steps": [
                {
                    "id": "roast",
                    "text": "Roast until the edges catch.",
                    "temperatures": [
                        {
                            "amount": {"value": "200"},
                            "unit": "temperature.celsius",
                            "purpose": {"kind": "oven"},
                        },
                    ],
                },
            ],
        },
    ],
}


if __name__ == "__main__":
    raise SystemExit(main())
