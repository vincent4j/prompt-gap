#!/usr/bin/env python3
"""Local, provider-independent image-call recorder. Never invokes a model."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import struct
import sys
import tempfile
from urllib.parse import parse_qsl, urlsplit, urlunsplit
import uuid

VERSION = 1
START = "<!-- prompt-gap:start -->"
END = "<!-- prompt-gap:end -->"
HERE = Path(__file__).resolve().parent
SECRET_KEY = re.compile(r"^(authorization|proxy.authorization|cookie|set.cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|client_secret)$", re.I)
SIGNED_KEY = re.compile(r"(signature|credential|token|api.?key|^sig$|^key$)", re.I)
EVENTS = {"submitted", "poll", "response_received", "download_started", "download_completed", "download_failed", "cancel_requested", "transport_error", "retry_observed", "fallback_observed"}


class RecordError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def require(condition, message):
    if not condition:
        raise RecordError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value), "Invalid record identifier")
    return value


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise RecordError("Record is unreadable or invalid JSON") from None


def atomic(path, text):
    require(not path.is_symlink(), "Refusing symlink destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def pointer(value, path):
    require(isinstance(path, str) and (path == "" or path.startswith("/")), "Invalid JSON Pointer")
    try:
        for part in path.split("/")[1:]:
            require(not re.search(r"~(?![01])", part), "Invalid JSON Pointer escape")
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                require(bool(re.fullmatch(r"0|[1-9][0-9]*", part)), "Invalid array index")
                value = value[int(part)]
            else:
                value = value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError):
        raise RecordError("Binding points to a missing field") from None


def sanitize(data, explicit=()):
    """Redact known secrets and caller-specified JSON Pointers, record every change."""
    changes = []
    explicit = set(explicit)
    for path in explicit:
        pointer(data, path)

    def walk(value, path=""):
        if path in explicit:
            changes.append(path)
            return "[REDACTED]"
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                p = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if SECRET_KEY.match(key):
                    changes.append(p)
                    result[key] = "[REDACTED]"
                else:
                    result[key] = walk(item, p)
            return result
        if isinstance(value, list):
            return [walk(item, path + "/" + str(i)) for i, item in enumerate(value)]
        if isinstance(value, str):
            redacted = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
            if value.startswith(("https://", "http://")):
                url = urlsplit(value)
                if url.username or url.password or any(SIGNED_KEY.search(k) for k, _ in parse_qsl(url.query)):
                    host = url.netloc.rsplit("@", 1)[-1]
                    redacted = urlunsplit((url.scheme, host, url.path, "", ""))
            if redacted != value:
                changes.append(path)
            return redacted
        return value

    return walk(data), changes


def media_info(blob):
    if blob.startswith(b"\x89PNG\r\n\x1a\n") and len(blob) >= 24:
        width, height = struct.unpack(">II", blob[16:24])
        return {"width": width, "height": height, "format": "png", "icc": "not_inspected"}
    # Optional dependency improves metadata only, never blocks preservation of bytes.
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(blob)) as im:
            return {"width": im.width, "height": im.height, "format": im.format.lower(), "icc": "present" if im.info.get("icc_profile") else "absent"}
    except (ImportError, OSError, ValueError):
        return {"width": None, "height": None, "format": None, "icc": "unknown"}


class Recorder:
    def __init__(self, project):
        self.project = Path(project).expanduser().resolve(strict=True)
        require(self.project.is_dir(), "Project must be an existing directory")
        self.local = self.project / ".prompt-gap"

    def safe(self, base, *parts):
        path = base.joinpath(*parts)
        require(not base.is_symlink(), "Refusing symlink storage root")
        current = base
        for part in path.relative_to(base).parts:
            require(part != "..", "Path escapes storage")
            current = current / part
            require(not current.is_symlink(), "Refusing symlink inside storage")
        return path

    def config(self):
        path = self.safe(self.local, "config.json")
        if not path.exists():
            return None
        cfg = read_json(path)
        require(cfg.get("schema_version") == VERSION, "Unsupported configuration version")
        return cfg

    def store(self):
        cfg = self.config()
        require(cfg is not None, "Project is not enabled")
        base = Path(cfg["storage_root"])
        require(not base.is_symlink(), "Refusing symlink storage")
        return base

    def active(self):
        cfg = self.config()
        require(cfg is not None and cfg["enabled"], "Recording is disabled")

    @contextmanager
    def lock(self):
        # Project identity gives independent processes one lock even before enabling.
        parent = Path(tempfile.gettempdir()) / ("prompt-gap-" + str(os.getuid() if hasattr(os, "getuid") else "local"))
        require(not parent.is_symlink(), "Unsafe lock directory")
        if not parent.exists():
            parent.mkdir(mode=0o700, exist_ok=True)
        path = parent / (hashlib.sha256(str(self.project).encode()).hexdigest() + ".lock")
        require(not path.is_symlink(), "Unsafe lock file")
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def status(self):
        cfg = self.config()
        return {"project": str(self.project), "state": "not_enabled" if cfg is None else ("enabled" if cfg["enabled"] else "disabled"), "coverage": cfg.get("coverage", {}) if cfg else {}}

    def enable(self, data):
        previous = self.config()
        base = Path(data.get("storage_root", self.local)).expanduser()
        if not base.is_absolute():
            base = self.project / base
        require(not base.is_symlink(), "Refusing symlink storage root")
        base = base.resolve()
        require(base != self.project and base != Path(base.anchor), "Storage must be a dedicated directory")
        if previous:
            require("storage_root" not in data or str(base) == previous["storage_root"], "Changing existing storage requires explicit migration")
            base = Path(previous["storage_root"])
        agents = self.project / "AGENTS.md"
        ignore = self.project / ".gitignore"
        require(not agents.is_symlink() and not ignore.is_symlink(), "Refusing symlink project configuration")
        original = agents.read_text() if agents.exists() else ""
        require(original.count(START) == original.count(END) and original.count(START) <= 1, "Malformed PromptGap block; original file preserved")
        require(START not in original or original.index(START) < original.index(END), "Malformed PromptGap block")
        command = "python3 " + shlex.quote(str(HERE / "prompt_gap.py")) + " --project " + shlex.quote(str(self.project))
        block = f'''{START}
## PromptGap 留档
记录工具：`{command}`。先用 `status` 检查 enabled 状态；关闭时跳过留档。
开启后，对本项目内置和 API 图像调用先保存相关用户原话、当时理解和实际请求，再调用原生图工具，返回后保存原始结果或错误。
首次记录或更换调用入口时按 `{HERE.parent / 'references' / 'capture.md'}` 执行。每个对话明确 task_id，返修传 parent_attempt_id，不猜全项目当前任务。
只保留可见事实，缺项注明；不新增生图、重试、评审或能力研究。调用出错仍留档；日志失败不伪称成功。
用户选择 PromptGap 时才展示菜单/分析。普通生图只返回简短记录状态，不回显完整日志。
{END}'''
        if START in original:
            a, b = original.index(START), original.index(END) + len(END)
            updated = original[:a] + block + original[b:]
        else:
            updated = original + ("\n\n" if original else "") + block + "\n"
        ignored = ignore.read_text() if ignore.exists() else ""
        lines = ["/.prompt-gap/"]
        if base.is_relative_to(self.project) and base != self.local:
            lines.append("/" + base.relative_to(self.project).as_posix() + "/")
        for line in lines:
            if line not in ignored.splitlines():
                ignored += ("\n" if ignored and not ignored.endswith("\n") else "") + line + "\n"
        self.safe(self.local).mkdir(parents=True, exist_ok=True)
        base.mkdir(parents=True, exist_ok=True)
        cfg = previous or {"schema_version": VERSION, "storage_root": str(base), "coverage": {"codex_builtin": "agent_protocol_unverified", "api": "agent_protocol_unverified"}}
        cfg.update(enabled=True, updated_at=now())
        # Write enabled configuration last: a partial setup does not report enabled.
        atomic(agents, updated)
        atomic(ignore, ignored)
        write_json(self.local / "config.json", cfg)
        self.rebuild({})
        return self.status()

    def disable(self, data):
        cfg = self.config()
        require(cfg is not None, "Project has not been enabled")
        cfg.update(enabled=False, updated_at=now())
        write_json(self.local / "config.json", cfg)
        return self.status()

    def task_path(self, task_id):
        path = self.safe(self.store(), "tasks", identifier(task_id))
        require(self.safe(path, "task.json").is_file(), "Task not found")
        return path

    def attempt_path(self, task_id, attempt_id):
        path = self.safe(self.task_path(task_id), "attempts", identifier(attempt_id))
        require(self.safe(path, "request.json").is_file(), "Attempt not found")
        return path

    def touch(self, task_id):
        path = self.task_path(task_id) / "task.json"
        data = read_json(path)
        data["updated_at"] = now()
        write_json(path, data)
        self.rebuild({})

    def task(self, data):
        self.active()
        require(bool(data.get("title")), "Task title is required")
        task_id = uid()
        path = self.safe(self.store(), "tasks", task_id)
        task = {"schema_version": VERSION, "task_id": task_id, "title": data["title"], "conversation_ref": data.get("conversation_ref"), "created_at": now(), "updated_at": now()}
        write_json(path / "task.json", task)
        self.rebuild({})
        return {"task_id": task_id}

    def requirement(self, data):
        self.active()
        require(bool(data.get("user_text")) and bool(data.get("interpretation")), "User text and contemporary interpretation are required")
        task_id = data["task_id"]
        path = self.safe(self.task_path(task_id), "requirements")
        if not path.exists():
            path.mkdir(exist_ok=True)
        version = str(len(list(path.glob("*.json"))) + 1).zfill(3)
        require(not (path / (version + ".json")).exists(), "Requirement version conflict")
        record = dict(data, schema_version=VERSION, version=version, recorded_at=now())
        record.setdefault("confirmation", "not_explicitly_confirmed")
        record.setdefault("assumptions", [])
        write_json(path / (version + ".json"), record)
        self.touch(task_id)
        return {"requirement_version": version}

    def asset(self, item):
        require(isinstance(item, dict), "Asset must be an object")
        if not item.get("path"):
            require(bool(item.get("missing_reason")), "Unresolved asset requires missing_reason")
            return dict(item, available=False)
        src = Path(item["path"]).expanduser()
        if not src.is_absolute():
            src = self.project / src
        require(src.is_file(), "Input/output asset file is missing")
        blob = src.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        # Content identity, not filename, determines storage and deduplication.
        target = self.safe(self.store(), "media", digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(hashlib.sha256(target.read_bytes()).hexdigest() == digest, "Stored asset was modified")
        else:
            fd, name = tempfile.mkstemp(prefix=".asset-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(blob)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return {**item, "source_path": str(src.resolve()), "path": target.relative_to(self.store()).as_posix(), "asset_id": digest, "sha256": digest, "available": True, "size_bytes": len(blob), "media_type": mimetypes.guess_type(src.name)[0], **media_info(blob)}

    def begin(self, data):
        self.active()
        for key in ("task_id", "requirement_version", "execution_note", "transport", "task_type", "capture_mode", "capture_scope", "request_snapshot"):
            require(key in data, "Missing begin field: " + key)
        task_id = data["task_id"]
        task = self.task_path(task_id)
        require(data["transport"] in {"api", "codex_builtin"}, "Unsupported transport")
        require(data["task_type"] in {"text_to_image", "image_to_image"}, "Unsupported task type")
        require(data["capture_mode"] in {"live", "synthetic", "backfilled"}, "Invalid capture mode")
        require(data["capture_scope"] in {"api_payload", "tool_boundary", "summary"}, "Invalid capture scope")
        version = identifier(data["requirement_version"])
        require(self.safe(task, "requirements", version + ".json").is_file(), "Requirement version missing")
        if data.get("parent_attempt_id"):
            self.attempt_path(task_id, data["parent_attempt_id"])
        bindings = data.get("bindings", {})
        prompts = [pointer(data["request_snapshot"], p) for p in bindings.get("prompt_pointers", [])]
        require(all(isinstance(p, str) for p in prompts), "Prompt bindings must point to text")
        for p in bindings.get("input_pointers", []):
            pointer(data["request_snapshot"], p)
        requested_model = pointer(data["request_snapshot"], bindings["requested_model_pointer"]) if bindings.get("requested_model_pointer") else None
        inputs = [self.asset(item) for item in data.get("inputs", [])]
        gaps = list(data.get("missing_evidence", []))
        if not prompts:
            gaps.append("No prompt bindings; snapshot preserved without inferred prompt")
        if requested_model is None:
            gaps.append("Requested model not visible or not bound")
        if data["task_type"] == "image_to_image" and not inputs:
            require(bool(gaps), "Image edit needs inputs or an explicit evidence gap")
            gaps.append("Input asset identities unavailable")
        attempt_id = uid()
        path = self.safe(task, "attempts", attempt_id)
        record = {**data, "schema_version": VERSION, "attempt_id": attempt_id, "recorded_at": now(), "inputs": inputs, "requested_model": requested_model, "missing_evidence": gaps}
        record.setdefault("parent_attempt_id", None)
        # Prepare in an isolated directory so an interrupted begin cannot look complete.
        if not path.parent.exists():
            path.parent.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".begin-", dir=path.parent))
        write_json(staging / "request.json", record)
        atomic(staging / "prompt.txt", "\n\n".join(prompts))
        write_json(staging / "integrity.json", {name: hashlib.sha256((staging / name).read_bytes()).hexdigest() for name in ("request.json", "prompt.txt")})
        write_json(staging / "result.json", {"schema_version": VERSION, "attempt_id": attempt_id, "generation_status": "pending", "archive_status": "pending", "outputs": [], "usage": None, "cost": None, "started_at": record["recorded_at"], "ended_at": None, "missing_evidence": ["No terminal response recorded"]})
        os.replace(staging, path)
        self.touch(task_id)
        return {"task_id": task_id, "attempt_id": attempt_id, "state": "pending", "missing_evidence_count": len(gaps)}

    def finish(self, data):
        # Allow closing an already submitted call after recording is disabled.
        path = self.attempt_path(data["task_id"], data["attempt_id"])
        status = data.get("generation_status")
        require(status in {"succeeded", "failed", "unknown", "cancelled"}, "Invalid completion state")
        if status == "cancelled":
            require(data.get("cancellation_confirmed") is True, "Cancellation requires remote confirmation")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = read_json(path / "result.json")
        if old.get("completion_fingerprint"):
            if old["completion_fingerprint"] == fingerprint:
                return {"attempt_id": data["attempt_id"], "idempotent": True}
            require(data.get("supersedes") == old["completion_fingerprint"], "Conflicting completion; provide supersedes for explicit recovery")
            require(old["generation_status"] == "unknown" or (old["generation_status"] == "succeeded" and old["archive_status"] != "complete" and status == "succeeded"), "Terminal completed evidence cannot be replaced")
        outputs = []
        for i, item in enumerate(data.get("outputs", [])):
            outputs.append(dict(self.asset(item), output_id=str(i + 1)))
        supplied = data.get("archive_status")
        available = sum(bool(item["available"]) for item in outputs)
        archive = "complete" if outputs and available == len(outputs) else ("partial" if available else ("failed" if status == "succeeded" else "not_applicable"))
        if supplied:
            require(supplied in {"pending", "complete", "partial", "failed", "not_applicable"}, "Invalid archive state")
            require(supplied != "complete" or archive == "complete", "Cannot mark missing output complete")
            require(supplied != "not_applicable" or status != "succeeded", "Successful output requires an archive state")
            archive = supplied
        request = read_json(path / "request.json")
        response = data.get("response_snapshot")
        for p in data.get("bindings", {}).get("output_pointers", []):
            pointer(response, p)
        job_pointer = data.get("bindings", {}).get("external_job_pointer")
        job_id = pointer(response, job_pointer) if job_pointer else data.get("provider_job_id")
        result = {**data, "schema_version": VERSION, "outputs": outputs, "archive_status": archive, "completion_fingerprint": fingerprint, "started_at": old["started_at"], "ended_at": now(), "usage": data.get("usage"), "cost": data.get("cost"), "returned_model": data.get("returned_model"), "provider_request_id": data.get("provider_request_id"), "provider_job_id": data.get("provider_job_id"), "error": data.get("error"), "missing_evidence": list(data.get("missing_evidence", []))}
        if result["usage"] is None:
            result["missing_evidence"].append("Usage not provided")
        if result["cost"] is None:
            result["missing_evidence"].append("Cost not provided")
        if status == "succeeded" and archive != "complete":
            result["missing_evidence"].append("Generation succeeded but output archive is incomplete")
        result["capture_mode"] = request["capture_mode"]
        result["provider_job_id"] = job_id
        if old.get("completion_fingerprint"):
            history = self.safe(path, "result-history", old["completion_fingerprint"] + ".json")
            if not history.exists():
                write_json(history, old)
        write_json(path / "result.json", result)
        self.touch(data["task_id"])
        return {"attempt_id": data["attempt_id"], "generation_status": status, "archive_status": archive, "output_count": len(outputs)}

    def append(self, kind, data):
        task = self.task_path(data["task_id"])
        if kind in {"event", "observe"}:
            path = self.attempt_path(data["task_id"], data["attempt_id"])
            filename = "events.jsonl" if kind == "event" else "observations.jsonl"
        else:
            path, filename = task, ("feedback.jsonl" if kind == "feedback" else "processing.jsonl")
            if data.get("attempt_id"):
                self.attempt_path(data["task_id"], data["attempt_id"])
        if kind == "event":
            require(data.get("type") in EVENTS, "Unknown event type")
        else:
            require(bool(data.get("content")), "Observation/feedback/processing content required")
        record = dict(data, event_id=uid(), recorded_at=now())
        dest = self.safe(path, filename)
        previous = dest.read_text() if dest.exists() else ""
        atomic(dest, previous + json.dumps(record, ensure_ascii=False) + "\n")
        self.touch(data["task_id"])
        return {"event_id": record["event_id"]}

    def rebuild(self, data):
        base = self.store()
        tasks, warnings = [], []
        for path in sorted(self.safe(base, "tasks").glob("*/task.json")):
            require(not path.parent.is_symlink() and not path.is_symlink(), "Symlink task entry")
            try:
                item = read_json(path)
                attempts = [p for p in self.safe(path.parent, "attempts").glob("*/request.json") if not p.parent.name.startswith(".")]
                feedback = self.safe(path.parent, "feedback.jsonl")
                last = None
                if feedback.exists():
                    entries = [json.loads(line) for line in feedback.read_text().splitlines() if line]
                    last = entries[-1].get("content") if entries else None
                tasks.append({"task_id": item["task_id"], "title": item["title"], "updated_at": item["updated_at"], "attempt_count": len(attempts), "feedback": last})
            except (RecordError, ValueError, KeyError):
                warnings.append("Unreadable task: " + path.parent.name)
        tasks.sort(key=lambda item: (item["updated_at"], item["task_id"]), reverse=True)
        write_json(self.safe(base, "index.json"), {"schema_version": VERSION, "tasks": tasks, "warnings": warnings})
        return {"task_count": len(tasks), "warnings": warnings}

    def listing(self, data):
        if not self.config():
            return {"tasks": [], "warnings": []}
        path = self.safe(self.store(), "index.json")
        try:
            index = read_json(path)
            require(index.get("schema_version") == VERSION, "Unsupported index")
        except RecordError:
            self.rebuild({})
            index = read_json(path)
        page = int(data.get("page", 0))
        require(page >= 0, "Invalid page")
        return {"tasks": index["tasks"][page * 5:page * 5 + 5], "page": page, "has_more": len(index["tasks"]) > (page + 1) * 5, "warnings": index.get("warnings", [])}

    def menu(self, data):
        state = self.status()["state"]
        rows = []
        page = int(data.get("page", 0))
        if data.get("view") == "task":
            self.task_path(data["task_id"])
            rows = [{"label": "查看最近三次生成的审核报告（不足三次也可）", "action": "review", "task_id": data["task_id"]},
                    {"label": "补充反馈并重新审核", "action": "revise", "task_id": data["task_id"]},
                    {"label": "调研模型的某项能力（选择后才联网）", "action": "research", "task_id": data["task_id"]},
                    {"label": "查看原始记录", "action": "inspect", "task_id": data["task_id"]}]
        elif data.get("view") == "history":
            items = self.listing({"page": page})
            rows = [{"label": f'{t["title"]}｜{t["updated_at"][:16]}｜{t["attempt_count"]} 次生成｜{t["feedback"] or "未记录用户反馈"}', "action": "task_menu", "task_id": t["task_id"]} for t in items["tasks"]]
            if items["has_more"]:
                rows.append({"label": "查看更早的任务", "action": "history", "page": page + 1})
            if page:
                rows.append({"label": "上一页", "action": "history", "page": page - 1})
        elif state == "not_enabled":
            rows = [{"label": "启用本项目自动留档", "action": "enable"}, {"label": "从现有上下文补录（M2 手工记录入口）", "action": "backfill"}, {"label": "查看使用说明", "action": "help"}]
        else:
            recent = self.listing({})["tasks"]
            if state == "disabled":
                rows.append({"label": "恢复本项目自动留档", "action": "enable"})
            if recent:
                rows.append({"label": "查看最近任务的审核报告", "action": "review", "task_id": recent[0]["task_id"]})
                rows.append({"label": "选择其他历史任务", "action": "history", "page": 0})
            else:
                rows.append({"label": "尚无记录：查看使用说明", "action": "help"})
            if state == "enabled":
                rows.append({"label": "关闭本项目自动留档", "action": "disable"})
        options = {str(i + 1): row for i, row in enumerate(rows)}
        options["0"] = {"label": "返回" if data.get("view") in {"history", "task"} else "退出", "action": "home" if data.get("view") in {"history", "task"} else "exit"}
        menu_id = uid()
        # Menus may exist before enabling; keep transient snapshots outside the project.
        parent = Path(tempfile.gettempdir()) / ("prompt-gap-menus-" + str(os.getuid() if hasattr(os, "getuid") else "local"))
        require(not parent.is_symlink(), "Unsafe menu directory")
        if not parent.exists():
            parent.mkdir(mode=0o700, exist_ok=True)
        write_json(parent / (menu_id + ".json"), {"project": str(self.project), "state": state, "options": options})
        return {"menu_id": menu_id, "project_state": state, "options": options, "notice": "按需审核，不自动重新生图或调研能力。"}

    def select(self, data):
        menu_id = identifier(data["menu_id"])
        parent = Path(tempfile.gettempdir()) / ("prompt-gap-menus-" + str(os.getuid() if hasattr(os, "getuid") else "local"))
        snapshot = read_json(self.safe(parent, menu_id + ".json"))
        require(snapshot["project"] == str(self.project), "Menu belongs to another project")
        choice = snapshot["options"].get(str(data["choice"]))
        require(choice is not None, "Invalid menu choice; use the displayed numbers")
        if choice["action"] in {"enable", "disable"}:
            require(snapshot["state"] == self.status()["state"], "Project state changed; reopen menu")
        # Resolution only: caller executes the returned action. No surprise mutation.
        return choice

    def inspect(self, data):
        task = self.task_path(data["task_id"])
        attempts = []
        for file in sorted(self.safe(task, "attempts").glob("*/request.json")):
            if file.parent.name.startswith("."):
                continue
            request = read_json(self.safe(file.parent, "request.json"))
            result_path = self.safe(file.parent, "result.json")
            result = read_json(result_path) if result_path.exists() else {}
            attempts.append({"attempt_id": request["attempt_id"], "recorded_at": request["recorded_at"], "transport": request["transport"], "capture_mode": request["capture_mode"], "generation_status": result.get("generation_status", "unknown"), "request_path": str(file), "prompt_path": str(file.parent / "prompt.txt"), "result_path": str(result_path)})
        attempts.sort(key=lambda row: row["recorded_at"])
        return {"task": read_json(task / "task.json"), "attempts": attempts, "notice": "Raw evidence; use review-prepare/review-save for an on-demand report"}

    def check(self, data):
        task = self.task_path(data["task_id"])
        issues = []
        for file in self.safe(task, "attempts").glob("*/request.json"):
            if file.parent.name.startswith("."):
                continue
            try:
                request = read_json(self.safe(file.parent, "request.json"))
                result = read_json(self.safe(file.parent, "result.json"))
                integrity = read_json(self.safe(file.parent, "integrity.json"))
                for name in ("request.json", "prompt.txt"):
                    if hashlib.sha256(self.safe(file.parent, name).read_bytes()).hexdigest() != integrity[name]:
                        issues.append({"attempt_id": request["attempt_id"], "issue": "request_or_prompt_changed"})
                if not self.safe(task, "requirements", identifier(request["requirement_version"]) + ".json").is_file():
                    issues.append({"attempt_id": request["attempt_id"], "issue": "requirement_missing"})
                if result["generation_status"] in {"pending", "unknown"}:
                    issues.append({"attempt_id": request["attempt_id"], "issue": "unfinished_or_unknown"})
                for asset in request.get("inputs", []) + result.get("outputs", []):
                    if not asset.get("available"):
                        issues.append({"attempt_id": request["attempt_id"], "issue": "asset_unavailable"})
                        continue
                    target = self.safe(self.store(), *Path(asset["path"]).parts)
                    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != asset["sha256"]:
                        issues.append({"attempt_id": request["attempt_id"], "issue": "asset_missing_or_changed"})
            except (RecordError, ValueError, KeyError, OSError):
                issues.append({"attempt_id": file.parent.name, "issue": "record_unreadable"})
        staging = list(self.safe(task, "attempts").glob(".begin-*"))
        if staging:
            issues.append({"issue": "interrupted_begin", "count": len(staging)})
        return {"task_id": data["task_id"], "issues": issues, "scope": "Cannot detect completely unrecorded calls"}

    def coverage(self, data):
        cfg = self.config()
        require(cfg is not None, "Project is not enabled")
        require(data.get("transport") in {"api", "codex_builtin"}, "Invalid transport")
        require(data.get("state") in {"agent_protocol_unverified", "partial", "verified", "not_connected"}, "Invalid coverage state")
        require(bool(data.get("evidence")), "Coverage requires evidence or limitation")
        cfg["coverage"][data["transport"]] = {"state": data["state"], "evidence": data["evidence"], "recorded_at": now()}
        write_json(self.local / "config.json", cfg)
        return self.status()

    def run(self, command, data=None):
        data = data or {}
        require(isinstance(data, dict), "Input must be a JSON object")
        clean, redactions = sanitize(data, data.get("redact_pointers", []))
        if redactions:
            clean["redactions"] = redactions
        with self.lock():
            if command in {"review-prepare", "review-save", "review-open", "research-save", "research-list"}:
                import importlib.util
                spec = importlib.util.spec_from_file_location("prompt_gap_review", HERE / "review.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.COMMANDS[command](self, clean)
            if command == "status":
                return self.status()
            if command in {"event", "observe", "feedback", "processing"}:
                return self.append(command, clean)
            methods = {"enable": self.enable, "disable": self.disable, "task": self.task, "requirement": self.requirement, "begin": self.begin, "finish": self.finish, "list": self.listing, "rebuild": self.rebuild, "menu": self.menu, "select": self.select, "inspect": self.inspect, "check": self.check, "coverage": self.coverage}
            require(command in methods, "Unknown command")
            return methods[command](clean)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=".")
    parser.add_argument("command", choices=["status", "enable", "disable", "task", "requirement", "begin", "finish", "event", "observe", "feedback", "processing", "list", "rebuild", "menu", "select", "inspect", "check", "coverage", "review-prepare", "review-save", "review-open", "research-save", "research-list"])
    parser.add_argument("--data", help="JSON file, or - for stdin; never pass secrets on argv")
    args = parser.parse_args()
    try:
        data = json.load(sys.stdin) if args.data == "-" else read_json(Path(args.data)) if args.data else {}
        result = Recorder(args.project).run(args.command, data)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (RecordError, OSError, ValueError, KeyError, TypeError, IndexError) as error:
        message = str(error) if isinstance(error, RecordError) else "Invalid input or filesystem operation failed; original evidence was not intentionally replaced"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
