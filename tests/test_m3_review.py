import copy
import json
from pathlib import Path
import unittest
import test_m2_recorder as m2


class ReviewTests(unittest.TestCase):
    setUp = m2.RecorderTests.setUp
    begin = m2.RecorderTests.begin
    path = m2.RecorderTests.path
    finish_data = m2.RecorderTests.finish_data

    def complete(self, **kwargs):
        a = self.begin(**kwargs)
        self.r.run("finish", self.finish_data(a))
        return a

    def prepare(self):
        return self.r.run("review-prepare", {"task_id": self.t})

    def analysis(self, a):
        c = {"id": "R01", "requirement": "两个红圆", "origin": "explicit", "kind": "target", "expected": "两个红圆", "observed": "合成检查示例", "requirement_evidence": {"source": "requirement", "pointer": "/user_text", "quote": "两个红圆"}}
        for stage, source, p in (("understanding", "requirement", "/interpretation"), ("prompt", "request", "/request_snapshot/prompt"), ("output", "outputs", "/0")):
            c[stage] = {"status": "pass", "reason": "测试审核，并非真实视觉评测", "evidence": [{"source": source, "pointer": p}]}
        return {"goal": "测试完整过程", "summary": "合成成功，不代表模型能力", "next_step": "无需为了报告再次生成", "risks": ["合成数据"], "attempts": [{"attempt_id": a, "outcome": "测试中符合", "analysis": "依照原话、理解、请求和输出依次审核。", "next_step": "保留现有结果，无需返修", "visual_inspected": True, "checks": [c], "changes": {"resolved": [], "new": [], "unresolved": []}}]}

    def save(self, draft, analysis, **extra):
        return self.r.run("review-save", {"task_id": self.t, "review_id": draft["review_id"], "analysis": analysis, **extra})

    def test_success_report_and_links(self):
        a = self.complete()
        result = self.save(self.prepare(), self.analysis(a))
        report, detail = Path(result["report_path"]), Path(result["details_path"])
        self.assertTrue(report.is_file() and detail.is_file())
        bundle = json.loads((report.parent / "evidence.json").read_text())
        self.assertTrue((report.parent / bundle["attempts"][0]["outputs"][0]["review_path"]).is_file())
        diagnosis = json.loads((report.parent / "diagnosis.json").read_text())
        self.assertEqual([], diagnosis["findings"][0]["divergent_stages"])
        self.assertFalse(diagnosis["findings"][0]["capability_limit_proven"])

    def test_three_stage_gaps(self):
        ids = [self.complete() for _ in range(3)]
        analysis = self.analysis(ids[0])
        analysis["attempts"] = [self.analysis(a)["attempts"][0] for a in ids]
        for row, stage in zip(analysis["attempts"], ("understanding", "prompt", "output")):
            row["checks"][0][stage]["status"] = "fail"
        result = self.save(self.prepare(), analysis)
        findings = json.loads((Path(result["report_path"]).parent / "diagnosis.json").read_text())["findings"]
        self.assertEqual([["understanding"], ["prompt"], ["output"]], [f["divergent_stages"] for f in findings])
        self.assertTrue(findings[2]["execution_gap_candidate"])
        self.assertFalse(findings[0]["execution_gap_candidate"])

    def test_report_labels_chinese_without_changing_evidence(self):
        prompt = "Create exactly two red circles."
        a = self.complete(request_snapshot={"prompt": prompt})
        result = self.save(self.prepare(), self.analysis(a))
        for name in ("report_path", "details_path"):
            text = Path(result[name]).read_text()
            for raw in ("not_explicitly_confirmed", "codex_builtin", "tool_boundary", "来源：explicit", "类型：target", "输出图片/0"):
                self.assertNotIn(raw, text)
            self.assertIn("用户尚未明确确认", text)
            self.assertIn("调用成功", text)
            self.assertIn(prompt, text)
        evidence = json.loads((Path(result["report_path"]).parent / "evidence.json").read_text())
        self.assertEqual("not_explicitly_confirmed", evidence["attempts"][0]["requirement"]["confirmation"])

    def test_primary_report_contains_complete_success_data_flow(self):
        prompt = "先画两个红圆。\n不要文字。"
        a = self.complete(request_snapshot={"prompt": prompt, "size": "1024x1024"})
        self.r.run("observe", {"task_id": self.t, "attempt_id": a, "content": "独立的当时检查原话"})
        self.r.run("feedback", {"task_id": self.t, "attempt_id": a, "content": "独立的用户反馈原话"})
        draft = self.prepare()
        bundle = json.loads(Path(draft["evidence_path"]).read_text())
        analysis = self.analysis(a)
        analysis["attempts"][0]["prompt_explanation"] = "中文解释不冒充发送文本"
        result = self.save(draft, analysis)
        report = Path(result["report_path"]).read_text()
        facts = bundle["attempts"][0]
        # A reader needs only the primary report to see each actual node and image.
        nodes = [facts["requirement"]["user_text"], facts["requirement"]["interpretation"], prompt,
                 facts["outputs"][0]["review_path"], "独立的当时检查原话", "独立的用户反馈原话", analysis["attempts"][0]["analysis"]]
        positions = [report.index(node) for node in nodes]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("1024x1024", report)
        self.assertIn("中文解释不冒充发送文本", report)
        self.assertIn(analysis["attempts"][0]["checks"][0]["observed"], report)

    def test_missing_interpretation_cannot_be_guessed(self):
        v = self.r.run("requirement", {"task_id": self.t, "user_text": "两个红圆", "interpretation": "未留存", "interpretation_status": "missing"})["requirement_version"]
        a = self.complete(requirement_version=v, capture_mode="backfilled")
        draft, analysis = self.prepare(), self.analysis(a)
        with self.assertRaises(ValueError):
            self.save(draft, analysis)
        analysis["attempts"][0]["checks"][0]["understanding"]["status"] = "unknown"
        self.save(draft, analysis)

    def test_evidence_quote_must_match(self):
        a = self.complete()
        analysis = self.analysis(a)
        analysis["attempts"][0]["checks"][0]["prompt"]["evidence"][0]["quote"] = "invented text"
        with self.assertRaises(ValueError):
            self.save(self.prepare(), analysis)

    def test_requirement_not_derived_only_from_prompt(self):
        a = self.complete()
        analysis = self.analysis(a)
        del analysis["attempts"][0]["checks"][0]["requirement_evidence"]
        with self.assertRaises(ValueError):
            self.save(self.prepare(), analysis)

    def test_image_edit_requires_preservation_check(self):
        a = self.complete(task_type="image_to_image", inputs=[{"path": str(self.img), "role": "editing_base"}])
        draft, analysis = self.prepare(), self.analysis(a)
        with self.assertRaises(ValueError):
            self.save(draft, analysis)
        c = copy.deepcopy(analysis["attempts"][0]["checks"][0]); c.update(id="R02", kind="preserve")
        c["output"]["status"] = "fail"
        analysis["attempts"][0]["checks"].append(c)
        result = self.save(draft, analysis)
        findings = json.loads((Path(result["report_path"]).parent / "diagnosis.json").read_text())["findings"]
        self.assertEqual(["output"], findings[1]["divergent_stages"])

    def test_missing_image_blocks_visual_claim(self):
        a = self.begin()
        self.r.run("finish", self.finish_data(a, outputs=[{"missing_reason": "download failed"}]))
        with self.assertRaises(ValueError):
            self.save(self.prepare(), self.analysis(a))

    def test_changed_evidence_requires_new_review(self):
        a = self.complete()
        draft = self.prepare()
        self.r.run("feedback", {"task_id": self.t, "attempt_id": a, "content": "纠正"})
        with self.assertRaises(ValueError):
            self.save(draft, self.analysis(a))

    def test_correction_new_version_preserves_old(self):
        a = self.complete()
        draft = self.prepare()
        first = self.save(draft, self.analysis(a))
        old = Path(first["report_path"]).read_bytes()
        self.r.run("feedback", {"task_id": self.t, "attempt_id": a, "content": "重新检查数量"})
        self.assertTrue(self.r.run("review-open", {"task_id": self.t})["changed_sources"])
        second = self.save(self.prepare(), self.analysis(a), supersedes=first["review_id"])
        self.assertNotEqual(first["review_id"], second["review_id"])
        self.assertEqual(old, Path(first["report_path"]).read_bytes())

    def test_scope_and_chronology(self):
        ids = [self.complete() for _ in range(4)]
        draft = self.prepare()
        bundle = json.loads(Path(draft["evidence_path"]).read_text())
        self.assertEqual(ids[-3:], [a["attempt_id"] for a in bundle["attempts"]])
        analysis = self.analysis(ids[-1])
        with self.assertRaises(ValueError):
            self.save(draft, analysis)

    def test_research_requires_explicit_choice_and_sources(self):
        data = {"model": "test", "version": "unknown", "provider": "test", "capability": "count", "conclusion": "证据不足", "limitations": "无真实评测", "verdict": "insufficient_evidence", "sources": []}
        with self.assertRaises(ValueError):
            self.r.run("research-save", data)
        data["user_authorized"] = True
        self.r.run("research-save", data)
        self.assertEqual(1, len(self.r.run("research-list", {"model": "test"})["entries"]))
        data["verdict"] = "unsupported"
        with self.assertRaises(ValueError):
            self.r.run("research-save", data)

    def test_research_menu_is_only_an_action_not_network(self):
        menu = self.r.run("menu", {"view": "task", "task_id": self.t})
        n = next(k for k, v in menu["options"].items() if v["action"] == "research")
        self.assertEqual("research", self.r.run("select", {"menu_id": menu["menu_id"], "choice": n})["action"])
        self.assertFalse((self.r.store() / "research").exists())

    def test_unknown_integrity_and_disabled_review(self):
        a = self.complete()
        (self.path(a) / "prompt.txt").write_text("changed")
        self.r.run("disable")
        draft, analysis = self.prepare(), self.analysis(a)
        with self.assertRaises(ValueError):
            self.save(draft, analysis)
        for stage in ("understanding", "prompt", "output"):
            analysis["attempts"][0]["checks"][0][stage]["status"] = "unknown"
        self.save(draft, analysis)


if __name__ == "__main__":
    unittest.main()
