"""Evidence-bound, versioned reports. The calling Agent supplies the review, not a hidden model."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from datetime import datetime, timezone


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def token(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value), "Invalid review identifier")
    return value


def resolve(obj, pointer):
    require(isinstance(pointer, str) and (pointer == "" or pointer.startswith("/")), "Invalid evidence pointer")
    for part in pointer.split("/")[1:]:
        require(not re.search(r"~(?![01])", part), "Invalid evidence escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(obj, list):
            require(bool(re.fullmatch(r"0|[1-9][0-9]*", part)), "Invalid evidence index")
            obj = obj[int(part)]
        else:
            obj = obj[part]
    return obj


def code(value, language="text"):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", text)), default=0))
    return f"{fence}{language}\n{text}\n{fence}\n"


def prepare(r, data):
    task = r.task_path(data["task_id"])
    selected = []
    for p in r.safe(task, "attempts").glob("*/request.json"):
        if not p.parent.name.startswith("."):
            r.safe(p.parent, "request.json")
            req = read(p)
            selected.append((req["recorded_at"], p.parent.name))
    selected.sort()
    wanted = data.get("attempt_ids")
    if wanted is not None:
        require(isinstance(wanted, list) and wanted and len(wanted) == len(set(wanted)), "Select unique attempts")
        require(set(wanted) <= {a for _, a in selected}, "Attempt is outside this task")
        selected = [row for row in selected if row[1] in wanted]
    else:
        last = int(data.get("last", 3))
        require(last >= 0, "Invalid review scope")
        selected = selected[-last:] if last else selected
    require(selected, "No generation attempts to review")
    review_id = uuid.uuid4().hex
    base = r.safe(task, "reviews")
    draft = r.safe(base, ".draft-" + review_id)
    draft.mkdir(parents=True, mode=0o700)
    manifest, gaps = {}, []

    def load(path, default=None, lines=False):
        r.safe(r.store(), *path.relative_to(r.store()).parts)
        key = path.relative_to(r.store()).as_posix()
        if not path.exists():
            manifest[key] = None
            return default
        manifest[key] = digest(path)
        try:
            return [json.loads(s) for s in path.read_text().splitlines() if s] if lines else read(path)
        except (ValueError, OSError):
            gaps.append("Unreadable evidence: " + key)
            return default

    task_info = load(task / "task.json")
    feedback = load(task / "feedback.jsonl", [], True)
    processing = load(task / "processing.jsonl", [], True)
    entries = []
    issues = r.check({"task_id": data["task_id"]})["issues"]
    for _, attempt_id in selected:
        path = r.attempt_path(data["task_id"], attempt_id)
        request = load(path / "request.json", {})
        requirement = load(task / "requirements" / (token(request["requirement_version"]) + ".json"), {})
        result = load(path / "result.json", {})
        load(path / "integrity.json", {})
        prompt_path = r.safe(path, "prompt.txt")
        manifest[prompt_path.relative_to(r.store()).as_posix()] = digest(prompt_path) if prompt_path.exists() else None
        entry = {"attempt_id": attempt_id, "requirement": requirement, "request": request, "result": result,
                 "observations": load(path / "observations.jsonl", [], True), "events": load(path / "events.jsonl", [], True),
                 "feedback": [f for f in feedback if not f.get("attempt_id") or f["attempt_id"] == attempt_id],
                 "processing": [p for p in processing if not p.get("attempt_id") or p["attempt_id"] == attempt_id],
                 "integrity_issues": [i for i in issues if i.get("attempt_id") == attempt_id or not i.get("attempt_id")],
                 "inputs": [], "outputs": []}
        for kind, raw in (("inputs", request), ("outputs", result)):
            for asset in raw.get(kind, []):
                item = dict(asset, review_path=None)
                if asset.get("available"):
                    source = r.safe(r.store(), *Path(asset["path"]).parts)
                    manifest[asset["path"]] = digest(source) if source.is_file() else None
                    if manifest[asset["path"]] == asset["sha256"]:
                        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg"}.get(asset.get("media_type"), "bin")
                        name = "assets/" + asset["sha256"] + "." + ext
                        target = draft / name
                        target.parent.mkdir(exist_ok=True)
                        if not target.exists():
                            shutil.copyfile(source, target)
                        item["review_path"] = name
                entry[kind].append(item)
        entries.append(entry)
    bundle = {"schema_version": 1, "review_id": review_id, "task_id": data["task_id"], "created_at": stamp(), "task": task_info,
              "attempts": entries, "manifest": manifest, "gaps": gaps, "scope": "Selected attempts only; unrecorded calls cannot be detected"}
    write(draft / "evidence.json", bundle)
    return {"review_id": review_id, "evidence_path": str(draft / "evidence.json"), "attempt_count": len(entries), "next": "Read review.md; inspect evidence/images, then supply review-save analysis"}


STAGES = {"understanding": "需求理解", "prompt": "提示词覆盖", "output": "结果符合"}
STATUS = {"pass": "符合", "partial": "部分符合", "fail": "不符合", "unknown": "无法判断"}


def validate(bundle, analysis):
    for field in ("goal", "summary", "next_step"):
        require(isinstance(analysis.get(field), str) and analysis[field].strip(), "Missing review " + field)
    rows = analysis.get("attempts", [])
    require([a["attempt_id"] for a in rows] == [a["attempt_id"] for a in bundle["attempts"]], "Review must cover selected attempts in chronological order")
    for item, facts in zip(rows, bundle["attempts"]):
        for field in ("outcome", "analysis", "next_step"):
            require(isinstance(item.get(field), str) and item[field].strip(), "Each attempt needs " + field)
        checks = item.get("checks", [])
        require(checks and len({c["id"] for c in checks}) == len(checks), "Requirements need unique checks")
        if facts["request"]["task_type"] == "image_to_image":
            require(any(c.get("kind") == "target" for c in checks) and any(c.get("kind") == "preserve" for c in checks), "Image edit review must cover both target and preservation")
        for check in checks:
            for field in ("id", "requirement", "expected", "observed"):
                require(isinstance(check.get(field), str) and check[field].strip(), "Incomplete requirement check")
            require(check.get("origin") in {"explicit", "inherited", "agent_choice", "unconfirmed"}, "Requirement origin missing")
            require(check.get("kind") in {"target", "preserve", "constraint"}, "Requirement kind missing")
            if check["origin"] in {"explicit", "inherited"}:
                ref = check.get("requirement_evidence", {})
                require(ref.get("source") == "requirement", "User requirements must trace to recorded requirements, not just prompts")
                text = resolve(facts["requirement"], ref.get("pointer", ""))
                require(isinstance(text, str) and bool(ref.get("quote")) and ref["quote"] in text, "Requirement quote must match recorded source")
            for stage in STAGES:
                value = check[stage]
                require(value.get("status") in STATUS and bool(value.get("reason")), "Stage needs status and explanation")
                refs = value.get("evidence", [])
                require(value["status"] == "unknown" or refs, "Determinate findings require evidence")
                if value["status"] != "unknown":
                    needed = {"understanding": "requirement", "prompt": "request", "output": "outputs"}[stage]
                    require(any(ref.get("source") == needed for ref in refs), "Evidence must address the reviewed stage")
                for ref in refs:
                    require(ref.get("source") in {"requirement", "request", "result", "inputs", "outputs", "events", "observations", "feedback", "processing"}, "Invalid evidence source")
                    text = resolve(facts[ref["source"]], ref.get("pointer", ""))
                    if "quote" in ref:
                        require(isinstance(text, str) and ref["quote"] in text, "Evidence quotation does not match source")
                if stage == "understanding" and (not facts["requirement"].get("interpretation") or facts["requirement"].get("interpretation_status") == "missing"):
                    require(value["status"] == "unknown", "Missing contemporary interpretation cannot be diagnosed")
                if stage == "output" and value["status"] != "unknown":
                    require(facts["result"].get("generation_status") == "succeeded" and item.get("visual_inspected") is True, "Visual conclusion requires inspecting successful output")
                    require(any(ref["source"] == "outputs" for ref in refs), "Output conclusion requires output-image evidence")
                    require(any(a.get("review_path") for a in facts["outputs"]), "Missing images cannot receive visual verdict")
                    for ref in refs:
                        if ref["source"] == "outputs":
                            index = ref.get("pointer", "").split("/")
                            require(len(index) >= 2 and index[1].isdigit() and facts["outputs"][int(index[1])].get("review_path"), "Output evidence must reference a specific available image")
                if facts["integrity_issues"]:
                    require(value["status"] == "unknown", "Integrity gaps require unknown findings; preserve facts without firm attribution")
        changes = item.get("changes", {})
        require(all(isinstance(changes.get(key), list) for key in ("resolved", "new", "unresolved")), "Record resolved/new/unresolved changes")
    return analysis


LABELS = {
    "not_explicitly_confirmed": "用户尚未明确确认", "confirmed": "用户已明确确认", "user_confirmed": "用户已明确确认", "corrected": "用户已纠正",
    "synthetic": "合成演示", "live": "实时留档", "backfilled": "事后补录",
    "codex_builtin": "Codex 内置生图", "api": "外部接口调用", "tool_boundary": "工具调用时可见的输入", "api_payload": "接口请求内容", "summary": "仅有摘要记录",
    "succeeded": "调用成功", "failed": "失败", "unknown": "未知", "cancelled": "已取消", "pending": "待完成", "complete": "完整", "partial": "部分完成", "not_applicable": "不适用",
    "synthetic_fixture": "合成示例图片", "original_output": "模型原始输出", "editing_base": "编辑底图", "product": "商品参考", "style": "风格参考", "material_surface_reference": "材质表面参考",
    "explicit": "用户明确要求", "inherited": "继承此前要求", "agent_choice": "助手自行选择", "unconfirmed": "尚未确认的假设", "target": "目标修改", "preserve": "必须保留的内容", "constraint": "约束要求",
    "requirement": "需求记录", "request": "实际请求", "result": "返回记录", "inputs": "输入图片", "outputs": "输出图片", "events": "调用事件", "observations": "当时自检", "feedback": "用户反馈", "processing": "后处理记录",
    "unfinished_or_unknown": "调用尚未结束或结果未知", "request_or_prompt_changed": "请求或提示词文件已变化", "asset_missing_or_changed": "图片文件缺失或已变化", "asset_unavailable": "图片不可用", "requirement_missing": "缺少需求记录", "record_unreadable": "记录无法读取", "interrupted_begin": "开始留档时发生中断",
}
GAPS = {
    "Requested model not visible or not bound": "请求模型不可见或尚未建立字段关联",
    "Usage not provided": "未提供用量记录", "Cost not provided": "未提供费用记录",
    "No terminal response recorded": "尚未记录最终响应",
    "Generation succeeded but output archive is incomplete": "生成调用成功，但输出图片归档不完整",
    "Input asset identities unavailable": "无法确认输入图片身份",
    "No prompt bindings; snapshot preserved without inferred prompt": "未关联提示词字段；保留请求快照，不猜补提示词",
    "Synthetic example: no actual tool response, model version, elapsed time, cost or user approval": "合成示例：没有真实工具响应、模型版本、耗时、费用或用户验收记录",
}


def label(value):
    if value is None:
        return "未记录"
    return LABELS.get(value, value if re.search(r"[\u4e00-\u9fff]", str(value)) else "自定义值（详见底层记录）")


def evidence_label(ref):
    source = label(ref["source"])
    pointer = ref.get("pointer", "")
    if ref["source"] in {"inputs", "outputs"} and re.match(r"/\d+(?:/|$)", pointer):
        source += "第 " + str(int(pointer.split("/")[1]) + 1) + " 张"
    elif pointer == "/interpretation":
        source += "中的当时理解"
    elif pointer == "/user_text":
        source += "中的用户原话"
    elif pointer == "/request_snapshot/prompt":
        source += "中的提示词原文"
    return source + ("：“" + ref["quote"] + "”" if "quote" in ref else "")


def media(items):
    if not items:
        return "没有记录图片。\n"
    parts = []
    for i, item in enumerate(items, 1):
        parts.append(f"图片 {i}；职责：{label(item.get('role'))}。")
        if item.get("review_path"):
            path = item["review_path"]
            parts.append(f"![图片 {i}]({path})" if not path.endswith(".bin") else f"[原始文件 {i}]({path})")
        else:
            parts.append("图片不可查看：" + item.get("missing_reason", "文件缺失、哈希不一致或无法归档"))
        for key in ("controls", "must_not_control"):
            if key in item:
                parts.append(f"{'用于约束' if key == 'controls' else '不得影响'}（原始记录）：{item[key]}")
    return "\n\n".join(parts) + "\n"


def render(bundle, analysis):
    synthetic = any(a["request"].get("capture_mode") == "synthetic" for a in bundle["attempts"])
    notice = "合成演示：没有真实模型调用，不构成模型能力证据。" if synthetic else "根据已留存事实，由当前 Agent 按需审核；不揭示模型内部推理。"
    detail = ["# 每次执行的完整经过", "[返回主报告](report.md)", notice,
              "范围：本报告选中的 " + str(len(bundle["attempts"])) + " 次调用，按记录时间排列；不代表任务全部历史。审核文字是本次复盘解释，不冒充当时记录。"]
    for n, (facts, a) in enumerate(zip(bundle["attempts"], analysis["attempts"]), 1):
        req, request, result = facts["requirement"], facts["request"], facts["result"]
        section_start = len(detail)
        detail.extend([f"## 第 {n} 次执行：{a['outcome']}", f"记录方式：{label(request['capture_mode'])}；调用编号：{a['attempt_id']}。", "### 用户当时要求什么", code(req.get("user_text", "未留存用户原话，无法复原")),
                       f"需求版本：{request['requirement_version']}；确认状态：{label(req.get('confirmation'))}。原话需包含本轮继承要求；缺失部分不能猜补。",
                       "### AI 当时怎么理解", code(req.get("interpretation", "未留存当时理解，无法确定")), "当时执行说明：", code(request.get("execution_note", "未记录")),
                       "### 最终传给模型什么", f"入口：{label(request['transport'])}；可见边界：{label(request['capture_scope'])}；请求模型：{request.get('requested_model') or '未知'}。", "完整可见请求（包含实际提示词、参数及图片引用；脱敏字段以记录为准）：", code(request.get("request_snapshot"), "json"), media(facts["inputs"]),
                       "### 实际返回什么", f"调用状态：{label(result.get('generation_status', 'unknown'))}；归档状态：{label(result.get('archive_status', 'unknown'))}。调用成功不等于视觉通过。", media(facts["outputs"]), "完整可见响应：", code(result.get("response_snapshot", "未留存响应正文"), "json"),
                       f"返回模型：{result.get('returned_model') or '未知'}；费用：{result.get('cost') if result.get('cost') is not None else '未知'}；用量：{result.get('usage') if result.get('usage') is not None else '未知'}。",
                       "缺失或不可见证据：" + "；".join(dict.fromkeys(GAPS.get(g, g if re.search(r"[\u4e00-\u9fff]", g) else "原始缺失说明（待补中文解释）：" + g) for g in request.get("missing_evidence", []) + result.get("missing_evidence", [])))])
        # Show actual prompt text directly, not only escaped inside a JSON payload.
        prompt_blocks = []
        for pointer in request.get("bindings", {}).get("prompt_pointers", []):
            try:
                prompt_blocks.append(code(resolve(request["request_snapshot"], pointer)))
            except (KeyError, IndexError, TypeError, ValueError):
                prompt_blocks.append("这段提示词引用无法解析，不能猜补。")
        input_heading = detail.index("### 最终传给模型什么", section_start)
        detail[input_heading + 1:input_heading + 1] = ["实际提示词全文：", *prompt_blocks] if prompt_blocks else ["没有可提取的提示词绑定；下面保留实际可见请求，不用审核者的转述替代原文。"]
        if a.get("prompt_explanation"):
            detail[input_heading + 1 + (1 + len(prompt_blocks) if prompt_blocks else 1):input_heading + 1 + (1 + len(prompt_blocks) if prompt_blocks else 1)] = ["提示词中文说明（本次审核解释，不是另一次发送的提示词）：" + a["prompt_explanation"]]
        if result.get("error"):
            detail.extend(["调用错误原文：", code(result["error"])])
        for title, key in (("调用事件", "events"), ("当时 Agent 自检（不是模型解释）", "observations"), ("用户反馈（未绑定调用的属于任务级反馈）", "feedback"), ("后处理记录（不能把后处理错误归给模型）", "processing")):
            if facts[key]:
                detail.extend([title + "：", "\n\n".join(code(item.get("content", item.get("details", item))) for item in facts[key])])
            else:
                detail.append(title + "：未留存，不能推断为已经执行或已经通过。")
        detail.extend(["### 这些数据怎样一步步流转", "用户原话形成当时的需求理解；Agent 据此构造下方所核对的提示词、图片与参数；可见调用返回图片或错误；Agent 自检和用户反馈是返回之后的独立记录，不是模型解释。这里只还原外部可观察过程，不推断模型内部推理。"])
        for c in a["checks"]:
            original = c.get("requirement_evidence", {}).get("quote", "未记录对应原话")
            prompt_quotes = [ref["quote"] for ref in c["prompt"].get("evidence", []) if ref.get("quote")]
            detail.append(f"**{c['requirement']}**：原话“{original}” → 当时理解核对：{c['understanding']['reason']} → 实际请求" + ("中的“" + "；".join(prompt_quotes) + "”" if prompt_quotes else "核对：" + c["prompt"]["reason"]) + f" → 返回观察：{c['observed']}。")
        detail.extend(["### 本次审核：差异发生在哪里", a["analysis"]])
        for c in a["checks"]:
            detail.extend([f"#### {c['id']}：{c['requirement']}", f"来源：{label(c['origin'])}；类型：{label(c['kind'])}。预期：{c['expected']}；观察：{c['observed']}。"])
            for stage, stage_label in STAGES.items():
                v = c[stage]
                detail.append(f"{stage_label}：{STATUS[v['status']]}。{v['reason']}")
                if v.get("evidence"):
                    detail.append("依据：" + "；".join(evidence_label(ref) for ref in v["evidence"]))
        detail.extend(["本轮变化：" + "；".join(label + "：" + ("、".join(a["changes"][key]) or "无") for key, label in (("resolved", "已解决"), ("new", "新增"), ("unresolved", "未解决"))), "下一步及保留项：" + a["next_step"]])
        if facts["integrity_issues"]:
            detail.extend(["证据完整性风险：", "；".join(label(i.get("issue")) for i in facts["integrity_issues"])])
    verdicts = []
    for stage, stage_label in STAGES.items():
        states = {c[stage]["status"] for a in analysis["attempts"] for c in a["checks"]}
        verdict = next(s for s in ("fail", "partial", "unknown", "pass") if s in states)
        verdicts.append(stage_label + "：" + STATUS[verdict])
    # The first file must be self-contained for success AND failure, not a summary gate.
    report = ["# PromptGap 完整执行审核报告", notice, *detail[3:], "## 整体审核结论", analysis["summary"], "本次所选范围的逐项检查汇总（含各轮，不只最后一轮）：" + "；".join(verdicts) + "。", "## 下一步", analysis["next_step"]]
    risks = analysis.get("risks", []) + bundle["gaps"]
    if risks:
        report += ["## 证据边界", "\n".join("- " + str(r) for r in risks)]
    report += ["本报告已包含每轮完整过程，无需另开文件才能理解输入与输出。[同内容的逐次执行详情](details.md)。", "本报告不自动联网研究、修改提示词或重新生成。一次或数次未执行，不足以证明模型完全没有能力。"]
    return "\n\n".join(report) + "\n", "\n\n".join(detail) + "\n"


def save(r, data):
    task = r.task_path(data["task_id"])
    review_id = token(data["review_id"])
    draft = r.safe(task, "reviews", ".draft-" + review_id)
    bundle = read(r.safe(draft, "evidence.json"))
    require(bundle["task_id"] == data["task_id"], "Review belongs to another task")
    for name, expected in bundle["manifest"].items():
        path = r.safe(r.store(), *Path(name).parts)
        require((digest(path) if path.is_file() else None) == expected, "Evidence changed after prepare; create a new review")
    analysis = validate(bundle, data["analysis"])
    report, details = render(bundle, analysis)
    findings = [{"attempt_id": a["attempt_id"], "requirement_id": c["id"], "divergent_stages": [s for s in STAGES if c[s]["status"] in {"fail", "partial"}], "unknown_stages": [s for s in STAGES if c[s]["status"] == "unknown"], "execution_gap_candidate": c["understanding"]["status"] == c["prompt"]["status"] == "pass" and c["output"]["status"] == "fail", "capability_limit_proven": False} for a in analysis["attempts"] for c in a["checks"]]
    write(r.safe(draft, "diagnosis.json"), {"schema_version": 1, "review_id": review_id, "reviewed_at": stamp(), "reviewer": "calling_agent", "supersedes": data.get("supersedes"), "findings": findings, "analysis": analysis})
    for name, content in (("report.md", report), ("details.md", details)):
        r.safe(draft, name).write_text(content, encoding="utf-8")
    final = r.safe(task, "reviews", review_id)
    require(not final.exists(), "Review already exists")
    if data.get("supersedes"):
        require(r.safe(task, "reviews", token(data["supersedes"]), "report.md").is_file(), "Previous review not found")
    os.replace(draft, final)
    return {"review_id": review_id, "report_path": str(final / "report.md"), "details_path": str(final / "details.md")}


def latest(r, data):
    task = r.task_path(data["task_id"])
    paths = [p for p in r.safe(task, "reviews").glob("*/diagnosis.json") if not p.parent.name.startswith(".")]
    if data.get("review_id"):
        paths = [r.safe(task, "reviews", token(data["review_id"]), "diagnosis.json")]
    require(paths, "No completed review yet")
    path = max(paths, key=lambda p: read(r.safe(p.parent, p.name))["reviewed_at"])
    bundle = read(r.safe(path.parent, "evidence.json"))
    stale = []
    for name, expected in bundle["manifest"].items():
        source = r.safe(r.store(), *Path(name).parts)
        if (digest(source) if source.is_file() else None) != expected:
            stale.append(name)
    return {"report_path": str(path.parent / "report.md"), "details_path": str(path.parent / "details.md"), "changed_sources": stale, "notice": "Historical snapshot; new attempts outside scope are not included"}


def research(r, data):
    """Cache explicit research only; this command never searches the network."""
    require(data.get("user_authorized") is True, "Capability research requires explicit user choice")
    for key in ("model", "version", "provider", "capability", "conclusion", "limitations"):
        require(isinstance(data.get(key), str) and data[key].strip(), "Missing research " + key)
    require(data.get("verdict") in {"unsupported", "unstable", "insufficient_evidence"}, "Invalid research verdict")
    sources = data.get("sources", [])
    require(sources or data["verdict"] == "insufficient_evidence", "Capability claim needs sources")
    for source in sources:
        require(source.get("kind") in {"official", "benchmark", "project_experiment"}, "Separate source types")
        for key in ("reference", "retrieved_at", "published_at", "model_match", "task_match", "finding"):
            require(isinstance(source.get(key), str) and source[key], "Incomplete research source")
    path = r.safe(r.store(), "research", uuid.uuid4().hex + ".json")
    write(path, dict(data, schema_version=1, recorded_at=stamp()))
    return {"research_path": str(path), "notice": "Source claims recorded, not independently verified by this script"}


def research_list(r, data):
    rows = []
    for p in r.safe(r.store(), "research").glob("*.json"):
        item = read(r.safe(p.parent, p.name))
        if all(not data.get(k) or data[k] == item[k] for k in ("model", "version", "provider", "capability")):
            rows.append({k: item[k] for k in ("model", "version", "provider", "capability", "verdict", "recorded_at")} | {"path": str(p)})
    return {"entries": sorted(rows, key=lambda row: row["recorded_at"], reverse=True), "notice": "Check date, model/task match and changed evidence before reusing"}


COMMANDS = {"review-prepare": prepare, "review-save": save, "review-open": latest, "research-save": research, "research-list": research_list}
