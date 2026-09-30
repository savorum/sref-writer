# Usage

## Write

```python
import sref_writer
from fractions import Fraction

document = {
    "id": "shortbread",
    "title": "Shortbread",
    "ingredient_sections": [
        {
            "id": "dough",
            "ingredients": [
                {
                    "id": "butter",
                    "name": "butter",
                    "quantity": {
                        "kind": "simple",
                        "amount": {"value": Fraction(1, 2)},
                        "unit": "mass.pound.avoirdupois",
                    },
                }
            ],
        }
    ],
    "instruction_sections": [
        {
            "id": "method",
            "steps": [{"id": "bake", "text": "Bake until pale gold."}],
        }
    ],
}

open("shortbread.recipe.json", "wb").write(sref_writer.write_recipe(document))
open("shortbread.sref", "wb").write(sref_writer.write_package(document, {}))
open("library.srefbundle", "wb").write(sref_writer.write_bundle([(document, None)]))
```

`write_package` takes the recipe and a mapping from asset ID to bytes.
`write_bundle` takes an ordered sequence of `(recipe, assets)` pairs and keeps
that order.

The writer works on its own copy of the document, so the caller's value is
never changed.

## Diagnostics

Invalid input raises `WriteRefusedError`:

```python
try:
    sref_writer.write_recipe(document)
except sref_writer.WriteRefusedError as refused:
    print(refused.category, refused.requirement_id, refused.path)
```

- `category` is one of SREF's seven portable failure categories.
- `requirement_id` names the specific rule the input failed. It is diagnostic
  detail and is not portable between implementations. `code` is an alias for
  it.
- `path` locates the offending value in the document.

## Normalization

Amounts must be exact: a `Fraction`, an `int`, or a canonical rational string.

```python
{"value": Fraction(2, 4)}  # written as "1/2"
{"value": 3}  # written as "3"
{"value": "2/4"}  # refused: unreduced-rational
{"value": 1 / 3}  # refused: a float is not an exact amount
```

A member set to its defined default, such as `"approximate": false`, is omitted,
because SREF defines both spellings as equal. An explicit `true` is always kept.

A package manifest records the digests of the bytes actually written. A stale
digest supplied by the caller is replaced, not trusted.

The same bundle inputs in the same order produce the same bytes. SREF defines no
canonical ZIP encoding, so consumers must not rely on this.

## Units

Unit IDs are checked against the unit registry the document declares. A document
declaring a compatible newer registry keeps unit IDs this build does not know,
because they may have been added after it.

## What a recipe must contain

A recipe needs `ingredient_sections`, `instruction_sections`, or both, and any
section array present must be nonempty. A recipe with only one of them is
written as it is, with nothing added. A record with neither is refused.

An ingredient may be a choice: a list of complete `alternatives`, each with its
own ID, name, quantity, and modifiers, joined by a `relation` of `or`,
`and_or`, or a preferred substitution. The choice and each branch keep their own
notes and optionality. A step may refer to the choice as a whole, or to one
branch with that branch's amount.

## Refusals

The writer refuses input that would produce an invalid artifact, including:

- a step with a title but no instruction text;
- a quantity alternative nested inside another;
- a range whose minimum exceeds its maximum;
- a unit the declared registry does not define;
- a temperature unit where an amount is expected, or a mass or volume unit
  where a temperature is expected;
- a count or container unit used as a package size;
- an ingredient, step, section, asset, or timing ID used twice;
- a reference to an ingredient, image, or timing that does not exist;
- an asset path outside `assets/`;
- a package whose recipe declares an asset with no supplied bytes, or bytes for
  an asset the recipe does not declare;
- an empty bundle;
- a language or locale that is not a well-formed BCP 47 tag, such as `en_US`,
  including the languages of translation relationships;
- a `published` or `modified` value that is prose, or a date that does not exist,
  such as `2026-02-31`;
- a duration that is not ISO 8601 as the reader reads it: fractions only on
  seconds, with a period, so `PBANANA`, `P1.5D`, and `PT0,5H` are refused;
- a value JSON cannot represent, such as a nonfinite number;
- anything else the published recipe schema rejects.
