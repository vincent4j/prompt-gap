#!/usr/bin/env python3
"""Exercise M2→M3 with existing synthetic evidence, without network or model calls."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prompt_gap", ROOT / "scripts/prompt_gap.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)


def build(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Destination exists; choose a new directory, never overwrite a review")
    source = ROOT / "examples/success-demo"
    fixture = json.loads((source / "fixture.json").read_text())
    original = fixture["attempts"][0]
    # Deterministic validation of this synthetic SVG, not a general image evaluator.
    svg = ET.parse(source / original["output_file"]).getroot()
    circles = list(svg.iter("{http://www.w3.org/2000/svg}circle"))
    assert len(circles) == 2 and all(c.get("fill") == "#dc2626" for c in circles)
    with tempfile.TemporaryDirectory(prefix="prompt-gap-m3-demo-") as folder:
        r = pg.Recorder(folder)
        r.run("enable")
        t = r.run("task", {"title": "成功生成全过程（合成演示）"})["task_id"]
        v = r.run("requirement", {"task_id": t, "user_text": fixture["user_request"], "interpretation": original["interpretation"], "source": "M1 synthetic fixture"})["requirement_version"]
        a = r.run("begin", {"task_id": t, "requirement_version": v, "transport": "codex_builtin", "task_type": "text_to_image", "capture_mode": "synthetic", "capture_scope": "tool_boundary", "execution_note": "一次生成，圆的大小和间距由 Agent 自选；此处仅回放合成资料", "request_snapshot": original["request_snapshot"], "bindings": {"prompt_pointers": ["/prompt"]}, "inputs": [], "missing_evidence": original["missing_evidence"]})["attempt_id"]
        r.run("finish", {"task_id": t, "attempt_id": a, "generation_status": "succeeded", "outputs": [{"path": str(source / original["output_file"]), "role": "synthetic_fixture"}], "missing_evidence": original["missing_evidence"]})
        r.run("observe", {"task_id": t, "attempt_id": a, "source": "synthetic_fixture", "content": original["recorded_self_check"]})
        draft = r.run("review-prepare", {"task_id": t})
        checks = []
        for i, (requirement, quote, prompt_quote, observed) in enumerate([
            ("白色背景", "白底", "plain white background", "示意图为白底"),
            ("恰好两个圆", "两个", "exactly two red circles", "示意图中两个分开的圆"),
            ("圆形为红色", "红色", "red circles", "两个圆都为红色"),
            ("不加文字或其他物体", "不要文字", "Do not include text or any other objects", "示意图没有文字或其他物体"),
        ], 1):
            checks.append({"id": f"R{i:02}", "requirement": requirement, "origin": "explicit", "kind": "constraint", "requirement_evidence": {"source": "requirement", "pointer": "/user_text", "quote": quote}, "expected": requirement, "observed": observed,
                           "understanding": {"status": "pass", "reason": "当时理解保留了这一要求，没有另加相反目标。", "evidence": [{"source": "requirement", "pointer": "/interpretation"}]},
                           "prompt": {"status": "pass", "reason": "实际可见提示词明确表达这一要求。", "evidence": [{"source": "request", "pointer": "/request_snapshot/prompt", "quote": prompt_quote}]},
                           "output": {"status": "pass", "reason": "合成示意结果与这一要求相符；不是模型能力测评。", "evidence": [{"source": "outputs", "pointer": "/0"}]}})
        analysis = {"goal": fixture["user_request"], "summary": "这次合成执行在已核验的背景、数量、颜色和无文字要求上均符合。需求理解、实际提示词与示意结果没有发现偏差；没有用户最终验收记录，不能称为用户已批准。", "next_step": "如果只是了解这张图如何产生，到这里即可结束，不需要返修或调研模型能力。", "risks": ["没有真实模型调用、模型版本、费用或用户验收；不能由此推断实际模型可靠性。"], "attempts": [{"attempt_id": a, "outcome": "按要求完成白底两个红圆", "analysis": "用户明确要求白底、两个红圆和不要文字；当时理解保留了这些要求，并把未指定的大小与间距留作布局选择。实际提示词明确写了数量和颜色，示意图与之相符。因此没有必要强行寻找失败原因，也不能把某个英文词的使用说成成功的因果证明。", "next_step": "无须返修。保留数量、颜色和背景；没有证据需要改变模型。", "visual_inspected": True, "checks": checks, "changes": {"resolved": [], "new": [], "unresolved": []}}]}
        analysis["attempts"][0]["prompt_explanation"] = "生成一张纯白背景图片，恰好有两个红色圆形，不包含文字或其他物体。数量写成 exactly two，而不是模糊的若干圆；圆的大小与间距没有指定。输入只有这段文字，没有参考图、遮罩或上一轮图片。"
        result = r.run("review-save", {"task_id": t, "review_id": draft["review_id"], "analysis": analysis})
        shutil.copytree(Path(result["report_path"]).parent, destination)
    return str(destination / "report.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="New output directory; existing directories are never overwritten")
    print(build(parser.parse_args().output))
