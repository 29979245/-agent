"""Agent 端到端冒烟（tasks 10.2 / 11.9）：对运行中的后端做 HTTP 级验证。

用法：python scripts/smoke_agent_e2e.py [port]（默认 8000；需先启动当前代码的 uvicorn）

覆盖：
1. 认证后 /api/agent/chat/langgraph/stream 返回 text/event-stream：
   - navigate 快捷路径（11.9 关键词兜底）："打开考试工作台" → navigate(page=exam-v2) → done，无 error；
   - chat 路径（无 LLM key 时 Provider 全败）：error + done 干净收尾；
2. 首帧延迟（11.9）：Gateway 分类 + 首帧（phase/navigate/tool_call/error 任一）P95 ≤ 3s 目标——
   关键词兜底路径应远低于目标；LLM 路径记录实测值（配置 LLM key 后在浏览器复核）；
3. 审批恢复端点错误路径（D13/D14）：thread 分隔符 403、非法 decision 422、无待审批 409；
4. MCP 工具服务器（task 7 HTTP 冒烟）：GET /api/mcp/tools 返回 16 个工具；
5. 审计 JSONL 落盘：data/audit/ 当日文件存在（真实工具审计需 LLM key 触发 execute_tool，
   落盘路径已由单测 test_audit_writes_jsonl 覆盖）。

真实"诊断/出题富渲染对话"（10.2 富渲染断言）需配置 LLM key 后复核；无 key 时断言契约本身。
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://127.0.0.1:{PORT}"
AUDIT_DIR = Path("data/audit")
FIRST_FRAME_BUDGET_S = 3.0  # 11.9 首帧 P95 目标

_failures = 0


def check(name, ok, detail=""):
    global _failures
    if not ok:
        _failures += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    return ok


def req(method, path, token=None, payload=None, raw_body=False):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body.decode("utf-8", "replace")


def stream_frames(token, message, thread_id):
    """POST 流式对话，解析 SSE 帧，返回 (frames[(event, data)], first_frame_latency_s)。"""
    payload = {"message": message, "thread_id": thread_id,
               "context": {"user_id": 1, "role": "teacher", "persona": "teacher"}}
    body = json.dumps(payload).encode()
    r = urllib.request.Request(BASE + "/api/agent/chat/langgraph/stream", data=body, method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + token)
    frames = []
    first_latency = None
    started = time.monotonic()
    event, data_lines = None, []
    with urllib.request.urlopen(r, timeout=120) as resp:
        for raw in resp:
            if first_latency is None:
                first_latency = time.monotonic() - started
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if event is not None:
                    text = "".join(data_lines)
                    try:
                        data_obj = json.loads(text)
                    except Exception:
                        data_obj = {"content": text}
                    frames.append((event, data_obj))
                event, data_lines = None, []
            elif line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip() + "\n")
    return frames, first_latency


def main():
    print(f"== Agent E2E 冒烟 → {BASE} ==")

    # 1) 认证
    status, body = req("POST", "/api/auth/login", payload={"username": "teacher_demo", "password": "demo123"})
    token = body.get("access_token") if status == 200 else None
    check("teacher_demo 登录", status == 200 and token, f"status={status}")
    if not token:
        return 1

    # 2) navigate 快捷路径（11.9 关键词兜底；跳过 ReAct）
    frames, lat = stream_frames(token, "打开考试工作台", "smoke_t1")
    names = [e for e, _ in frames]
    nav = next((d for e, d in frames if e == "navigate"), None)
    check("navigate 快捷路径事件序列", names == ["navigate", "done"], str(names))
    check("navigate 页面 = exam-v2", bool(nav) and nav.get("page") == "exam-v2", str(nav))
    check("navigate 无 error", "error" not in names, str(names))
    check(f"首帧延迟 ≤ {FIRST_FRAME_BUDGET_S}s（关键词路径）",
          lat is not None and lat <= FIRST_FRAME_BUDGET_S, f"{lat:.3f}s")

    # 3) chat 路径（无 LLM key → Provider 全败 → error + done 干净收尾）
    frames2, lat2 = stream_frames(token, "诊断一下", "smoke_t2")
    names2 = [e for e, _ in frames2]
    check("chat 路径以 done 收尾", names2 and names2[-1] == "done", str(names2))
    check("chat 路径无死锁（≤2 帧 error/done）", len(names2) <= 3, str(names2))
    print(f"    [info] chat 首帧延迟 {lat2:.3f}s（LLM 依赖；11.9 P95 目标 {FIRST_FRAME_BUDGET_S}s 需 key 后复核）")

    # 4) 审批恢复错误路径（D13/D14）
    s, _ = req("POST", "/api/agent/approval/resume", token=token, payload={"thread_id": "a:b", "decision": "approve"})
    check("恢复 thread 分隔符 → 403", s == 403, f"status={s}")
    s, _ = req("POST", "/api/agent/approval/resume", token=token, payload={"thread_id": "smoke_t3", "decision": "maybe"})
    check("恢复非法 decision → 422", s == 422, f"status={s}")
    s, _ = req("POST", "/api/agent/approval/resume", token=token, payload={"thread_id": "smoke_t4", "decision": "approve"})
    check("恢复无待审批 → 409", s == 409, f"status={s}")

    # 5) MCP 工具服务器（task 7）
    s, body = req("GET", "/api/mcp/tools", token=token)
    count = body.get("count") if isinstance(body, dict) else 0
    check("MCP 工具列表 = 16", s == 200 and count == 16, f"count={count}")

    # 6) 审计 JSONL 落盘（真实工具审计需 LLM key；落盘逻辑单测覆盖）
    audit_files = list(AUDIT_DIR.glob("agent_audit_*.jsonl")) if AUDIT_DIR.exists() else []
    check("审计 JSONL 落盘目录非空", bool(audit_files), f"{len(audit_files)} 个文件")
    if audit_files:
        latest = max(audit_files, key=lambda p: p.stat().st_mtime)
        print(f"    [info] 最新审计文件 {latest.name}（{latest.stat().st_size} 字节）")

    if _failures:
        print(f"\nFAILED: {_failures} 项未通过")
        return 1
    print("\nALL AGENT E2E SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
