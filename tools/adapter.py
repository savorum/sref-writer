#!/usr/bin/env python3
"""Conformance adapter for sref-writer, paired with sref-reader.

Implements the command-line contract in the SREF specification's
`docs/conformance-runner.md`.

    tools/adapter.py --reader ../sref-reader capabilities
    tools/adapter.py --reader ../sref-reader rewrite fixture.recipe.json

This adapter declares itself as **sref-reader+sref-writer**, since round-trip
needs both. Every artifact is also checked by `tools/independent.py` against
the published schemas; a disagreement between it and the reader is a failure.

The reader is a test dependency of this adapter, never of the library.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_writer
from sref_writer import independent
from sref_writer.errors import WriteRefusedError

#: What the pair claims. The writer's own capabilities, plus the one that only
#: exists when a reader and a writer are put together.
CAPABILITIES = ("json-writer", "package-writer", "bundle-writer", "round-trip")

LOGGER = logging.getLogger(__name__)


def _refused(refusal: WriteRefusedError, detail: str | None = None) -> dict:
    return {
        "outcome": "rejected",
        "error_category": refusal.category,
        "requirement_id": refusal.requirement_id,
        "detail": detail or str(refusal),
    }


def _reader_refused(exc, detail: str) -> dict:
    return {
        "outcome": "rejected",
        "error_category": exc.category,
        "requirement_id": exc.requirement_id,
        "detail": detail,
    }


def _reported_refusal(report, detail: str = "") -> dict:
    return {
        "outcome": "rejected",
        "error_category": report.category,
        "requirement_id": report.requirement_id,
        **({"detail": detail} if detail else {}),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader", default="../sref-reader", help="path to a sref-reader checkout")
    parser.add_argument("command", choices=("capabilities", "check", "rewrite"))
    parser.add_argument("arguments", nargs="*")
    parsed = parser.parse_args()

    reader_path = pathlib.Path(parsed.reader).resolve()
    if not (reader_path / "sref_reader").is_dir():
        LOGGER.error("no sref_reader package under %s", reader_path)
        return 2
    sys.path.insert(0, str(reader_path))
    import sref_reader

    if parsed.command == "capabilities":
        return _emit(
            {
                "implementation": "sref-reader+sref-writer",
                "version": f"{sref_reader.__version__}+{sref_writer.__version__}",
                "sref_version": sref_writer.SUPPORTED_FORMAT,
                "unit_registry_version": sref_writer.SUPPORTED_REGISTRY,
                "capabilities": list(CAPABILITIES),
            },
        )

    if not parsed.arguments:
        LOGGER.error("a fixture path is required")
        return 2

    if parsed.command == "rewrite":
        return _rewrite(sref_reader, pathlib.Path(parsed.arguments[-1]))

    capability = parsed.arguments[0] if len(parsed.arguments) > 1 else "json-writer"
    return _check(sref_reader, capability, pathlib.Path(parsed.arguments[-1]))


def _check(sref_reader, capability: str, fixture: pathlib.Path) -> int:
    """Can this writer produce the fixture, and is what it produced valid?

    Valid cases are read through the paired reader. Invalid writer cases are
    decoded only far enough to recover the source content supplied to the
    writer; accepting or rejecting that content remains the writer's decision.
    """
    if capability not in CAPABILITIES:
        return _emit({"outcome": "unsupported", "detail": f"{capability} is not claimed"})
    try:
        if capability == "package-writer":
            recipe, assets = _package_input(fixture.read_bytes())
            written = sref_writer.write_package(recipe, assets)
            _, report = sref_reader.validate_package(written)
            problems = independent.check_package(written)
        elif capability == "bundle-writer":
            recipes = _bundle_input(fixture.read_bytes())
            written = sref_writer.write_bundle(
                recipes,
            )
            _, report = sref_reader.validate_bundle(written)
            problems = independent.check_bundle(written)
        elif capability == "json-writer":
            document = json.loads(fixture.read_text(encoding="utf-8"))
            written = sref_writer.write_recipe(document)
            _, report = sref_reader.validate_recipe(written)
            problems = independent.check_recipe(written)
        else:
            document = sref_reader.read_recipe(fixture.read_bytes())
            written = sref_writer.write_recipe(document)
            _, report = sref_reader.validate_recipe(written)
            problems = independent.check_recipe(written)
    except WriteRefusedError as refused:
        return _emit(_refused(refused))
    except sref_reader.SrefError as exc:
        # Nothing was tested about the writer.
        return _emit(_reader_refused(exc, "fixture unreadable"))

    if problems:
        # Reported even if the reader accepted it.
        detail = "; ".join(problems[:3])
        if report.valid:
            detail = f"the reader accepted what the published schemas reject: {detail}"
        return _emit(
            {
                "outcome": "rejected",
                "error_category": "invalid-artifact",
                "requirement_id": "schema-violation",
                "detail": detail,
            },
        )
    if report.valid:
        # Returned so the runner can check it independently.
        return _emit({"outcome": "accepted", **_artifact(written, capability)})
    return _emit(_reported_refusal(report, "the writer emitted an invalid document"))


def _package_input(content: bytes) -> tuple[dict, dict[str, bytes]]:
    """Recover the caller input represented by a package fixture.

    No SREF validation, so the writer, not the reader, is what refuses invalid
    content.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        recipe = json.loads(archive.read(manifest["recipe"]["path"]))
        assets = {asset["id"]: archive.read(asset["path"]) for asset in manifest.get("assets", [])}
    return recipe, assets


def _bundle_input(content: bytes) -> list[tuple[dict, dict[str, bytes]]]:
    """Recover the ordered writer inputs represented by a bundle fixture."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        return [_package_input(archive.read(member["path"])) for member in manifest["recipes"]]


def _rewrite(sref_reader, fixture: pathlib.Path) -> int:
    try:
        document = sref_reader.read_recipe(fixture.read_bytes())
        written = sref_writer.write_recipe(document)
        reread, report = sref_reader.validate_recipe(written)
    except WriteRefusedError as refused:
        return _emit(_refused(refused))
    except sref_reader.SrefError as exc:
        return _emit(_reader_refused(exc, "fixture unreadable"))
    problems = independent.check_recipe(written)
    if problems:
        return _emit(
            {
                "outcome": "rejected",
                "error_category": "invalid-artifact",
                "requirement_id": "schema-violation",
                "detail": "; ".join(problems[:3]),
            },
        )
    if reread is None or not report.valid:
        return _emit(_reported_refusal(report))
    # The runner compares this with the original, and checks the bytes.
    return _emit({"outcome": "accepted", "document": reread, **_artifact(written, "json-writer")})


def _artifact(written: bytes, capability: str) -> dict:
    """What was produced, in the encoding the protocol defines for its kind.

    A recipe document is UTF-8 text and travels as itself. A package or a
    bundle is a ZIP, so it travels base64-encoded rather than mangled through
    a JSON string.
    """
    if capability in ("package-writer", "bundle-writer"):
        return {
            "artifact": base64.b64encode(written).decode("ascii"),
            "artifact_encoding": "base64",
        }
    return {"artifact": written.decode("utf-8"), "artifact_encoding": "utf-8"}


def _emit(answer: dict) -> int:
    json.dump(answer, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
