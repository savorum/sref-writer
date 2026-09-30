"""Emitting a SREF recipe document (specification sections 4 to 13).

Of what section 4 says a writer MUST NOT emit, the cases possible from a Python
mapping (such as nonfinite numbers) are checked here. Every amount is
normalized, so a caller cannot emit a non-canonical one.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from . import rational, schema, units, validate
from .errors import WriteRefusedError

SUPPORTED_FORMAT = "0.4.0"
SUPPORTED_REGISTRY = "0.2.0"

#: Members whose values are amounts, wherever they appear in a recipe.
_AMOUNT_MEMBERS = ("value", "min", "max")


def write(document: dict[str, Any], *, indent: int | None = 2) -> bytes:
    """Serialize a recipe document as UTF-8 SREF JSON.

    `indent` is presentation only. SREF defines no canonical bytes (section 4),
    so a reader must not depend on the formatting, and a writer is free to
    choose it. Compact output is `indent=None`.
    """
    prepared = prepare(document)
    text = json.dumps(prepared, indent=indent, ensure_ascii=False, allow_nan=False)
    return (text + ("\n" if indent is not None else "")).encode("utf-8")


def prepare(document: dict[str, Any]) -> dict[str, Any]:
    """Return the document this writer would emit, without serializing it.

    A package writer needs it to digest and store the same content.
    """
    if not isinstance(document, dict):
        raise WriteRefusedError("malformed-recipe", "a recipe document is a mapping")

    # Copied deeply, because normalization rewrites nested values.
    document = copy.deepcopy(document)

    # The version header first, for consumers sniffing an unknown file.
    prepared = {"sref": _header(document.get("sref"))}
    prepared.update({name: value for name, value in document.items() if name != "sref"})
    for required in ("id", "title"):
        if not prepared.get(required):
            raise WriteRefusedError(
                "missing-required-member",
                f"a recipe needs {required!r}",
                required,
            )

    collections = ("ingredient_sections", "instruction_sections")
    if not any(member in prepared for member in collections):
        raise WriteRefusedError(
            "recipe-content-required", "a recipe needs ingredients or instructions"
        )
    for member in collections:
        if member in prepared and (not isinstance(prepared[member], list) or not prepared[member]):
            raise WriteRefusedError(
                "recipe-collection-nonempty",
                "a present collection must be a nonempty array",
                member,
            )

    # Which registry the units are checked against is the document's own
    # declaration, not this build's, so a document from a newer compatible
    # release keeps unit IDs this build has never heard of.
    registry = units.registry(prepared["sref"]["unit_registry"], SUPPORTED_REGISTRY)

    for member, normalize in (
        ("ingredient_sections", _ingredient_section),
        ("instruction_sections", _instruction_section),
    ):
        if member in prepared:
            prepared[member] = [
                normalize(section, f"{member}[{index}]", registry)
                for index, section in enumerate(_each(prepared[member]))
            ]
    if "equipment" in prepared:
        prepared["equipment"] = [
            _equipment(item, f"equipment[{index}]", registry)
            for index, item in enumerate(_each(prepared["equipment"]))
        ]
    if "yields" in prepared:
        prepared["yields"] = [
            _yield(item, f"yields[{index}]", registry)
            for index, item in enumerate(_each(prepared["yields"]))
        ]
    for index, statement in enumerate(_each(prepared.get("nutrition"))):
        basis = statement.get("basis") if isinstance(statement, dict) else None
        if isinstance(basis, dict) and "quantity" in basis:
            basis["quantity"] = _quantity(
                basis["quantity"],
                f"nutrition[{index}].basis.quantity",
                registry,
                temperature_code="temperature-nutrition-basis",
            )
    _normalize_microwave_percentages(prepared)
    _reject_nonfinite(prepared, "")
    _reject_control_characters(prepared, "")

    # Whole-document checks run after normalization: identities and references
    # first, for their specific categories, then the schema as the backstop.
    validate.check(prepared)
    schema.check(prepared, "recipe")
    # Last, because it is the only check that depends on what the document
    # declares rather than on what it contains (section 13).
    schema.check_version_scoped_members(prepared, prepared["sref"].get("version"), SUPPORTED_FORMAT)
    return prepared


def _each(node: Any) -> list[Any]:
    """Iterate what should be an array, and leave anything else alone.

    A value of the wrong shape is left for the schema check at the end of
    `prepare` to refuse.
    """
    return node if isinstance(node, list) else []


def _normalize_microwave_percentages(document: dict[str, Any]) -> None:
    times = document.get("times")
    if not isinstance(times, dict):
        return
    for assertion in _each(times.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        microwave = assertion.get("microwave")
        if not isinstance(microwave, dict):
            continue
        powers = [microwave.get("power")]
        powers.extend(
            choice.get("power")
            for choice in _each(microwave.get("choices"))
            if isinstance(choice, dict)
        )
        for power in powers:
            if isinstance(power, dict) and "percent" in power:
                try:
                    power["percent"] = rational.normalize(power["percent"])
                except rational.NotCanonicalError as exc:
                    raise WriteRefusedError(
                        exc.code, str(exc), "times.assertions.microwave.power"
                    ) from exc


def _shaped(node: Any) -> bool:
    return isinstance(node, dict)


def _header(header: Any) -> dict[str, Any]:
    """Every document states the versions it was written against.

    A document from a compatible newer release in the same line keeps the
    version it declared, since it still carries that release's members.
    """
    if header is None:
        return {"version": SUPPORTED_FORMAT, "unit_registry": SUPPORTED_REGISTRY}
    if not isinstance(header, dict):
        raise WriteRefusedError("missing-format-version", "the sref header is an object", "sref")
    written = dict(header)
    written.setdefault("version", SUPPORTED_FORMAT)
    written.setdefault("unit_registry", SUPPORTED_REGISTRY)
    # Each header member reports its own category.
    for member, supported, code in (
        ("version", SUPPORTED_FORMAT, "unsupported-format-version"),
        ("unit_registry", SUPPORTED_REGISTRY, "unsupported-registry-version"),
    ):
        if not _compatible(written[member], supported):
            raise WriteRefusedError(
                code,
                f"this writer emits {supported}, not {written[member]!r}",
                f"sref.{member}",
            )
    return written


def _compatible(declared: Any, supported: str) -> bool:
    """Same major line, and not older than what this build implements.

    Older is refused; newer in the same line is carried through untouched.
    """
    got, want = _parse_version(declared), _parse_version(supported)
    return got is not None and want is not None and got[0] == want[0] and got >= want


def _parse_version(version: Any) -> tuple[int, int, int] | None:
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if len(parts) != 3:
        return None
    numbers = []
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return None
        numbers.append(int(part))
    return numbers[0], numbers[1], numbers[2]


def _ingredient_section(section: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(section):
        return section
    prepared = dict(section)
    prepared["ingredients"] = [
        _ingredient(ingredient, f"{path}.ingredients[{index}]", registry)
        for index, ingredient in enumerate(_each(section.get("ingredients")))
    ]
    if not prepared["ingredients"]:
        raise WriteRefusedError(
            "empty-ingredient-section",
            "an ingredient section carries ingredients",
            path,
        )
    return prepared


def _equipment(item: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(item):
        return item
    prepared = dict(item)
    if not str(prepared.get("name", "")).strip():
        raise WriteRefusedError(
            "equipment-name-required",
            "an equipment requirement preserves its authored name",
            f"{path}.name",
        )
    if "quantity" in prepared:
        prepared["quantity"] = _quantity(
            prepared["quantity"],
            f"{path}.quantity",
            registry,
            temperature_code="temperature-equipment",
        )
    return prepared


def _ingredient(ingredient: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(ingredient):
        return ingredient
    prepared = dict(ingredient)
    if isinstance(prepared.get("alternatives"), list):
        prepared["alternatives"] = [
            _ingredient(branch, f"{path}.alternatives[{index}]", registry)
            if isinstance(branch, dict) and "alternatives" not in branch
            else branch
            for index, branch in enumerate(prepared["alternatives"])
        ]
    if "quantity" in prepared:
        prepared["quantity"] = _quantity(prepared["quantity"], f"{path}.quantity", registry)
    if "package_size" in prepared:
        size_path = f"{path}.package_size"
        prepared["package_size"] = _simple(prepared["package_size"], size_path)
        if registry is not None and _shaped(prepared["package_size"]):
            registry.package_size(prepared["package_size"].get("unit"), f"{size_path}.unit")
    if "temperature" in prepared:
        prepared["temperature"] = _temperature(
            prepared["temperature"],
            f"{path}.temperature",
            registry,
        )
    if prepared.get("optional") is False:
        # An explicit `false` and an omitted member mean the same thing
        # (section 10), so the shorter one is written.
        del prepared["optional"]
    return prepared


def _instruction_section(
    section: Any,
    path: str,
    registry: units.Registry | None,
) -> dict[str, Any]:
    if not _shaped(section):
        return section
    prepared = dict(section)
    prepared["steps"] = [
        _step(step, f"{path}.steps[{index}]", registry)
        for index, step in enumerate(_each(section.get("steps")))
    ]
    if not prepared["steps"]:
        raise WriteRefusedError(
            "empty-instruction-section",
            "an instruction section carries steps",
            path,
        )
    return prepared


def _step(step: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(step):
        return step
    prepared = dict(step)
    if not str(prepared.get("text", "")).strip():
        # A step needs instruction text, not only a title.
        raise WriteRefusedError(
            "step-text-required",
            "a step carries instruction text",
            f"{path}.text",
        )
    uses = prepared.get("ingredient_uses")
    if uses:
        prepared["ingredient_uses"] = [
            _ingredient_use(use, f"{path}.ingredient_uses[{index}]", registry)
            for index, use in enumerate(_each(uses))
        ] or uses
    temperatures = prepared.get("temperatures")
    if temperatures:
        prepared["temperatures"] = [
            _temperature(temperature, f"{path}.temperatures[{index}]", registry)
            for index, temperature in enumerate(_each(temperatures))
        ] or temperatures
    return prepared


def _ingredient_use(use: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(use):
        return use
    prepared = dict(use)
    if not prepared.get("ingredient_ref"):
        raise WriteRefusedError(
            "ingredient-use-reference-required",
            "an ingredient use names one",
            path,
        )
    if "quantity" in prepared:
        # A temperature unit here has its own category.
        prepared["quantity"] = _quantity(
            prepared["quantity"],
            f"{path}.quantity",
            registry,
            temperature_code="temperature-ingredient-use",
        )
    return prepared


def _yield(value: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(value):
        return value
    prepared = dict(value)
    if "quantity" in prepared:
        prepared["quantity"] = _quantity(
            prepared["quantity"],
            f"{path}.quantity",
            registry,
            temperature_code="temperature-yield",
        )
    if not (prepared.get("text") or prepared.get("quantity")):
        raise WriteRefusedError(
            "yield-requires-text-or-quantity",
            "a yield states text or a quantity",
            path,
        )
    return prepared


def _quantity(
    quantity: Any,
    path: str,
    registry: units.Registry | None,
    *,
    temperature_code: str = "temperature-as-quantity",
) -> dict[str, Any]:
    if not isinstance(quantity, dict):
        raise WriteRefusedError("malformed-quantity", "a quantity is an object", path)
    kind = quantity.get("kind")
    prepared = dict(quantity)
    if kind == "opaque":
        if not str(prepared.get("text", "")).strip():
            raise WriteRefusedError(
                "malformed-quantity",
                "an opaque quantity carries its wording",
                path,
            )
        # An opaque quantity is wording, not a measurement, and names no unit.
        return prepared
    if kind == "simple":
        prepared = _simple(prepared, path)
        _check_unit(prepared, path, registry, temperature_code)
        return prepared
    if kind == "sum":
        terms = []
        for index, term in enumerate(_each(prepared.get("terms"))):
            term_path = f"{path}.terms[{index}]"
            term = _simple(term, term_path)
            _check_unit(term, term_path, registry, temperature_code)
            terms.append(term)
        prepared["terms"] = terms
        return prepared
    if kind == "alternatives":
        options = []
        for index, option in enumerate(_each(prepared.get("options"))):
            option_path = f"{path}.options[{index}]"
            if not _shaped(option):
                options.append(option)
                continue
            if option.get("kind") in ("alternatives", "opaque"):
                # Alternatives may not nest.
                raise WriteRefusedError(
                    "nested-quantity-alternative",
                    "an alternative option is a simple or sum quantity",
                    option_path,
                )
            if option.get("kind") == "sum":
                option = dict(option)
                terms = []
                for term_index, term in enumerate(_each(option.get("terms"))):
                    term_path = f"{option_path}.terms[{term_index}]"
                    term = _simple(term, term_path)
                    _check_unit(term, term_path, registry, temperature_code)
                    terms.append(term)
                option["terms"] = terms
                options.append(option)
            else:
                option = _simple(option, option_path)
                _check_unit(option, option_path, registry, temperature_code)
                options.append(option)
        prepared["options"] = options
        return prepared
    raise WriteRefusedError("malformed-quantity", f"{kind!r} is not a quantity kind", path)


def _simple(quantity: Any, path: str) -> dict[str, Any]:
    if not _shaped(quantity):
        return quantity
    prepared = dict(quantity)
    prepared["amount"] = _amount(prepared.get("amount"), f"{path}.amount")
    return prepared


def _check_unit(
    quantity: Any,
    path: str,
    registry: units.Registry | None,
    temperature_code: str,
) -> None:
    """A simple quantity's unit, when there is a registry to check it against.

    A quantity without a unit is ordinary — `2 eggs` counts something the
    registry does not have to name (section 8.4) — so only a stated one is
    checked.
    """
    if registry is not None and _shaped(quantity) and "unit" in quantity:
        registry.quantity(quantity["unit"], f"{path}.unit", temperature_code=temperature_code)


def _temperature(temperature: Any, path: str, registry: units.Registry | None) -> dict[str, Any]:
    if not _shaped(temperature):
        return temperature
    prepared = dict(temperature)
    if "text" in prepared:
        if "amount" in prepared or "unit" in prepared:
            raise WriteRefusedError(
                "opaque-temperature-conflict",
                "opaque temperature text has no numeric amount or unit",
                path,
            )
        if not str(prepared.get("text", "")).strip():
            raise WriteRefusedError(
                "opaque-temperature-text-required",
                "an opaque temperature preserves its authored wording",
                f"{path}.text",
            )
        return prepared
    prepared["amount"] = _amount(prepared.get("amount"), f"{path}.amount", signed=True)
    if not prepared.get("unit"):
        raise WriteRefusedError(
            "invalid-temperature-unit",
            "a temperature names its unit",
            f"{path}.unit",
        )
    if registry is not None:
        registry.temperature(prepared["unit"], f"{path}.unit")

    purpose = prepared.get("purpose")
    if (
        isinstance(purpose, dict)
        and purpose.get("kind") == "other"
        and not str(purpose.get("label", "")).strip()
    ):
        raise WriteRefusedError(
            "temperature-purpose-other-label-required",
            "`other` carries the source's own wording",
            f"{path}.purpose",
        )
    return prepared


def _amount(amount: Any, path: str, *, signed: bool = False) -> dict[str, Any]:
    if not isinstance(amount, dict):
        raise WriteRefusedError("malformed-amount", "an amount is an object", path)
    prepared = dict(amount)
    for member in _AMOUNT_MEMBERS:
        if member not in prepared:
            continue
        try:
            prepared[member] = rational.normalize(prepared[member], signed=signed)
        except rational.NotCanonicalError as exc:
            raise WriteRefusedError(exc.code, str(exc), f"{path}.{member}") from exc

    if "min" in prepared and "max" in prepared:
        low = rational.parse(prepared["min"], signed=signed)
        high = rational.parse(prepared["max"], signed=signed)
        if low > high:
            raise WriteRefusedError("range-order", "a range minimum exceeds its maximum", path)
    if prepared.get("approximate") is False:
        # `approximate` defaults to false, and the two spellings are
        # semantically equal (section 24), so the shorter one is written.
        del prepared["approximate"]
    return prepared


#: The C0 controls section 6.2 permits inside text, where the schema allows them.
_PERMITTED_CONTROLS = frozenset("\t\n\r")


def _reject_control_characters(node: Any, path: str) -> None:
    """Refuse prohibited C0 controls anywhere in the document.

    Every string is scanned, not only text members, over the document as it
    will be written.
    """
    if isinstance(node, str):
        for character in node:
            if ord(character) < 0x20 and character not in _PERMITTED_CONTROLS:
                raise WriteRefusedError(
                    "prohibited-control-character",
                    f"U+{ord(character):04X} is not permitted in SREF text",
                    path,
                )
    elif isinstance(node, dict):
        for name, value in node.items():
            _reject_control_characters(name, f"{path}.{name}" if path else name)
            _reject_control_characters(value, f"{path}.{name}" if path else name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_control_characters(value, f"{path}[{index}]")


def _reject_nonfinite(node: Any, path: str) -> None:
    """Catch the values JSON cannot carry before json.dumps turns them into
    `NaN` or `Infinity`, which are not JSON and which section 4 forbids."""
    if isinstance(node, float):
        if node != node or node in (float("inf"), float("-inf")):
            raise WriteRefusedError("nonfinite-json-number", "a nonfinite number is not JSON", path)
    elif isinstance(node, dict):
        for name, value in node.items():
            _reject_nonfinite(value, f"{path}.{name}" if path else name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_nonfinite(value, f"{path}[{index}]")
