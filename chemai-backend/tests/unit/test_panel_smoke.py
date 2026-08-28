"""学情面板冒烟测试（task 5.1）：应用可导入启动，panel/classes 路由可达。"""
from app.main import app

EXPECTED_ROUTES = {
    "/api/panel/class/{class_id}",
    "/api/panel/class/{class_id}/knowledge/{knowledge_point}",
    "/api/panel/class/{class_id}/student/{student_id}",
    "/api/panel/class/{class_id}/trend",
    "/api/panel/grade/{grade_id}/trend",
    "/api/panel/export/{class_id}",
    "/api/panel/dashboard/{teacher_id}",
    "/api/classes/{class_id}/students",
}


def test_panel_routes_registered():
    paths = {r.path for r in app.routes}
    assert EXPECTED_ROUTES <= paths


def test_panel_router_reachable_via_app():
    # 确认面板路由挂载在 /api/panel 前缀下，未被其他路由遮蔽
    panel_paths = [r.path for r in app.routes if getattr(r, "path", "").startswith("/api/panel")]
    assert len(panel_paths) == 7
