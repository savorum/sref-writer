"""Property tests that feed the writer hostile input.

The writer promises to refuse input it cannot write correctly with
`WriteRefusedError` and never to raise anything else, so each test asserts that
the outcome is either bytes or that exception. Anything it does write must be
well formed.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import pathlib
import sys
import unittest
import zipfile
from fractions import Fraction

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_writer
from sref_writer import WriteRefusedError

VALID = {
    "id": "soup",
    "title": "Soup",
    "ingredient_sections": [
        {
            "id": "i",
            "ingredients": [
                {
                    "id": "salt",
                    "name": "salt",
                    "quantity": {"kind": "simple", "amount": {"value": "1/2"}, "unit": "mass.gram"},
                }
            ],
        }
    ],
    "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Cook."}]}],
    "assets": [
        {
            "id": "photo",
            "path": "assets/photo.png",
            "media_type": "image/png",
            "sha256": "0" * 64,
            "size": 1,
        }
    ],
}

FUZZ = settings(
    max_examples=int(os.environ.get("SREF_FUZZ_EXAMPLES", "200")),
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**30), max_value=10**30),
    st.floats(),
    st.fractions(),
    st.text(max_size=40),
    st.binary(max_size=8),
)
values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.tuples(children, children),
        st.dictionaries(st.one_of(st.text(max_size=12), st.integers()), children, max_size=4),
    ),
    max_leaves=25,
)


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, child in node.items():
            yield from paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from paths(child, (*prefix, index))


ALL_PATHS = [path for path in paths(VALID) if path]


@st.composite
def mutated_recipes(draw):
    body = copy.deepcopy(VALID)
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        *parents, last = draw(st.sampled_from(ALL_PATHS))
        target = body
        try:
            for step in parents:
                target = target[step]
            if draw(st.booleans()):
                del target[last]
            else:
                target[last] = draw(values)
        except (KeyError, IndexError, TypeError):
            continue
    return body


asset_names = st.one_of(
    st.just("photo"), st.sampled_from(["../x", "", "a/b"]), st.text(max_size=12)
)
asset_maps = st.one_of(
    st.none(),
    values,
    st.dictionaries(asset_names, st.binary(max_size=64), max_size=3),
    st.dictionaries(asset_names, values, max_size=3),
)


def writes_or_refuses(function, *arguments):
    with contextlib.suppress(WriteRefusedError):
        return function(*arguments)
    return None


class HostileInput(unittest.TestCase):
    def test_the_base_document_is_writable(self):
        # Without this the mutations below could all be refusals.
        sref_writer.write_recipe(VALID)
        sref_writer.write_package(VALID, {"photo": b"x"})

    @FUZZ
    @given(values)
    def test_recipe_values(self, value):
        writes_or_refuses(sref_writer.write_recipe, value)

    @FUZZ
    @given(mutated_recipes())
    def test_mutated_recipe_is_well_formed_or_refused(self, document):
        data = writes_or_refuses(sref_writer.write_recipe, document)
        if data is not None:
            self.assertIsInstance(json.loads(data), dict)

    @FUZZ
    @given(mutated_recipes(), asset_maps)
    def test_package_is_a_valid_archive_or_refused(self, document, assets):
        data = writes_or_refuses(sref_writer.write_package, document, assets)
        if data is not None:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("manifest.json", archive.namelist())

    @FUZZ
    @given(st.lists(st.one_of(st.tuples(mutated_recipes(), asset_maps), values), max_size=3))
    def test_bundle_is_a_valid_archive_or_refused(self, members):
        data = writes_or_refuses(sref_writer.write_bundle, members)
        if data is not None:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.assertIsNone(archive.testzip())

    @FUZZ
    @given(st.fractions())
    def test_amounts_are_written_canonically(self, amount):
        document = copy.deepcopy(VALID)
        document["ingredient_sections"][0]["ingredients"][0]["quantity"]["amount"]["value"] = amount
        data = writes_or_refuses(sref_writer.write_recipe, document)
        if data is not None:
            written = json.loads(data)["ingredient_sections"][0]["ingredients"][0]["quantity"][
                "amount"
            ]["value"]
            self.assertEqual(Fraction(written), amount)


if __name__ == "__main__":
    unittest.main()
