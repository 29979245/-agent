"""8.4a 打包检查：rules.yaml 随包部署声明 + 文件实际存在于包内。

打包清单验证（wheel 内含 YAML）在配置变更时以 `python -m build --wheel` 手工核对；
此静态用例守护声明不被误删，保持 L1 <5s 门禁预算。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_YAML = ROOT / "app" / "services" / "diagnosis" / "rules" / "rules.yaml"


def test_rules_yaml_exists_in_package():
    assert RULES_YAML.exists()
    assert RULES_YAML.stat().st_size > 0


def test_pyproject_declares_package_data_for_rules_yaml():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"app.services.diagnosis.rules"' in text
    assert '["*.yaml"]' in text
    assert "[tool.setuptools.packages.find]" in text
