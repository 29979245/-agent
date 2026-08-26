"""接线冒烟测试（task 9.1）：应用可启动、新路由已注册且鉴权可达。"""
from fastapi.testclient import TestClient

from app.main import app

NEW_ROUTE_TEMPLATES = [
    "/api/practice/student/{uid}/tasks",
    "/api/practice/submit",
    "/api/practice/effect/{student_id}",
    "/api/review/tasks/{student_id}",
    "/api/review/submit",
    "/api/wrong-questions/{student_id}",
    "/api/wrong-questions/variants",
    "/api/wrong-questions/train",
    "/api/wrong-questions/{question_id}/mastered",
]

# 具体路径（去掉路径参数占位）→ 请求方法，用于鉴权可达探测
SMOKE_REQUESTS = [
    ("GET", "/api/practice/student/1/tasks"),
    ("POST", "/api/practice/submit"),
    ("GET", "/api/practice/effect/1"),
    ("GET", "/api/review/tasks/1"),
    ("POST", "/api/review/submit"),
    ("GET", "/api/wrong-questions/1"),
    ("POST", "/api/wrong-questions/variants"),
    ("POST", "/api/wrong-questions/train"),
    ("POST", "/api/wrong-questions/1/mastered"),
]


def test_app_starts_and_health():
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_new_routes_registered():
    registered = {r.path for r in app.routes if getattr(r, "path", None)}
    for path in NEW_ROUTE_TEMPLATES:
        assert path in registered, f"路由未注册: {path}"


def test_protected_routes_reachable_no_token():
    """未带 token 访问新端点 → 401（路由可达且鉴权生效）。"""
    with TestClient(app) as c:
        for method, path in SMOKE_REQUESTS:
            resp = c.request(method, path)
            assert resp.status_code == 401, f"{method} {path} 预期 401 实得 {resp.status_code}"
