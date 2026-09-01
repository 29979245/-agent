"""E2E 冒烟验证：登录 → 三种来源出题审核 → 批准/重生成 → 学生无权限。

用 urllib 直连本地后端，避免 shell 转义问题。运行前需启动 uvicorn：
    python -m uvicorn app.main:app --port 8000
"""
import json
import urllib.request

BASE = "http://localhost:8000"


def post(path, body, token=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path):
    try:
        with urllib.request.urlopen(BASE + path) as r:
            return r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "")


def gen_batch(token, kps, quantity=1, question_types=None, variant_qid=None):
    """Mode 1 批量生成（设计 doc 25）：返回响应体，questions[0] 为单题。"""
    payload = {
        "knowledge_points": kps, "difficulty": "medium", "quantity": quantity,
        "question_types": question_types or ["choice"],
    }
    if variant_qid:
        payload["variant_qid"] = variant_qid
        payload["variant_source"] = "historical"
    return post("/api/question/generate", payload, token)


def imp(token, content, source="manual"):
    """Mode 2 手动录入 / OCR 单题。"""
    return post("/api/question/import", {
        "content": content, "options": [], "answer": "", "analysis": "",
        "knowledge_points": "配平与计算", "difficulty": "medium", "source": source,
    }, token)


def main():
    ok = []
    T = lambda c: "✓" if c else "✗"  # noqa: E731 — ✓/✗

    # 1. 静态页（含 JS 模块）
    for p in ("/pages/login.html", "/pages/exam-v2.html",
              "/pages/js/api.js", "/pages/js/auth.js",
              "/pages/js/katex.js", "/pages/js/workbench.js"):
        code, ct = get(p)
        good = code == 200 and ct != ""
        ok.append(good)
        print(f"GET {p} -> {code} {ct} {T(good)}")

    # 2. 教师登录
    code, login = post("/api/auth/login", {"username": "teacher_demo", "password": "demo123"})
    good = code == 200 and login["role"] == "teacher"
    ok.append(good)
    print(f"teacher login -> {code} role={login.get('role')} {T(good)}")
    token = login["access_token"]

    # 3. Mode 1 批量生成（balance 方程式）→ questions[0] 四维方程级 passed
    code, r = gen_batch(token, ["配平与计算"])
    q0 = (r.get("questions") or [{}])[0] if code == 200 else {}
    ar = q0.get("audit_report", {})
    good = (code == 200 and r.get("generated_count") >= 1
            and q0.get("overall_status") == "passed"
            and ar.get("equation_level", {}).get("overall_status") == "passed")
    ok.append(good)
    print(f"batch generate -> {code} count={r.get('generated_count')} "
          f"status={q0.get('overall_status')} "
          f"eq={ar.get('equation_level', {}).get('overall_status')} {T(good)}")

    # 4. import 未配平（manual）→ blocked，批准被拒 400
    code, r = imp(token, "配平：$\\ce{H2 + O2 -> H2O}$", "manual")
    qid = r.get("question_id")
    if code == 200 and qid:
        code2, r2 = post(f"/api/question/{qid}/approve", {}, token)
        good = code2 == 400 and "blocked" in str(r2.get("detail", "")).lower()
        ok.append(good)
        print(f"import unbalanced + approve -> import={code} approve={code2} "
              f"detail={r2.get('detail')} {T(good)}")
    else:
        ok.append(False)
        print(f"import unbalanced -> 前置失败 {code} ✗")

    # 5. import 无方程式（manual）→ ql null + eq note 无方程式可校验
    code, r = imp(token, "下列物质中既能与盐酸反应又能与氢氧化钠溶液反应的是（ ）。", "manual")
    ar = r.get("audit_report", {})
    good = (code == 200 and r.get("overall_status") == "passed"
            and ar.get("question_level") is None
            and ar.get("equation_level", {}).get("note") == "无方程式可校验")
    ok.append(good)
    print(f"manual import -> {code} status={r.get('overall_status')} "
          f"ql={ar.get('question_level')} eq_note={ar.get('equation_level', {}).get('note')} {T(good)}")

    # 6. 批准 passed 题目 → 200（warning 时带复核标记）
    code, r = gen_batch(token, ["配平与计算"])
    q0 = (r.get("questions") or [{}])[0] if code == 200 else {}
    qid = q0.get("question_id")
    if code == 200 and qid:
        code2, r2 = post(f"/api/question/{qid}/approve", {}, token)
        good = code2 == 200 and r2.get("status") == "approved"
        ok.append(good)
        print(f"approve passed -> {code2} status={r2.get('status')} review_flag={r2.get('review_flag')} {T(good)}")
    else:
        ok.append(False)
        print("approve passed -> 前置生成失败 ✗")

    # 8. 学生登录（后端放行，前端角色门控拦截）
    code, login = post("/api/auth/login", {"username": "student_demo", "password": "demo123"})
    good = code == 200 and login["role"] == "student"
    ok.append(good)
    print(f"student login -> {code} role={login.get('role')} {T(good)}")

    passed = sum(1 for x in ok if x)
    print(f"\n结果：{passed}/{len(ok)} 项通过")
    if all(ok):
        print("全部通过 ✓")
    else:
        print("存在失败项，请检查 ✗")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
