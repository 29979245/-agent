"""CDP 浏览器验证：headless Edge 登录后逐 Tab 断言渲染与控制台错误。

用法：python scripts/cdp_browser_check.py（需先启动 uvicorn:8000 并存在 smoke_t 教师）
退出码 0 = 全部通过；非 0 = 有失败项。
"""
import json
import subprocess
import sys
import time
import urllib.request

import websocket

BASE = "http://localhost:8000"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9223
PROFILE = r"C:\Users\Administrator\AppData\Local\Temp\chemai_edge_cdp"

console_errors = []


def cdp(ws, method, params=None):
    """同步发送 CDP 命令，返回 result。"""
    global _msg_id
    _msg_id += 1
    mid = _msg_id
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError(f"{method} -> {msg['error']}")
            return msg.get("result", {})
        if msg.get("method") in (
            "Runtime.consoleAPICalled",
            "Runtime.exceptionThrown",
            "Log.entryAdded",
        ):
            _collect(msg)


def _collect(msg):
    method = msg["method"]
    params = msg["params"]
    if method == "Runtime.exceptionThrown":
        d = params.get("exceptionDetails", {})
        exc = d.get("exception", {}).get("description", "")
        console_errors.append("exceptionThrown: " + exc)
    elif method == "Log.entryAdded":
        entry = params.get("entry", {})
        if entry.get("level") in ("error", "warning"):
            console_errors.append(f"log[{entry.get('level')}]: {entry.get('text')} url={entry.get('url')}")
    elif method == "Runtime.consoleAPICalled":
        if params.get("type") == "error":
            args = params.get("args", [])
            text = " ".join(a.get("value") or a.get("description") or "" for a in args)
            console_errors.append("console.error: " + text)


def evaluate(ws, expr):
    res = cdp(ws, "Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    if "exceptionDetails" in res:
        return None, res["exceptionDetails"]
    return res.get("result", {}).get("value"), None


def wait(ws, seconds):
    cdp(ws, "Runtime.evaluate", {"expression": "new Promise(r => setTimeout(r, %d))" % int(seconds * 1000), "awaitPromise": True})


def main():
    global _msg_id
    _msg_id = 0
    fails = []

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
         "--disable-extensions", "--remote-allow-origins=*",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # 等待 CDP 端口就绪
        ws_url = None
        for _ in range(40):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                    pages = json.loads(r.read())
                page = next((p for p in pages if p.get("type") == "page"), None)
                if page:
                    ws_url = page["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
        if not ws_url:
            print("FAIL: CDP 端口未就绪")
            return 1

        ws = websocket.create_connection(ws_url, timeout=30)
        cdp(ws, "Runtime.enable")
        cdp(ws, "Log.enable")
        cdp(ws, "Page.enable")

        # 1) 同源登录并写入 localStorage
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/login.html"})
        time.sleep(1.0)
        token_val, exc = evaluate(ws, """
            (async () => {
              const r = await fetch('/api/auth/login', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'smoke_t', password: 'Smoke!2026'}),
              });
              const d = await r.json();
              if (!d.access_token) return 'NO_TOKEN ' + JSON.stringify(d);
              localStorage.setItem('chemai_token', d.access_token);
              localStorage.setItem('chemai_user', JSON.stringify({
                user_id: d.user_id, role: d.role, name: d.name, school_id: d.school_id,
              }));
              return 'OK';
            })()
        """)
        check("login+token", (token_val == "OK") and exc is None, f"val={token_val}")

        # 2) 进入工作台
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/exam-v2.html"})
        time.sleep(2.5)  # 等待 mounted 四路加载

        title, _ = evaluate(ws, "document.title")
        check("workbench title", title and "工作台" in title, f"title={title}")

        user_name, _ = evaluate(ws, "document.body.innerText.includes('冒烟教师')")
        check("sidebar teacher name", user_name is True)

        tabs, _ = evaluate(ws, "Array.from(document.querySelectorAll('.nav-tab')).map(t=>t.textContent)")
        check("nav tabs", tabs and len(tabs) == 4, f"tabs={tabs}")

        # 3) Tab 2 题库管理
        evaluate(ws, "document.querySelectorAll('.nav-tab')[1].click()")
        time.sleep(1.2)
        folder_seen, _ = evaluate(ws, "document.body.innerText.includes('冒烟题库')")
        check("Tab2 folder list", folder_seen is True)
        bank_has_card, _ = evaluate(ws, "document.querySelectorAll('.bank-card').length > 0")
        check("Tab2 card grid", bank_has_card is True)

        # 4) Tab 3 历史真题库
        evaluate(ws, "document.querySelectorAll('.nav-tab')[2].click()")
        time.sleep(0.6)
        hist_ok, _ = evaluate(ws, "document.querySelectorAll('.tree-item.region').length > 0")
        check("Tab3 paper tree", hist_ok is True)

        # 5) Tab 4 考试列表
        evaluate(ws, "document.querySelectorAll('.nav-tab')[3].click()")
        time.sleep(1.2)
        exam_seen, _ = evaluate(ws, "document.body.innerText.includes('冒烟期中')")
        check("Tab4 exam list", exam_seen is True)
        chips, _ = evaluate(ws, "Array.from(document.querySelectorAll('.status-chip')).map(c=>c.className)")
        check("Tab4 status chips", chips is not None and len(chips) >= 1, f"chips={chips}")
        chip_label, _ = evaluate(ws, "document.querySelector('.status-chip') && document.querySelector('.status-chip').textContent")
        check("Tab4 archived chip", chip_label == "已归档", f"label={chip_label}")
        # 逐卡校验：chip 状态 ↔ 条件按钮矩阵一一对应（支持混合状态的开发库）
        matrix_problems, _ = evaluate(ws, """
          (() => {
            const exp = { draft: ['编辑','发布','删除'], published: ['开始阅卷','导出'],
              in_progress: ['开始阅卷','导出'], grading: ['完成统计','导出'],
              completed: ['归档','导出','看结果'], archived: ['导出'] };
            const problems = [];
            document.querySelectorAll('.exam-card').forEach(card => {
              const chip = card.querySelector('.status-chip');
              if (!chip) { problems.push('no-chip'); return; }
              const st = Array.from(chip.classList).find(c => exp[c]);
              const btns = Array.from(card.querySelectorAll('.btn-outline, .btn-danger-outline')).map(b => b.textContent);
              const want = exp[st] || null;
              if (!st || !want || JSON.stringify(btns) !== JSON.stringify(want))
                problems.push((st || '?') + ':' + JSON.stringify(btns) + '!=' + JSON.stringify(want));
            });
            return problems;
          })()
        """)
        check("Tab4 status-button matrix", matrix_problems is not None and matrix_problems == [], f"problems={matrix_problems}")

        # 6) KaTeX 渲染（Tab2 卡片里应有 KaTeX 结构）
        katex_seen, _ = evaluate(ws, "document.querySelectorAll('.katex').length > 0")
        check("KaTeX rendered", katex_seen is True)

        # 7) 控制台无错误（白名单常见无害项）
        real_errors = [e for e in console_errors
                       if "favicon" not in e and "favicon.ico" not in e]
        check("no console errors", not real_errors, f"errors={real_errors[:5]}")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\nRESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
