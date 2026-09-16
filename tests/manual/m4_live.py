"""Authorized M4 experiment only. Not part of the distributable Skill.

Uses the existing ClawPower skill module without changing it. The request hook
records the exact argument then delegates once. No credentials enter records.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "output/m4-live-20260916"
SKILL = Path("/Users/vincent4j/.codex/skills/clawpower-image/scripts/generate.py")
GENERATE = "用途：独立生图接口联调素材，不是服装商品图。生成一张写实静物照片：浅灰色哑光桌面与干净浅灰背景上，只有一只红色釉面陶瓷马克杯，杯口敞开，只有一个把手且在画面右侧。杯子完整入镜，三分之四视角，画面中央，柔和的左侧光线，真实柔和落影。不要文字、Logo、水印、其他物体或拼图。"
EDIT = "输入图1是唯一编辑底图。只把这只陶瓷马克杯的红色杯身釉面改成蓝色；把手也改成一致的蓝色。严格保留原图的杯形、杯口、把手形状与右侧位置、杯子数量、构图、视角、灰色桌面及背景、左侧光照和落影。不要增加文字、Logo、水印、其他物体或拼图。输出一张编辑后的写实照片。"
USER = "允许。用 clawpower-image 这个 skill 生图，也可以用 codex 内置 image 2.5 生图。API 模型选择：2（GPT Image 2.5 Sunburst）。"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pg = module(ROOT / "scripts/prompt_gap.py", "m4_recorder")


def setup():
    PROJECT.mkdir(parents=True, exist_ok=True)
    r = pg.Recorder(PROJECT)
    state_path = PROJECT / "state.json"
    if state_path.exists():
        return r, pg.read_json(state_path)
    r.run("enable")
    tasks = {}
    for transport in ("api", "codex_builtin"):
        title = "Sunburst 接口" if transport == "api" else "Codex 内置生图"
        task = r.run("task", {"title": title + "：文生图与定向编辑真实联调"})["task_id"]
        versions = {}
        for mode, prompt in (("generate", GENERATE), ("edit", EDIT)):
            versions[mode] = r.run("requirement", {"task_id": task, "user_text": USER, "interpretation": "用户授权验证 PromptGap 留档链路，两种入口各一次生成和编辑，最多四次，不自动重试。红杯与改蓝测试是助手自选的验证案例，不是用户原始审美需求。", "source": "当前对话授权及选型", "test_spec": prompt, "assumptions": ["杯子场景及改色要求属于助手测试设计"], "confirmation": "confirmed"})["requirement_version"]
        tasks[transport] = {"task_id": task, "versions": versions}
    state = {"tasks": tasks, "steps": {}}
    pg.write_json(state_path, state)
    return r, state


def save_state(state):
    # Different transports may complete concurrently; do not replace the other's step.
    with pg.Recorder(PROJECT).lock():
        path = PROJECT / "state.json"
        merged = pg.read_json(path) if path.exists() else {"tasks": {}, "steps": {}}
        merged["tasks"].update(state["tasks"])
        for name, entry in state["steps"].items():
            merged["steps"].setdefault(name, {}).update(entry)
        pg.write_json(path, merged)


def begin(r, state, transport, mode, payload, inputs=()):
    step = transport + "-" + mode
    if step in state["steps"]:
        raise ValueError("Step already recorded; do not resubmit")
    t = state["tasks"][transport]
    bindings = {"prompt_pointers": ["/prompt"]}
    if transport == "api":
        bindings["requested_model_pointer"] = "/model"
        bindings["input_pointers"] = ["/image_urls/0"] if mode == "edit" else []
    elif mode == "edit":
        bindings["input_pointers"] = ["/referenced_image_paths/0"]
    data = {"task_id": t["task_id"], "requirement_version": t["versions"][mode], "execution_note": "真实联调，单次提交；" + ("新生成红杯" if mode == "generate" else "只改蓝色并检查保留项"), "transport": transport, "task_type": "text_to_image" if mode == "generate" else "image_to_image", "capture_mode": "live", "capture_scope": "api_payload" if transport == "api" else "tool_boundary", "request_snapshot": payload, "bindings": bindings, "inputs": list(inputs), "missing_evidence": ["未取得实际账单和隐藏模型内部信息"]}
    if mode == "edit":
        data["parent_attempt_id"] = state["steps"][transport + "-generate"]["attempt_id"]
    start = time.perf_counter()
    attempt = r.run("begin", data)["attempt_id"]
    state["steps"][step] = {"attempt_id": attempt, "task_id": t["task_id"], "record_begin_ms": (time.perf_counter() - start) * 1000, "started_at": pg.now(), "payload": payload}
    save_state(state)
    return state["steps"][step]


def api(mode):
    r, state = setup()
    step = "api-" + mode
    # Claim before upload/network, even an interrupted run must not blindly retry.
    claim = PROJECT / (step + ".submission-claimed")
    with claim.open("x") as file:
        file.write(pg.now())
    client = module(SKILL, "m4_clawpower_existing_skill")
    original_request = client.request_json
    original_upload = client.upload_local
    inputs, upload_info, response = [], [], {}
    output = PROJECT / (step + ".png")
    source = PROJECT / "api-generate.png"

    def upload(path, config, key):
        started = time.perf_counter()
        url = original_upload(path, config, key)
        # Archive the actual uploaded representation, not just the original source.
        with urlopen(url, timeout=70) as download:
            blob = download.read()
            mime = download.headers.get_content_type()
        suffix = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}.get(mime, ".bin")
        uploaded = PROJECT / ("api-edit-uploaded" + suffix)
        uploaded.write_bytes(blob)
        inputs.append({"path": str(uploaded), "role": "editing_base", "controls": "杯形、构图、背景、光线", "must_not_control": "原红色应改为蓝色", "original_source": str(source), "original_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "request_url": url})
        upload_info.append({"url": url, "duration_seconds": time.perf_counter() - started, "original_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "uploaded_sha256": hashlib.sha256(blob).hexdigest()})
        return url

    def request(url, key, payload, timeout):
        entry = begin(r, state, "api", mode, payload, inputs)
        r.run("event", {"task_id": entry["task_id"], "attempt_id": entry["attempt_id"], "type": "submitted", "details": {"endpoint": url, "timeout_seconds": timeout, "upload": upload_info, "client_script_sha256": hashlib.sha256(SKILL.read_bytes()).hexdigest()}})
        started = time.perf_counter()
        try:
            result = original_request(url, key, payload, timeout)
        finally:
            entry["request_seconds"] = time.perf_counter() - started
            save_state(state)
        pg.write_json(PROJECT / (step + "-response-original.json"), result)
        response.update(copy.deepcopy(result))
        for item in response.get("data", []):
            if "b64_json" in item:
                item["b64_json"] = "图片编码正文单独保存于本轮原始响应文件；解码图片见输出图"
        return result

    client.upload_local, client.request_json = upload, request
    args = [str(SKILL), "--model", "gpt-image-2.5-sunburst", "--prompt", GENERATE if mode == "generate" else EDIT, "--output", str(output)]
    if mode == "edit":
        args += ["--image", str(source)]
    sys.argv = args
    error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            client.main()
    except HTTPError as exc:
        error = {"http_status": exc.code, "message": "接口返回 HTTP 错误；未自动重试"}
    except Exception as exc:
        error = {"error_type": type(exc).__name__, "message": "生成或落盘未完成；未自动重试，需检查记录"}
    if step not in state["steps"]:
        print(json.dumps({"step": step, "submitted": False, "error": error}, ensure_ascii=False))
        return
    entry = state["steps"][step]
    start = time.perf_counter()
    result = r.run("finish", {"task_id": entry["task_id"], "attempt_id": entry["attempt_id"], "generation_status": "succeeded" if output.is_file() else ("failed" if error and error.get("http_status") else "unknown"), "response_snapshot": response or error, "outputs": [{"path": str(output), "role": "original_output"}] if output.is_file() else [], "error": error, "usage": response.get("usage"), "cost": None, "missing_evidence": ["实际费用未查账；公开标价每张1K为0.10元，不作为实际扣费", "响应中图片编码正文另存原始文件，此处为展示副本"], "response_file": str(PROJECT / (step + "-response-original.json"))})
    entry.update(record_finish_ms=(time.perf_counter() - start) * 1000, output=str(output) if output.is_file() else None, result=result, ended_at=pg.now())
    save_state(state)
    print(json.dumps({"step": step, "result": result, "request_seconds": entry.get("request_seconds"), "output": entry["output"]}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "setup":
        r, state = setup()
        print(json.dumps({"project": str(PROJECT), "state": state}, ensure_ascii=False))
    elif sys.argv[1] == "api":
        api(sys.argv[2])
