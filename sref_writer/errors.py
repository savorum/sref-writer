"""What a writer refuses to emit.

The writer raises on the first refusal rather than collecting violations: there
is no partially written recipe to report on.
"""

from __future__ import annotations

#: The normative vocabulary of SREF specification section 22.1. Requirement
#: identifiers remain useful diagnostic detail, but only these categories are
#: portable across implementations.
FAILURE_CATEGORIES = (
    "invalid-artifact",
    "unsupported-format-version",
    "unsupported-registry-version",
    "unknown-unit",
    "integrity-failure",
    "unsafe-archive",
    "resource-limit",
)

_CATEGORY_BY_REQUIREMENT = {
    "unsupported-format-version": "unsupported-format-version",
    "unsupported-registry-version": "unsupported-registry-version",
    "unknown-unit": "unknown-unit",
    "missing-asset-entry": "integrity-failure",
    "undeclared-archive-entry": "integrity-failure",
}


def category_for(requirement_id: str) -> str:
    """Map one detailed refusal onto SREF's portable vocabulary."""
    return _CATEGORY_BY_REQUIREMENT.get(requirement_id, "invalid-artifact")


class WriteRefusedError(Exception):
    """Content this writer will not turn into SREF.

    `category` is the normative, portable identifier a caller branches on.
    `requirement_id` names the precise snapshot-scoped conformance rule. The
    existing `code` attribute remains as a compatibility alias for that detail.
    """

    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.path = path
        where = f" at {path}" if path else ""
        super().__init__(f"{code}{where}: {message}")

    @property
    def category(self) -> str:
        """The normative failure category of SREF section 22.1."""
        return category_for(self.code)

    @property
    def requirement_id(self) -> str:
        """The precise snapshot-scoped rule this writer refused under."""
        return self.code
