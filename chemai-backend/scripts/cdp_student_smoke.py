"""CDP 学生端冒烟：真实点击走通 练习→答题→提交 → 错题 → 复习 三页（task 7.3）。

依赖 seed_student_demo.py 已为 student_demo 生成数据；后端须在 http://localhost:8000 运行。

运行前自动清空 student_demo 今日每日练习的作答/复习副作用（reset_demo_state），可重复执行。

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
PORT = 9224
PROFILE = r"C:\Users\Administrator\AppData\Local\Temp\chemai_edge_cdp_student"

console_errors = []


def reset_demo_state():
    """清空 student_demo 今日「每日练习」的作答与复习副作用，保证 smoke 从待完成开始。

    smoke 会作答今日练习（写 StudentAnswer 并触发错题/复习任务），重跑前必须清空，
    否则待完成计数不再是 1。幂等：无今日练习则直接返回。
    """
    import datetime
    import sys as _sys
    from pathlib import Path

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db.models import Account, AccountRole, ExamRecord, ExamType, Question, ReviewTask, Student, StudentAnswer
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        acct = db.query(Account).filter(Account.username == "student_demo").first()
        if acct is None:
            return 0
        student = db.get(Student, acct.role_id)
        if student is None:
            return 0
        today = datetime.date.today()
        exams = [
            e for e in db.query(ExamRecord).filter(
                ExamRecord.student_id == student.id,
                ExamRecord.exam_type == ExamType.practice,
                ExamRecord.exam_date == today,
            ).all()
            if (e.question_stats or {}).get("mode") == "daily"
        ]
        if not exams:
            return 0
        exam_ids = [e.id for e in exams]
        qids = [q.id for q in db.query(Question.id).filter(Question.record_id.in_(exam_ids)).all()]
        if qids:
            db.query(ReviewTask).filter(
                ReviewTask.student_id == student.id,
                ReviewTask.question_id.in_(qids),
            ).delete(synchronize_session=False)
        db.query(StudentAnswer).filter(StudentAnswer.exam_id.in_(exam_ids)).delete(
            synchronize_session=False
        )
        db.commit()
        return len(exam_ids)
    finally:
        db.close()


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
    # Windows 控制台默认 GBK，无法编码 ✓ 等字符，输出统一切 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    reset_demo_state()

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

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

        # ---- 0) 登录 student_demo，落 token + user(含 role_id) ----
        # about:blank 的 origin 是 null，跨源 fetch 会被 CORS 拦截；先落到同源页面再登录
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

        # ---- 1) 练习：任务列表 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/practice.html"})
        ok, _ = wait_for(ws, "document.getElementById('taskList') && document.getElementById('taskList').children.length >= 1", timeout=20)
        check("practice task list rendered", ok)
        ok, tab = wait_for(ws, "document.getElementById('tabPending') && document.getElementById('tabPending').textContent")
        ok, n = wait_for(ws, "(function(){var m=(document.getElementById('tabPending').textContent||'').match(/待完成\((\\d+)\)/); return m ? Number(m[1]) : -1;})()")
        check("待完成 count>=1", ok and n >= 1, f"tab={tab} n={n}")
        ok, txt = wait_for(ws, "document.body.innerText.includes('每日练习')")
        check("practice card shows 每日练习", ok)
        ok, btn = wait_for(ws, "document.body.innerText.includes('开始练习')")
        check("practice card 开始练习 button", ok)

        # ---- 2) 进入答题：逐题选第一项 → 提交 ----
        ok, pid = wait_for(ws, "document.querySelector('.card-btn') ? Number((document.querySelector('.card-btn').getAttribute('onclick').match(/\\d+/) || ['0'])[0]) : 0", desc="pending card")
        evaluate(ws, "window.__start(" + str(pid) + ")")
        ok, qcount = wait_for(ws, "!document.getElementById('view-list').classList.contains('hidden') ? 0 : document.querySelectorAll('#questionArea .option').length", timeout=15)
        check("answer view shows options", ok, f"options={qcount}")

        answered_ok = True
        for i in range(int(qcount) if qcount and str(qcount).isdigit() else 3):
            ok, _ = wait_for(ws, "document.querySelectorAll('#questionArea .option').length >= 1")
            evaluate(ws, "document.querySelector('#questionArea .option').click()")
            evaluate(ws, "document.getElementById('btnNext').click()")
            wait(ws, 0.4)
            if i == 2:
                break
        ok, _ = wait_for(ws, "!document.getElementById('view-result').classList.contains('hidden') && document.getElementById('resultBox').innerText.includes('逐题判定')", timeout=20)
        check("submit → result view", ok)
        # 结果视图渲染存在瞬时中间态（KaTeX/auto-render 处理），用轮询取分数而非单次取值
        ok, score = wait_for(ws, "(document.getElementById('resultBox').innerText.match(/\\d+\\/\\d/) || [''])[0] || null", timeout=10, desc="result score")
        check("result score present", ok and score is not None, f"score={score}")

        # ---- 3) 错题本 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/wrong.html"})
        ok, _ = wait_for(ws, "document.getElementById('wrongList') && (document.getElementById('wrongList').children.length >= 1 || document.getElementById('wrongList').innerText.includes('暂无错题'))", timeout=20)
        check("wrong list rendered", ok)
        ok, total = wait_for(ws, "document.getElementById('statTotal') && document.getElementById('statTotal').textContent")
        check("wrong stat 总错题>=2", ok and int(total) >= 2, f"total={total}")
        ok, _ = wait_for(ws, "document.querySelectorAll('#wrongList .card').length >= 2")
        check("wrong cards >=2", ok)
        # 展开第一张卡 → 显示操作按钮
        evaluate(ws, "document.querySelector('#wrongList .card-header').click()")
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('#wrongList .card button')).some(b => b.textContent.includes('生成变式题'))")
        check("wrong card expand shows actions", ok)

        # ---- 4) 复习中心 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/review.html"})
        ok, _ = wait_for(ws, "document.getElementById('reviewList') && (document.getElementById('reviewList').children.length >= 1 || document.getElementById('reviewList').innerText.includes('暂无待复习'))", timeout=20)
        check("review list rendered", ok)
        ok, due = wait_for(ws, "document.getElementById('statDue') && document.getElementById('statDue').textContent")
        check("review stat 待复习>=2", ok and int(due) >= 2, f"due={due}")
        ok, _ = wait_for(ws, "document.querySelectorAll('#reviewList .q-card').length >= 2")
        check("review cards >=2", ok)

        # ---- 5) 控制台无错误 ----
        real_errors = [e for e in console_errors if "favicon" not in e and "favicon.ico" not in e]
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
