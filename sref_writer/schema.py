"""Running the pinned JSON Schemas over what this writer is about to emit.

It runs after normalization and before serialization. A schema failure is
reported as `schema-violation`, the least specific requirement, after the
writer's more specific checks.
"""

from __future__ import annotations

import functools
from typing import Any

from . import snapshot
from .errors import WriteRefusedError

#: The pinned schemas, by the name a caller asks for.
_SCHEMAS = {
    "recipe": snapshot.recipe_schema,
    "package-manifest": snapshot.manifest_schema,
    "bundle-manifest": snapshot.bundle_manifest_schema,
}


def check(document: Any, name: str, prefix: str = "") -> None:
    """Refuse `document` if the named pinned schema rejects it.

    The first error is raised rather than all of them.
    """
    validator = _validator(name)
    error = next(iter(validator.iter_errors(document)), None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    if prefix:
        path = f"{prefix}.{path}" if path else prefix
    raise WriteRefusedError("schema-violation", error.message, path)


@functools.cache
def _validator(name: str):
    import jsonschema

    return jsonschema.Draft202012Validator(_SCHEMAS[name]())


def check_version_scoped_members(document: Any, declared: str, supported: str) -> None:
    """Refuse members the document's own declared version does not define.

    Section 13 permits unknown standard members only in a document declaring a
    newer compatible version, where they are carried through unchanged. At this
    build's version an unprefixed unknown member is refused. It is checked
    against a closed copy of the pinned schema.
    """
    if _is_newer(declared, supported):
        return
    validator = _closed_recipe_validator()
    error = next(
        (e for e in validator.iter_errors(document) if e.validator == "additionalProperties"),
        None,
    )
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    raise WriteRefusedError("member-not-defined-by-version", error.message, path)


def _is_newer(declared: str, supported: str) -> bool:
    """Whether the document declares a later version in this compatibility line.

    The supported version is passed in rather than imported: it belongs to the
    recipe module, which imports this one.
    """
    try:
        got = tuple(int(part) for part in str(declared).split("."))
        want = tuple(int(part) for part in str(supported).split("."))
    except (AttributeError, TypeError, ValueError):
        return False
    return len(got) == 3 and got[0] == want[0] and got > want


@functools.cache
def _closed_recipe_validator():
    import jsonschema

    return jsonschema.Draft202012Validator(_closed(_SCHEMAS["recipe"]()))


def _closed(node: Any) -> Any:
    if isinstance(node, dict):
        closed = {name: _closed(value) for name, value in node.items()}
        if closed.get("additionalProperties") is True:
            closed["additionalProperties"] = False
        return closed
    if isinstance(node, list):
        return [_closed(item) for item in node]
    return node
