"""A reference SREF writer.

It emits SREF 0.4.0 recipe documents, `.sref` packages and `.srefbundle`
bundles, and refuses to produce anything the specification forbids.

    import sref_writer

    data = sref_writer.write_recipe(document)
    archive = sref_writer.write_package(document, {"hero": jpeg_bytes})
    bundle = sref_writer.write_bundle([(first, None), (second, assets)])

It reads nothing; SREF Reader does.

`write_*` normalizes every amount. Hand it a `Fraction`, an `int`, or a
canonical string; it will not emit or accept `0.3333333333333333` or `2/4`.
"""

from __future__ import annotations

from .bundle import BUNDLE_VERSION
from .bundle import write as write_bundle
from .errors import FAILURE_CATEGORIES, WriteRefusedError
from .package import PACKAGE_VERSION
from .package import write as write_package
from .recipe import SUPPORTED_FORMAT, SUPPORTED_REGISTRY, prepare
from .recipe import write as write_recipe

#: The distribution version, which is not the SREF version it implements.
__version__ = "0.4.0"

#: The conformance capabilities this implementation claims, in the vocabulary
#: of the specification's section 22. Round-trip conformance is deliberately
#: absent: it requires a reader, and this library has none.
CAPABILITIES = ("json-writer", "package-writer", "bundle-writer")

__all__ = [
    "BUNDLE_VERSION",
    "CAPABILITIES",
    "FAILURE_CATEGORIES",
    "PACKAGE_VERSION",
    "SUPPORTED_FORMAT",
    "SUPPORTED_REGISTRY",
    "WriteRefusedError",
    "__version__",
    "prepare",
    "write_bundle",
    "write_package",
    "write_recipe",
]
