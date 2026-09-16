#!/usr/bin/env python3
"""Build a minimal local skill archive; never installs, uploads or calls models."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "SKILL.md", "agents/openai.yaml", "scripts/prompt_gap.py", "scripts/review.py",
    "references/menus.md", "references/capture.md", "references/api-recording.md",
    "references/recording.md", "references/review.md", "references/research.md",
    "examples/api-recording/fixtures.json", "examples/api-recording/record_example.py",
    "examples/success-demo/output-001.svg",
)


def build(destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination exists; choose a new archive path")
    payloads = {}
    for name in FILES:
        source = ROOT / name
        if any(p.is_symlink() for p in [source, *source.parents] if p != ROOT.parent):
            raise ValueError("Package source contains a symlink")
        data = source.read_bytes()
        if b"/Users/" in data or b"/private/var/folders/" in data:
            raise ValueError("Package source contains a machine-specific path: " + name)
        payloads[name] = data
    manifest = {"format_version": 1, "scope": "Local skill package; capture coverage depends on each project's actual integration", "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payloads.items():
            archive.writestr("prompt-gap/" + name, data)
        archive.writestr("prompt-gap/package-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"archive": str(destination.resolve()), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "runtime_files": len(payloads)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
