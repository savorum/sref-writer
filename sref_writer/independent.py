"""An oracle for what this writer emitted that is not the paired reader.

It checks the emitted bytes against the published schemas and the
specification's archive rules, so a rule neither the writer nor `sref-reader`
enforces is still caught. It does not interpret quantities or units.

Three independent things are asserted about a package or bundle: the archive
contains exactly the entries the manifest and recipe declare, every declared
digest and size matches the bytes actually stored, and every manifest validates
against its published schema.

It ships in the package for the conformance harness; nothing on a write path
calls it.

    python3 -m sref_writer.independent shortbread.sref
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import sys
import zipfile
from typing import Any

from . import snapshot


class DuplicateMemberError(ValueError):
    """A JSON object may not repeat member names."""

    def __init__(self, name: str) -> None:
        super().__init__(f"duplicate member {name!r}")


def check_recipe(data: bytes) -> list[str]:
    problems: list[str] = []
    document = _decode(data, "recipe.json", problems)
    if document is None:
        return problems
    _schema(document, snapshot.recipe_schema(), "recipe.json", problems)
    return problems


def check_package(data: bytes) -> list[str]:
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return [f"not a readable ZIP archive: {exc}"]

    names = archive.namelist()
    for name in names:
        _safe_path(name, problems)
    problems.extend(
        f"the package has no root {required}"
        for required in ("manifest.json", "recipe.json")
        if required not in names
    )
    if problems:
        return problems

    manifest = _decode(archive.read("manifest.json"), "manifest.json", problems)
    if manifest is None:
        return problems
    _schema(manifest, snapshot.manifest_schema(), "manifest.json", problems)

    recipe_bytes = archive.read("recipe.json")
    problems.extend(check_recipe(recipe_bytes))
    _entry_matches(manifest.get("recipe"), recipe_bytes, "recipe.json", problems)

    document = _decode(recipe_bytes, "recipe.json", [])
    declared = {entry.get("path") for entry in _entries(manifest.get("assets"))}
    for entry in _entries(manifest.get("assets")):
        path = entry.get("path")
        if path not in names:
            problems.append(f"the manifest declares {path!r}, which the package does not carry")
            continue
        _entry_matches(entry, archive.read(path), path, problems)
    problems.extend(
        f"{name!r} is carried but declared nowhere"
        for name in names
        if name not in declared | {"manifest.json", "recipe.json"} and not name.endswith("/")
    )

    # The manifest and the recipe must say the same thing about every asset,
    # which is a rule about two documents rather than about either one.
    if isinstance(document, dict):
        _assets_agree(document.get("assets"), manifest.get("assets"), problems)
    return problems


def check_bundle(data: bytes) -> list[str]:
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return [f"not a readable ZIP bundle: {exc}"]

    names = archive.namelist()
    for name in names:
        _safe_path(name, problems)
    if "manifest.json" not in names:
        return [*problems, "the bundle has no root manifest.json"]

    manifest = _decode(archive.read("manifest.json"), "manifest.json", problems)
    if manifest is None:
        return problems
    _schema(manifest, snapshot.bundle_manifest_schema(), "manifest.json", problems)

    declared: set[str] = set()
    for entry in _entries(manifest.get("recipes")):
        path = entry.get("path")
        member_id = entry.get("member_id")
        if path != f"recipes/{member_id}.sref":
            problems.append(f"member {member_id!r} declares path {path!r}")
            continue
        declared.add(path)
        if path not in names:
            problems.append(f"the manifest declares {path!r}, which the bundle does not carry")
            continue
        member = archive.read(path)
        _entry_matches(entry, member, path, problems)
        # Every member must stand alone as a package.
        problems.extend(f"{path}: {problem}" for problem in check_package(member))
    problems.extend(
        f"{name!r} is carried but declared nowhere"
        for name in names
        if name != "manifest.json" and name not in declared and not name.endswith("/")
    )
    return problems


def _decode(data: bytes, where: str, problems: list[str]) -> Any:
    """Decode strictly, because SREF forbids what `json` accepts by default."""
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append(f"{where} begins with a byte-order mark")
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"{where} is not UTF-8: {exc}")
        return None
    try:
        return json.loads(text, object_pairs_hook=_unique_members)
    except ValueError as exc:
        problems.append(f"{where} is not valid SREF JSON: {exc}")
        return None


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for name, value in pairs:
        if name in seen:
            raise DuplicateMemberError(name)
        seen[name] = value
    return seen


def _schema(document: Any, schema: Any, where: str, problems: list[str]) -> None:
    import jsonschema

    for error in jsonschema.Draft202012Validator(schema).iter_errors(document):
        path = ".".join(str(part) for part in error.absolute_path)
        problems.append(f"{where}{'.' + path if path else ''}: {error.message}")


def _entries(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, list):
        return []
    return [item for item in node if isinstance(item, dict)]


def _entry_matches(entry: Any, content: bytes, where: str, problems: list[str]) -> None:
    if not isinstance(entry, dict):
        return
    if entry.get("size") != len(content):
        problems.append(
            f"{where} is {len(content)} bytes, and the manifest records {entry.get('size')!r}",
        )
    digest = hashlib.sha256(content).hexdigest()
    if entry.get("sha256") != digest:
        problems.append(f"{where} does not match the digest the manifest records")


def _assets_agree(recipe_assets: Any, manifest_assets: Any, problems: list[str]) -> None:
    declared = {asset.get("id"): asset for asset in _entries(recipe_assets)}
    recorded = {asset.get("id"): asset for asset in _entries(manifest_assets)}
    problems.extend(
        f"asset {asset_id!r} is declared by only one of the recipe and the manifest"
        for asset_id in declared.keys() ^ recorded.keys()
    )
    for asset_id in declared.keys() & recorded.keys():
        problems.extend(
            f"asset {asset_id!r} disagrees on {member!r} between the recipe and the manifest"
            for member in ("path", "media_type", "sha256", "size")
            if declared[asset_id].get(member) != recorded[asset_id].get(member)
        )


def _safe_path(name: str, problems: list[str]) -> None:
    if (
        name.startswith("/")
        or "\\" in name
        or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
    ):
        problems.append(f"{name!r} is not a safe archive path")


def main(argv: list[str]) -> int:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)
    kinds = {".sref": check_package, ".srefbundle": check_bundle}
    if not argv:
        logger.error("a path to a recipe document, package or bundle is required")
        return 2
    path = pathlib.Path(argv[0])
    found = kinds.get(path.suffix, check_recipe)(path.read_bytes())
    logger.info("\n".join(found) if found else "no problems")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
