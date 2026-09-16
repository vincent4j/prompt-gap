"""Agent-authored visual findings for the four authorized M4 calls; no generation."""
import base64
import json
from pathlib import Path
import shutil
import time
import m4_live as live


def check(number, requirement, observed, kind='target', status='pass'):
    return {'id': number, 'requirement': requirement, 'origin': 'agent_choice', 'kind': kind,
            'expected': requirement, 'observed': observed,
            'understanding': {'status': 'pass', 'reason': '生成前的测试设计已明确记录；这是助手自选测试，不冒充用户的杯子审美要求。', 'evidence': [{'source': 'requirement', 'pointer': '/test_spec'}]},
            'prompt': {'status': 'pass', 'reason': '已逐字核对实际提交的提示词，包含本项约束。', 'evidence': [{'source': 'request', 'pointer': '/request_snapshot/prompt'}]},
            'output': {'status': status, 'reason': observed, 'evidence': [{'source': 'outputs', 'pointer': '/0'}]}}


def main():
    r, state = live.setup()
    audit = {'checks': {}, 'reports': {}, 'report_build_seconds': {}}
    for mode in ('generate', 'edit'):
        original = live.pg.read_json(live.PROJECT / ('api-' + mode + '-response-original.json'))
        decoded = base64.b64decode(original['data'][0]['b64_json'])
        assert decoded == (live.PROJECT / ('api-' + mode + '.png')).read_bytes()
    for transport in ('api', 'codex_builtin'):
        task = state['tasks'][transport]['task_id']
        integrity = r.run('check', {'task_id': task})
        assert not integrity['issues'], integrity
        audit['checks'][transport] = integrity
        if transport == 'api':
            for mode, content in [('generate', '已查看原图：一只红色釉面杯、敞口、单个右侧把手，灰色桌面及背景，左侧亮光、右侧落影，无可见文字和其他道具。'), ('edit', '已对照实际上传底图与结果：杯身和把手变蓝；构图、位置与背景目视保持，但釉面细节重绘，把手上部连接处及内缘有轻微轮廓差异，不能声称严格不变。')]:
                r.run('observe', {'task_id': task, 'attempt_id': state['steps'][transport + '-' + mode]['attempt_id'], 'content': content})
        started = time.perf_counter()
        draft = r.run('review-prepare', {'task_id': task, 'last': 0})
        bundle = live.pg.read_json(Path(draft['evidence_path']))
        assert len(bundle['attempts']) == 2
        rows = []
        for i, facts in enumerate(bundle['attempts']):
            edit = i == 1
            if not edit:
                checks = [check('R01', '一只红色釉面陶瓷马克杯，杯口敞开、单个把手在右侧', '原图可见红杯、敞口及单个右侧把手，未看到第二只杯子。'), check('R02', '完整入镜、中央三分之四视角、浅灰桌面和背景，柔和左侧光线及落影', '主体完整居中，可见杯口与右侧把手；浅灰环境、左侧高光和向右落影符合测试构图。'), check('R03', '不要文字、Logo、水印、其他物体或拼图', '检查整张原图，未发现可见文字、标志、水印、附加道具或拼图。', 'constraint')]
                outcome = '本次文生图返回可查看原图，红杯静物测试要求目视符合。'
                narrative = '用户实际授权的是检验留档链路，并选择 Sunburst 接口；红杯是助手预先声明的独立测试设计，不是服装需求。本轮没有输入图片，记录的完整中文提示词直接作为请求提交，结果是一张红杯照片。审核分别核对需求记录、实际请求和原图，没有发现这份测试设计在理解或提示词覆盖中遗漏。一次简单静物成功不能证明服装纽扣、材质等复杂约束也可靠。'
                changes = {'resolved': ['取得真实生成响应和可查看原图，完成文生图留档'], 'new': [], 'unresolved': ['实际费用及隐藏模型信息未返回']}
            else:
                drift = '改蓝成功。杯子数量、位置、杯口、右侧把手、灰背景及落影目视基本保持；釉面纹理重绘，不能认定严格保真。'
                if transport == 'api':
                    drift += '把手上部连接处和内缘有轻微轮廓差异。'
                checks = [check('R01', '只把杯身釉面及把手从红色改成一致蓝色', '输出杯身和把手均呈蓝色，未见仍为红色的杯身或把手。'), check('R02', '严格保留杯形、杯口、把手形状与右侧位置、数量、构图、视角、灰背景、光线和落影', drift, 'preserve', 'partial'), check('R03', '不增加文字、Logo、水印、其他物体或拼图', '本轮输出未见新增文字、水印、附加道具或拼图。', 'constraint')]
                outcome = '定向改蓝成功；严格保留其他内容只达到部分符合，不能当成精确保真成功。'
                narrative = '本次接续同一路径的红杯原图，不是重新文生图。理解记录和实际提示词都明确要求改蓝并保留其余结构，输入记录可追溯到上一轮图片。逐项对照后，改色目标符合，宏观场景与构图基本保持，但仍有细节重绘。现有证据更支持本次执行未完全满足严格保留约束，而不是提示词忘记写保留；只有单次结果，没有对照实验，不能确定唯一原因，更不能推断模型根本没有此能力。'
                changes = {'resolved': ['红色杯身与把手改成蓝色', '编辑输入与上轮输出关联已落档'], 'new': ['釉面细节出现重绘；严格保留判为部分符合'], 'unresolved': ['未验证逐像素保留或复杂商品保真', '实际费用未查账']}
            if transport == 'api':
                narrative += ' API 记录的是实际请求载荷；编辑底图先由既有 Skill 上传，再记录传入的 URL 与下载留存的实际上传版本，不把原文件路径冒充模型收到的图片。'
            else:
                narrative += ' 内置工具只能取得可见调用参数及返回图片，没有返回可核实的模型版本，所以不把用户期望的 Image 2.5 写成已证实版本。'
            rows.append({'attempt_id': facts['attempt_id'], 'outcome': outcome, 'analysis': narrative, 'next_step': '本轮用于验证记录和审核闭环，已取得所需证据，不追加生图。若后续做商品精确保真，另行定义局部保护验收。', 'visual_inspected': True, 'changes': changes, 'checks': checks})
        analysis = {'goal': '验证用户授权的真实文生图与图生图留档，并让用户看到需求、理解、实际输入、返回和检查如何逐步流转。需求原文栏是本轮授权与菜单选择的合并记录，不是一条逐字聊天原话；杯子案例由助手自选。', 'summary': '两次真实调用及图片留档完整，文生图测试目视符合，编辑改蓝符合；严格保留其余细节仅部分符合。记录链路通过不等于模型保真全通过。', 'next_step': '请按下面两次执行顺序 review 报告。无需重跑生图；M4 后续另验安装发现及实际菜单交互。', 'risks': ['仅代表本次被留档的两次调用，不证明所有未来入口自动接入。', '实际扣费、模型内部处理过程不可见；不以返回成功代替视觉验收。', '未进行模型能力测评研究；不能据此断言模型没有能力。', '本次并未处理冻结的服装商品，也没有用户对测试图片的满意反馈。'], 'attempts': rows}
        result = r.run('review-save', {'task_id': task, 'review_id': draft['review_id'], 'analysis': analysis})
        audit['report_build_seconds'][transport] = time.perf_counter() - started
        destination = live.PROJECT / 'reports' / transport
        shutil.copytree(Path(result['report_path']).parent, destination)
        audit['reports'][transport] = str(destination / 'report.md')
    audit['uploaded_bytes_match_source'] = (live.PROJECT / 'api-edit-uploaded.png').read_bytes() == (live.PROJECT / 'api-generate.png').read_bytes()
    live.pg.write_json(live.PROJECT / 'verification.json', audit)
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == '__main__':
    main()
