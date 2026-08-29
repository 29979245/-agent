"""化学式标准化 `_normalize_chem_formulas`（doc 30 §3.2 generate_questions 契约）。

Step 1 将 $...$ 内 LaTeX 箭头统一：→ → \\rightarrow、⇌ → \\rightleftharpoons。
Step 2 检测裸化学式——白名单（60+ 常见化学式）中的下标数字转为 LaTeX 并包装为 $...$。
"""
from __future__ import annotations

import re

# Step 2 白名单：常见化学式（不含下标写法，统一用小写下标数字书写）
CHEM_FORMULA_WHITELIST: frozenset[str] = frozenset({
    "H2O", "CO2", "NaCl", "NaOH", "HCl", "H2SO4", "H2SO3", "HNO3", "H3PO4",
    "CuSO4", "CuSO4·5H2O", "KMnO4", "K2Cr2O7", "Fe2O3", "Fe3O4", "Fe(OH)3",
    "Al2O3", "Al(OH)3", "CaCO3", "Ca(OH)2", "CaO", "CaCl2", "CaSO4", "MgO",
    "MgCl2", "Mg(OH)2", "ZnO", "ZnCl2", "ZnSO4", "NH3", "NH4Cl", "NH4NO3",
    "NH4HCO3", "(NH4)2SO4", "Na2CO3", "NaHCO3", "Na2O2", "Na2O", "Na2SO4",
    "NaNO3", "NaClO", "NaClO3", "KCl", "KClO3", "KOH", "K2CO3", "KNO3",
    "K2SO4", "BaCl2", "BaSO4", "BaCO3", "Ba(OH)2", "AgNO3", "AgCl", "AgBr",
    "CuO", "Cu2O", "Cu(OH)2", "CuCl2", "FeCl2", "FeCl3", "FeSO4", "Fe2(SO4)3",
    "FeS", "FeS2", "Fe(OH)2", "MnO2", "C2H5OH", "CH4", "CH3OH", "C6H12O6",
    "C12H22O11", "(C6H10O5)n", "SiO2", "SO2", "SO3", "H2S", "CO", "N2O4",
    "N2O", "NO", "NO2", "P2O5", "Na3PO4", "Ca3(PO4)2", "MgSO4", "AlCl3",
    "NH4OH", "NaAlO2", "KAl(SO4)2", "KAl(SO4)2·12H2O", "H2O2", "Na2S",
})

# 惰性编译：交替按长度降序保证最长匹配优先（CO2 先于 CO，避免前缀截断）。
# 前后断言排除 \w、·、$：防止部分命中（CO 命中 CO2 内部）与重复包装 $..$ 内部。
_FORMULA_ALT = "|".join(
    re.escape(f) for f in sorted(CHEM_FORMULA_WHITELIST, key=len, reverse=True)
)
_PATTERNS = [
    re.compile(r"(?<![\w·$])(" + _FORMULA_ALT + r")(?![\w·$])"),
]

# Step 1：$...$ 内部箭头替换
_ARROW_PAIRS = (("→", r"\rightarrow"), ("⇌", r"\rightleftharpoons"))


def _subscript_latex(formula: str) -> str:
    """把数字下标转为 LaTeX：H2O → H_{2}O。`·` 后的系数数字（如 CuSO4·5H2O 的 5）不下标。"""
    out: list[str] = []
    i = 0
    while i < len(formula):
        ch = formula[i]
        if ch == "·":
            out.append(r"\cdot ")
            i += 1
            continue
        if ch.isdigit():
            if out and out[-1].endswith(r"\cdot "):
                run = ch
                while i + 1 < len(formula) and formula[i + 1].isdigit():
                    run += formula[i + 1]
                    i += 1
                out.append(run)  # 水合系数：·5H2O / ·12H2O 的系数是数字串，不转下标
            else:
                out.append("_{" + ch + "}")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def normalize_latex_arrows(text: str) -> str:
    """Step 1：仅替换 $...$ 片段内的箭头。"""
    def _fix_math(m: re.Match) -> str:
        seg = m.group(1)
        for src, dst in _ARROW_PAIRS:
            seg = seg.replace(src, dst)
        return f"${seg}$"

    return re.sub(r"\$([^$]*)\$", _fix_math, text)


def _wrap_formula(m: re.Match) -> str:
    formula = m.group(0)
    return f"${_subscript_latex(formula)}$"


def _sub_in_plain(seg: str) -> str:
    """对裸文本段做 Step 2 替换：白名单化学式 → LaTeX 下标 + $..$ 包装。"""
    if not seg:
        return seg
    out = seg
    for pat in _PATTERNS:
        out = pat.sub(_wrap_formula, out)
    return out


def normalize_chem_formulas(text: str) -> str:
    """完整标准化（Step 1 + Step 2）。幂等：已包装 $..$ 的不再处理。"""
    text = normalize_latex_arrows(text)
    return _sub_plain_segments(text)


def _sub_plain_segments(text: str) -> str:
    """按数学模式分片，仅裸文本区做 Step 2 替换。"""
    parts = re.split(r"(\$[^$]*\$|\\\(.*?\\\))", text)
    return "".join(_sub_in_plain(p) if not p.startswith("$") and not p.startswith("\\(") else p for p in parts)


# 兼容导出名（doc 30 记作 _normalize_chem_formulas）
_normalize_chem_formulas = normalize_chem_formulas
