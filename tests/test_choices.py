import json
import pathlib
import sys
import unittest
from fractions import Fraction

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_writer


class ChoiceTests(unittest.TestCase):
    def test_normalizes_every_branch_without_mutating_input(self):
        body = {
            "id": "choices",
            "title": "Choices",
            "ingredient_sections": [
                {
                    "id": "i",
                    "ingredients": [
                        {
                            "id": "fat",
                            "relation": "or",
                            "alternatives": [
                                {
                                    "id": "butter",
                                    "name": "butter",
                                    "quantity": {
                                        "kind": "simple",
                                        "amount": {"value": Fraction(1, 2)},
                                    },
                                },
                                {
                                    "id": "oil",
                                    "name": "oil",
                                    "quantity": {
                                        "kind": "simple",
                                        "amount": {"value": Fraction(1, 3)},
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        result = json.loads(sref_writer.write_recipe(body))
        branches = result["ingredient_sections"][0]["ingredients"][0]["alternatives"]
        self.assertEqual([b["quantity"]["amount"]["value"] for b in branches], ["1/2", "1/3"])
        self.assertIsInstance(
            body["ingredient_sections"][0]["ingredients"][0]["alternatives"][0]["quantity"][
                "amount"
            ]["value"],
            Fraction,
        )
