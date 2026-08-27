"""真题库演示数据（可重复执行）：写入 data/exam_bank/{region}/{year}/{paper}.json。

data/exam_bank/ 在 .gitignore 中，故真题内容以脚本形式入库版本控制，
执行本脚本即可在任意环境还原出可抽样的选择题库。

练习生成（daily/adaptive/variants）经 sample_questions(choice_only=True) 抽样，
只抽带 options 的选择题；本脚本全部为选项完整的化学选择题，覆盖常见知识点与难度。
"""
from __future__ import annotations

import json
from pathlib import Path

REGION = "全国卷"
YEAR = "2024"
PAPER = "高考化学"

# 每道: content / options / answer / analysis / knowledge_points / difficulty
QUESTIONS = [
    {
        "id": "q1",
        "content": "下列反应中，水既作氧化剂又作还原剂的是？",
        "options": ["2H₂O = 2H₂↑ + O₂↑", "2Na + 2H₂O = 2NaOH + H₂↑",
                    "Cl₂ + H₂O = HCl + HClO", "SO₃ + H₂O = H₂SO₄"],
        "answer": "2H₂O = 2H₂↑ + O₂↑",
        "analysis": "水分解时氢元素被还原、氧元素被氧化，水同时作氧化剂与还原剂；2Na + 2H₂O 中水只作氧化剂。",
        "knowledge_points": ["氧化还原反应"],
        "difficulty": "easy",
    },
    {
        "id": "q2",
        "content": "下列离子方程式书写正确的是？",
        "options": ["盐酸与氢氧化钠反应：H⁺ + OH⁻ = H₂O",
                    "铁与稀硫酸反应：2Fe + 6H⁺ = 2Fe³⁺ + 3H₂↑",
                    "碳酸钙与盐酸反应：CO₃²⁻ + 2H⁺ = CO₂↑ + H₂O",
                    "铜与硝酸银反应：Cu + Ag⁺ = Cu²⁺ + Ag"],
        "answer": "盐酸与氢氧化钠反应：H⁺ + OH⁻ = H₂O",
        "analysis": "铁与稀硫酸反应生成 Fe²⁺ 而非 Fe³⁺；碳酸钙难溶不能拆写为 CO₃²⁻；电荷不守恒，应为 Cu + 2Ag⁺ = Cu²⁺ + 2Ag。",
        "knowledge_points": ["离子反应"],
        "difficulty": "easy",
    },
    {
        "id": "q3",
        "content": "对反应 N₂ + 3H₂ ⇌ 2NH₃，下列措施能提高 H₂ 转化率的是？",
        "options": ["增大压强", "升高温度", "加入催化剂", "恒容充入惰性气体"],
        "answer": "增大压强",
        "analysis": "该反应是气体分子数减小的反应，增大压强平衡向正方向移动，H₂ 转化率提高；升高温度平衡向吸热的逆方向移动，催化剂不改变化学平衡。",
        "knowledge_points": ["化学平衡"],
        "difficulty": "medium",
    },
    {
        "id": "q4",
        "content": "在无色溶液中，下列离子能大量共存的是？",
        "options": ["K⁺、Na⁺、SO₄²⁻、MnO₄⁻", "H⁺、Ba²⁺、Cl⁻、CO₃²⁻",
                    "Na⁺、Ca²⁺、Cl⁻、NO₃⁻", "Fe³⁺、K⁺、Cl⁻、NO₃⁻"],
        "answer": "Na⁺、Ca²⁺、Cl⁻、NO₃⁻",
        "analysis": "MnO₄⁻ 紫红色、Fe³⁺ 黄色均不符合无色要求；H⁺ 与 CO₃²⁻ 不能大量共存。",
        "knowledge_points": ["离子反应"],
        "difficulty": "easy",
    },
    {
        "id": "q5",
        "content": "相同状况下，下列气体密度最大的是？",
        "options": ["H₂", "O₂", "Cl₂", "CO₂"],
        "answer": "Cl₂",
        "analysis": "同温同压下气体密度与摩尔质量成正比，Cl₂ 摩尔质量 71 g/mol 最大，故密度最大。",
        "knowledge_points": ["物质的量"],
        "difficulty": "easy",
    },
    {
        "id": "q6",
        "content": "将铁片投入下列溶液中，溶液质量会减小的是？",
        "options": ["CuSO₄ 溶液", "稀硫酸", "Fe₂(SO₄)₃ 溶液", "稀盐酸"],
        "answer": "CuSO₄ 溶液",
        "analysis": "Fe 置换 Cu，进入溶液的 Fe（56）少于析出的 Cu（64），溶液质量减小；与酸反应放出气体，Fe 进入使溶液质量增加。",
        "knowledge_points": ["金属及其化合物"],
        "difficulty": "medium",
    },
    {
        "id": "q7",
        "content": "在反应 KMnO₄ + HCl → KCl + MnCl₂ + Cl₂↑ + H₂O 中，氧化产物是？",
        "options": ["KCl", "MnCl₂", "Cl₂", "H₂O"],
        "answer": "Cl₂",
        "analysis": "HCl 中的 Cl⁻ 被氧化为 Cl₂，Cl₂ 是氧化产物；KMnO₄ 中 Mn 被还原为 MnCl₂，是还原产物。",
        "knowledge_points": ["氧化还原反应", "配平与计算"],
        "difficulty": "medium",
    },
    {
        "id": "q8",
        "content": "用 $\\ce{Na2CO3}$ 与稀盐酸反应制取 $\\ce{CO2}$，下列装置合理的是？",
        "options": ["启普发生器", "简易气体发生装置（锥形瓶+分液漏斗）",
                    "酒精灯加热圆底烧瓶", "蒸馏装置"],
        "answer": "简易气体发生装置（锥形瓶+分液漏斗）",
        "analysis": "块状固体与液体不加热反应常用简易气体发生装置；启普发生器适用于块状固体与液体不加热反应，Na₂CO₃ 为粉末状不适用。",
        "knowledge_points": ["常见气体的制备"],
        "difficulty": "medium",
    },
    {
        "id": "q9",
        "content": "下列物质中，属于电解质的是？",
        "options": ["铜", "蔗糖", "氯化钠固体", "盐酸"],
        "answer": "氯化钠固体",
        "analysis": "电解质指在水溶液或熔融状态下能导电的化合物；铜是单质，蔗糖是非电解质，盐酸是混合物。",
        "knowledge_points": ["电解质溶液"],
        "difficulty": "easy",
    },
    {
        "id": "q10",
        "content": "下列各组原子半径大小比较正确的是？",
        "options": ["Na > Mg > Al", "O > F > Cl", "Li < Na < K",
                    "S > Cl > Na"],
        "answer": "Na > Mg > Al",
        "analysis": "同周期从左到右原子半径减小，Na > Mg > Al 正确；同主族自上而下半径增大，O < Cl、F < Cl。",
        "knowledge_points": ["元素周期律"],
        "difficulty": "easy",
    },
    {
        "id": "q11",
        "content": "下列反应属于氧化还原反应的是？",
        "options": ["CO₂ + H₂O = H₂CO₃", "NaOH + HCl = NaCl + H₂O",
                    "2H₂O₂ = 2H₂O + O₂↑", "CaCO₃ = CaO + CO₂↑"],
        "answer": "2H₂O₂ = 2H₂O + O₂↑",
        "analysis": "H₂O₂ 分解中 O 元素化合价由 -1 变为 0 和 -2，发生氧化还原反应；其余反应各元素化合价均不变。",
        "knowledge_points": ["氧化还原反应"],
        "difficulty": "easy",
    },
    {
        "id": "q12",
        "content": "原电池中，下列说法正确的是？",
        "options": ["负极发生还原反应", "电子由负极经导线流向正极",
                    "电解质溶液中阳离子向负极移动", "正极是较活泼的金属"],
        "answer": "电子由负极经导线流向正极",
        "analysis": "原电池负极失电子发生氧化反应，电子经外电路流向正极；阳离子向正极移动，较活泼金属作负极。",
        "knowledge_points": ["电化学"],
        "difficulty": "medium",
    },
]


def main() -> int:
    base = Path(__file__).resolve().parent.parent / "data" / "exam_bank"
    out = base / REGION / str(YEAR)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"title": PAPER, "questions": QUESTIONS}
    target = out / f"{PAPER}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"真题库演示数据就绪：{target}  选择题 {len(QUESTIONS)} 道")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
