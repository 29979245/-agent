"""CDP 学生端新页面冒烟：我的页 profile.html 全流程 + AI 助教页 ai-tutor.html 降级（task 4.1/4.2）。

依赖 seed_student_demo.py 已为 student_demo 生成数据；后端须在 http://localhost:8000 运行（JWT_SECRET 需设置）。

覆盖：
- profile.html：报告渲染（姓名/班级/统计/绑定码）、绑定码复制 toast、周报弹窗、学习计划弹窗、
  改密码校验分支（两次新密码不一致），不实际改密以保 demo123 可用。
- ai-tutor.html：欢迎语、快捷芯片、4-tab 激活、发送消息 → 阶段标签「分析中」→ 降级卡「AI 助教即将上线」（后端 agent 端点 404）。
- 回归：4-tab 各页可导航、tabbar 高亮正确、全流程无 console 错误。

退出码 0 = 全部通过；非 0 = 有失败项。
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request

import websocket

BASE = "http://localhost:8000"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9225
PROFILE = r"C:\Users\Administrator\AppData\Local\Temp\chemai_edge_cdp_pages"

console_errors = []
_msg_id = 0


def cdp(ws, method, params=None):
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
            "Runtime.exceptionThrown", "Log.entryAdded", "Runtime.consoleAPICalled",
        ):
            _collect(msg)


def _collect(msg):
    method = msg["method"]
    params = msg["params"]
    if method == "Runtime.exceptionThrown":
        exc = params.get("exceptionDetails", {}).get("exception", {}).get("description", "")
        console_errors.append("exceptionThrown: " + exc)
    elif method == "Log.entryAdded":
        entry = params.get("entry", {})
        if entry.get("level") in ("error", "warning"):
            url = entry.get("url") or ""
            console_errors.append(f"log[{entry.get('level')}]: {entry.get('text')} url={url}")
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
    try:
        cdp(ws, "Runtime.evaluate", {"expression": "new Promise(r => setTimeout(r, %d))" % int(seconds * 1000), "awaitPromise": True})
    except RuntimeError as e:
        if "navigated or closed" in str(e):
            return
        raise


def wait_for(ws, expr, timeout=15.0, desc=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        last, exc = evaluate(ws, expr)
        if exc:
            return False, "EXC"
        if last:
            return True, last
        wait(ws, 0.3)
    return False, last


def main():
    global _msg_id
    _msg_id = 0
    fails = []
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

    shutil.rmtree(PROFILE, ignore_errors=True)  # 复用固定 profile 会有磁盘缓存，导致静态页被旧版命中
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
         "--disable-extensions", "--remote-allow-origins=*",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
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

        # ---- 0) 登录 student_demo ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/student-login.html"})
        ok, _ = wait_for(ws, "location.href.indexOf('/pages/student-login.html') >= 0 && document.readyState === 'complete'", timeout=20)
        token_ok, exc = evaluate(ws, """
            (async () => {
              const r = await fetch('/api/auth/login', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'student_demo', password: 'demo123'}),
              });
              const d = await r.json();
              if (!d.access_token) return 'NO_TOKEN ' + JSON.stringify(d);
              localStorage.setItem('chemai_token', d.access_token);
              localStorage.setItem('chemai_user', JSON.stringify({
                user_id: d.user_id, role: d.role, name: d.name, school_id: d.school_id, role_id: d.role_id,
              }));
              return 'OK';
            })()
        """)
        check("login student_demo", token_ok == "OK" and exc is None, f"val={token_ok}")

        # ---- 1) 我的页：报告渲染 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/profile.html"})
        ok, _ = wait_for(ws, "document.getElementById('name') && document.getElementById('name').textContent === '演示学生'", timeout=20)
        check("profile name 演示学生", ok)
        ok, cn = wait_for(ws, "document.getElementById('className') && document.getElementById('className').textContent")
        check("profile class 高一（3）班", ok and "高一（3）班" in cn, f"class={cn}")
        ok, ex = wait_for(ws, "document.getElementById('statExer') && Number(document.getElementById('statExer').textContent) >= 1")
        check("profile 完成练习>=1", ok, f"ex={ex}")
        ok, acc = wait_for(ws, "document.getElementById('statAcc') && document.getElementById('statAcc').textContent")
        check("profile 正确率 50%", ok and acc == "50%", f"acc={acc}")
        ok, bind = wait_for(ws, "document.getElementById('bindCode') && document.getElementById('bindCode').textContent")
        check("profile 绑定码 000000", ok and bind == "000000", f"bind={bind}")

        # 4-tab 结构：4 项 + 我的 激活
        ok, n = wait_for(ws, "document.getElementById('tabbar') && document.querySelectorAll('#tabbar .tabbar-item').length")
        check("tabbar 4 items", ok and n == 4, f"n={n}")
        ok, act = wait_for(ws, "document.querySelector('#tabbar .tabbar-item.active span') && document.querySelector('#tabbar .tabbar-item.active span').textContent")
        check("tabbar 我的 激活", ok and act == "我的", f"active={act}")

        # 绑定码点击 → 复制 toast
        evaluate(ws, "document.getElementById('bindCode').click()")
        ok, toast = wait_for(ws, "document.getElementById('std-toast') && document.getElementById('std-toast').textContent")
        check("绑定码点击 toast 已复制", ok and "已复制" in toast, f"toast={toast}")

        # 周报弹窗
        evaluate(ws, "document.getElementById('openWeekly').click()")
        ok, _ = wait_for(ws, "document.getElementById('sheetWeekly') && document.getElementById('sheetWeekly').classList.contains('open')")
        check("周报弹窗打开", ok)
        ok, wex = wait_for(ws, "document.getElementById('weekExer') && document.getElementById('weekExer').textContent")
        check("周报本周练习渲染", ok and int(wex) >= 0, f"weekExer={wex}")
        ok, _ = wait_for(ws, "document.getElementById('weekKp') && (document.getElementById('weekKp').innerHTML.length > 0)")
        check("周报知识点区渲染", ok)
        evaluate(ws, "document.querySelector('#sheetWeekly .sheet-close-btn').click()")

        # 学习计划弹窗（learning_plan 可能 null → 空态）
        evaluate(ws, "document.getElementById('openPlan').click()")
        ok, _ = wait_for(ws, "document.getElementById('sheetPlan') && document.getElementById('sheetPlan').classList.contains('open')")
        check("学习计划弹窗打开", ok)
        ok, pbody = wait_for(ws, "document.getElementById('planBody') && document.getElementById('planBody').innerHTML.length > 0")
        check("学习计划内容/空态渲染", ok, f"len={pbody}")
        evaluate(ws, "document.querySelector('#sheetPlan .sheet-close-btn').click()")

        # 改密码校验分支：两次新密码不一致 → 错误提示（不实际改密）
        evaluate(ws, "document.getElementById('openSettings').click()")
        ok, _ = wait_for(ws, "document.getElementById('sheetSettings') && document.getElementById('sheetSettings').classList.contains('open')")
        check("个人设置弹窗打开", ok)
        evaluate(ws, "document.getElementById('oldPwd').value='demo123'; document.getElementById('newPwd').value='abc12345'; document.getElementById('confirmPwd').value='different'; document.getElementById('savePwdBtn').click()")
        ok, err = wait_for(ws, "document.getElementById('pwdError') && document.getElementById('pwdError').textContent")
        check("改密码不一致校验提示", ok and "不一致" in err, f"err={err}")
        evaluate(ws, "document.querySelector('#sheetSettings .sheet-close').click()")

        # ---- 2) AI 助教页：欢迎语 + 芯片 + 4-tab ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/ai-tutor.html"})
        ok, _ = wait_for(ws, "document.querySelector('.bubble.ai') && document.querySelector('.bubble.ai').textContent.includes('ChemAI 助教')", timeout=20)
        check("ai-tutor 欢迎语", ok)
        ok, n = wait_for(ws, "document.getElementById('chipRow') && document.querySelectorAll('#chipRow .chip-btn').length")
        check("ai-tutor 快捷芯片 5 个", ok and n == 5, f"n={n}")
        ok, act = wait_for(ws, "document.querySelector('#tabbar .tabbar-item.active span') && document.querySelector('#tabbar .tabbar-item.active span').textContent")
        check("tabbar AI助教 激活", ok and act == "AI助教", f"active={act}")

        # 发送消息 → 阶段标签 → 降级卡（agent 端点 404）
        # 阶段标签在 click() 同步派发期间创建；404 降级会在后续微任务里立即 clearStage，
        # 独立 wait_for 读取必已错过，须在 click 同一表达式内同步读取
        stage, _ = evaluate(ws, "document.getElementById('chatInput').value='帮我讲解氧化还原'; document.getElementById('sendBtn').click(); (document.getElementById('stageTag') ? document.getElementById('stageTag').textContent : '')")
        check("ai-tutor 阶段标签分析中", stage == "分析中", f"stage={stage}")
        ok, _ = wait_for(ws, "document.querySelectorAll('.bubble.user').length >= 1", timeout=10)
        check("ai-tutor 用户气泡追加", ok)
        ok, _ = wait_for(ws, "document.querySelector('.degrade-card') && document.querySelector('.degrade-card').textContent.includes('AI 助教即将上线')", timeout=15)
        check("ai-tutor 降级卡出现", ok)
        ok, retry = wait_for(ws, "document.querySelector('.degrade-card .retry') ? document.querySelector('.degrade-card .retry').textContent : ''")
        check("ai-tutor 重试入口", ok and "重试" in retry, f"retry={retry}")
        ok, enabled = wait_for(ws, "!document.getElementById('sendBtn').disabled")
        check("ai-tutor 发送按钮恢复", ok)

        # 新对话 → 恢复欢迎语
        evaluate(ws, "document.getElementById('newChatBtn').click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('.bubble.ai').length === 1 && document.querySelector('.bubble.ai').textContent.includes('ChemAI 助教')", timeout=10)
        check("ai-tutor 新对话重置", ok)

        # 抽屉开关 + 内容（姓名来自会话、班级来自报告）
        evaluate(ws, "document.getElementById('menuBtn').click()")
        ok, _ = wait_for(ws, "document.getElementById('drawer').classList.contains('open')")
        check("ai-tutor 抽屉打开", ok)
        ok, dn = wait_for(ws, "document.getElementById('drawerName') && document.getElementById('drawerName').textContent")
        check("ai-tutor 抽屉姓名", ok and "演示学生" in dn, f"name={dn}")
        ok, dc = wait_for(ws, "document.getElementById('drawerClass') && document.getElementById('drawerClass').textContent")
        check("ai-tutor 抽屉班级", ok and "高一（3）班" in dc, f"class={dc}")
        evaluate(ws, "document.getElementById('drawerMask').click()")
        ok, _ = wait_for(ws, "!document.getElementById('drawer').classList.contains('open')")
        check("ai-tutor 抽屉关闭", ok)

        # ---- 3) 4-tab 回归 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/practice.html"})
        ok, act = wait_for(ws, "document.querySelector('#tabbar .tabbar-item.active span') && document.querySelector('#tabbar .tabbar-item.active span').textContent", timeout=20)
        check("回归 practice tab 激活", ok and act == "练习", f"active={act}")
        ok, _ = wait_for(ws, "document.body.innerText.includes('每日练习') || document.body.innerText.includes('暂无')")
        check("回归 practice 内容渲染", ok)

        cdp(ws, "Page.navigate", {"url": BASE + "/pages/wrong.html"})
        ok, act = wait_for(ws, "document.querySelector('#tabbar .tabbar-item.active span') && document.querySelector('#tabbar .tabbar-item.active span').textContent", timeout=20)
        check("回归 wrong tab 激活", ok and act == "错题", f"active={act}")
        ok, _ = wait_for(ws, "document.getElementById('wrongList') && (document.getElementById('wrongList').children.length >= 1 || document.getElementById('wrongList').innerText.includes('暂无错题'))")
        check("回归 wrong 内容渲染", ok)

        # 手风琴单开：点第 2 张卡时第 1 张先收起
        ok, _ = wait_for(ws, "document.querySelectorAll('#wrongList .card').length >= 2")
        evaluate(ws, "document.querySelectorAll('#wrongList .card-header')[0].click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('#wrongList .card')[0].classList.contains('expanded')")
        evaluate(ws, "document.querySelectorAll('#wrongList .card-header')[1].click()")
        ok, single = wait_for(ws, "(function(){var c=document.querySelectorAll('#wrongList .card'); return c[0].classList.contains('expanded') ? 'both' : (c[1].classList.contains('expanded') ? 'single' : 'none');})()")
        check("手风琴单开(第2张开、第1张收)", ok and single == "single", f"state={single}")
        # 再点已展开卡 → 自身收起
        evaluate(ws, "document.querySelectorAll('#wrongList .card-header')[1].click()")
        ok, closed = wait_for(ws, "!document.querySelectorAll('#wrongList .card')[1].classList.contains('expanded')")
        check("手风琴点已展开卡自身收起", ok)

        cdp(ws, "Page.navigate", {"url": BASE + "/pages/profile.html"})
        ok, _ = wait_for(ws, "document.getElementById('name') && document.getElementById('name').textContent === '演示学生'", timeout=20)
        check("回归 profile 重新渲染", ok)

        # ---- 4) 控制台无错误 ----
        # agent 端点 404 是预期的降级触发点，其网络日志不计为错误
        real_errors = [
            e for e in console_errors
            if "favicon" not in e and "favicon.ico" not in e
            and "/api/agent/chat/langgraph/stream" not in e
        ]
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
