"""方程式级四维安全审核引擎（doc 26）。

纯确定性规则实现，不依赖 LLM / RDKit。四个维度：
1. 系数配平（HARD RED LINE，100% 零误差，元素原子计数法）
2. 反应条件（14 类关键词规则库，召回率 ≥80%）
3. 产物正确性/稳定性（正则规则，召回率 ≥80%）
4. 分子结构（格式规范校验）

综合判定（doc 26 §11.3）：仅系数配平失败硬阻断 blocked；
条件/产物/结构问题为软标记 warning，不阻断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 反应箭头（多字符在前，避免 "=" 误吞 "<=>"）；_find_arrow / _arrow_len 共用此单一来源
_ARROWS = ("→", "⇌", "->", "<=>", "=")

# 化学式元素解析：支持 ASCII 下标（H2O）、LaTeX 下标（H_2O / H_{2}O）
_ELEMENT_PAT = re.compile(r"([A-Z][a-z]?)(?:_\{(\d+)\}|_(\d+)|(\d*))")
_COEF_PAT = re.compile(r"^(\d+)(.*)$")
# 离子电荷后缀中紧邻正负号的数字是"下标"而非电荷量的已知化学式尾（doc 26 离子方程式）：
# HCO3- 的 3、NH4+ 的 4、CO32- 的 "3" 都保留，只有 Fe3+ / S2- 的电荷量才剥离。
_CHARGE_SUBSCRIPT_SUFFIXES = frozenset({
    "CO2", "CO3", "HCO3", "SO3", "SO4", "HSO4", "NO2", "NO3", "NH4",
    "PO4", "HPO4", "H2PO4", "ClO2", "ClO3", "ClO4", "MnO4", "CrO4",
    "Cr2O7", "C2O4", "S2O3", "H3O",
})
_STATE_MARK_PAT = re.compile(r"[↑↓ \t]")
_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}

# 14 类条件关键词规则库（doc 26 §3.1）
COMMON_CONDITIONS: dict[str, tuple[str, ...]] = {
    "点燃": ("点燃",),
    "加热": ("加热", "△", "Δ"),
    "高温": ("高温",),
    "催化剂": ("催化剂", "MnO2催化", "MnO₂催化", "Cu催化", "Fe催化"),
    "通电": ("通电", "电解"),
    "光照": ("光照", "光"),
    "加压": ("加压", "高压"),
    "一定条件": ("一定条件",),
    "浓": ("浓",),
    "稀": ("稀",),
    "过量": ("过量",),
    "足量": ("足量",),
    "适量": ("适量",),
    "高温高压": ("高温高压",),
}

# 燃烧燃料（doc 26 §3.3）：燃料元素/分子存在则必须标注"点燃"
_COMBUSTION_FUELS = {"CH4", "C2H5OH", "C6H12O6", "S", "P", "Fe"}
# 催化指示物：出现则建议标注催化剂
_CATALYST_INDICATORS = {"H2O2", "KClO3", "KMnO4"}
# 热分解指示物：分解需加热/高温
_THERMAL_DECOMPOSITION = {"CaCO3", "NaHCO3", "Cu2(OH)2CO3", "Fe(OH)3", "Al(OH)3"}


class EquationParseError(ValueError):
    """方程式无法解析（缺分隔符/格式异常）。"""


@dataclass
class ParsedEquation:
    reactants: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)


@dataclass
class BalanceResult:
    status: str  # passed / blocked
    message: str
    left_elements: dict[str, int] = field(default_factory=dict)
    right_elements: dict[str, int] = field(default_factory=dict)
    differences: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class ConditionResult:
    status: str  # passed / warning / failed
    message: str
    conditions_found: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class ProductResult:
    status: str  # passed / warning / failed
    message: str
    issues: list[str] = field(default_factory=list)


@dataclass
class StructureResult:
    status: str  # passed / failed
    message: str
    issues: list[str] = field(default_factory=list)


@dataclass
class EquationAuditReport:
    equation: str
    balance: BalanceResult
    condition: ConditionResult
    product: ProductResult
    structure: StructureResult
    overall_status: str  # passed / warning / blocked
    overall_message: str

    def to_dict(self) -> dict:
        return {
            "equation": self.equation,
            "balance": {
                "status": self.balance.status,
                "message": self.balance.message,
                "left_elements": self.balance.left_elements,
                "right_elements": self.balance.right_elements,
                "differences": self.balance.differences,
            },
            "condition": {
                "status": self.condition.status,
                "message": self.condition.message,
                "conditions_found": self.condition.conditions_found,
                "missing_conditions": self.condition.missing_conditions,
            },
            "product": {
                "status": self.product.status,
                "message": self.product.message,
                "issues": self.product.issues,
            },
            "structure": {
                "status": self.structure.status,
                "message": self.structure.message,
                "issues": self.structure.issues,
            },
            "overall_status": self.overall_status,
            "overall_message": self.overall_message,
        }


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _find_arrow(equation: str) -> int | None:
    """按 _ARROWS 优先级定位反应箭头。"""
    for arrow in _ARROWS:
        idx = equation.find(arrow)
        if idx != -1:
            return idx
    return None


def _arrow_len(equation: str, arrow: int) -> int:
    """箭头字符长度（ASCII `->`/`<=>` 多字符，其余单字符）。"""
    for a in _ARROWS:
        if equation.startswith(a, arrow):
            return len(a)
    return 1


def parse_equation(equation: str) -> ParsedEquation:
    """拆分反应物与产物：去空格 → 按箭头分侧 → 按 + 拆化合物（保护括号与离子电荷）。"""
    arrow = _find_arrow(equation)
    if arrow is None:
        raise EquationParseError(f"方程式缺少反应箭头: {equation}")
    left_raw, right_raw = equation[:arrow], equation[arrow + _arrow_len(equation, arrow):]
    # 跳过箭头随附条件区，如 ->(MnO2) / ->[MnO2][△]
    right_raw = _strip_arrow_condition(right_raw)
    return ParsedEquation(
        reactants=_split_compounds(left_raw),
        products=_split_compounds(right_raw),
    )


def _strip_arrow_condition(segment: str) -> str:
    """剥离开头的箭头条件标注：(MnO2)、[MnO2][△] 等。"""
    seg = segment.strip()
    while seg.startswith(("(", "[")):
        depth = 0
        closing = {"(": ")", "[": "]"}[seg[0]]
        i = 0
        while i < len(seg):
            if seg[i] in "([":
                depth += 1
            elif seg[i] in ")]":
                depth -= 1
                if depth == 0:
                    seg = seg[i + 1:].strip()
                    break
            i += 1
    return seg


def _split_compounds(segment: str) -> list[str]:
    """按 + 拆分化合物。

    + 作为离子电荷（Cu2+ / Fe+3 前导 / e- 相邻）时不拆分；
    仅当 + 前后以空白隔开（独立分隔符）且后跟化合物起始字符时才是分隔符。
    依赖空白区分：'3Cu + 8HNO3' 的 + 是分隔符，'Fe+3' 的 + 是电荷符号。
    """
    compounds: list[str] = []
    current: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        is_separator = False
        if ch == "+":
            prev_is_space = i == 0 or segment[i - 1] in " \t"
            j = i + 1
            while j < n and segment[j] in " \t":
                j += 1
            next_starts_compound = j < n and (segment[j].isalnum() or segment[j] in "([")
            is_separator = prev_is_space and next_starts_compound
        if is_separator:
            if current:
                compounds.append("".join(current).strip())
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        compounds.append("".join(current).strip())
    return [c for c in compounds if c]


def _strip_state_markers(formula: str) -> str:
    return _STATE_MARK_PAT.sub("", formula)


def _strip_coefficient(formula: str) -> tuple[int, str]:
    m = _COEF_PAT.match(formula)
    if m:
        return int(m.group(1)), m.group(2)
    return 1, formula


def _strip_charge_suffix(formula: str) -> str:
    """去掉离子电荷后缀：Fe3+ → Fe、CO32- → CO3、OH- → OH、HCO3- → HCO3。

    规则：
    1. 先剥正负号；
    2. 符号前一字符是数字时——再前一字符也是数字（CO32- 的 '32'），末位数字是电荷量，只剥一位；
       仅一位数字则看式尾是否命中已知下标后缀（HCO3 / NH4 / SO4）：命中则数字是下标，只剥符号；
       否则（Fe3+ / S2-）数字是电荷量，连同符号一起剥。
    """
    m = re.search(r"([+-])$", formula)
    if not m:
        return formula
    sign = m.start(1)
    if sign > 0 and formula[sign - 1].isdigit():
        d = sign - 1
        if d > 0 and formula[d - 1].isdigit():
            return formula[:d]
        if any(formula[:sign].endswith(s) for s in _CHARGE_SUBSCRIPT_SUFFIXES):
            return formula[:sign]
        return formula[:d]
    return formula[:sign]


def _parse_group(formula: str, i: int) -> tuple[dict[str, int], int]:
    """从下标 i 解析一个基团，返回 (元素计数, 结束下标)。支持 (…)n 括号递归。"""
    counts: dict[str, int] = {}
    n = len(formula)
    while i < n:
        ch = formula[i]
        if ch in _BRACKET_PAIRS:
            inner, i = _parse_group(formula, i + 1)
            m = re.match(r"(\d+)", formula[i:])
            mult = int(m.group(1)) if m else 1
            i += len(m.group(1)) if m else 0
            for el, c in inner.items():
                counts[el] = counts.get(el, 0) + c * mult
        elif ch in ")]":
            return counts, i + 1
        else:
            m = _ELEMENT_PAT.match(formula, i)
            if m:
                el = m.group(1)
                num = int(m.group(2) or m.group(3) or m.group(4) or "1")
                counts[el] = counts.get(el, 0) + num
                i = m.end()
            else:
                i += 1  # 跳过非元素字符（e-、状态符等）
    return counts, i


def count_elements(formula: str) -> dict[str, int]:
    """统计单化合物各元素原子数：剥离系数 → 去电荷 → 展开括号。"""
    formula = _strip_state_markers(formula)
    if not formula:
        return {}
    coef, body = _strip_coefficient(formula)
    body = _strip_charge_suffix(body)
    counts, _ = _parse_group(body, 0)
    return {el: n * coef for el, n in counts.items()}


def _side_counts(equation: str) -> tuple[dict[str, int], dict[str, int]]:
    parsed = parse_equation(equation)
    left: dict[str, int] = {}
    right: dict[str, int] = {}
    for c in parsed.reactants:
        for el, n in count_elements(c).items():
            left[el] = left.get(el, 0) + n
    for c in parsed.products:
        for el, n in count_elements(c).items():
            right[el] = right.get(el, 0) + n
    return left, right


# ---------------------------------------------------------------------------
# 维度 1：系数配平（HARD RED LINE）
# ---------------------------------------------------------------------------


def check_balance(equation: str) -> BalanceResult:
    """元素原子计数法：两侧各元素原子数逐一比对，任一无差则阻断。"""
    left, right = _side_counts(equation)
    differences = {
        el: (left.get(el, 0), right.get(el, 0))
        for el in set(left) | set(right)
        if left.get(el, 0) != right.get(el, 0)
    }
    if differences:
        detail = ", ".join(f"{el}: 左{l} vs 右{r}" for el, (l, r) in sorted(differences.items()))
        return BalanceResult(
            status="blocked",
            message=f"系数未配平: {detail}",
            left_elements=left,
            right_elements=right,
            differences=differences,
        )
    return BalanceResult(
        status="passed",
        message="系数配平正确",
        left_elements=left,
        right_elements=right,
    )


# ---------------------------------------------------------------------------
# 维度 2：反应条件
# ---------------------------------------------------------------------------


def _scan_conditions(equation: str) -> list[str]:
    found: list[str] = []
    for cond, keywords in COMMON_CONDITIONS.items():
        if any(kw in equation for kw in keywords):
            found.append(cond)
    return found


def _arrow_catalyst_present(equation: str) -> bool:
    """箭头随附催化剂标注（doc 26 §10.5）：→(MnO2) / ->[MnO2][△] / -->MnO2-->。"""
    return bool(re.search(r"(?:→|⇌|->|=)\s*\(?\s*MnO[₂2]", equation))


def _compound_formulas(equation: str) -> tuple[list[str], list[str]]:
    try:
        parsed = parse_equation(equation)
    except EquationParseError:
        return [], []
    return [_strip_coefficient(_strip_charge_suffix(_strip_state_markers(c)))[1]
            for c in parsed.reactants], \
           [_strip_coefficient(_strip_charge_suffix(_strip_state_markers(c)))[1]
            for c in parsed.products]


def check_conditions(equation: str) -> ConditionResult:
    """14 类条件关键词扫描 + 反应类型-条件映射 + 矛盾条件检测。"""
    conditions_found = _scan_conditions(equation)
    reactants, _products = _compound_formulas(equation)
    catalyst_found = "催化剂" in conditions_found or _arrow_catalyst_present(equation)
    missing: list[str] = []
    issues: list[str] = []
    hard = False  # 是否含硬性缺失（failed）

    # 燃烧反应 → 需"点燃"（FAIL）
    if any(f in _COMBUSTION_FUELS for f in reactants) and "点燃" not in conditions_found:
        missing.append("点燃")
        issues.append("燃烧反应必须标注条件：点燃")
        hard = True
    # 催化指示物 → 建议"催化剂"（WARNING）
    if any(f in _CATALYST_INDICATORS for f in reactants) and not catalyst_found:
        missing.append("催化剂(建议标注)")
        issues.append("催化分解反应建议标注催化剂")
    # 电解关键词 → 需"通电/电解"（FAIL）
    if "电解" in equation and "通电" not in conditions_found:
        missing.append("通电")
        issues.append("电解反应必须标注条件：通电/电解")
        hard = True
    # 电解水（H2O → H2 + O2）→ 需"通电"（FAIL）
    if any(f == "H2O" for f in reactants) and any(f == "H2" for f in _products) \
            and any(f == "O2" for f in _products) and "通电" not in conditions_found:
        missing.append("通电")
        issues.append("电解水反应必须标注条件：通电")
        hard = True
    # 合成氨（N2 + H2 → NH3）→ 高温高压 + 催化剂（FAIL）
    if any(f == "N2" for f in reactants) and any(f == "H2" for f in reactants) \
            and any(f == "NH3" for f in _products):
        if "高温高压" not in conditions_found:
            missing.append("高温高压")
            issues.append("合成氨反应需要条件：高温高压")
            hard = True
        if not catalyst_found:
            missing.append("催化剂")
            issues.append("合成氨反应需要条件：催化剂")
            hard = True
    # 热分解指示物 → 需"加热/高温"（FAIL）
    if any(f in _THERMAL_DECOMPOSITION for f in reactants) \
            and not any(c in conditions_found for c in ("加热", "高温")):
        missing.append("加热")
        issues.append("热分解反应需要条件：加热/高温")
        hard = True

    # 矛盾条件检测（doc 26 §3.5）
    if "浓" in conditions_found and "稀" in conditions_found:
        issues.append("矛盾条件：浓 + 稀 同时出现")
        hard = True
    if "过量" in conditions_found and "适量" in conditions_found:
        issues.append("矛盾条件：过量 + 适量 同时出现")
        hard = True
    if "点燃" in conditions_found and "通电" in conditions_found:
        issues.append("非常规组合：点燃 + 通电 同时出现")

    if hard or missing:
        status = "failed" if hard else "warning"
        message = "；".join(issues) or "存在缺失条件"
        return ConditionResult(
            status=status,
            message=message,
            conditions_found=conditions_found,
            missing_conditions=missing,
            issues=issues,
        )
    if issues:
        return ConditionResult(
            status="warning",
            message="；".join(issues),
            conditions_found=conditions_found,
            missing_conditions=missing,
            issues=issues,
        )
    return ConditionResult(
        status="passed",
        message="反应条件完整",
        conditions_found=conditions_found,
    )


# ---------------------------------------------------------------------------
# 维度 3：产物正确性/稳定性
# ---------------------------------------------------------------------------


def check_product_stability(equation: str) -> ProductResult:
    """产物合理性规则库（doc 26 §4.1/§4.2）：不稳定产物模式 + 浓稀氧化还原产物。"""
    issues: list[str] = []
    _reactants, products = _compound_formulas(equation)
    if not products:
        return ProductResult(status="passed", message="无产物可校验")

    condition_text = equation
    has_concentrated = "浓" in condition_text
    has_dilute = "稀" in condition_text

    for p in products:
        # 规则：不稳定的酸式产物（碳酸/亚硫酸/氢氧化铵不存在游离态）
        if p in ("H2CO3", "H2SO3"):
            issues.append(f"产物 {p} 不稳定，应分解为对应氧化物 + 水")
        if p == "NH4OH":
            issues.append("产物 NH4OH 不稳定，应为 NH3↑ + H2O")
        # 规则：碳单质应标注形态
        if p == "C":
            issues.append("碳单质应标注形态，如 CO₂ 或 C(固)")
        # 规则：有机物提示
        if re.search(r"C\d*H\d+O?\d*", p) and p not in ("CO", "CO2"):
            issues.append(f"产物 {p} 为有机物，应写分子式或结构简式")
        # 规则：浓硫酸与金属不产氢
        if has_concentrated and p == "H2" and "H2SO4" in condition_text:
            issues.append("浓硫酸与金属反应产物应为 SO₂，不产生 H₂")
        # 规则：稀硫酸与活泼金属产物应为 H2 而非 SO2
        if has_dilute and p == "SO2" and "H2SO4" in condition_text:
            issues.append("稀硫酸与活泼金属产物应为 H₂，不产生 SO₂")
        # 规则：浓硝酸产物为 NO2，稀硝酸产物为 NO
        if has_concentrated and p == "NO" and "HNO3" in condition_text:
            issues.append("浓硝酸还原产物应为 NO₂")
        if has_dilute and p == "NO2" and "HNO3" in condition_text:
            issues.append("稀硝酸还原产物应为 NO")

    if issues:
        return ProductResult(status="warning", message="；".join(issues), issues=issues)
    return ProductResult(status="passed", message="产物合理性正确")


# ---------------------------------------------------------------------------
# 维度 4：分子结构
# ---------------------------------------------------------------------------


# 单字母元素符号（14 个）：用于识别连续大写错误（FE/MG/CL 等），
# 同时不误判 CO/NO/FeO 等两个单字母元素相邻的合法化学式。
_ONE_LETTER_ELEMENTS = set("HBCNOFPSKVYIWU")


def _uppercase_sequence_error(compound: str) -> bool:
    """连续大写且第二位非单字母元素 → 元素符号格式错误（如 FE、MG、CL、NA）。"""
    for i in range(len(compound) - 1):
        if compound[i].isupper() and compound[i + 1].isupper():
            if compound[i + 1] not in _ONE_LETTER_ELEMENTS:
                return True
    return False


def _bracket_check(compound: str) -> list[str]:
    stack: list[str] = []
    for ch in compound:
        if ch in _BRACKET_PAIRS:
            stack.append(ch)
        elif ch in _BRACKET_PAIRS.values():
            if not stack or _BRACKET_PAIRS[stack[-1]] != ch:
                return [f"括号不匹配: {compound}"]
            stack.pop()
    if stack:
        return [f"未闭合的括号: {compound}"]
    return []


def check_structure(equation: str) -> StructureResult:
    """分子结构格式规范：元素符号大小写、下标、括号匹配、离子电荷、LaTeX 完整性。"""
    issues: list[str] = []
    try:
        parsed = parse_equation(equation)
    except EquationParseError:
        return StructureResult(status="failed", message="方程式无法解析", issues=["方程式格式异常"])
    all_compounds = parsed.reactants + parsed.products

    for c in all_compounds:
        formula = _strip_state_markers(c)
        if not formula:
            continue
        if _uppercase_sequence_error(formula):
            issues.append(f"元素符号连续大写: {formula}")
        if re.match(r"^[a-z]", formula):
            issues.append(f"元素符号首字母需大写: {formula}")
        issues.extend(_bracket_check(formula))
        # 离子电荷顺序错误：+3 / -2 数字在前
        if re.search(r"[+-]\d", formula):
            issues.append(f"离子电荷格式错误（数字应在前）: {formula}")
        # 残留 LaTeX 命令（未归一化）
        if "\\ce" in formula or formula.count("$") % 2 == 1:
            issues.append(f"LaTeX 格式未归一化: {formula}")

    if issues:
        return StructureResult(status="failed", message="；".join(issues), issues=issues)
    return StructureResult(status="passed", message="分子结构格式规范")


# ---------------------------------------------------------------------------
# 综合审核
# ---------------------------------------------------------------------------


def audit_equation(equation: str) -> EquationAuditReport:
    """四维综合审核。仅系数配平硬阻断；条件/产物/结构为软标记。"""
    try:
        balance = check_balance(equation)
    except EquationParseError as e:
        raise EquationParseError(str(e))
    condition = check_conditions(equation)
    product = check_product_stability(equation)
    structure = check_structure(equation)

    if balance.status == "blocked":
        overall_status = "blocked"
        overall_message = "系数未配平，硬阻断"
    elif condition.status != "passed" or product.status != "passed" or structure.status != "passed":
        overall_status = "warning"
        overall_message = "存在条件/产物/结构问题（软标记）"
    else:
        overall_status = "passed"
        overall_message = "四维全部通过"

    return EquationAuditReport(
        equation=equation,
        balance=balance,
        condition=condition,
        product=product,
        structure=structure,
        overall_status=overall_status,
        overall_message=overall_message,
    )


def _strip_latex_wrappers(equation: str) -> str:
    r"""剥离前端 LaTeX 标记：$\ce{...}$ / $...$ → 裸方程式。

    KaTeX 排版用 $\ce{...}$ 包裹方程式，直接按裸文本解析会被 \ce{ } 污染
    元素计数（把 \ce、{、} 当化合物字符）。此处去掉 $ 并解 \ce{...} 花括号。
    \ce{...} 可能带中文引导语（如 "配平化学方程式 \ce{...}"），须用 search
    而非 match 定位，否则前缀会使包裹层残留并污染元素计数。
    """
    s = equation.replace("$", "").strip()
    while True:
        m = re.search(r"\\ce\s*\{", s)
        if not m:
            return s
        depth = 0
        start = m.end() - 1  # 定位 '{'
        end = None
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            return s
        s = s[start + 1:end].strip()


def extract_equations(text: str) -> list[str]:
    """从题目文本中启发式提取含箭头的方程式片段（供集成调用）。"""
    equations: list[str] = []
    remaining = text
    while True:
        arrow_idx = _find_arrow(remaining)
        if arrow_idx is None:
            break
        # 向左右扩展捕获完整方程式（中文引导语 "配平："、"方程式：" 处截断）
        start = arrow_idx
        while start > 0 and remaining[start - 1] not in "\n；;。，：:(":
            start -= 1
        end = arrow_idx + 1
        while end < len(remaining) and remaining[end] not in "\n；;。，：:)":
            end += 1
        candidate = remaining[start:end].strip().strip("，。；;：:")
        candidate = _strip_latex_wrappers(candidate)
        if candidate:
            equations.append(candidate)
        remaining = remaining[end:]
    return equations
