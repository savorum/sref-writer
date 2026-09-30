"""Tests for what the corpus does not pin down.

`tools/conformance.py` runs the published corpus, which is the real proof that
this writer follows the specification. These cover the API, the normalizations
the writer performs, and the things it refuses.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re
import sys
import unicodedata
import unittest
import zipfile
from fractions import Fraction
from importlib import resources

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_writer
from sref_writer import snapshot
from sref_writer.errors import WriteRefusedError

MINIMAL = {
    "id": "minimal",
    "title": "Minimal",
    "ingredient_sections": [{"id": "i", "ingredients": [{"id": "salt", "name": "salt"}]}],
    "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Cook."}]}],
}


def recipe(**changes):
    body = json.loads(json.dumps(MINIMAL))
    body.update(changes)
    return body


def quantity(value, **extra):
    return {"kind": "simple", "amount": {"value": value, **extra}, "unit": "mass.gram"}


def rich_recipe():
    """A document touching every structure the writer normalizes."""
    body = recipe()
    body["assets"] = [
        {
            "id": "a",
            "path": "assets/a.png",
            "media_type": "image/png",
            "sha256": "0" * 64,
            "size": 1,
        },
    ]
    body["image_refs"] = ["a"]
    body["primary_image"] = "a"
    body["yields"] = [{"text": "4", "quantity": {"kind": "simple", "amount": {"value": "4"}}}]
    body["times"] = {"assertions": [{"id": "t", "kind": "cook", "duration": {"value": "PT1H"}}]}
    body["ingredient_sections"][0]["ingredients"][0].update(
        {
            "quantity": {
                "kind": "sum",
                "terms": [
                    {"kind": "simple", "amount": {"value": "1"}, "unit": "mass.gram"},
                    {"kind": "simple", "amount": {"min": "1", "max": "2"}, "unit": "mass.gram"},
                ],
            },
            "package_size": {"kind": "simple", "amount": {"value": "400"}, "unit": "mass.gram"},
            "temperature": {"amount": {"value": "4"}, "unit": "temperature.celsius"},
        },
    )
    body["instruction_sections"][0]["steps"][0].update(
        {
            "ingredient_uses": [
                {
                    "ingredient_ref": "salt",
                    "quantity": {
                        "kind": "alternatives",
                        "options": [
                            {"kind": "simple", "amount": {"value": "1"}, "unit": "mass.gram"},
                            {
                                "kind": "simple",
                                "amount": {"value": "1"},
                                "unit": "volume.teaspoon.us.legal",
                            },
                        ],
                    },
                },
            ],
            "image_refs": ["a"],
            "timing_refs": ["t"],
            "temperatures": [
                {
                    "amount": {"value": "180"},
                    "unit": "temperature.celsius",
                    "purpose": {"kind": "oven"},
                },
            ],
        },
    )
    return body


def with_quantity(value, **extra):
    body = recipe()
    body["ingredient_sections"][0]["ingredients"][0]["quantity"] = quantity(value, **extra)
    return body


class MinimumContent(unittest.TestCase):
    def test_packages_and_bundles_preserve_absence(self):
        for omitted in ("ingredient_sections", "instruction_sections"):
            document = {key: value for key, value in MINIMAL.items() if key != omitted}
            package = sref_writer.write_package(document, {})
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                recipe = json.loads(archive.read("recipe.json"))
                self.assertNotIn(omitted, recipe)
            bundle = sref_writer.write_bundle([(document, None)])
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                packages = [name for name in archive.namelist() if name.endswith(".sref")]
                self.assertEqual(len(packages), 1)
                with zipfile.ZipFile(io.BytesIO(archive.read(packages[0]))) as member:
                    self.assertNotIn(omitted, json.loads(member.read("recipe.json")))


class Equipment(unittest.TestCase):
    def test_equipment_order_names_quantities_and_notes_are_written(self):
        body = recipe(
            equipment=[
                {
                    "name": "baking sheet",
                    "quantity": {
                        "kind": "simple",
                        "amount": {"value": 2},
                        "unit": "count.item",
                    },
                },
                {"name": "stand mixer with dough hook", "note": "Use the dough hook."},
            ]
        )
        written = json.loads(sref_writer.write_recipe(body))
        self.assertEqual(
            [item["name"] for item in written["equipment"]],
            [
                "baking sheet",
                "stand mixer with dough hook",
            ],
        )
        self.assertEqual(written["equipment"][0]["quantity"]["amount"]["value"], "2")
        self.assertNotIn("id", written["equipment"][0])

    def test_temperature_is_not_an_equipment_quantity(self):
        body = recipe(
            equipment=[
                {
                    "name": "oven",
                    "quantity": {
                        "kind": "simple",
                        "amount": {"value": 180},
                        "unit": "temperature.celsius",
                    },
                }
            ]
        )
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        self.assertEqual(caught.exception.code, "temperature-equipment")


class Amounts(unittest.TestCase):
    def test_a_fraction_becomes_a_canonical_string(self):
        written = json.loads(sref_writer.write_recipe(with_quantity(Fraction(2, 4))))
        amount = written["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]
        self.assertEqual(amount["value"], "1/2")

    def test_an_integer_is_accepted_without_a_denominator(self):
        written = json.loads(sref_writer.write_recipe(with_quantity(3)))
        amount = written["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]
        self.assertEqual(amount["value"], "3")

    def test_a_precomposed_noncanonical_string_is_not_trusted(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(with_quantity("2/4"))
        self.assertEqual(caught.exception.code, "unreduced-rational")
        self.assertEqual(caught.exception.requirement_id, "unreduced-rational")
        self.assertEqual(caught.exception.category, "invalid-artifact")

    def test_a_float_is_refused_rather_than_rounded(self):
        with self.assertRaises(WriteRefusedError):
            sref_writer.write_recipe(with_quantity(1 / 3))

    def test_a_reversed_range_is_refused(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["quantity"] = {
            "kind": "simple",
            "amount": {"min": "3", "max": "2"},
            "unit": "mass.gram",
        }
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        self.assertEqual(caught.exception.code, "range-order")


class Defaults(unittest.TestCase):
    def test_a_member_holding_its_own_default_is_omitted(self):
        # Section 24 makes an omitted default and an explicit one equal, so the
        # shorter spelling is written.
        written = json.loads(sref_writer.write_recipe(with_quantity("2", approximate=False)))
        amount = written["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]
        self.assertNotIn("approximate", amount)

    def test_an_authored_true_is_never_dropped(self):
        written = json.loads(sref_writer.write_recipe(with_quantity("2", approximate=True)))
        amount = written["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]
        self.assertTrue(amount["approximate"])

    def test_optional_true_survives_while_optional_false_is_omitted(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["optional"] = True
        self.assertTrue(
            json.loads(sref_writer.write_recipe(body))["ingredient_sections"][0]["ingredients"][0][
                "optional"
            ],
        )
        body["ingredient_sections"][0]["ingredients"][0]["optional"] = False
        self.assertNotIn(
            "optional",
            json.loads(sref_writer.write_recipe(body))["ingredient_sections"][0]["ingredients"][0],
        )


class Versions(unittest.TestCase):
    def test_the_header_is_supplied_and_written_first(self):
        written = sref_writer.write_recipe(recipe())
        self.assertEqual(json.loads(written)["sref"]["version"], "0.4.0")
        self.assertLess(written.index(b'"sref"'), written.index(b'"id"'))

    def test_a_compatible_newer_document_keeps_the_version_it_declared(self):
        # A newer compatible version is kept, not relabelled.
        body = recipe(
            sref={"version": "0.5.0", "unit_registry": "0.3.0"},
            publication={"status": "draft"},
        )
        written = json.loads(sref_writer.write_recipe(body))
        self.assertEqual(written["sref"]["version"], "0.5.0")
        self.assertEqual(written["publication"], {"status": "draft"})

    def test_an_older_line_is_refused(self):
        with self.assertRaises(WriteRefusedError):
            sref_writer.write_recipe(recipe(sref={"version": "0.0.1", "unit_registry": "0.2.0"}))


class Preservation(unittest.TestCase):
    def test_unknown_members_and_extensions_pass_through_untouched(self):
        body = recipe(**{"x-org.example.note": {"kept": True, "count": 4}})
        written = json.loads(sref_writer.write_recipe(body))
        self.assertEqual(written["x-org.example.note"], {"kept": True, "count": 4})

    def test_the_writer_refuses_content_json_cannot_carry(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(recipe(**{"x-org.example.n": float("inf")}))
        self.assertEqual(caught.exception.code, "nonfinite-json-number")


class Packages(unittest.TestCase):
    def _recipe_with_asset(self):
        return recipe(
            assets=[
                {
                    "id": "hero",
                    "path": "assets/hero.png",
                    "media_type": "image/png",
                    "sha256": "0" * 64,
                    "size": 0,
                },
            ],
        )

    def test_the_manifest_describes_the_bytes_actually_written(self):
        # The caller's declared digest is deliberately wrong.
        import hashlib

        content = b"not really a png"
        data = sref_writer.write_package(self._recipe_with_asset(), {"hero": content})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            recipe_bytes = archive.read("recipe.json")
        self.assertEqual(manifest["assets"][0]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(manifest["recipe"]["sha256"], hashlib.sha256(recipe_bytes).hexdigest())
        self.assertEqual(manifest["recipe"]["size"], len(recipe_bytes))

    def test_a_declared_asset_with_no_bytes_is_refused(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_package(self._recipe_with_asset(), {})
        self.assertEqual(caught.exception.code, "missing-asset-entry")

    def test_bytes_the_recipe_does_not_declare_are_refused(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_package(recipe(), {"stowaway": b"x"})
        self.assertEqual(caught.exception.code, "undeclared-archive-entry")

    def test_no_directory_entries_are_written(self):
        data = sref_writer.write_package(self._recipe_with_asset(), {"hero": b"x"})
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["assets/hero.png", "manifest.json", "recipe.json"],
            )


class Bundles(unittest.TestCase):
    def test_order_is_preserved_and_member_ids_come_from_position(self):
        data = sref_writer.write_bundle([(recipe(id="first"), None), (recipe(id="second"), None)])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(
            [entry["member_id"] for entry in manifest["recipes"]],
            ["r000001", "r000002"],
        )
        self.assertEqual([entry["recipe_id"] for entry in manifest["recipes"]], ["first", "second"])

    def test_a_member_id_never_leaks_a_title_or_a_local_identifier(self):
        data = sref_writer.write_bundle([(recipe(id="secret-database-key", title="Private"), None)])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ["manifest.json", "recipes/r000001.sref"])

    def test_the_manifest_records_no_clock(self):
        data = sref_writer.write_bundle([(recipe(), None)])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(set(manifest), {"format", "version", "recipes"})

    def test_the_same_members_in_the_same_order_produce_the_same_bytes(self):
        members = [(recipe(id="a"), None), (recipe(id="b"), None)]
        self.assertEqual(sref_writer.write_bundle(members), sref_writer.write_bundle(members))

    def test_an_empty_bundle_is_refused(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_bundle([])
        self.assertEqual(caught.exception.code, "empty-bundle")

    def test_a_member_with_an_unknown_unit_is_refused(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["quantity"] = {
            "kind": "simple",
            "amount": {"value": 1},
            "unit": "mass.smidgen",
        }
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_bundle([(body, None)])
        self.assertEqual(caught.exception.category, "unknown-unit")
        self.assertEqual(caught.exception.requirement_id, "unknown-unit")


class Refusals(unittest.TestCase):
    def test_a_step_without_text_is_refused(self):
        body = recipe()
        body["instruction_sections"][0]["steps"][0] = {"id": "s", "title": "Heading only"}
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        self.assertEqual(caught.exception.code, "step-text-required")

    def test_a_nested_alternative_is_refused(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["quantity"] = {
            "kind": "alternatives",
            "options": [{"kind": "alternatives", "options": []}],
        }
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        self.assertEqual(caught.exception.code, "nested-quantity-alternative")


class Units(unittest.TestCase):
    """The refusals that need the pinned registry to answer.

    A unit the declared registry does not define is refused (section 22).
    """

    def ingredient(self, **changes):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0].update(changes)
        return body

    def refusal(self, body):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        return caught.exception

    def test_a_registered_unit_is_written(self):
        body = self.ingredient(quantity=quantity(1))
        written = json.loads(sref_writer.write_recipe(body))
        self.assertEqual(
            written["ingredient_sections"][0]["ingredients"][0]["quantity"]["unit"],
            "mass.gram",
        )

    def test_an_unregistered_unit_is_refused(self):
        refused = self.refusal(
            self.ingredient(
                quantity={"kind": "simple", "amount": {"value": 1}, "unit": "mass.smidgen"},
            ),
        )
        self.assertEqual(refused.code, "unknown-unit")
        self.assertEqual(refused.requirement_id, "unknown-unit")
        self.assertEqual(refused.category, "unknown-unit")
        self.assertTrue(refused.path.endswith(".quantity.unit"), refused.path)

    def test_an_unregistered_unit_inside_a_sum_is_refused(self):
        refused = self.refusal(
            self.ingredient(
                quantity={
                    "kind": "sum",
                    "terms": [
                        {
                            "kind": "simple",
                            "amount": {"value": 1},
                            "unit": "mass.pound.avoirdupois",
                        },
                        {"kind": "simple", "amount": {"value": 2}, "unit": "mass.smidgen"},
                    ],
                },
            ),
        )
        self.assertEqual(refused.code, "unknown-unit")

    def test_a_quantity_without_a_unit_is_written(self):
        # `2 eggs` counts something the registry does not have to name.
        body = self.ingredient(quantity={"kind": "simple", "amount": {"value": 2}})
        self.assertIn(b'"value": "2"', sref_writer.write_recipe(body))

    def test_a_temperature_unit_in_a_quantity_is_refused(self):
        refused = self.refusal(
            self.ingredient(
                quantity={
                    "kind": "simple",
                    "amount": {"value": 180},
                    "unit": "temperature.celsius",
                },
            ),
        )
        self.assertEqual(refused.code, "temperature-as-quantity")

    def test_a_temperature_unit_in_a_yield_is_its_own_refusal(self):
        body = recipe()
        body["yields"] = [
            {
                "quantity": {
                    "kind": "simple",
                    "amount": {"value": 180},
                    "unit": "temperature.celsius",
                }
            },
        ]
        self.assertEqual(self.refusal(body).code, "temperature-yield")

    def test_a_claim_scoped_to_a_missing_variant_is_refused(self):
        body = recipe()
        body["allergen_declarations"] = [
            {"text": "Nut-free", "substance": "nuts", "presence": "free_from", "variant_ref": "x"},
        ]
        self.assertEqual(self.refusal(body).code, "dangling-claim-variant-reference")

    def test_a_dependency_on_this_recipe_is_refused(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["recipe"] = {"recipe_id": body["id"]}
        self.assertEqual(self.refusal(body).code, "dependency-self-reference")

    def test_a_temperature_unit_in_a_step_ingredient_use_is_its_own_refusal(self):
        body = recipe()
        body["instruction_sections"][0]["steps"][0]["ingredient_uses"] = [
            {
                "ingredient_ref": "salt",
                "quantity": {
                    "kind": "simple",
                    "amount": {"value": 180},
                    "unit": "temperature.celsius",
                },
            },
        ]
        self.assertEqual(self.refusal(body).code, "temperature-ingredient-use")

    def test_a_count_unit_as_a_package_size_is_refused(self):
        refused = self.refusal(
            self.ingredient(
                package_size={"kind": "simple", "amount": {"value": 1}, "unit": "count.clove"},
            ),
        )
        self.assertEqual(refused.code, "nonphysical-package-size")

    def test_a_physical_package_size_is_written(self):
        body = self.ingredient(
            quantity={"kind": "simple", "amount": {"value": 2}, "unit": "container.can"},
            package_size={
                "kind": "simple",
                "amount": {"value": 14},
                "unit": "mass.ounce.avoirdupois",
            },
        )
        self.assertIn(b"container.can", sref_writer.write_recipe(body))

    def test_a_unit_that_does_not_measure_temperature_is_refused_in_one(self):
        refused = self.refusal(
            self.ingredient(temperature={"amount": {"value": 180}, "unit": "mass.gram"}),
        )
        self.assertEqual(refused.code, "invalid-temperature-unit")

    def test_a_newer_registry_carries_units_this_build_has_never_seen(self):
        # Section 5: an ID from a compatible newer registry is preserved.
        body = self.ingredient(
            quantity={"kind": "simple", "amount": {"value": 1}, "unit": "mass.smidgen"},
        )
        body["sref"] = {"version": "0.4.0", "unit_registry": "0.3.0"}
        written = json.loads(sref_writer.write_recipe(body))
        self.assertEqual(
            written["ingredient_sections"][0]["ingredients"][0]["quantity"]["unit"],
            "mass.smidgen",
        )


class MicrowaveConditions(unittest.TestCase):
    def body(self, microwave, duration=None, version=None):
        body = recipe()
        if version is not None:
            body["sref"] = {"version": version, "unit_registry": "0.2.0"}
        body["times"] = {
            "assertions": [
                {
                    "id": "heat",
                    "kind": "cook",
                    "duration": duration or {"value": "PT2M"},
                    "microwave": microwave,
                },
            ],
        }
        body["instruction_sections"][0]["steps"][0]["timing_refs"] = ["heat"]
        return body

    def test_percentage_is_normalized_without_changing_the_caller(self):
        microwave = {
            "rated_output_watts": 1000,
            "power": {"percent": 50},
            "source_text": "50% power in a 1000 W microwave",
        }
        written = json.loads(sref_writer.write_recipe(self.body(microwave)))
        self.assertEqual(
            written["times"]["assertions"][0]["microwave"]["power"]["percent"],
            "50",
        )
        self.assertEqual(microwave["power"]["percent"], 50)

    def test_authored_choice_order_and_restrictions_survive(self):
        microwave = {
            "source_text": "600 W: 4 minutes; 500 W: 5 minutes",
            "restrictions": ["Use only 500 W or 600 W."],
            "choices": [
                {
                    "power": {"watts": 600},
                    "duration": {"value": "PT4M"},
                    "source_text": "600 W: 4 minutes",
                },
                {
                    "power": {"watts": 500},
                    "duration": {"value": "PT5M"},
                    "source_text": "500 W: 5 minutes",
                },
            ],
        }
        written = json.loads(
            sref_writer.write_recipe(
                self.body(microwave, {"text": "600 W: 4 minutes; 500 W: 5 minutes"})
            )
        )
        kept = written["times"]["assertions"][0]["microwave"]
        self.assertEqual([choice["power"]["watts"] for choice in kept["choices"]], [600, 500])
        self.assertEqual(kept["restrictions"], ["Use only 500 W or 600 W."])

    def test_invalid_percentage_and_duplicate_conditions_are_refused(self):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(
                self.body({"power": {"percent": 101}, "source_text": "at 101%"})
            )
        self.assertEqual(caught.exception.code, "microwave-percent-out-of-range")

        duplicate = {
            "source_text": "600 W alternatives",
            "choices": [
                {"power": {"watts": 600}, "duration": {"value": "PT4M"}, "source_text": "first"},
                {"power": {"watts": 600}, "duration": {"value": "PT5M"}, "source_text": "second"},
            ],
        }
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(self.body(duplicate, {"text": "600 W alternatives"}))
        self.assertEqual(caught.exception.code, "microwave-choice-duplicate-conditions")

    def test_writer_does_not_relabel_microwave_conditions_as_0_2(self):
        microwave = {"source_text": "at 600 W", "power": {"watts": 600}}
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(self.body(microwave, version="0.2.0"))
        self.assertEqual(caught.exception.code, "unsupported-format-version")


class PinnedSnapshot(unittest.TestCase):
    """The snapshot is read as package data, not as a path beside the package.

    The `installed` CI job checks packaging; this checks the library never
    reaches outside itself for the snapshot.
    """

    def test_every_snapshot_file_loads_and_matches_its_digest(self):
        for name in snapshot.manifest()["files"]:
            with self.subTest(name=name):
                self.assertIsNotNone(snapshot.load(name))

    def test_the_snapshot_is_read_from_inside_the_package(self):
        located = resources.files("sref_writer") / "_vendor" / "sref-0.4.0" / "manifest.json"
        self.assertTrue(located.is_file())

    def test_the_shipped_registry_is_the_one_this_build_writes_against(self):
        self.assertEqual(
            snapshot.manifest()["unit_registry_version"],
            sref_writer.SUPPORTED_REGISTRY,
        )

    def test_a_snapshot_file_the_release_did_not_record_is_refused(self):
        with self.assertRaises(snapshot.SnapshotCorruptError):
            snapshot.read("schema/invented.schema.json")


class ManifestSchemas(unittest.TestCase):
    """Everything this writer emits validates against the pinned schemas.

    `jsonschema` is a test dependency only. Includes an asset-free package,
    whose manifest still requires `assets`.
    """

    def setUp(self):
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - the CI image installs it
            self.skipTest("jsonschema is not installed")
        self.jsonschema = jsonschema

    def check(self, instance, schema):
        errors = list(self.jsonschema.Draft202012Validator(schema).iter_errors(instance))
        self.assertEqual([error.message for error in errors], [])

    def manifest_of(self, package_bytes):
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
            return json.loads(archive.read("manifest.json"))

    def test_a_package_with_no_assets_still_declares_an_assets_array(self):
        manifest = self.manifest_of(sref_writer.write_package(recipe(), {}))
        self.assertEqual(manifest["assets"], [])
        self.check(manifest, snapshot.manifest_schema())

    def test_a_package_with_an_asset_validates(self):
        body = recipe()
        body["assets"] = [
            {
                "id": "note",
                "path": "assets/note.txt",
                "media_type": "text/plain",
                "sha256": "0" * 64,
                "size": 0,
            },
        ]
        manifest = self.manifest_of(sref_writer.write_package(body, {"note": b"salt to taste"}))
        self.check(manifest, snapshot.manifest_schema())

    def test_a_bundle_manifest_and_every_member_manifest_validate(self):
        written = sref_writer.write_bundle([(recipe(id="a"), None), (recipe(id="b"), None)])
        with zipfile.ZipFile(io.BytesIO(written)) as archive:
            self.check(json.loads(archive.read("manifest.json")), snapshot.bundle_manifest_schema())
            for name in archive.namelist():
                if name == "manifest.json":
                    continue
                with self.subTest(member=name):
                    self.check(self.manifest_of(archive.read(name)), snapshot.manifest_schema())


class Identities(unittest.TestCase):
    """What the writer refuses because the specification forbids emitting it.

    Negative tests, which a corpus of valid fixtures cannot provide.
    """

    def refusal(self, body, assets=None):
        with self.assertRaises(WriteRefusedError) as caught:
            if assets is None:
                sref_writer.write_recipe(body)
            else:
                sref_writer.write_package(body, assets)
        return caught.exception

    def ingredient(self, **changes):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0].update(changes)
        return body

    def with_asset(self, path="assets/a.png", media_type="image/png", **document_changes):
        body = recipe()
        body["assets"] = [
            {"id": "a", "path": path, "media_type": media_type, "sha256": "0" * 64, "size": 0},
        ]
        body.update(document_changes)
        return body

    def test_an_id_that_is_not_portable_is_refused(self):
        self.assertEqual(self.refusal(recipe(id="INVALID ID")).code, "schema-violation")

    def test_an_ingredient_id_used_twice_is_refused(self):
        body = recipe()
        body["ingredient_sections"][0]["ingredients"].append({"id": "salt", "name": "more salt"})
        self.assertEqual(self.refusal(body).code, "duplicate-ingredient-id")

    def test_an_ingredient_id_reused_in_another_section_is_refused(self):
        # Recipe-wide, not section-wide: a step's reference names one
        # ingredient in the recipe.
        body = recipe()
        body["ingredient_sections"].append(
            {"id": "j", "ingredients": [{"id": "salt", "name": "salt again"}]},
        )
        self.assertEqual(self.refusal(body).code, "duplicate-ingredient-id")

    def test_a_step_id_used_twice_is_refused(self):
        body = recipe()
        body["instruction_sections"][0]["steps"].append({"id": "s", "text": "Again."})
        self.assertEqual(self.refusal(body).code, "duplicate-step-id")

    def test_a_section_id_used_twice_within_its_type_is_refused(self):
        body = recipe()
        body["ingredient_sections"].append({"id": "i", "ingredients": [{"id": "x", "name": "x"}]})
        self.assertEqual(self.refusal(body).code, "duplicate-section-id")

    def test_a_malformed_extension_name_is_refused(self):
        body = recipe()
        body["x-bad"] = 1
        self.assertEqual(self.refusal(body).code, "schema-violation")

    def test_a_step_referring_to_no_such_ingredient_is_refused(self):
        body = recipe()
        body["instruction_sections"][0]["steps"][0]["ingredient_uses"] = [
            {"ingredient_ref": "nope"},
        ]
        self.assertEqual(self.refusal(body).code, "dangling-ingredient-reference")

    def test_a_step_using_one_ingredient_twice_is_refused(self):
        body = recipe()
        body["instruction_sections"][0]["steps"][0]["ingredient_uses"] = [
            {"ingredient_ref": "salt"},
            {"ingredient_ref": "salt"},
        ]
        self.assertEqual(self.refusal(body).code, "duplicate-step-ingredient-reference")

    def test_an_asset_id_declared_twice_is_refused(self):
        body = self.with_asset()
        body["assets"].append(dict(body["assets"][0], path="assets/b.png"))
        self.assertEqual(self.refusal(body).code, "duplicate-asset-id")

    def test_an_asset_path_declared_twice_is_refused(self):
        body = self.with_asset()
        body["assets"].append(dict(body["assets"][0], id="b"))
        self.assertEqual(self.refusal(body).code, "duplicate-asset-path")

    def test_an_image_reference_that_resolves_to_nothing_is_refused(self):
        body = self.with_asset(image_refs=["missing"])
        self.assertEqual(self.refusal(body).code, "dangling-recipe-image-reference")

    def test_an_image_reference_to_a_file_that_is_not_an_image_is_refused(self):
        body = self.with_asset(media_type="text/plain", image_refs=["a"])
        self.assertEqual(self.refusal(body).code, "recipe-image-media-type")

    def test_a_primary_image_outside_the_recipes_images_is_refused(self):
        body = self.with_asset(primary_image="a")
        self.assertEqual(self.refusal(body).code, "primary-image-not-in-image-refs")

    def test_a_step_timing_reference_that_resolves_to_nothing_is_refused(self):
        body = recipe()
        body["instruction_sections"][0]["steps"][0]["timing_refs"] = ["nope"]
        self.assertEqual(self.refusal(body).code, "dangling-step-timing-reference")

    def test_an_array_member_holding_something_else_is_refused(self):
        body = recipe()
        body["assets"] = "not-an-array"
        self.assertEqual(self.refusal(body).code, "schema-violation")


class AssetPaths(unittest.TestCase):
    """No path reaches `zipfile` before it has been proven safe.

    `zipfile` stores whatever name it is given, so paths are checked first.
    """

    def package_with(self, path):
        body = recipe()
        body["assets"] = [
            {
                "id": "a",
                "path": path,
                "media_type": "application/octet-stream",
                "sha256": "0" * 64,
                "size": 0,
            },
        ]
        return sref_writer.write_package(body, {"a": b"x"})

    def test_an_escaping_path_is_refused_rather_than_stored(self):
        for path in (
            "../../escape.bin",
            "/absolute.bin",
            "assets/../escape.bin",
            "assets\\escape.bin",
            "assets/./escape.bin",
            "elsewhere/a.bin",
        ):
            with self.subTest(path=path):
                with self.assertRaises(WriteRefusedError) as caught:
                    self.package_with(path)
                self.assertEqual(caught.exception.code, "unsafe-asset-path")

    def test_an_ordinary_asset_path_is_written(self):
        with zipfile.ZipFile(io.BytesIO(self.package_with("assets/ok.bin"))) as archive:
            self.assertIn("assets/ok.bin", archive.namelist())


class CallerDocument(unittest.TestCase):
    def test_writing_does_not_edit_the_document_it_was_given(self):
        # Packaging must not mutate the caller's mapping.
        body = recipe()
        body["assets"] = [
            {
                "id": "a",
                "path": "assets/a.bin",
                "media_type": "application/octet-stream",
                "sha256": "0" * 64,
                "size": 0,
            },
        ]
        before = json.loads(json.dumps(body))
        sref_writer.write_package(body, {"a": b"hello"})
        self.assertEqual(body, before)

    def test_a_fraction_the_caller_still_holds_is_untouched(self):
        amount = Fraction(2, 4)
        body = recipe()
        body["ingredient_sections"][0]["ingredients"][0]["quantity"] = {
            "kind": "simple",
            "amount": {"value": amount},
            "unit": "mass.gram",
        }
        sref_writer.write_recipe(body)
        self.assertEqual(
            body["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]["value"],
            amount,
        )


class MalformedInput(unittest.TestCase):
    """Every malformed shape leaves as `WriteRefusedError`, never as a raw exception.

    The documented failure carries a category and a path, which a raw
    `AttributeError` would not.
    """

    #: Every wrong kind of value, including the ones that are not hashable and
    #: so cannot be a set member or a mapping key.
    POISON = ("", "text", 0, 1.5, True, None, [], {}, [1], {"k": "v"}, [None], [[]])

    def positions(self, node, prefix=()):
        if isinstance(node, dict):
            for name, value in node.items():
                yield (*prefix, name)
                yield from self.positions(value, (*prefix, name))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield (*prefix, index)
                yield from self.positions(value, (*prefix, index))

    def test_no_mutation_of_any_member_escapes_as_a_raw_exception(self):
        document = rich_recipe()
        positions = list(self.positions(document))
        self.assertGreater(len(positions), 40)
        for position in positions:
            for value in self.POISON:
                candidate = json.loads(json.dumps(document))
                node = candidate
                for part in position[:-1]:
                    node = node[part]
                try:
                    node[position[-1]] = value
                except (IndexError, KeyError, TypeError):
                    continue
                for entry in (sref_writer.write_recipe, sref_writer.prepare):
                    _assert_refused(entry, candidate, position, value, self)


def _assert_refused(entry, candidate, position, value, test_case):
    try:
        entry(candidate)
    except WriteRefusedError:
        return
    except Exception as exc:  # pragma: no cover - the failure this guards
        test_case.fail(
            f"{'.'.join(map(str, position))} = {value!r} raised {type(exc).__name__}: {exc}",
        )


class IndependentOracle(unittest.TestCase):
    """The independent oracle, checked itself."""

    @classmethod
    def setUpClass(cls):
        from sref_writer import independent

        cls.independent = independent

    def rebuilt(self, change):
        """A package rebuilt around an altered manifest, bypassing the writer."""
        source = zipfile.ZipFile(io.BytesIO(sref_writer.write_package(recipe(), {})))
        manifest = json.loads(source.read("manifest.json"))
        change(manifest)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as out:
            out.writestr("manifest.json", json.dumps(manifest).encode())
            out.writestr("recipe.json", source.read("recipe.json"))
        return buffer.getvalue()

    def test_it_accepts_what_this_writer_emits(self):
        self.assertEqual(self.independent.check_recipe(sref_writer.write_recipe(rich_recipe())), [])
        self.assertEqual(
            self.independent.check_package(sref_writer.write_package(recipe(), {})),
            [],
        )
        self.assertEqual(
            self.independent.check_bundle(sref_writer.write_bundle([(recipe(), None)])),
            [],
        )

    def test_it_rejects_a_manifest_missing_its_assets_array(self):
        problems = self.independent.check_package(
            self.rebuilt(lambda manifest: manifest.pop("assets")),
        )
        self.assertTrue(any("assets" in problem for problem in problems), problems)

    def test_it_rejects_a_manifest_that_disagrees_with_the_bytes(self):
        problems = self.independent.check_package(
            self.rebuilt(lambda manifest: manifest["recipe"].update(sha256="0" * 64)),
        )
        self.assertTrue(any("digest" in problem for problem in problems), problems)

    def test_it_rejects_a_package_version_the_release_does_not_define(self):
        problems = self.independent.check_package(
            self.rebuilt(lambda manifest: manifest["sref_package"].update(version="9.9.9")),
        )
        self.assertTrue(problems)

    def test_it_rejects_an_undeclared_file_riding_along(self):
        buffer = io.BytesIO()
        source = zipfile.ZipFile(io.BytesIO(sref_writer.write_package(recipe(), {})))
        with zipfile.ZipFile(buffer, "w") as out:
            for name in source.namelist():
                out.writestr(name, source.read(name))
            out.writestr("stowaway.txt", b"x")
        problems = self.independent.check_package(buffer.getvalue())
        self.assertTrue(any("stowaway" in problem for problem in problems), problems)


class FormatsTheReaderChecks(unittest.TestCase):
    """The writer refuses what the reader would reject on these fields."""

    def refusal(self, body):
        with self.assertRaises(WriteRefusedError) as caught:
            sref_writer.write_recipe(body)
        return caught.exception

    def test_an_underscore_locale_is_refused(self):
        error = self.refusal(recipe(source_locale="en_US"))
        self.assertEqual((error.code, error.path), ("invalid-language-tag", "source_locale"))

    def test_a_recipe_language_that_is_not_a_tag_is_refused(self):
        error = self.refusal(recipe(language="not a tag!!"))
        self.assertEqual((error.code, error.path), ("invalid-language-tag", "language"))

    def test_a_translation_language_that_is_not_a_tag_is_refused(self):
        body = recipe(
            language="en",
            translations=[{"relation": "translation", "language": "de_DE", "title": "Salz"}],
        )
        error = self.refusal(body)
        self.assertEqual(error.code, "translation-invalid-language")
        self.assertEqual(error.path, "translations[0].language")

    def test_well_formed_tags_are_written(self):
        for tag in ("en", "en-US", "zh-Hant-TW", "de-DE-u-nu-latn", "x-kitchen"):
            sref_writer.write_recipe(recipe(language=tag, source_locale=tag))

    def test_an_impossible_calendar_date_is_refused(self):
        for value in ("2026-02-31", "2026-13-01", "2025-02-29T10:00:00Z"):
            error = self.refusal(recipe(published=value))
            self.assertEqual((error.code, error.path), ("invalid-publication-date", "published"))

    def test_prose_is_not_a_publication_date(self):
        error = self.refusal(recipe(modified="yesterday"))
        self.assertEqual(error.code, "invalid-publication-chronology")

    def test_real_dates_and_date_times_are_written(self):
        for value in ("2024-02-29", "2026-02-28T23:59:59Z", "2026-02-28T10:00:00.5+02:00"):
            sref_writer.write_recipe(recipe(published=value, modified=value))

    def test_a_duration_that_is_not_iso_8601_is_refused(self):
        for value in ("PBANANA", "P", "PT", "10 minutes", "P1.5D", "PT0,5H", "1H"):
            error = self.refusal(recipe(times={"prep": {"value": value}}))
            self.assertEqual((error.code, error.path), ("invalid-duration", "times.prep.value"))

    def test_a_timing_assertion_duration_is_checked_too(self):
        body = recipe(
            times={"assertions": [{"id": "t", "kind": "cook", "duration": {"max": "PBANANA"}}]}
        )
        error = self.refusal(body)
        self.assertEqual(error.path, "times.assertions[0].duration.max")

    def test_durations_the_reader_accepts_are_written(self):
        for value in ("PT10M", "P1W", "P1Y2M", "P1DT12H", "PT0.5S", "PT1H30M"):
            sref_writer.write_recipe(recipe(times={"prep": {"value": value}}))


class ReadmePromises(unittest.TestCase):
    """Every `sref_writer.` name the README mentions exists."""

    def readme(self) -> str:
        return (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text()

    def test_every_documented_name_exists(self):
        documented = set(re.findall(r"\bsref_writer\.([A-Za-z_][A-Za-z0-9_]*)", self.readme()))
        self.assertTrue(documented, "the README stopped naming the package at all")
        missing = sorted(name for name in documented if not hasattr(sref_writer, name))
        self.assertEqual(missing, [], f"documented but absent from sref_writer: {missing}")


class CollidingArchivePaths(unittest.TestCase):
    """Two asset paths that differ only in case are one archive entry.

    Section 15's fifth rule compares paths after NFC and case folding.
    """

    def package(self, first: str, second: str) -> None:
        document = recipe()
        document["assets"] = [
            {
                "id": "a",
                "path": first,
                "media_type": "text/plain",
                "sha256": hashlib.sha256(b"one").hexdigest(),
                "size": 3,
            },
            {
                "id": "b",
                "path": second,
                "media_type": "text/plain",
                "sha256": hashlib.sha256(b"two").hexdigest(),
                "size": 3,
            },
        ]
        sref_writer.write_package(document, {"a": b"one", "b": b"two"})

    def test_paths_differing_only_in_case_are_refused(self):
        with self.assertRaises(sref_writer.WriteRefusedError) as caught:
            self.package("assets/source.txt", "assets/Source.txt")
        self.assertEqual(caught.exception.code, "duplicate-asset-path")

    def test_paths_differing_only_in_unicode_form_are_refused(self):
        with self.assertRaises(sref_writer.WriteRefusedError) as caught:
            self.package(
                unicodedata.normalize("NFC", "assets/sourc\u00e9.txt"),
                unicodedata.normalize("NFD", "assets/sourc\u00e9.txt"),
            )
        self.assertEqual(caught.exception.code, "duplicate-asset-path")

    def test_genuinely_distinct_paths_still_pass(self):
        self.package("assets/one.txt", "assets/two.txt")


class VersionScopedMembers(unittest.TestCase):
    """A member is admitted by the version the document declares.

    At the version this build implements, an unrecognized non-`x-` member is
    refused rather than carried as opaque future data.
    """

    def document(self, version: str = "0.4.0", **members) -> dict:
        base = {
            "sref": {"version": version, "unit_registry": "0.2.0"},
            "id": "scoped",
            "title": "Scoped",
            "ingredient_sections": [{"id": "i", "ingredients": [{"id": "salt", "name": "salt"}]}],
            "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Season."}]}],
        }
        base.update(members)
        return base

    def test_it_refuses_an_unknown_member_at_the_declared_version(self):
        with self.assertRaises(sref_writer.WriteRefusedError) as caught:
            sref_writer.write_recipe(self.document(future_standard_field={"value": 4}))
        self.assertEqual(caught.exception.code, "member-not-defined-by-version")

    def test_it_refuses_one_nested_inside_an_ingredient(self):
        document = self.document()
        document["ingredient_sections"][0]["ingredients"][0]["future_member"] = True
        with self.assertRaises(sref_writer.WriteRefusedError) as caught:
            sref_writer.write_recipe(document)
        self.assertEqual(caught.exception.code, "member-not-defined-by-version")

    def test_it_accepts_a_valid_extension_at_the_declared_version(self):
        written = json.loads(
            sref_writer.write_recipe(self.document(**{"x-org.example.note": {"reviewed": True}}))
        )
        self.assertEqual(written["x-org.example.note"], {"reviewed": True})

    def test_it_carries_a_compatible_newer_document_unchanged(self):
        written = json.loads(
            sref_writer.write_recipe(
                self.document(version="0.5.0", future_standard_field={"value": 4})
            )
        )
        self.assertEqual(written["sref"]["version"], "0.5.0")
        self.assertEqual(written["future_standard_field"], {"value": 4})

    def test_it_does_not_relabel_a_newer_document_as_its_own_version(self):
        written = json.loads(sref_writer.write_recipe(self.document(version="0.5.0")))
        self.assertNotEqual(written["sref"]["version"], sref_writer.SUPPORTED_FORMAT)


class MistypedInput(unittest.TestCase):
    """Input of the wrong type is a refusal, never an exception of another kind."""

    def test_asset_bytes_must_be_bytes(self):
        body = recipe(
            assets=[
                {
                    "id": "a",
                    "path": "assets/a.png",
                    "media_type": "image/png",
                    "sha256": "0" * 64,
                    "size": 0,
                }
            ]
        )
        with self.assertRaises(WriteRefusedError):
            sref_writer.write_package(body, {"a": None})

    def test_assets_must_be_a_mapping_of_string_ids(self):
        for assets in ([("a", b"x")], {1: b"x", "": b"y"}):
            with self.subTest(assets=assets), self.assertRaises(WriteRefusedError):
                sref_writer.write_package(recipe(), assets)

    def test_member_names_must_be_strings(self):
        with self.assertRaises(WriteRefusedError):
            sref_writer.write_recipe(recipe(extra={1: "x"}))

    def test_a_bundle_member_is_a_pair(self):
        with self.assertRaises(WriteRefusedError):
            sref_writer.write_bundle([recipe()])


if __name__ == "__main__":
    unittest.main()


class TranslationRelationships(unittest.TestCase):
    def body(self, translations, version=None, language="en"):
        body = recipe()
        if version is not None:
            body["sref"] = {"version": version, "unit_registry": "0.2.0"}
        if language is not None:
            body["language"] = language
        body["translations"] = translations
        return body

    def test_relationships_are_written_in_order_without_resolution(self):
        translations = [
            {"relation": "original", "language": "ar", "recipe_id": "mint-tea-ar"},
            {"relation": "alternate", "language": "fr", "url": "https://example.com/fr/the"},
        ]
        written = json.loads(sref_writer.write_recipe(self.body(translations)))
        self.assertEqual(written["translations"], translations)

    def test_rules_the_schema_cannot_state_are_refused(self):
        cases = [
            (
                self.body(
                    [{"relation": "alternate", "language": "fr", "title": "Thé"}], language=None
                ),
                "translation-requires-document-language",
            ),
            (
                self.body(
                    [
                        {"relation": "original", "language": "fr", "title": "Thé"},
                        {"relation": "original", "language": "de", "title": "Tee"},
                    ]
                ),
                "translation-single-original",
            ),
            (
                self.body(
                    [{"relation": "translation", "language": "fr", "recipe_id": recipe()["id"]}]
                ),
                "translation-self-reference",
            ),
            (
                self.body([{"relation": "alternate", "language": "fr", "title": "Thé"}], "0.2.0"),
                "unsupported-format-version",
            ),
        ]
        for body, code in cases:
            with self.subTest(code=code), self.assertRaises(WriteRefusedError) as caught:
                sref_writer.write_recipe(body)
            self.assertEqual(caught.exception.code, code)
