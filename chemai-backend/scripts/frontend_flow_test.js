/* B5 联调：用前端真实 JS 模块（mock.js/api.js/auth.js）驱动核心用户流程。
   在 node 里给 window/localStorage/location 打桩，让前端代码原样跑在真实后端上，
   专抓「前端期望 ↔ 后端返回」契约不一致。运行前需启动 uvicorn。 */
'use strict';

const assert = require('assert');

// ---- 浏览器环境打桩 ----
const store = {};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
global.location = { href: '', replace(url) { this.href = url; } };
global.window = globalThis;

require('../frontend/pages/js/mock.js');
require('../frontend/pages/js/api.js');
require('../frontend/pages/js/auth.js');

const ok = [];
const T = (c) => (c ? '✓' : '✗');
function check(name, cond, extra) {
  ok.push(!!cond);
  console.log(`${T(cond)} ${name}${extra ? '  ' + extra : ''}`);
}

async function main() {
  // 1. 教师登录（走前端 ChemAuth.login → ChemAPI.login 真实请求）
  const data = await window.ChemAuth.login('teacher_demo', 'demo123');
  check('教师登录 role=teacher', data.role === 'teacher', `role=${data.role}`);
  const token = localStorage.getItem('chemai_token');
  check('token 已写入 localStorage', !!token);

  // 2. AI 平衡式出题（workbench.js 的载荷形状）
  const payload = {
    content: '配平并判断反应：$\\ce{2H2 + O2 -> 2H2O}$，该反应属于哪种基本反应类型？',
    options: [], answer: '', analysis: '',
    knowledge_points: '配平与计算', difficulty: 'medium', source: 'ai',
  };
  const r = await window.ChemAPI.generate(payload);
  const eq = (r.audit_report && r.audit_report.equation_level) || {};
  const ql = (r.audit_report && r.audit_report.question_level) || null;
  check('AI 出题整体 passed', r.overall_status === 'passed', `status=${r.overall_status}`);
  check('方程式级四维徽标齐备', ['系数配平', '反应条件', '产物正确性', '分子结构'].every((k) => k in eq),
    `keys=${Object.keys(eq).join(',')}`);
  check('question_level=null(llm_unconfigured)', ql === null && eq.overall_status === 'passed');

  // 3. 批准入库
  const ap = await window.ChemAPI.approve(r.question_id);
  check('批准入库 status=approved', ap.status === 'approved', `review_flag=${ap.review_flag}`);

  // 4. AI 未配平 → 重生成耗尽
  const rb = await window.ChemAPI.generate({
    ...payload, content: '配平：$\\ce{H2 + O2 -> H2O}$',
  });
  check('未配平 → generation_failed', rb.generation_failed === true && rb.overall_status === 'blocked',
    `failed=${rb.generation_failed} status=${rb.overall_status}`);

  // 5. 打回重生成 → 改配平后通过
  const rg = await window.ChemAPI.regenerate(rb.question_id, {
    content: '配平：$\\ce{2H2 + O2 -> 2H2O}$',
    answer: '2H2 + O2 → 2H2O', analysis: '化学计量数配平为 2、1、2。', knowledge_points: '配平与计算',
  });
  check('重生成后通过', rg.overall_status === 'passed', `status=${rg.overall_status}`);
  check('重生成 attempts 累加', (rg.audit_report.meta.regeneration_attempts || 0) >= 1,
    `attempts=${rg.audit_report.meta.regeneration_attempts}`);

  // 6. 手动录入 / OCR（只过方程式级）
  const manual = await window.ChemAPI.generate({ ...payload, source: 'manual',
    content: '下列物质中既能与盐酸反应又能与氢氧化钠溶液反应的是（ ）。' });
  check('manual 无题目级综合分', manual.audit_report.question_level === null, `status=${manual.overall_status}`);
  const ocr = await window.ChemAPI.generate({ ...payload, source: 'ocr',
    content: '识别题目：配平化学方程式 $\\ce{2H2 + O2 -> 2H2O}$' });
  check('ocr 只过方程式级', ocr.audit_report.question_level === null && ocr.overall_status === 'passed');

  // 7. Tab2/3/4 mock 端点 → 带「演示数据」标注
  const sets = await window.ChemAPI.getExamSets();
  check('Tab2 题库列表 mock 标注', sets._mock === true && Array.isArray(sets.items), `count=${sets.items.length}`);
  const papers = await window.ChemAPI.getPapers();
  check('Tab3 试卷树 mock 标注', papers._mock === true && Array.isArray(papers.tree));
  const hist = await window.ChemAPI.searchHistorical('全国');
  check('Tab3 真题搜索 mock', hist._mock === true);
  const cls = await window.ChemAPI.getClasses();
  check('Tab4 班级 mock', cls._mock === true, `count=${cls.items.length}`);
  const exams = await window.ChemAPI.getExams();
  check('Tab4 考试列表 mock', exams._mock === true);
  const created = await window.ChemAPI.createExam({ name: 'B5联调-测试', class_id: cls.items[0].id });
  check('Tab4 创建考试 mock', created._mock === true && created.item && created.item.name === 'B5联调-测试');
  const kp = await window.ChemAPI.getKnowledge();
  check('知识点列表 mock', kp._mock === true && kp.items.length > 0);

  // 8. 学生登录 → isTeacherLike 为 false（登录页会拦截）
  const stu = await window.ChemAuth.login('student_demo', 'demo123');
  check('学生登录 role=student', stu.role === 'student', `role=${stu.role}`);
  check('学生非教师 → 工作台拦截', window.ChemAuth.isTeacherLike() === false);

  const passed = ok.filter(Boolean).length;
  console.log(`\n结果：${passed}/${ok.length} 项通过`);
  if (!ok.every(Boolean)) process.exit(1);
  console.log('全部通过 ✓');
}

main().catch((e) => {
  console.error('联调失败:', e.message);
  process.exit(1);
});
