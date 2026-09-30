"""The refusals that need the unit registry (specification sections 9 and 11).

Section 22 requires referenced unit IDs to exist in the declared registry. A
document declaring a newer compatible registry is written as it stands, since
section 5 requires its unit IDs to be preserved.
"""

from __future__ import annotations

import functools
from typing import Any

from . import snapshot
from .errors import WriteRefusedError


class Registry:
    """The pinned unit registry, answering the questions a writer asks of it."""

    def __init__(self, units: dict[str, dict[str, Any]]) -> None:
        self._units = units

    def quantity(self, unit_id: Any, path: str, *, temperature_code: str) -> None:
        """A unit naming how much of something there is.

        A temperature unit is refused here.
        """
        unit = self._known(unit_id, path)
        if unit is not None and unit.get("dimension") == "temperature":
            raise WriteRefusedError(
                temperature_code,
                "a temperature unit belongs in a temperature field, not a quantity",
                path,
            )

    def package_size(self, unit_id: Any, path: str) -> None:
        """A package size describes each counted container, so it is physical.

        Two 14-ounce cans are `container.can` in the quantity and 14 ounces of
        mass here. A count unit would be circular.
        """
        unit = self._known(unit_id, path)
        if unit is None:
            return
        if unit.get("dimension") == "temperature":
            raise WriteRefusedError(
                "temperature-package-size",
                "a package size is not a temperature",
                path,
            )
        if unit.get("kind") != "physical":
            raise WriteRefusedError(
                "nonphysical-package-size",
                "a package size uses a registered mass, volume or length unit",
                path,
            )

    def temperature(self, unit_id: Any, path: str) -> None:
        unit = self._known(unit_id, path)
        if unit is not None and unit.get("dimension") != "temperature":
            raise WriteRefusedError(
                "invalid-temperature-unit",
                f"{unit_id!r} does not measure temperature",
                path,
            )

    def _known(self, unit_id: Any, path: str) -> dict[str, Any] | None:
        if not isinstance(unit_id, str):
            return None
        unit = self._units.get(unit_id)
        if unit is None:
            raise WriteRefusedError("unknown-unit", f"unknown unit {unit_id!r}", path)
        return unit


def registry(declared: str, supported: str) -> Registry | None:
    """The registry to check against, or `None` when the document outranks it.

    A document declaring a newer compatible registry may carry unit IDs this
    build has never seen.
    """
    if declared != supported:
        return None
    return _pinned()


@functools.lru_cache(maxsize=1)
def _pinned() -> Registry:
    return Registry({unit["id"]: unit for unit in snapshot.units()["units"]})
