#!/usr/bin/env python3
"""Offline persistence demonstration; does not call any image model or API."""
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prompt_gap", ROOT / "scripts/prompt_gap.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def main():
    with tempfile.TemporaryDirectory(prefix="prompt-gap-demo-") as folder:
        recorder = pg.Recorder(folder)
        recorder.run("enable")
        task = recorder.run("task", {"title": "合成 API 留档演示"})["task_id"]
        version = recorder.run("requirement", {"task_id": task, "user_text": "生成两个红色圆形", "interpretation": "白底两个红圆，无文字"})["requirement_version"]
        request = {"engine": {"id": "synthetic-model"}, "instruction": {"text": "白底两个红圆，无文字"}, "arbitrary_new_field": {"keep": True}}
        attempt = recorder.run("begin", {"task_id": task, "requirement_version": version, "execution_note": "仅演示持久化，不发送模型请求", "transport": "api", "task_type": "text_to_image", "capture_mode": "synthetic", "capture_scope": "api_payload", "request_snapshot": request, "bindings": {"prompt_pointers": ["/instruction/text"], "requested_model_pointer": "/engine/id"}, "inputs": []})["attempt_id"]
        # In a real integration, the EXISTING client submits the same request here.
        # Preserve the actual response, errors, polls and downloaded files below.
        recorder.run("event", {"task_id": task, "attempt_id": attempt, "type": "submitted", "details": {"ticket": "synthetic-job", "state": "QUEUED"}})
        result = recorder.run("finish", {"task_id": task, "attempt_id": attempt, "generation_status": "succeeded", "response_snapshot": {"ticket": "synthetic-job", "artifacts": [{"location": "synthetic://fixture"}]}, "bindings": {"external_job_pointer": "/ticket", "output_pointers": ["/artifacts/0/location"]}, "outputs": [{"path": str(ROOT / "examples/success-demo/output-001.svg"), "role": "synthetic_fixture"}], "missing_evidence": ["Synthetic fixture, not a real generation"]})
        print(json.dumps({"mode": "synthetic", "result": result, "check": recorder.run("check", {"task_id": task}), "notice": "Temporary demo records removed on exit; no real API was called"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
