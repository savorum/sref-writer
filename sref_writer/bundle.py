"""Writing a `.srefbundle` (specification sections 17 to 20).

Every member is written by the ordinary package writer, with nothing shared or
deduplicated, so each member stands alone.

Member IDs are generated from bundle position, so no title, filename, or
application key appears in a member path.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import TYPE_CHECKING, Any

from . import package as package_module
from . import schema
from .errors import WriteRefusedError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

BUNDLE_FORMAT = "sref-bundle"
BUNDLE_VERSION = 1
EPOCH = package_module.EPOCH


def write(recipes: Iterable[tuple[dict[str, Any], Mapping[str, bytes] | None]]) -> bytes:
    """Build a bundle from an ordered sequence of recipes and their assets.

    The order given is preserved exactly. The same members in the same order
    produce the same bytes, though SREF defines no canonical ZIP bytes.
    """
    members: list[tuple[str, bytes]] = []
    manifest_entries: list[dict[str, Any]] = []

    for index, member in enumerate(recipes, start=1):
        if not isinstance(member, tuple | list) or len(member) != 2:
            raise WriteRefusedError(
                "bundle-member-shape",
                "a bundle member is a (document, assets) pair",
                f"recipes[{index - 1}]",
            )
        document, assets = member
        member_id = f"r{index:06d}"
        path = f"recipes/{member_id}.sref"
        content = package_module.write(document, assets)
        manifest_entries.append(
            {
                "member_id": member_id,
                "recipe_id": document.get("id"),
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            },
        )
        members.append((path, content))

    if not manifest_entries:
        raise WriteRefusedError("empty-bundle", "a bundle carries at least one recipe")

    manifest = {"format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "recipes": manifest_entries}
    schema.check(manifest, "bundle-manifest", "manifest.json")
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _add(archive, "manifest.json", manifest_bytes, zipfile.ZIP_DEFLATED)
        for path, content in members:
            # Members are already compressed.
            _add(archive, path, content, zipfile.ZIP_STORED)
    return buffer.getvalue()


def _add(archive: zipfile.ZipFile, name: str, content: bytes, method: int) -> None:
    info = zipfile.ZipInfo(filename=name, date_time=EPOCH)
    info.compress_type = method
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)
