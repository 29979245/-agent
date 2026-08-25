"""CDP 黄金路径浏览器验证：真实点击走通教师端核心用户流程（task 6.1）。

流程：登录 → 预置题库文件夹 → Tab1 手动录入出题 → 批准入库（方案1，保存到文件夹）
      → Tab2 校验文件夹/题目 → Tab4 建考试 → 抽屉从题库加入 → 发布 → 开始阅卷
      → 完成统计 → 看结果 → 归档 → 导出（校验下载文件名 + API 字节）。

实现要点：
- Vue 3 挂载后 v-model 指令属性被移除，故用静态属性/结构位置定位表单元素，再派发
  input/change 事件驱动 v-model（等价于真实输入）。
- 页面内直接 fetch 需带 Authorization 头（存 localStorage），走 __authFetch 封装。

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

MARKER = "黄金路径"
FOLDER_NAME = "黄金题库"

console_errors = []
net_events = []


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
            "Network.requestWillBeSent",
            "Network.responseReceived",
        ):
            _collect(msg)


def _collect(msg):
    method = msg["method"]
    params = msg["params"]
    if method == "Network.requestWillBeSent":
        req = params.get("request", {})
        url = req.get("url", "")
        if "/api/" in url:
            net_events.append("REQ  " + req.get("method", "?") + " " + url.replace(BASE, ""))
        return
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


def wait_for(ws, expr, timeout=15.0, desc=""):
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


# 页面内一次注入的辅助函数：认证 fetch、input/select 事件驱动
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
"""


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
        cdp(ws, "Network.enable")

        # ---- 0) 登录 ----
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

        # 每次导航会销毁 JS 全局上下文，需在导航后重新注入辅助函数
        evaluate(ws, HELPERS)

        # ---- 0.5) 预置题库文件夹（带认证；避免 window.prompt 阻塞 headless） ----
        folder_id, exc = evaluate(ws, """
            (async () => {
              const d = await window.__authFetch('/api/exam-bank/exam-sets', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: '%s'}),
              });
              return d.ok && d.json && d.json.id ? d.json.id : 'ERR ' + JSON.stringify(d);
            })()
        """ % FOLDER_NAME)
        check("preset folder created", isinstance(folder_id, int) and exc is None, f"id={folder_id}")

        # ---- 1) 进入工作台 ----
        cdp(ws, "Page.navigate", {"url": BASE + "/pages/exam-v2.html"})
        time.sleep(2.5)
        evaluate(ws, HELPERS)  # 重新注入（导航销毁了上下文）
        title, _ = evaluate(ws, "document.title")
        check("workbench title", title and "工作台" in title, f"title={title}")

        # ---- 2) Tab1 手动录入 ----
        ok, _ = wait_for(ws, "document.querySelectorAll('.sub-tab').length === 3")
        check("Tab1 3 sub-modes", ok)
        evaluate(ws, "document.querySelectorAll('.sub-tab')[1].click()")  # 手动录入
        wait(ws, 0.3)

        fill, _ = evaluate(ws, """
            (() => {
              const sec = document.querySelectorAll('main > section')[0];
              const card = sec.querySelectorAll('.card')[1];
              if (!card) return 'NO_MANUAL_CARD';
              const tas = card.querySelectorAll('textarea');
              const ins = card.querySelectorAll('input');
              const sel = card.querySelector('select');
              if (tas.length < 3 || ins.length < 2 || !sel) return 'BAD_STRUCT ' + tas.length + '/' + ins.length;
              window.__fill(tas[0], '%s：配平 $\\\\ce{2H2 + O2 -> 2H2O}$，并判断反应类型。');
              window.__fill(tas[1], '');
              window.__fill(ins[0], '化合反应');
              window.__fill(tas[2], '氢气和氧气点燃生成水，两种物质生成一种物质，属于化合反应。');
              window.__fill(ins[1], '配平与计算,氧化还原');
              window.__select(sel, 'medium');
              return 'OK';
            })()
        """ % MARKER)
        check("fill manual form", fill == "OK", f"fill={fill}")

        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('提交手动录入')).click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('.qcard').length >= 1", timeout=20)
        check("question card appears", ok)
        ok, status = wait_for(ws, "document.querySelector('.qcard .audit-badge') && document.querySelector('.qcard .audit-badge').textContent")
        check("audit status shown", ok, f"status={status}")
        marker_ok, _ = evaluate(ws, "document.body.innerText.includes('%s')" % MARKER)
        check("card shows marker content", marker_ok is True)

        # ---- 3) 批准入库（方案1：先选保存文件夹） ----
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('#app .card')).some(c => c.innerText.includes('批准入库后保存到'))")
        check("save folder card rendered", ok)
        sel_val, _ = evaluate(ws, """
            (() => {
              const cards = Array.from(document.querySelectorAll('#app .card'));
              const saveCard = cards.find(c => c.innerText.includes('批准入库后保存到'));
              const sel = saveCard.querySelector('select');
              const opt = Array.from(sel.options).find(o => o.value !== '');
              if (!opt) return 'NO_OPTS';
              window.__select(sel, opt.value);
              return opt.value;
            })()
        """)
        check("select save folder", sel_val and sel_val != "NO_OPTS", f"set_id={sel_val}")

        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.qcard .btn-primary')).some(b => b.textContent.includes('批准入库'))")
        check("approve button present", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.qcard .btn-primary')).find(b => b.textContent.includes('批准入库')).click()")
        ok, _ = wait_for(ws, "!!document.querySelector('.qcard .in-bank')", timeout=20)
        check("approve saved to folder", ok)
        ok, toast = wait_for(ws, "document.querySelector('.toast') && JSON.stringify({cls: document.querySelector('.toast').className, t: document.querySelector('.toast').textContent})", timeout=5)
        tj = json.loads(toast) if ok and toast and toast.startswith("{") else None
        check("approve toast", bool(tj) and "ok" in tj.get("cls", "") and "入库" in tj.get("t", ""), f"toast={toast}")

        # ---- 4) Tab2 校验文件夹与题目 ----
        evaluate(ws, "document.querySelectorAll('.nav-tab')[1].click()")
        time.sleep(1.0)
        ok, _ = wait_for(ws, "Array.from(document.querySelectorAll('.folder-name')).some(n => n.textContent === '%s')" % FOLDER_NAME)
        check("Tab2 folder listed", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.folder-item')).find(f => f.querySelector('.folder-name').textContent === '%s').click()" % FOLDER_NAME)
        ok, _ = wait_for(ws, "document.querySelectorAll('.bank-card').length >= 1")
        check("Tab2 folder has question card", ok)
        marker_ok, _ = evaluate(ws, "document.querySelectorAll('.bank-card').length > 0 && document.body.innerText.includes('%s')" % MARKER)
        check("Tab2 card marker content", marker_ok is True)

        # ---- 5) Tab4 创建考试 ----
        evaluate(ws, "document.querySelectorAll('.nav-tab')[3].click()")
        time.sleep(0.8)
        exam_name = MARKER + str(int(time.time()))[-6:]
        fill, _ = evaluate(ws, """
            (() => {
              const input = document.querySelector('input[placeholder*="期中考试"]');
              if (!input) return 'NO_INPUT';
              window.__fill(input, '%s');
              return 'OK';
            })()
        """ % exam_name)
        check("fill exam name", fill == "OK", f"fill={fill}")
        evaluate(ws, "Array.from(document.querySelectorAll('button')).find(b => b.textContent === '创建考试').click()")

        def exam_state():
            val, _ = evaluate(ws, """
                (() => {
                  const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
                  if (!card) return null;
                  const chip = card.querySelector('.status-chip');
                  const btns = Array.from(card.querySelectorAll('button')).map(b => b.textContent.trim());
                  return JSON.stringify({chip: chip && chip.textContent.trim(), btns: btns});
                })()
            """ % exam_name)
            if not val:
                return None
            try:
                return json.loads(val)
            except Exception:
                return None

        st = None
        for _ in range(40):
            st = exam_state()
            if st and st["chip"] == "草稿" and st["btns"] == ["编辑", "发布", "删除"]:
                break
            wait(ws, 0.3)
        check("draft status+buttons", st and st["chip"] == "草稿" and st["btns"] == ["编辑", "发布", "删除"], f"state={st}")

        def click_exam_btn(label):
            r, exc = evaluate(ws, """
                (() => {
                  const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
                  if (!card) return 'NO_CARD';
                  const b = Array.from(card.querySelectorAll('button')).find(b => b.textContent.trim() === '%s');
                  if (!b) return 'NO_BTN ' + Array.from(card.querySelectorAll('button')).map(x => x.textContent.trim()).join('/');
                  b.click(); return 'OK';
                })()
            """ % (exam_name, label))
            if r != "OK":
                print("   [click_exam_btn]", label, "->", r, exc)
            return r

        def assert_state(chip_want, btn_want, label, timeout=30):
            # 轮询直到 chip 达到目标态（返回 false 直到匹配，避免读到过渡中间态）
            ok, raw = wait_for(ws, """
                (() => {
                  const card = Array.from(document.querySelectorAll('.exam-card')).find(c => c.innerText.includes('%s'));
                  if (!card) return false;
                  const chip = card.querySelector('.status-chip');
                  if (!chip || chip.textContent.trim() !== '%s') return false;
                  const btns = Array.from(card.querySelectorAll('button')).map(b => b.textContent.trim());
                  return JSON.stringify(btns);
                })()
            """ % (exam_name, chip_want), timeout=timeout)
            ok2 = ok and json.loads(raw) == btn_want if raw else False
            check(label, ok2, f"btns={raw}")

        # ---- 6) 编辑抽屉：从题库加入 ----
        click_exam_btn("编辑")
        ok, _ = wait_for(ws, "!!document.querySelector('.modal-box.drawer')")
        check("edit drawer opens", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent === '刷新题库').click()")
        ok, _ = wait_for(ws, "document.querySelectorAll('.modal-box.drawer select option').length >= 2")
        check("drawer bank sets loaded", ok)
        evaluate(ws, """
            (() => {
              const sel = document.querySelector('.modal-box.drawer select');
              const opt = Array.from(sel.options).find(o => o.textContent.includes('%s'));
              window.__select(sel, opt.value);
            })()
        """ % FOLDER_NAME)
        ok, rows = wait_for(ws, "document.querySelectorAll('.modal-box.drawer .edit-qrow').length >= 1")
        check("drawer bank questions loaded", ok, f"rows={rows}")
        evaluate(ws, "document.querySelector('.modal-box.drawer .edit-qrow input[type=checkbox]').click()")
        # 诊断：勾选后 加入所选 按钮是否带数量（证明 editBank.selected 已注册）
        ok, selcnt = wait_for(ws, """
            (() => {
              const b = Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent.includes('加入所选'));
              if (!b) return '';
              const t = b.textContent;
              return t.indexOf('（') >= 0 ? t : '';
            })()
        """, timeout=5, desc="selected count on add button")
        print("   [diag] add-btn after checkbox:", repr(selcnt))
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent.includes('加入所选')).click()")
        time.sleep(1.0)
        ok, toast2 = wait_for(ws, "document.querySelector('.toast') && document.querySelector('.toast').textContent", timeout=5)
        print("   [diag] toast after 加入所选:", repr(toast2), "cls:", end=" ")
        ok2, cls2 = evaluate(ws, "document.querySelector('.toast') ? document.querySelector('.toast').className : ''")
        print(repr(cls2))
        # 当前题目列表数量从 0 → 1（比 body 文本更精确，避免命中抽屉里的题库列表）
        ok, cnt = wait_for(ws, """
            (() => {
              const t = Array.from(document.querySelectorAll('.modal-box.drawer .report-title'))
                .find(t => t.textContent.includes('当前题目'));
              return t ? t.textContent : '';
            })()
        """, desc="question added to exam")
        check("question added to exam", ok and "（1）" in cnt, f"title={cnt}")
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-box.drawer button')).find(b => b.textContent === '关闭').click()")
        ok, _ = wait_for(ws, "!document.querySelector('.modal-box.drawer')")
        check("edit drawer closes", ok)

        # ---- 7) 发布 ----
        click_exam_btn("发布")
        assert_state("已发布", ["开始阅卷", "导出"], "published status+buttons")

        # ---- 8) 开始阅卷 ----
        click_exam_btn("开始阅卷")
        assert_state("阅卷中", ["完成统计", "导出"], "grading status+buttons")

        # ---- 9) 完成统计 ----
        click_exam_btn("完成统计")
        assert_state("已完成", ["归档", "导出", "看结果"], "completed status+buttons")

        # ---- 10) 看结果 ----
        click_exam_btn("看结果")
        ok, _ = wait_for(ws, "document.body.innerText.includes('参考人数')")
        check("results modal", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-overlay button')).find(b => b.textContent === '关闭').click()")
        wait(ws, 0.3)

        # ---- 11) 归档 ----
        click_exam_btn("归档")
        assert_state("已归档", ["导出"], "archived status+buttons")

        # ---- 12) 导出：拦截下载 + 校验 API 字节 ----
        eid, eexc = evaluate(ws, """
            (async () => {
              try {
                const d = await window.__authFetch('/api/exam?page_size=100');
                if (!d.ok) return 'FETCH_ERR ' + d.status;
                const items = (d.json && d.json.items) || [];
                const it = items.find(x => x.name === '%s');
                if (!it) return 'NOT_FOUND (' + items.length + ' exams)';
                return it.exam_id;
              } catch (e) { return 'EXC ' + e.message; }
            })()
        """ % exam_name)
        if eexc:
            print("   [diag] exam_id evaluate EXC:", eexc.get("text") or eexc)
        check("resolved exam_id", isinstance(eid, int) and eexc is None, f"exam_id={eid}")

        evaluate(ws, """
            (() => {
              window.__dl = null;
              const orig = HTMLAnchorElement.prototype.click;
              window.__orig_click = orig;
              HTMLAnchorElement.prototype.click = function () {
                window.__dl = { download: this.download };
                return orig.apply(this, arguments);
              };
            })()
        """)
        click_exam_btn("导出")
        ok, _ = wait_for(ws, "!!document.querySelector('.modal-overlay')")
        check("export modal opens", ok)
        evaluate(ws, "Array.from(document.querySelectorAll('.modal-overlay button')).find(b => b.textContent === '下载').click()")
        ok, _ = wait_for(ws, "!!window.__dl", timeout=20)
        check("download triggered", ok)
        ok, _ = wait_for(ws, "!document.querySelector('.modal-overlay')")
        check("export modal closes after download", ok)
        dname, _ = evaluate(ws, "window.__dl && window.__dl.download")
        check("download filename docx", dname and dname.endswith(".docx"), f"name={dname}")

        bchk, _ = evaluate(ws, """
            (async () => {
              if (!window.__authFetch) return 'NO_AUTHFETCH';
              const d = await window.__authFetch('/api/question/export/%s?with_answers=true');
              return JSON.stringify({ok: d.ok, bytes: d.bytes, pk: d.head[0] === 0x50 && d.head[1] === 0x4b});
            })()
        """ % eid)
        bj = json.loads(bchk) if bchk and bchk.startswith("{") else None
        check("export api returns docx", bj and bj.get("pk") and bj.get("bytes", 0) > 1000, f"chk={bchk}")

        # ---- 13) 控制台无错误 ----
        real_errors = [e for e in console_errors if "favicon" not in e and "favicon.ico" not in e]
        check("no console errors", not real_errors, f"errors={real_errors[:5]}")

        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    print("\n[net] request timeline:")
    for ev in net_events:
        print("  ", ev)

    print("\nRESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
