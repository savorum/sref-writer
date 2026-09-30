"""Running the published conformance corpus against this writer.

A writer case is usually a valid fixture, and the question it asks is whether
this writer can produce it. So the fixture is written, and the result is checked
two ways: it must still be a valid SREF document, and it must still say the same
thing.

A case whose `expected` is `invalid` is a request this writer must decline,
handed straight to the writer without the reader seeing it first.

Output is validated with `sref-reader`, a test dependency, and independently by
`tools/independent.py` against the published schemas and archive rules. Output
the reader accepts and the schemas reject fails as `schema-shortfall`.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import TYPE_CHECKING, Any

from . import bundle as bundle_module
from . import independent
from . import package as package_module
from . import recipe as recipe_module
from .errors import WriteRefusedError

if TYPE_CHECKING:
    import pathlib

#: Two spellings SREF defines as equal (section 24), so a comparison that
#: treated them as different would report a conformant writer as lossy.
_OMISSIBLE_DEFAULTS = {"approximate": False, "optional": False}


def run_case(root: pathlib.Path, case: dict[str, Any]) -> tuple[str, str]:
    fixture = root / case["fixture"]
    if not fixture.exists():
        return "missing-fixture", str(fixture)
    capabilities = set(case["capabilities"])

    if case.get("expected") == "invalid":
        return _refusal_case(fixture, case, capabilities)

    try:
        if "bundle-writer" in capabilities:
            return _bundle_case(fixture)
        if "package-writer" in capabilities:
            return _package_case(fixture)
        if capabilities & {"json-writer", "round-trip"}:
            return _document_case(fixture)
    except WriteRefusedError as exc:
        return "refused", str(exc)
    return "unsupported", ", ".join(sorted(capabilities))


class _UnrepresentableError(Exception):
    """The request cannot be put to the writer at all, so nothing was tested."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"the request could not be read as one: {cause}")


def _refusal_case(
    fixture: pathlib.Path, case: dict[str, Any], capabilities: set[str]
) -> tuple[str, str]:
    """A request the writer must decline, put to the writer and not to a reader.

    Emitting what the case forbids, declining under the wrong category, and a
    request this writer's model cannot hold are reported apart.
    """
    wanted_category = case.get("error_category")
    wanted_requirement = case.get("requirement_id")
    try:
        request = _request(fixture, capabilities)
    except _UnrepresentableError as exc:
        return "unsupported", str(exc)

    try:
        if "bundle-writer" in capabilities:
            bundle_module.write(request)
        elif "package-writer" in capabilities:
            package_module.write(*request)
        else:
            recipe_module.write(request)
    except WriteRefusedError as exc:
        if wanted_category and exc.category != wanted_category:
            return (
                "wrong-category",
                f"refused as {exc.category!r}, expected {wanted_category!r}",
            )
        if wanted_requirement and exc.requirement_id != wanted_requirement:
            return (
                "wrong-requirement",
                f"refused under {exc.requirement_id!r}, expected {wanted_requirement!r}",
            )
        return "pass", ""
    return "wrote-forbidden", "emitted a document this case forbids"


def _request(fixture: pathlib.Path, capabilities: set[str]) -> Any:
    """The fixture as something the writer's public API accepts.

    Read with the standard library, so no reader stands between the corpus and
    the writer.
    """
    data = fixture.read_bytes()
    try:
        if "bundle-writer" in capabilities:
            return _bundle_request(data)
        if "package-writer" in capabilities:
            return _package_request(data)
        return json.loads(data.decode("utf-8"))
    except (
        KeyError,
        UnicodeDecodeError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise _UnrepresentableError(exc) from exc


def _package_request(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = json.loads(archive.read("recipe.json").decode("utf-8"))
        assets = {
            declaration["id"]: archive.read(declaration["path"])
            for declaration in document.get("assets") or []
        }
    return document, assets


def _bundle_request(data: bytes) -> list[tuple[dict[str, Any], dict[str, bytes]]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        return [_package_request(archive.read(m["path"])) for m in manifest["recipes"]]


def _document_case(fixture: pathlib.Path) -> tuple[str, str]:
    import sref_reader

    original, report = sref_reader.validate_recipe(fixture.read_bytes())
    if original is None or not report.valid:
        return "unreadable-fixture", str(report.codes)

    written = recipe_module.write(original)
    shortfall = _independent("check_recipe", written)
    if shortfall:
        return shortfall
    reread, report = sref_reader.validate_recipe(written)
    if reread is None or not report.valid:
        return "wrote-invalid", str(report.codes)
    return _compare(original, reread)


def _independent(check: str, artifact: bytes) -> tuple[str, str] | None:
    """What the published schemas say about the artifact, without the reader."""
    problems = getattr(independent, check)(artifact)
    if problems:
        return "schema-shortfall", "; ".join(problems[:2])
    return None


def _package_case(fixture: pathlib.Path) -> tuple[str, str]:
    import sref_reader

    opened, report = sref_reader.validate_package(fixture.read_bytes())
    if opened is None or not report.valid:
        return "unreadable-fixture", str(report.codes)

    written = package_module.write(opened.recipe, opened.assets)
    shortfall = _independent("check_package", written)
    if shortfall:
        return shortfall
    reopened, report = sref_reader.validate_package(written)
    if reopened is None or not report.valid:
        return "wrote-invalid", str(report.codes)
    if reopened.assets != opened.assets:
        return "asset-mismatch", "the packaged bytes changed"
    return _compare(opened.recipe, reopened.recipe)


def _bundle_case(fixture: pathlib.Path) -> tuple[str, str]:
    import sref_reader

    opened, report = sref_reader.validate_bundle(fixture.read_bytes())
    if opened is None or not report.valid:
        return "unreadable-fixture", str(report.codes)

    written = bundle_module.write(
        [(member.package.recipe, member.package.assets) for member in opened.members],
    )
    shortfall = _independent("check_bundle", written)
    if shortfall:
        return shortfall
    reopened, report = sref_reader.validate_bundle(written)
    if reopened is None or not report.valid:
        return "wrote-invalid", str(report.codes)
    if len(reopened.members) != len(opened.members):
        return "member-count", "the bundle lost or gained a member"
    for before, after in zip(opened.members, reopened.members, strict=True):
        # Order is the bundle's own meaning, so members are compared in place
        # rather than matched up by recipe id.
        if before.recipe_id != after.recipe_id:
            return "member-order", "bundle order changed"
        outcome, detail = _compare(before.package.recipe, after.package.recipe)
        if outcome != "pass":
            return outcome, detail
    return "pass", ""


def _compare(before: Any, after: Any) -> tuple[str, str]:
    left, right = _canonical(before), _canonical(after)
    if left == right:
        return "pass", ""
    return "lossy", _first_difference(left, right, "")


def _canonical(node: Any) -> Any:
    """Reduce a document to what it means.

    Member order is not meaning (section 4), and a member holding its defined
    default is equal to that member being absent (section 24). Everything else
    is compared exactly, including array order, which is meaning everywhere it
    appears.
    """
    if isinstance(node, dict):
        return {
            name: _canonical(value)
            for name, value in sorted(node.items())
            if _OMISSIBLE_DEFAULTS.get(name, object()) != value
        }
    if isinstance(node, list):
        return [_canonical(item) for item in node]
    return node


def _first_difference(left: Any, right: Any, path: str) -> str:
    if isinstance(left, dict) and isinstance(right, dict):
        for name in sorted(set(left) | set(right)):
            where = f"{path}.{name}" if path else name
            if name not in left:
                return f"{where}: added"
            if name not in right:
                return f"{where}: lost"
            if left[name] != right[name]:
                return _first_difference(left[name], right[name], where)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f"{path}: {len(left)} items became {len(right)}"
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            if a != b:
                return _first_difference(a, b, f"{path}[{index}]")
    return f"{path}: {json.dumps(left)[:80]} became {json.dumps(right)[:80]}"
