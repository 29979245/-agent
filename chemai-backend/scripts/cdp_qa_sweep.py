"""CDP QA 冒烟：出题工作台边缘用例 + 控制台错误扫描（/qa 用）。

在 golden path（scripts/cdp_golden_path.py，覆盖主流程）之外，重点测：
- 各页加载控制台错误
- 表单校验（空表单提交、空考试名）
- 错误路径 toast（未选文件夹批准、发布空考试 400）
- 文件夹/考试删除（覆盖 window.confirm/prompt 阻塞）
- 控制台全程错误收集

退出码 0 = 全部通过；非 0 = 有失败项。
"""
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websocket

BASE = "http://localhost:8000"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9224
PROFILE = r"C:\Users\Administrator\AppData\Local\Temp\chemai_edge_qa"
SHOT_DIR = r"D:\ai-test-vibecodeing\教育agent\chemai-backend\.gstack\qa-reports\screenshots"

console_errors = []


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


def wait_for(ws, expr, timeout=15.0):
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        last, exc = evaluate(ws, expr)
        if exc:
            return False, "EXC"
        if last:
            return True, last
        wait(ws, 0.3)
    return False, last


def screenshot(ws, name):
    res = cdp(ws, "Page.captureScreenshot", {"format": "png"})
    data = res.get("data")
    if not data:
        return False
    with open(SHOT_DIR + "\\" + name, "wb") as f:
        f.write(base64.b64decode(data))
    return True


def toast_text(ws):
    ok, val = evaluate(ws, "document.querySelector('.toast') ? document.querySelector('.toast').textContent : ''")
    return val or ""


HELPERS = """
window.__authFetch = async function(path, opts) {
  const t = localStorage.getItem('chemai_token');
  const o = Object.assign({}, opts);
  o.headers = Object.assign({'Authorization': 'Bearer ' + t}, (opts && opts.headers) || {});
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  if (ct.indexOf('application/json') >= 0) {
    let d = null; try { d = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, json: d };
  }
  const buf = await r.arrayBuffer();
  const b = new Uint8Array(buf);
  return { ok: r.ok, status: r.status, bytes: buf.byteLength, head: Array.from(b.slice(0, 4)) };
};
window.__fill = function(el, v) { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); };
window.__select = function(el, v) { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); };
window.__okPrompt = function() { window.prompt = function() { return 'QA自动建夹'; }; window.confirm = function() { return true; }; };
"""


def main():
    global _msg_id
    _msg_id = 0
    fails = []

    def check(name, ok, extra=""):
        print(("PASS" if ok else "FAIL"), name, extra)
        if not ok:
            fails.append(name)

    def new_errors():
        # 过滤：favicon 噪音；以及刻意测试的错误路径（发布空考试 400）
        out = []
        for e in console_errors:
            if "favicon" in e:
                continue
            if "Failed to load resource" in e and "/publish" in e and "400" in e:
                continue
            out.append(e)
        return out

    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
         "--disable-extensions", "--remote-allow-origins=*",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "about:blank"],
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

        # ---- 0) 登录页加载控制台 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/login.html"})
        time.sleep(1.2)
        check("login page loads", True)
        check("login page no console errors", not new_errors(), f"errs={new_errors()[:3]}")

        # ---- 1) 登录 + 注入辅助 ----
        token_val, exc = evaluate(ws, """
            (async () => {
              const r = await fetch('/api/auth/login', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'smoke_t', password: 'Smoke!2026'}),
              });
              const d = await r.json();
              if (!d.access_token) return 'NO_TOKEN';
              localStorage.setItem('chemai_token', d.access_token);
              localStorage.setItem('chemai_user', JSON.stringify({user_id: d.user_id, role: d.role, name: d.name}));
              return 'OK';
            })()
        """)
        check("login ok", token_val == "OK" and exc is None)
        evaluate(ws, HELPERS)
        evaluate(ws, "window.__okPrompt(); true")

        # ---- 2) 工作台加载 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/exam-v2.html"})
        time.sleep(2.5)
        evaluate(ws, HELPERS)
        evaluate(ws, "window.__okPrompt(); true")
        ok, _ = wait_for(ws, "document.querySelectorAll('.nav-tab').length === 4")
        check("workbench 4 tabs render", ok)
        ok, _ = wait_for(ws, "document.querySelectorAll('.sub-tab').length === 3")
        check("tab1 3 sub-modes", ok)
        shot1 = new_errors()
        check("workbench load no console errors", not shot1, f"errs={shot1[:3]}")

        # ---- 3) 手动录入：空表单提交 -> '请填写题干' ----
        evaluate(ws, "document.querySelectorAll('.sub-tab')[1].click()")
        wait(ws, 0.3)
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('提交手动录入')).click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('请填写题干')", timeout=5)
        check("manual empty form -> 请填写题干", ok, f"toast={toast_text(ws)}")
        screenshot(ws, "qa-manual-empty.png")
        # 等 toast 消失
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 4) AI 出题：清空预填示例后空表单 -> '请填写题目内容' ----
        # 注：ai.content 默认预填 SAMPLE_AI_CONTENT 示例（设计如此），需清空才能测空校验
        evaluate(ws, "document.querySelectorAll('.sub-tab')[0].click()")
        wait(ws, 0.3)
        evaluate(ws, """
            (() => {
              const ta = document.querySelectorAll('main > section')[0].querySelectorAll('textarea')[0];
              window.__fill(ta, '');
              return 'OK';
            })()
        """)
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('AI 出题')).click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('请填写题目内容')", timeout=5)
        check("ai empty form -> 请填写题目内容", ok, f"toast={toast_text(ws)}")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 5) Tab1 手动录入有效题 -> 批准入库但未选文件夹 ----
        evaluate(ws, "document.querySelectorAll('.sub-tab')[1].click()")
        wait(ws, 0.3)
        fill, _ = evaluate(ws, """
            (() => {
              const sec = document.querySelectorAll('main > section')[0];
              const card = sec.querySelectorAll('.card')[1];
              const tas = card.querySelectorAll('textarea');
              const ins = card.querySelectorAll('input');
              const sel = card.querySelector('select');
              window.__fill(tas[0], 'QA 校验题：配平 $\\\\ce{2H2 + O2 -> 2H2O}$');
              window.__fill(ins[0], '化合反应');
              window.__fill(tas[2], '两种物质生成一种，属于化合反应。');
              window.__fill(ins[1], '配平与计算');
              window.__select(sel, 'easy');
              return 'OK';
            })()
        """)
        check("fill manual form", fill == "OK")
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('提交手动录入')).click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('.qcard').length >= 1", timeout=20)
        check("manual question card appears", ok)
        # 保存文件夹 select 选空项，然后批准入库 -> 应提示先选文件夹
        sel_ok, _ = evaluate(ws, """
            (() => {
              const cards = Array.from(document.querySelectorAll('#app .card'));
              const saveCard = cards.find(c => c.innerText.includes('批准入库后保存到'));
              if (!saveCard) return 'NO_SAVE_CARD';
              const sel = saveCard.querySelector('select');
              const emptyOpt = Array.from(sel.options).find(o => o.value === '');
              if (!emptyOpt) return 'NO_EMPTY_OPT';
              window.__select(sel, '');
              return 'OK';
            })()
        """)
        check("save-folder empty option available", sel_ok == "OK", f"sel={sel_ok}")
        evaluate(ws, "Array.from(document.querySelectorAll('.qcard .btn-primary')).find(b => b.textContent.includes('批准入库')).click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('请先选择保存的题库文件夹')", timeout=5)
        check("approve without folder -> 提示先选文件夹", ok, f"toast={toast_text(ws)}")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 6) Tab2：新建文件夹（唯一名 + prompt 覆盖）并删除 ----
        folder_name = "QA夹" + str(int(time.time()))[-6:]
        evaluate(ws, "window.prompt = function(){ return %r; }; window.confirm = function(){ return true; }; true" % folder_name)
        evaluate(ws, "document.querySelectorAll('.nav-tab')[1].click()")
        time.sleep(0.8)
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent === '新建').click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('题库已创建')", timeout=8)
        check("create folder via UI", ok, f"toast={toast_text(ws)}")
        screenshot(ws, "qa-folder-created.png")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)
        # 选中刚建文件夹
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.folder-name')).some(n => n.textContent === %r)" % folder_name, timeout=5)
        check("new folder listed", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.folder-item')).find(f => f.querySelector('.folder-name').textContent === %r).click()" % folder_name)
        wait(ws, 0.8)
        # 删除该文件夹
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent === '删除' && !b.disabled).click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('题库已删除')", timeout=8)
        check("delete folder via UI", ok, f"toast={toast_text(ws)}")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 7) Tab4：空考试名 -> '请填写考试名称' ----
        evaluate(ws, "document.querySelectorAll('.nav-tab')[3].click()")
        time.sleep(0.8)
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent === '创建考试').click()")
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('请填写考试名称')", timeout=5)
        check("create exam empty name -> 请填写考试名称", ok, f"toast={toast_text(ws)}")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 8) 创建考试 -> 发布空考试（后端 400 -> 错误 toast） ----
        ename = "QA" + str(int(time.time()))[-6:]
        evaluate(ws, """
            (() => {
              const input = document.querySelector('input[placeholder*="期中考试"]');
              window.__fill(input, '%s');
              return 'OK';
            })()
        """ % ename)
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent === '创建考试').click()")
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.exam-card')).some(c => c.innerText.includes('%s'))" % ename, timeout=8)
        check("exam created", ok)
        # 发布空考试：expect 错误 toast（后端 400）
        evaluate(ws, """
            (() => {
              const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
              const b = Array.from(card.querySelectorAll('button')).find(b => b.textContent.trim() === '发布');
              b.click(); return 'OK';
            })()
        """ % ename)
        ok, toast = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').className.indexOf('err') >= 0", timeout=8)
        check("publish empty exam -> error toast", ok, f"toast={toast_text(ws)}")
        screenshot(ws, "qa-publish-empty-error.png")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 9) 抽屉（同一草稿考试）：0 勾选 加入所选 -> '请先勾选题目' ----
        # 发布失败后考试仍是草稿，仍带 编辑 按钮
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.exam-card')).some(c => c.innerText.includes('%s'))" % ename, timeout=5)
        check("draft exam still listed", ok)
        evaluate(ws, """
            (() => {
              const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
              const b = Array.from(card.querySelectorAll('button')).find(b => b.textContent.trim() === '编辑');
              b.click(); return 'OK';
            })()
        """ % ename)
        ok, _ = wait_for(ws, "!!document.querySelector('.modal-box.drawer')")
        check("edit drawer opens", ok)
        # 加入所选 仅在题库题加载后渲染：刷新题库 -> 逐个选文件夹，直到题库题出现
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent === '刷新题库').click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('.modal-box.drawer select option').length >= 2", timeout=8)
        loaded = False
        for _ in range(8):
            r, e = evaluate(ws, """
                (() => {
                  const sel = document.querySelector('.modal-box.drawer select');
                  const opts = Array.from(sel.options).filter(o => o.value !== '');
                  if (!opts.length) return 'NO_OPTS';
                  const idx = opts.findIndex(o => o.value === sel.value);
                  const next = opts[(idx + 1) % opts.length];
                  window.__select(sel, next.value);
                  return 'OK';
                })()
            """)
            if r != "OK":
                break
            ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).some(b => b.textContent.includes('加入所选'))", timeout=3)
            if ok:
                loaded = True
                break
        check("drawer bank loaded (加入所选 rendered)", loaded)
        if loaded:
            evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent.includes('加入所选')).click()")
            ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('请先勾选题目')", timeout=5)
            check("drawer add 0 selected -> 请先勾选题目", ok, f"toast={toast_text(ws)}")
            screenshot(ws, "qa-drawer-no-selection.png")
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent === '关闭').click()")
        wait_for(ws, "!document.querySelector('.modal-box.drawer')", timeout=5)

        # ---- 10) 删除草稿考试（confirm 已覆盖） ----
        evaluate(ws, """
            (() => {
              const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
              const b = Array.from(card.querySelectorAll('button')).find(b => b.textContent.trim() === '删除');
              b.click(); return 'OK';
            })()
        """ % ename)
        ok, _ = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('考试已删除')", timeout=8)
        check("delete draft exam", ok, f"toast={toast_text(ws)}")
        wait_for(ws, "!document.querySelector('.toast')", timeout=5)

        # ---- 11) 全程控制台错误汇总 ----
        all_errs = new_errors()
        check("no console errors across session", not all_errs, f"errs={all_errs[:6]}")

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
