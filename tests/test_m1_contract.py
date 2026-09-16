"""Validate M1 examples, not a production recorder or real model integration."""

import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def pointer(value, path):
    """Fixture helper using explicit field paths, with no provider-name routing."""
    if path is None:
        return None
    if path == "":
        return value
    if not path.startswith("/"):
        raise ValueError("Expected JSON Pointer")
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


class M1ContractTests(unittest.TestCase):
    def test_api_shapes_use_explicit_bindings(self):
        data = json.loads((ROOT / "examples/api-recording/fixtures.json").read_text())
        for case in data["cases"]:
            with self.subTest(case=case["case_id"]):
                request, response, bindings = (
                    case["request_snapshot"], case["response_snapshot"], case["bindings"]
                )
                self.assertEqual(case["capture_mode"], "synthetic")
                self.assertEqual(pointer(request, bindings["prompt_pointers"][0]), case["expected_prompt"])
                self.assertEqual([pointer(request, p) for p in bindings["input_pointers"]], case["expected_inputs"])
                self.assertEqual([pointer(response, p) for p in bindings["output_pointers"]], case["expected_outputs"])
                self.assertEqual(pointer(request, bindings["requested_model_pointer"]), case["expected_model"])
                self.assertEqual(pointer(response, bindings["external_job_pointer"]), case["expected_job"])
                self.assertEqual(json.loads(json.dumps(request, ensure_ascii=False)), request)

    def test_pointer_escaping_and_missing_evidence(self):
        data = {"options": {"custom/field": {"~future": "保留"}}}
        self.assertEqual(pointer(data, "/options/custom~1field/~0future"), "保留")
        with self.assertRaises(KeyError):
            pointer(data, "/prompt")

    def test_demo_prompts_and_parent_chain(self):
        folder = ROOT / "examples/review-demo"
        data = json.loads((folder / "fixture.json").read_text())
        self.assertEqual(data["capture_mode"], "synthetic")
        previous = None
        for attempt in data["attempts"]:
            self.assertEqual(attempt["parent_attempt_id"], previous)
            previous = attempt["attempt_id"]
            # The text file has one terminal newline; request text is exact otherwise.
            self.assertEqual((folder / attempt["prompt_file"]).read_text(), attempt["request_snapshot"]["prompt"] + "\n")
            self.assertIsNone(attempt["cost"])
            self.assertIsNone(attempt["usage"])
            self.assertTrue(attempt["missing_evidence"])
            for asset in attempt["input_files"] + [attempt["output_file"]]:
                self.assertTrue((folder / asset).is_file())

    def test_demo_drawings_match_report_observations(self):
        folder = ROOT / "examples/review-demo"
        for number, count, color in [(1, 2, "#2563eb"), (2, 3, "#dc2626"), (3, 3, "#dc2626")]:
            svg = ET.parse(folder / f"output-{number:03}.svg").getroot()
            circles = svg.findall("{http://www.w3.org/2000/svg}circle")
            self.assertEqual(len(circles), count)
            self.assertTrue(all(c.get("fill") == color for c in circles))
            self.assertEqual(svg.find("{http://www.w3.org/2000/svg}rect").get("fill"), "white")
            self.assertEqual(svg.findall("{http://www.w3.org/2000/svg}text"), [])

    def test_local_markdown_links_resolve(self):
        for folder in ["docs", "references", "examples"]:
            for file in (ROOT / folder).rglob("*.md"):
                for target in re.findall(r"\]\(([^)]+)\)", file.read_text()):
                    if "://" in target or target.startswith("#"):
                        continue
                    self.assertTrue((file.parent / target.split("#")[0]).is_file(), f"{file}: {target}")


if __name__ == "__main__":
    unittest.main()
