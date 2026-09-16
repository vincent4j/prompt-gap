import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", ROOT / "scripts/package_skill.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_isolated_install_and_offline_demo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "skill.zip"
            package.build(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual({"prompt-gap/" + n for n in package.FILES} | {"prompt-gap/package-manifest.json"}, set(archive.namelist()))
                archive.extractall(root / "installed")
            installed = root / "installed/prompt-gap"
            manifest = json.loads((installed / "package-manifest.json").read_text())
            for name, digest in manifest["files"].items():
                self.assertEqual(digest, hashlib.sha256((installed / name).read_bytes()).hexdigest())
            for md in installed.rglob("*.md"):
                for target in re.findall(r"\]\(([^)]+)\)", md.read_text()):
                    if not target.startswith(("https:", "http:", "#")):
                        self.assertTrue((md.parent / target.split("#")[0]).exists(), target)
            project = root / "project"
            project.mkdir()
            cli = [sys.executable, str(installed / "scripts/prompt_gap.py"), "--project", str(project)]
            for command in ("menu", "enable", "disable", "enable"):
                result = subprocess.run(cli + [command], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(str(installed), (project / "AGENTS.md").read_text())
            demo = subprocess.run([sys.executable, str(installed / "examples/api-recording/record_example.py")], capture_output=True, text=True)
            self.assertEqual(0, demo.returncode, demo.stderr)
            self.assertEqual([], json.loads(demo.stdout)["check"]["issues"])

    def test_existing_archive_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "skill.zip"
            package.build(output)
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                package.build(output)
            self.assertEqual(before, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
