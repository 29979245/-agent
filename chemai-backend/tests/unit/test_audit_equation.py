"""方程式级四维安全审核引擎测试（doc 26）。

86 道确定性配平测试（HARD RED LINE，100%）——覆盖 9 类反应：
化合 12 / 分解 10 / 置换 8 / 复分解 8 / 氧化还原 14 / 有机 8 / 离子 10 / 电极 6 / 工业 10。
另覆盖：解析、条件召回、产物合理性、分子结构、四维聚合。
"""
import pytest

from app.services.audit.equation import (
    EquationParseError,
    _strip_latex_wrappers,
    audit_equation,
    check_balance,
    check_conditions,
    check_product_stability,
    check_structure,
    count_elements,
    extract_equations,
    parse_equation,
)

# ---------------------------------------------------------------------------
# 86 道确定性配平用例（按反应类型分组）
# ---------------------------------------------------------------------------

BALANCED_86 = {
    "化合反应": [
        "2H2 + O2 → 2H2O",
        "4P + 5O2 → 2P2O5",
        "2Mg + O2 → 2MgO",
        "3Fe + 2O2 → Fe3O4",
        "2Na + Cl2 → 2NaCl",
        "2K + Cl2 → 2KCl",
        "CaO + H2O → Ca(OH)2",
        "CO2 + H2O → H2CO3",
        "2SO2 + O2 → 2SO3",
        "2NO + O2 → 2NO2",
        "N2 + 3H2 → 2NH3",
        "2Cu + O2 → 2CuO",
    ],
    "分解反应": [
        "2H2O2 → 2H2O + O2",
        "2KClO3 → 2KCl + 3O2",
        "2KMnO4 → K2MnO4 + MnO2 + O2",
        "2HgO → 2Hg + O2",
        "2H2O → 2H2 + O2",
        "CaCO3 → CaO + CO2",
        "2NaHCO3 → Na2CO3 + H2O + CO2",
        "2Fe(OH)3 → Fe2O3 + 3H2O",
        "2Al(OH)3 → Al2O3 + 3H2O",
        "Cu2(OH)2CO3 → 2CuO + H2O + CO2",
    ],
    "置换反应": [
        "Fe + CuSO4 → FeSO4 + Cu",
        "Zn + H2SO4 → ZnSO4 + H2",
        "Fe + 2HCl → FeCl2 + H2",
        "2Al + 3CuSO4 → Al2(SO4)3 + 3Cu",
        "Zn + CuCl2 → ZnCl2 + Cu",
        "Fe + Cu(NO3)2 → Fe(NO3)2 + Cu",
        "2Na + 2H2O → 2NaOH + H2",
        "Cl2 + 2NaBr → 2NaCl + Br2",
    ],
    "复分解反应": [
        "NaOH + HCl → NaCl + H2O",
        "CaCO3 + 2HCl → CaCl2 + H2O + CO2",
        "BaCl2 + Na2SO4 → BaSO4 + 2NaCl",
        "AgNO3 + NaCl → AgCl + NaNO3",
        "2NaOH + H2SO4 → Na2SO4 + 2H2O",
        "Ca(OH)2 + Na2CO3 → CaCO3 + 2NaOH",
        "Fe2O3 + 6HCl → 2FeCl3 + 3H2O",
        "Cu(OH)2 + 2HCl → CuCl2 + 2H2O",
    ],
    "氧化还原反应": [
        "2Fe + 3Cl2 → 2FeCl3",
        "Fe2O3 + 3CO → 2Fe + 3CO2",
        "Fe2O3 + 3H2 → 2Fe + 3H2O",
        "CuO + H2 → Cu + H2O",
        "2CuO + C → 2Cu + CO2",
        "2Mg + CO2 → 2MgO + C",
        "Cl2 + 2NaOH → NaCl + NaClO + H2O",
        "2H2S + SO2 → 3S + 2H2O",
        "8NH3 + 3Cl2 → N2 + 6NH4Cl",
        "2KMnO4 + 16HCl → 2KCl + 2MnCl2 + 5Cl2 + 8H2O",
        "MnO2 + 4HCl → MnCl2 + Cl2 + 2H2O",
        "2FeCl3 + 2KI → 2FeCl2 + 2KCl + I2",
        "3Cu + 8HNO3 → 3Cu(NO3)2 + 2NO + 4H2O",
        "Cu + 2H2SO4 → CuSO4 + SO2 + 2H2O",
    ],
    "有机反应": [
        "CH4 + 2O2 → CO2 + 2H2O",
        "2C2H6 + 7O2 → 4CO2 + 6H2O",
        "C2H5OH + 3O2 → 2CO2 + 3H2O",
        "C6H12O6 + 6O2 → 6CO2 + 6H2O",
        "C2H4 + 3O2 → 2CO2 + 2H2O",
        "C2H2 + 2H2 → C2H6",
        "C2H5OH → C2H4 + H2O",
        "C6H6 + Br2 → C6H5Br + HBr",
    ],
    "离子方程式": [
        "Fe + Cu2+ → Fe2+ + Cu",
        "Zn + 2H+ → Zn2+ + H2",
        "H+ + OH- → H2O",
        "2H+ + CO32- → H2O + CO2",
        "Ba2+ + SO42- → BaSO4",
        "Ag+ + Cl- → AgCl",
        "Fe3+ + 3OH- → Fe(OH)3",
        "Cu2+ + 2OH- → Cu(OH)2",
        "2H+ + S2- → H2S",
        "HCO3- + H+ → H2O + CO2",
    ],
    "电极反应": [
        "Fe3+ + e- → Fe2+",
        "Cu2+ + 2e- → Cu",
        "2H+ + 2e- → H2",
        "Zn → Zn2+ + 2e-",
        "O2 + 2H2O + 4e- → 4OH-",
        "2H2O + 2e- → H2 + 2OH-",
    ],
    "工业流程反应": [
        "Fe2O3 + 3CO → 2Fe + 3CO2",
        "CaCO3 → CaO + CO2",
        "N2 + 3H2 → 2NH3",
        "2SO2 + O2 → 2SO3",
        "SO3 + H2O → H2SO4",
        "2Al2O3 → 4Al + 3O2",
        "2NaCl + 2H2O → 2NaOH + H2 + Cl2",
        "4FeS2 + 11O2 → 2Fe2O3 + 8SO2",
        "3Fe + 4H2O → Fe3O4 + 4H2",
        "SiO2 + 2NaOH → Na2SiO3 + H2O",
    ],
}

# 负例：故意改错系数的未配平方程式
UNBALANCED = [
    "H2 + O2 → H2O",
    "Fe + O2 → Fe2O3",
    "Na + H2O → NaOH + H2",
    "CH4 + O2 → CO2 + H2O",
    "2H2 + O2 → H2O",
    "Fe + CuSO4 → FeSO4 + 2Cu",
    "2Mg + O2 → MgO",
    "N2 + H2 → NH3",
    "Zn + H2SO4 → ZnSO4 + H",
    "CaCO3 → CaO",
]


def _flatten_86() -> list[str]:
    return [eq for group in BALANCED_86.values() for eq in group]


def test_balanced_86_count_matches_doc26():
    """9 类反应总数 = 86（doc 26 §9.3）。"""
    counts = {k: len(v) for k, v in BALANCED_86.items()}
    assert counts == {
        "化合反应": 12,
        "分解反应": 10,
        "置换反应": 8,
        "复分解反应": 8,
        "氧化还原反应": 14,
        "有机反应": 8,
        "离子方程式": 10,
        "电极反应": 6,
        "工业流程反应": 10,
    }
    assert len(_flatten_86()) == 86


# ---- 维度 1：系数配平（86/86 HARD RED LINE）----


@pytest.mark.parametrize("equation", _flatten_86())
def test_balance_accepts_all_86(equation):
    result = check_balance(equation)
    assert result.status == "passed", f"配平被误判: {equation} → {result.message}"


@pytest.mark.parametrize("equation", UNBALANCED)
def test_balance_rejects_unbalanced(equation):
    result = check_balance(equation)
    assert result.status == "blocked", f"未配平方程被放行: {equation}"
    assert result.differences, "未配平方程应输出差异明细"


# ---- 解析 ----

def test_parse_splits_sides_and_compounds():
    parsed = parse_equation("2H2 + O2 → 2H2O")
    assert parsed.reactants == ["2H2", "O2"]
    assert parsed.products == ["2H2O"]


def test_parse_keeps_parens_group():
    parsed = parse_equation("Ca(OH)2 + CO2 → CaCO3 + H2O")
    assert parsed.reactants == ["Ca(OH)2", "CO2"]


def test_parse_keeps_ion_charge():
    parsed = parse_equation("Fe + Cu2+ → Fe2+ + Cu")
    assert parsed.reactants == ["Fe", "Cu2+"]


def test_parse_raises_without_arrow():
    with pytest.raises(EquationParseError):
        parse_equation("H2O nothing here")


def test_count_elements_basic():
    assert count_elements("H2O") == {"H": 2, "O": 1}
    assert count_elements("2H2O") == {"H": 4, "O": 2}


def test_count_elements_parens():
    assert count_elements("Ca(OH)2") == {"Ca": 1, "O": 2, "H": 2}
    assert count_elements("Al2(SO4)3") == {"Al": 2, "S": 3, "O": 12}


def test_count_elements_charge_suffix():
    assert count_elements("Fe3+") == {"Fe": 1}
    assert count_elements("CO32-") == {"C": 1, "O": 3}
    assert count_elements("OH-") == {"O": 1, "H": 1}


def test_count_elements_latex_subscript():
    assert count_elements("H_2O") == {"H": 2, "O": 1}
    assert count_elements("H_{2}O") == {"H": 2, "O": 1}


# ---- 维度 2：反应条件（召回率 ≥80%）----


def test_condition_no_requirement_passes():
    result = check_conditions("2H2 + O2 → 2H2O")
    assert result.status == "passed"


def test_condition_combustion_missing_ignite():
    result = check_conditions("CH4 + 2O2 → CO2 + 2H2O")
    assert result.status == "failed"
    assert "点燃" in result.missing_conditions


def test_condition_catalyst_annotation_present():
    result = check_conditions("2H2O2 →(MnO2) 2H2O + O2")
    # 催化剂经箭头随附标注（→(MnO2)）检出，条件维度应放行
    assert result.status == "passed"


def test_condition_catalyst_missing_suggested():
    result = check_conditions("2KClO3 → 2KCl + 3O2")
    assert result.status == "warning"
    assert "催化剂" in result.missing_conditions[0]


def test_condition_ammonia_synthesis():
    result = check_conditions("N2 + 3H2 → 2NH3")
    assert result.status == "failed"
    assert "高温高压" in result.missing_conditions
    assert "催化剂" in result.missing_conditions


def test_condition_thermal_decomposition():
    result = check_conditions("CaCO3 → CaO + CO2")
    assert result.status == "failed"
    assert "加热" in result.missing_conditions


def test_condition_electrolysis():
    result = check_conditions("2H2O → 2H2 + O2")
    # 电解水缺通电（硬性）
    assert "通电" in result.missing_conditions


def test_condition_contradiction_concentrated_dilute():
    result = check_conditions("Cu + H2SO4(浓) + HCl(稀) → CuCl2 + H2O")
    assert result.status == "failed"
    assert any("浓" in i and "稀" in i for i in result.issues)


def test_condition_recall_metric():
    """条件检测召回：全部应检出缺条件的用例中，检出率 ≥80%。"""
    positive = [
        "CH4 + 2O2 → CO2 + 2H2O",        # 燃烧缺点燃
        "2KClO3 → 2KCl + 3O2",           # 催化缺催化剂
        "N2 + 3H2 → 2NH3",               # 合成氨缺条件
        "CaCO3 → CaO + CO2",             # 热分解缺加热
        "2H2O → 2H2 + O2",               # 电解缺通电
        "C2H5OH + 3O2 → 2CO2 + 3H2O",    # 有机燃烧缺点燃
    ]
    detected = sum(1 for eq in positive if check_conditions(eq).status != "passed")
    assert detected / len(positive) >= 0.8


# ---- 维度 3：产物合理性（召回率 ≥80%）----


def test_product_stable_passes():
    result = check_product_stability("CaCO3 + 2HCl → CaCl2 + H2O + CO2")
    assert result.status == "passed"


def test_product_h2co3_unstable():
    result = check_product_stability("CaCO3 + 2HCl → CaCl2 + H2CO3")
    assert result.status == "warning"
    assert any("H2CO3" in i for i in result.issues)


def test_product_nh4oh_unstable():
    result = check_product_stability("NH4Cl + NaOH → NaCl + NH4OH")
    assert result.status == "warning"
    assert any("NH4OH" in i for i in result.issues)


def test_product_concentrated_sulfuric_no_h2():
    result = check_product_stability("Cu + 2H2SO4(浓) → CuSO4 + H2 + 2H2O")
    assert result.status == "warning"
    assert any("H₂" in i or "H2" in i for i in result.issues)


def test_product_concentrated_sulfuric_so2_ok():
    result = check_product_stability("Cu + 2H2SO4(浓) → CuSO4 + SO2 + 2H2O")
    assert result.status == "passed"


def test_product_dilute_sulfuric_no_so2():
    result = check_product_stability("Zn + H2SO4(稀) → ZnSO4 + SO2 + H2O")
    assert result.status == "warning"
    assert any("SO₂" in i or "SO2" in i for i in result.issues)


def test_product_concentrated_nitric_no():
    result = check_product_stability("Cu + 4HNO3(浓) → Cu(NO3)2 + 2NO + 2H2O")
    assert result.status == "warning"
    assert any("NO₂" in i or "NO2" in i for i in result.issues)


def test_product_concentrated_nitric_no2_ok():
    result = check_product_stability("Cu + 4HNO3(浓) → Cu(NO3)2 + 2NO2 + 2H2O")
    assert result.status == "passed"


def test_product_dilute_nitric_no():
    result = check_product_stability("3Cu + 8HNO3(稀) → 3Cu(NO3)2 + 2NO + 4H2O")
    assert result.status == "passed"


def test_product_recall_metric():
    """产物合理性检测召回 ≥80%。"""
    positive = [
        "CaCO3 + 2HCl → CaCl2 + H2CO3",        # 碳酸不稳定
        "NH4Cl + NaOH → NaCl + NH4OH",         # 氢氧化铵不稳定
        "Cu + 2H2SO4(浓) → CuSO4 + H2 + 2H2O",  # 浓硫酸不产氢
        "Zn + H2SO4(稀) → ZnSO4 + SO2 + H2O",   # 稀硫酸不产二氧化硫
        "Cu + 4HNO3(浓) → Cu(NO3)2 + 2NO + 2H2O",  # 浓硝酸不产一氧化氮
    ]
    detected = sum(1 for eq in positive if check_product_stability(eq).status != "passed")
    assert detected / len(positive) >= 0.8


# ---- 维度 4：分子结构 ----

def test_structure_wellformed_passes():
    result = check_structure("Fe2O3 + 3CO → 2Fe + 3CO2")
    assert result.status == "passed"


def test_structure_consecutive_uppercase():
    result = check_structure("FE + O2 → FE2O3")
    assert result.status == "failed"
    assert any("大写" in i for i in result.issues)


def test_structure_lowercase_leading():
    result = check_structure("fe + O2 → Fe2O3")
    assert result.status == "failed"
    assert any("首字母" in i for i in result.issues)


def test_structure_unclosed_bracket():
    result = check_structure("Ca(OH2 + CO2 → CaCO3 + H2O")
    assert result.status == "failed"
    assert any("括号" in i for i in result.issues)


def test_structure_charge_order():
    result = check_structure("Fe+3 + e- → Fe2+")
    assert result.status == "failed"
    assert any("电荷" in i for i in result.issues)


# ---- 综合审核聚合 ----

def test_audit_all_pass():
    report = audit_equation("2H2 + O2 → 2H2O")
    assert report.overall_status == "passed"


def test_audit_balance_fail_blocks():
    report = audit_equation("H2 + O2 → H2O")
    assert report.overall_status == "blocked"
    assert report.balance.status == "blocked"


def test_audit_condition_fail_soft_warning():
    report = audit_equation("CH4 + 2O2 → CO2 + 2H2O")
    assert report.overall_status == "warning"
    assert report.balance.status == "passed"
    assert report.condition.status == "failed"


def test_audit_structure_fail_soft_warning():
    # 电荷顺序错误使结构维度失败，但配平通过 → 软标记 warning（doc 26 §11.3）
    report = audit_equation("Fe+3 + e- → Fe2+")
    assert report.overall_status == "warning"
    assert report.balance.status == "passed"
    assert report.structure.status == "failed"


def test_audit_report_to_dict_shape():
    report = audit_equation("2H2 + O2 → 2H2O")
    d = report.to_dict()
    assert set(d) >= {"equation", "balance", "condition", "product", "structure", "overall_status"}
    assert d["overall_status"] == "passed"


# ---------------------------------------------------------------------------
# LaTeX 包裹剥离（B5 联调回归：中文引导语 + \ce{...} 不得污染元素计数）
# ---------------------------------------------------------------------------

def test_strip_latex_wrappers_prefix_text():
    # \ce{...} 带中文引导语，包裹层必须被剥离，否则前缀进入元素计数
    assert _strip_latex_wrappers("配平化学方程式 \\ce{2H2 + O2 -> 2H2O}") == "2H2 + O2 -> 2H2O"
    assert _strip_latex_wrappers("配平化学方程式 $\\ce{2H2 + O2 -> 2H2O}$") == "2H2 + O2 -> 2H2O"
    # 无包裹层时原样保留
    assert _strip_latex_wrappers("2H2 + O2 -> 2H2O") == "2H2 + O2 -> 2H2O"


def test_extract_equations_with_prefix_audits_balanced():
    # OCR/手动导入文本常带 "识别题目：配平化学方程式" 等引导语，提取后应只留裸方程式
    eqs = extract_equations("识别题目：配平化学方程式 $\\ce{2H2 + O2 -> 2H2O}$")
    assert eqs == ["2H2 + O2 -> 2H2O"]
    assert audit_equation(eqs[0]).overall_status == "passed"
