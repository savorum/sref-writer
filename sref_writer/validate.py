"""Identities and references (specification section 22).

Uniqueness and reference resolution, which JSON Schema cannot state and section
22 requires. The categories match the reference reader's.
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from typing import Any

from . import rational
from .errors import WriteRefusedError
from .language import well_formed_bcp47

#: RFC 3339 full dates and date-times, as section 6.7 requires of `published` and
#: `modified`. The shape is checked here and the calendar below.
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")

#: An ISO 8601 duration: designators in order, weeks alongside the others, and a
#: fraction only on the seconds. The specification names the standard without
#: choosing among its variants; this is the writer's choice and the reader's.
_DURATION = re.compile(
    r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?!$)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$",
)

#: A path below `assets/`, with nothing that could escape it, reported under
#: the precise requirement rather than a generic pattern failure.
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def check(document: dict[str, Any]) -> None:
    """Refuse a prepared document whose identities or references do not hold.

    A node of the wrong shape is left for the schema check.
    """
    _languages(document)
    _chronology(document)
    _translations(document)
    _claims(document)
    _nutrition(document)
    _dependencies(document)
    assets = _assets(document)
    _images(document, assets)
    timings = _timings(document)
    _durations(document)
    ingredients = _ingredients(document)
    _instructions(document, ingredients, assets, timings)
    choices = {
        item.get("id")
        for section in _items(document.get("ingredient_sections"))
        for item in _items(section.get("ingredients"))
        if "alternatives" in item and isinstance(item.get("id"), str)
    }
    for section in _items(document.get("instruction_sections")):
        for step in _items(section.get("steps")):
            for use in _items(step.get("ingredient_uses")):
                if _identity(use.get("ingredient_ref")) in choices and "quantity" in use:
                    raise WriteRefusedError(
                        "choice-use-quantity", "a whole-choice use cannot carry quantity"
                    )


def _languages(document: dict[str, Any]) -> None:
    """A language or locale is a well-formed BCP 47 tag, not `en_US`."""
    for member in ("language", "source_locale"):
        value = document.get(member)
        if isinstance(value, str) and not well_formed_bcp47(value):
            raise WriteRefusedError(
                "invalid-language-tag", f"{value!r} is not a BCP 47 tag", member
            )
    for index, translation in enumerate(_items(document.get("translations"))):
        language = translation.get("language")
        if isinstance(language, str) and not well_formed_bcp47(language):
            raise WriteRefusedError(
                "translation-invalid-language",
                f"{language!r} is not a BCP 47 tag",
                f"translations[{index}].language",
            )


def _chronology(document: dict[str, Any]) -> None:
    """`published` and `modified` are real RFC 3339 dates, not prose or 2026-02-31."""
    for member in ("published", "modified"):
        value = document.get(member)
        if not isinstance(value, str):
            continue
        if not (_DATE.match(value) or _DATE_TIME.match(value)):
            raise WriteRefusedError(
                "invalid-publication-chronology",
                f"{member} is an RFC 3339 full date or date-time, not localized prose",
                member,
            )
        try:
            datetime.date.fromisoformat(value[:10])
        except ValueError:
            raise WriteRefusedError(
                "invalid-publication-date", f"{value!r} is not a real calendar date", member
            ) from None


def _durations(document: dict[str, Any]) -> None:
    """Every numeric duration is an ISO 8601 duration."""
    times = document.get("times")
    if not isinstance(times, dict):
        return
    expressions = [
        (f"times.{member}", times[member])
        for member in ("prep", "cook", "additional", "total")
        if member in times
    ]
    for index, assertion in enumerate(_items(times.get("assertions"))):
        if "duration" in assertion:
            expressions.append((f"times.assertions[{index}].duration", assertion["duration"]))
    for path, expression in expressions:
        if not isinstance(expression, dict):
            continue
        for member in ("value", "min", "max"):
            value = expression.get(member)
            if isinstance(value, str) and not _DURATION.match(value):
                raise WriteRefusedError(
                    "invalid-duration",
                    f"{value!r} is not an ISO 8601 duration",
                    f"{path}.{member}",
                )


def _assets(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Asset ids and paths are both unique, and every path stays under `assets/`."""
    assets: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for index, asset in enumerate(_items(document.get("assets"))):
        path = f"assets[{index}]"
        asset_id = _identity(asset.get("id"))
        if asset_id is not None and asset_id in assets:
            raise WriteRefusedError(
                "duplicate-asset-id",
                f"asset {asset_id!r} is declared twice",
                path,
            )
        if asset_id is not None:
            assets[asset_id] = asset

        declared = asset.get("path")
        if declared is not None and not isinstance(declared, str):
            continue
        # Section 15's fifth rule compares paths after NFC and case folding.
        collated = _collate(declared)
        if collated in paths:
            raise WriteRefusedError(
                "duplicate-asset-path",
                f"{declared!r} collides with a path already declared",
                f"{path}.path",
            )
        paths.add(collated)
        if not _safe_asset_path(declared):
            # Checked before `zipfile`, which stores whatever name it is given.
            raise WriteRefusedError(
                "unsafe-asset-path",
                f"{declared!r} is not a safe asset path",
                f"{path}.path",
            )
    return assets


def _collate(declared: object) -> object:
    """One archive path reduced to what a reader will compare it as."""
    if not isinstance(declared, str):
        return declared
    return unicodedata.normalize("NFC", declared).casefold()


def _safe_asset_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("assets/"):
        return False
    if "\\" in value or "\x00" in value or _DRIVE_LETTER.match(value):
        return False
    segments = value.split("/")
    return all(segment not in ("", ".", "..") for segment in segments[1:])


def _images(document: dict[str, Any], assets: dict[str, dict[str, Any]]) -> None:
    primary = document.get("primary_image")
    if isinstance(primary, str):
        asset = assets.get(primary)
        if asset is None:
            raise WriteRefusedError(
                "dangling-recipe-image-reference",
                f"{primary!r} does not resolve",
                "primary_image",
            )
        if not str(asset.get("media_type", "")).startswith("image/"):
            raise WriteRefusedError(
                "primary-image-media-type",
                f"{primary!r} is not an image",
                "primary_image",
            )
        references = document.get("image_refs")
        if not isinstance(references, list) or primary not in references:
            raise WriteRefusedError(
                "primary-image-not-in-image-refs",
                "the primary image is one of the recipe's own images",
                "primary_image",
            )
    _references(
        document.get("image_refs"),
        assets,
        "image_refs",
        duplicate="duplicate-recipe-image-reference",
        dangling="dangling-recipe-image-reference",
        media_type="recipe-image-media-type",
    )


def _references(
    references: Any,
    assets: dict[str, dict[str, Any]],
    path: str,
    *,
    duplicate: str,
    dangling: str,
    media_type: str,
) -> None:
    if not isinstance(references, list):
        return
    seen: set[str] = set()
    for index, reference in enumerate(references):
        reference_path = f"{path}[{index}]"
        if _identity(reference) is None:
            continue
        if reference in seen:
            raise WriteRefusedError(duplicate, f"{reference!r} is referenced twice", reference_path)
        seen.add(reference)
        asset = assets.get(reference)
        if asset is None:
            raise WriteRefusedError(
                dangling,
                f"{reference!r} does not resolve to an asset",
                reference_path,
            )
        if not str(asset.get("media_type", "")).startswith("image/"):
            raise WriteRefusedError(
                media_type,
                f"{reference!r} is not an image asset",
                reference_path,
            )


def _items(node: Any) -> list[dict[str, Any]]:
    """The mappings in what should be an array of them, and nothing else."""
    if not isinstance(node, list):
        return []
    return [item for item in node if isinstance(item, dict)]


def _identity(value: Any) -> str | None:
    """An id worth tracking, which means a string.

    An id of some other type cannot collide with anything and cannot be
    referenced, so uniqueness has nothing to say about it. The schema does.
    """
    return value if isinstance(value, str) else None


def _timings(document: dict[str, Any]) -> set[str]:
    times = document.get("times")
    if not isinstance(times, dict):
        return set()
    timings: set[str] = set()
    for index, assertion in enumerate(_items(times.get("assertions"))):
        assertion_id = _identity(assertion.get("id"))
        if assertion_id is not None and assertion_id in timings:
            raise WriteRefusedError(
                "duplicate-timing-id",
                f"timing {assertion_id!r} is declared twice",
                f"times.assertions[{index}]",
            )
        if assertion_id is not None:
            timings.add(assertion_id)
        microwave = assertion.get("microwave")
        if isinstance(microwave, dict):
            version = document.get("sref", {}).get("version")
            if _version(version) is not None and _version(version) < (0, 4, 0):
                raise WriteRefusedError(
                    "member-not-defined-by-version",
                    "microwave conditions were added in SREF 0.4.0",
                    f"times.assertions[{index}].microwave",
                )
            _microwave(microwave, f"times.assertions[{index}].microwave")
    return timings


def _translations(document: dict[str, Any]) -> None:
    """Section 6.9: relationships name other renditions and at most one original."""
    translations = document.get("translations")
    if not isinstance(translations, list):
        return
    if translations and "language" not in document:
        raise WriteRefusedError(
            "translation-requires-document-language",
            "a recipe that declares translations states its own language",
            "language",
        )
    originals = 0
    for index, translation in enumerate(_items(translations)):
        if translation.get("recipe_id") == document.get("id"):
            raise WriteRefusedError(
                "translation-self-reference",
                "a relationship names another recipe, not this one",
                f"translations[{index}].recipe_id",
            )
        originals += translation.get("relation") == "original"
    if originals > 1:
        raise WriteRefusedError(
            "translation-single-original", "a recipe declares at most one original", "translations"
        )


def _claims(document: dict[str, Any]) -> None:
    """Section 6.11: variant IDs are unique and a claim's scope resolves."""
    variants: set[str] = set()
    for index, variant in enumerate(_items(document.get("variants"))):
        variant_id = _identity(variant.get("id"))
        if variant_id is None:
            continue
        if variant_id in variants:
            raise WriteRefusedError(
                "duplicate-variant-id",
                f"variant {variant_id!r} is declared twice",
                f"variants[{index}].id",
            )
        variants.add(variant_id)
    for member in ("dietary_claims", "allergen_declarations"):
        for index, claim in enumerate(_items(document.get(member))):
            reference = claim.get("variant_ref")
            if isinstance(reference, str) and reference not in variants:
                raise WriteRefusedError(
                    "dangling-claim-variant-reference",
                    f"variant {reference!r} does not exist",
                    f"{member}[{index}].variant_ref",
                )


def _nutrition(document: dict[str, Any]) -> None:
    """Section 6.10: a statement lists each nutrient and label once."""
    for index, statement in enumerate(_items(document.get("nutrition"))):
        seen: set[tuple[Any, Any]] = set()
        for position, nutrient in enumerate(_items(statement.get("nutrients"))):
            identity = (nutrient.get("nutrient"), nutrient.get("label", ""))
            if identity in seen:
                raise WriteRefusedError(
                    "nutrition-duplicate-nutrient",
                    f"{identity[0]!r} is listed twice",
                    f"nutrition[{index}].nutrients[{position}]",
                )
            seen.add(identity)


def _dependencies(document: dict[str, Any]) -> None:
    """Section 10.3: a dependency names another recipe."""
    for section in _items(document.get("ingredient_sections")):
        entries = list(_items(section.get("ingredients")))
        entries += [branch for item in entries for branch in _items(item.get("alternatives"))]
        for ingredient in entries:
            reference = ingredient.get("recipe")
            if isinstance(reference, dict) and reference.get("recipe_id") == document.get("id"):
                raise WriteRefusedError(
                    "dependency-self-reference",
                    "a recipe dependency names another recipe, not this one",
                    f"ingredient {ingredient.get('id')}.recipe.recipe_id",
                )


def _version(value: object) -> tuple[int, int, int] | None:
    try:
        parts = tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return None
    return parts if len(parts) == 3 else None


def _microwave(microwave: dict[str, Any], path: str) -> None:
    def check_power(power: Any, power_path: str) -> None:
        if not isinstance(power, dict) or "percent" not in power:
            return
        try:
            value = rational.parse(power["percent"])
        except rational.NotCanonicalError as exc:
            raise WriteRefusedError(exc.code, str(exc), f"{power_path}.percent") from exc
        if value <= 0 or value > 100:
            raise WriteRefusedError(
                "microwave-percent-out-of-range",
                "power percent must be greater than 0 and at most 100",
                f"{power_path}.percent",
            )

    check_power(microwave.get("power"), f"{path}.power")
    choices = microwave.get("choices")
    if not isinstance(choices, list):
        return
    if "rated_output_watts" in microwave or "power" in microwave:
        raise WriteRefusedError(
            "microwave-choice-shared-power-conflict",
            "a choice schedule must not also carry shared rated output or power",
            path,
        )
    seen: set[str] = set()
    for index, choice in enumerate(_items(choices)):
        check_power(choice.get("power"), f"{path}.choices[{index}].power")
        condition = json.dumps(
            {key: choice[key] for key in ("rated_output_watts", "power") if key in choice},
            sort_keys=True,
            separators=(",", ":"),
        )
        if condition in seen:
            raise WriteRefusedError(
                "microwave-choice-duplicate-conditions",
                "microwave choice conditions must be distinct",
                f"{path}.choices[{index}]",
            )
        seen.add(condition)


def _ingredients(document: dict[str, Any]) -> set[str]:
    """Ingredient ids are unique recipe-wide; section ids within their own type."""
    sections: set[str] = set()
    ingredients: set[str] = set()
    for index, section in enumerate(_items(document.get("ingredient_sections"))):
        path = f"ingredient_sections[{index}]"
        section_id = _identity(section.get("id"))
        if section_id is not None and section_id in sections:
            raise WriteRefusedError(
                "duplicate-section-id",
                f"section {section_id!r} is declared twice",
                path,
            )
        if section_id is not None:
            sections.add(section_id)
        entries = list(_items(section.get("ingredients")))
        entries += [branch for item in entries for branch in _items(item.get("alternatives"))]
        for position, ingredient in enumerate(entries):
            ingredient_id = _identity(ingredient.get("id"))
            if ingredient_id is not None and ingredient_id in ingredients:
                # Recipe-wide, since a step's `ingredient_ref` addresses the
                # whole recipe.
                raise WriteRefusedError(
                    "duplicate-ingredient-id",
                    f"ingredient {ingredient_id!r} is declared twice",
                    f"{path}.ingredients[{position}]",
                )
            if ingredient_id is not None:
                ingredients.add(ingredient_id)
    return ingredients


def _instructions(
    document: dict[str, Any],
    ingredients: set[str],
    assets: dict[str, dict[str, Any]],
    timings: set[str],
) -> None:
    sections: set[str] = set()
    steps: set[str] = set()
    for index, section in enumerate(_items(document.get("instruction_sections"))):
        path = f"instruction_sections[{index}]"
        section_id = _identity(section.get("id"))
        if section_id is not None and section_id in sections:
            raise WriteRefusedError(
                "duplicate-instruction-section-id",
                f"section {section_id!r} is declared twice",
                path,
            )
        if section_id is not None:
            sections.add(section_id)
        for position, step in enumerate(_items(section.get("steps"))):
            step_path = f"{path}.steps[{position}]"
            step_id = _identity(step.get("id"))
            if step_id is not None and step_id in steps:
                raise WriteRefusedError(
                    "duplicate-step-id",
                    f"step {step_id!r} is declared twice",
                    step_path,
                )
            if step_id is not None:
                steps.add(step_id)
            _step(step, step_path, ingredients, assets, timings)


def _step(
    step: dict[str, Any],
    path: str,
    ingredients: set[str],
    assets: dict[str, dict[str, Any]],
    timings: set[str],
) -> None:
    seen: set[str] = set()
    for index, use in enumerate(_items(step.get("ingredient_uses"))):
        use_path = f"{path}.ingredient_uses[{index}]"
        reference = _identity(use.get("ingredient_ref"))
        if reference is None:
            continue
        if reference in seen:
            raise WriteRefusedError(
                "duplicate-step-ingredient-reference",
                f"{reference!r} is used twice in one step",
                use_path,
            )
        seen.add(reference)
        if reference not in ingredients:
            raise WriteRefusedError(
                "dangling-ingredient-reference",
                f"{reference!r} does not resolve to an ingredient",
                use_path,
            )

    _references(
        step.get("image_refs"),
        assets,
        f"{path}.image_refs",
        duplicate="duplicate-step-image-reference",
        dangling="dangling-step-image-reference",
        media_type="step-image-media-type",
    )

    seen_timings: set[str] = set()
    references = step.get("timing_refs")
    for index, reference in enumerate(references if isinstance(references, list) else []):
        reference_path = f"{path}.timing_refs[{index}]"
        if _identity(reference) is None:
            continue
        if reference in seen_timings:
            raise WriteRefusedError(
                "duplicate-step-timing-reference",
                f"{reference!r} is referenced twice",
                reference_path,
            )
        seen_timings.add(reference)
        if reference not in timings:
            raise WriteRefusedError(
                "dangling-step-timing-reference",
                f"{reference!r} does not resolve to a timing assertion",
                reference_path,
            )
