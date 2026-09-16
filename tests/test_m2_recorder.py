import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prompt_gap", ROOT / "scripts/prompt_gap.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.r = pg.Recorder(self.root)
        self.r.run("enable")
        self.t = self.r.run("task", {"title": "红色圆形", "conversation_ref": "test"})["task_id"]
        self.v = self.r.run("requirement", {"task_id": self.t, "user_text": "两个红圆，不要文字", "interpretation": "白底两个红圆"})["requirement_version"]
        self.img = self.root / "sample.png"
        self.img.write_bytes(PNG)

    def begin(self, **extra):
        data = {"task_id": self.t, "requirement_version": self.v, "execution_note": "保持颜色和数量", "transport": "codex_builtin", "task_type": "text_to_image", "capture_mode": "synthetic", "capture_scope": "tool_boundary", "request_snapshot": {"prompt": "两个红圆。\n禁止文字。", "extra": {"未知": [1, True]}}, "bindings": {"prompt_pointers": ["/prompt"]}, "inputs": [], "missing_evidence": ["Synthetic fixture, no model call"]}
        data.update(extra)
        return self.r.run("begin", data)["attempt_id"]

    def path(self, attempt):
        return self.r.attempt_path(self.t, attempt)

    def finish_data(self, a, **extra):
        return {"task_id": self.t, "attempt_id": a, "generation_status": "succeeded", "response_snapshot": {"arbitrary": "ok"}, "outputs": [{"path": str(self.img)}], **extra}

    def test_enable_preserves_existing_content_and_is_idempotent(self):
        f = self.root / "AGENTS.md"
        f.write_text("我的规则\n" + f.read_text() + "\n尾部\n")
        self.r.run("enable")
        before = f.read_text()
        self.r.run("enable")
        self.assertEqual(before, f.read_text())
        self.assertEqual(1, before.count(pg.START))
        self.assertTrue(before.endswith("尾部\n"))
        self.assertIn("/.prompt-gap/", (self.root / ".gitignore").read_text())

    def test_malformed_block_preserved(self):
        f = self.root / "AGENTS.md"
        f.write_text(pg.START)
        with self.assertRaises(pg.RecordError):
            self.r.run("enable")
        self.assertEqual(pg.START, f.read_text())

    def test_disable_preserves_and_allows_inflight_finish(self):
        a = self.begin()
        self.r.run("disable")
        with self.assertRaises(pg.RecordError):
            self.begin()
        self.r.run("finish", self.finish_data(a))
        self.r.run("enable")
        self.assertEqual(1, self.r.run("list")["tasks"][0]["attempt_count"])

    def test_request_roundtrip_and_prompt_exact(self):
        a = self.begin()
        record = pg.read_json(self.path(a) / "request.json")
        self.assertEqual({"未知": [1, True]}, record["request_snapshot"]["extra"])
        self.assertEqual(record["request_snapshot"]["prompt"], (self.path(a) / "prompt.txt").read_text())
        self.assertIsNone(record["requested_model"])

    def test_arbitrary_api_shapes(self):
        for snapshot, bindings in [({"model": "m", "prompt": "红圆", "new": 1}, {"prompt_pointers": ["/prompt"], "requested_model_pointer": "/model"}), ({"engine": {"id": "n"}, "instruction": {"text": "新字段"}, "assets": []}, {"prompt_pointers": ["/instruction/text"], "requested_model_pointer": "/engine/id"})]:
            a = self.begin(transport="api", capture_scope="api_payload", request_snapshot=snapshot, bindings=bindings)
            self.assertEqual(snapshot, pg.read_json(self.path(a) / "request.json")["request_snapshot"])

    def test_asset_order_dedup_and_multiple_outputs(self):
        a = self.begin(task_type="image_to_image", inputs=[{"path": str(self.img), "role": "product"}, {"path": str(self.img), "role": "style"}])
        inputs = pg.read_json(self.path(a) / "request.json")["inputs"]
        self.assertEqual(["product", "style"], [i["role"] for i in inputs])
        self.assertEqual(inputs[0]["sha256"], inputs[1]["sha256"])
        self.assertEqual(1, inputs[0]["width"])
        self.r.run("finish", self.finish_data(a, outputs=[{"path": str(self.img)}, {"path": str(self.img)}]))
        self.assertEqual(1, len(list((self.r.store() / "media").iterdir())))
        self.assertEqual(2, len(pg.read_json(self.path(a) / "result.json")["outputs"]))

    def test_requirement_versions_and_parent(self):
        a = self.begin()
        v2 = self.r.run("requirement", {"task_id": self.t, "user_text": "改成三个", "interpretation": "三个红圆"})["requirement_version"]
        b = self.begin(requirement_version=v2, parent_attempt_id=a)
        self.assertEqual("001", pg.read_json(self.path(a) / "request.json")["requirement_version"])
        self.assertEqual(a, pg.read_json(self.path(b) / "request.json")["parent_attempt_id"])

    def test_finish_idempotence_and_conflict(self):
        a = self.begin()
        data = self.finish_data(a)
        self.r.run("finish", data)
        before = (self.path(a) / "result.json").read_bytes()
        self.assertTrue(self.r.run("finish", data)["idempotent"])
        with self.assertRaises(pg.RecordError):
            self.r.run("finish", self.finish_data(a, generation_status="failed"))
        self.assertEqual(before, (self.path(a) / "result.json").read_bytes())
        result = json.loads(before)
        self.assertIsNone(result["cost"])
        self.assertIsNone(result["usage"])

    def test_async_poll_and_download_recovery(self):
        a = self.begin(transport="api")
        for event in ("submitted", "poll", "poll", "download_failed"):
            self.r.run("event", {"task_id": self.t, "attempt_id": a, "type": event, "details": {"ticket": "job-1"}})
        self.r.run("finish", self.finish_data(a, outputs=[{"missing_reason": "download failed", "url": "https://example.test/output"}]))
        old = pg.read_json(self.path(a) / "result.json")
        self.r.run("finish", self.finish_data(a, supersedes=old["completion_fingerprint"]))
        self.assertEqual(1, self.r.run("list")["tasks"][0]["attempt_count"])
        self.assertEqual(1, len(list((self.path(a) / "result-history").glob("*.json"))))
        self.assertEqual("complete", pg.read_json(self.path(a) / "result.json")["archive_status"])

    def test_unknown_recovery_and_cancel_confirmation(self):
        a = self.begin()
        with self.assertRaises(pg.RecordError):
            self.r.run("finish", self.finish_data(a, generation_status="cancelled"))
        self.r.run("finish", self.finish_data(a, generation_status="unknown", outputs=[]))
        old = pg.read_json(self.path(a) / "result.json")
        self.r.run("finish", self.finish_data(a, supersedes=old["completion_fingerprint"]))

    def test_secrets_are_redacted(self):
        snapshot = {"prompt": "红圆", "headers": {"Authorization": "Bearer TOP_SECRET"}, "custom": "CUSTOM_SECRET", "image": "https://example.test/a?X-Amz-Signature=SIGNED_SECRET"}
        a = self.begin(request_snapshot=snapshot, redact_pointers=["/request_snapshot/custom"])
        text = (self.path(a) / "request.json").read_text()
        for secret in ("TOP_SECRET", "CUSTOM_SECRET", "SIGNED_SECRET"):
            self.assertNotIn(secret, text)
        self.assertEqual(3, len(json.loads(text)["redactions"]))

    def test_integrity_and_interrupted_begin(self):
        a = self.begin(inputs=[{"path": str(self.img)}])
        (self.path(a) / "prompt.txt").write_text("changed")
        request = pg.read_json(self.path(a) / "request.json")
        (self.r.store() / request["inputs"][0]["path"]).unlink()
        stage = self.path(a).parent / ".begin-interrupted"
        stage.mkdir()
        (stage / "request.json").write_text("{}")
        self.r.run("rebuild")
        self.assertEqual(1, self.r.run("list")["tasks"][0]["attempt_count"])
        issues = {i["issue"] for i in self.r.run("check", {"task_id": self.t})["issues"]}
        self.assertTrue({"unfinished_or_unknown", "request_or_prompt_changed", "asset_missing_or_changed", "interrupted_begin"}.issubset(issues))

    def test_menu_snapshot_survives_new_task(self):
        menu = self.r.run("menu")
        self.r.run("task", {"title": "new"})
        choice = self.r.run("select", {"menu_id": menu["menu_id"], "choice": 1})
        self.assertEqual(self.t, choice["task_id"])
        with self.assertRaises(pg.RecordError):
            self.r.run("select", {"menu_id": menu["menu_id"], "choice": 99})

    def test_pagination_and_stale_menu(self):
        for n in range(6):
            self.r.run("task", {"title": str(n)})
        self.assertTrue(self.r.run("list")["has_more"])
        self.assertEqual(2, len(self.r.run("list", {"page": 1})["tasks"]))
        m = self.r.run("menu")
        option = next(k for k, v in m["options"].items() if v["action"] == "disable")
        self.r.run("disable")
        with self.assertRaises(pg.RecordError):
            self.r.run("select", {"menu_id": m["menu_id"], "choice": option})

    def test_index_rebuild(self):
        (self.r.store() / "index.json").write_text("invalid")
        self.assertEqual(self.t, self.r.run("list")["tasks"][0]["task_id"])

    def test_concurrent_attempts(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.begin(), range(12)))
        self.assertEqual(12, len(set(ids)))
        self.assertEqual(12, self.r.run("list")["tasks"][0]["attempt_count"])

    def test_external_storage_and_empty_project_menu(self):
        project = self.root / "other"
        project.mkdir()
        r = pg.Recorder(project)
        self.assertEqual("not_enabled", r.run("menu")["project_state"])
        self.assertFalse((project / ".prompt-gap").exists())
        r.run("enable", {"storage_root": str(self.root / "separate")})
        self.assertEqual([], r.run("list")["tasks"])
        self.assertEqual((self.root / "separate").resolve(), r.store())

    def test_path_and_symlink_rejection(self):
        with self.assertRaises(pg.RecordError):
            self.r.run("inspect", {"task_id": "../outside"})
        task = self.r.task_path(self.t)
        (task / "attempts").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(pg.RecordError):
            self.begin()

    def test_json_pointer(self):
        self.assertEqual("ok", pg.pointer({"a/b": {"~": ["ok"]}}, "/a~1b/~0/0"))
        for p in ("/-1", "/01", "/9", "/~2"):
            with self.assertRaises(pg.RecordError):
                pg.pointer(["a"], p)

    def test_feedback_and_observation_are_append_only(self):
        a = self.begin()
        for kind in ("observe", "feedback", "processing"):
            for text in ("原记录", "补充记录"):
                self.r.run(kind, {"task_id": self.t, "attempt_id": a, "content": text, "source": "synthetic"})
        self.assertEqual("补充记录", self.r.run("list")["tasks"][0]["feedback"])
        self.assertEqual(2, len((self.path(a) / "observations.jsonl").read_text().splitlines()))

    def test_cli_no_sensitive_error_echo(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/prompt_gap.py"), "--project", str(self.root), "begin", "--data", "-"], input='{"private":"VERY_SECRET"}', text=True, capture_output=True)
        self.assertEqual(1, result.returncode)
        self.assertNotIn("VERY_SECRET", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
