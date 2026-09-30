"""Writing a `.sref` package (specification sections 14 to 16).

Every asset the recipe declares is carried at the exact path it declares, and
the manifest records the digest and size computed from the bytes written, not
what the caller claimed.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Mapping
from typing import Any

from . import recipe as recipe_module
from . import schema
from .errors import WriteRefusedError

PACKAGE_VERSION = "0.1.0"

#: A fixed archive timestamp, the ZIP epoch, so output is reproducible.
EPOCH = (1980, 1, 1, 0, 0, 0)


def write(document: dict[str, Any], assets: Mapping[str, bytes] | None = None) -> bytes:
    """Build a package in memory, keyed by the recipe's own asset IDs."""
    if assets is not None and not isinstance(assets, Mapping):
        raise WriteRefusedError(
            "assets-mapping-required", "asset bytes are a mapping keyed by asset ID"
        )
    assets = dict(assets or {})
    for asset_id in assets:
        if not isinstance(asset_id, str):
            raise WriteRefusedError(
                "assets-mapping-required", f"asset ID {asset_id!r} is not a string"
            )
    prepared = recipe_module.prepare(document)
    recipe_bytes = recipe_module.write(prepared)

    declared = [asset for asset in prepared.get("assets") or [] if isinstance(asset, dict)]
    manifest: dict[str, Any] = {
        "sref_package": {"version": PACKAGE_VERSION},
        "recipe": {
            "path": "recipe.json",
            "media_type": "application/json",
            "sha256": hashlib.sha256(recipe_bytes).hexdigest(),
            "size": len(recipe_bytes),
        },
    }

    entries: list[tuple[str, bytes]] = []
    manifest_assets: list[dict[str, Any]] = []
    for asset in declared:
        asset_id = asset.get("id")
        path = asset.get("path")
        if asset_id not in assets:
            raise WriteRefusedError(
                "missing-asset-entry",
                f"the recipe declares {asset_id!r} and no bytes were supplied",
                f"assets[{asset_id}]",
            )
        content = assets.pop(asset_id)
        if not isinstance(content, bytes | bytearray):
            raise WriteRefusedError(
                "asset-bytes-required",
                f"the bytes supplied for {asset_id!r} are {type(content).__name__}, not bytes",
                f"assets[{asset_id}]",
            )
        digest = hashlib.sha256(content).hexdigest()
        # Corrected to the bytes written rather than trusted.
        asset["sha256"] = digest
        asset["size"] = len(content)
        manifest_assets.append(
            {
                "id": asset_id,
                "path": path,
                "media_type": asset.get("media_type"),
                "sha256": digest,
                "size": len(content),
            },
        )
        entries.append((path, content))

    if assets:
        raise WriteRefusedError(
            "undeclared-archive-entry",
            f"bytes were supplied for {sorted(assets)!r}, which the recipe does not declare",
        )
    # Always written, empty array included; the manifest schema requires it.
    manifest["assets"] = manifest_assets

    # The recipe is serialized once more because correcting the asset digests
    # changed it, and the manifest must describe the bytes actually stored.
    recipe_bytes = recipe_module.write(prepared)
    manifest["recipe"]["sha256"] = hashlib.sha256(recipe_bytes).hexdigest()
    manifest["recipe"]["size"] = len(recipe_bytes)

    schema.check(manifest, "package-manifest", "manifest.json")

    manifest_bytes = (
        recipe_module.json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    )
    return _archive([("manifest.json", manifest_bytes), ("recipe.json", recipe_bytes), *entries])


def _archive(entries: list[tuple[str, bytes]]) -> bytes:
    """Write a ZIP with no directory entries and no local clock.

    Explicit directory entries are forbidden to writers (package format
    reference), and a stored timestamp is variation a consumer must ignore, so
    there is no reason to introduce it.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(filename=name, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()
