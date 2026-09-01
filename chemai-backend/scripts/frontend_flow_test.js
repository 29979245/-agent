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

  // 2. Mode 1 批量出题（workbench.js 的载荷形状）
  const batch = await window.ChemAPI.generate({
    knowledge_points: ['配平与计算'], difficulty: 'medium', quantity: 1,
    question_types: ['choice'], extra_requirements: '',
  });
  const q0 = (batch.questions && batch.questions[0]) || {};
  const eq = (q0.audit_report && q0.audit_report.equation_level) || {};
  check('批量生成 generated_count≥1', batch.generated_count >= 1, `count=${batch.generated_count}`);
  check('每题方程级四维徽标齐备', ['系数配平', '反应条件', '产物正确性', '分子结构'].every((k) => k in eq),
    `keys=${Object.keys(eq).join(',')}`);
  check('批量题无题目级综合分', (q0.audit_report || {}).question_level === null);

  // 3. 批准入库
  const ap = await window.ChemAPI.approve(q0.question_id);
  check('批准入库 status=approved', ap.status === 'approved', `review_flag=${ap.review_flag}`);

  // 4. 打回重生成：先 import 一道未配平 manual 题 → blocked → 重生成改配平后通过
  const un = await window.ChemAPI.importQuestion({
    content: '配平：$\\ce{H2 + O2 -> H2O}$',
    options: [], answer: '', analysis: '',
    knowledge_points: '配平与计算', difficulty: 'medium', source: 'manual',
  });
  const rg = await window.ChemAPI.regenerate(un.question_id, {
    content: '配平：$\\ce{2H2 + O2 -> 2H2O}$',
    answer: '2H2 + O2 → 2H2O', analysis: '化学计量数配平为 2、1、2。', knowledge_points: '配平与计算',
  });
  check('重生成后通过', rg.overall_status === 'passed', `status=${rg.overall_status}`);
  check('重生成 attempts 累加', (rg.audit_report.meta.regeneration_attempts || 0) >= 1,
    `attempts=${rg.audit_report.meta.regeneration_attempts}`);

  // 5. 手动录入 / OCR（只过方程式级）
  const manual = await window.ChemAPI.importQuestion({
    content: '下列物质中既能与盐酸反应又能与氢氧化钠溶液反应的是（ ）。',
    options: [], answer: '', analysis: '',
    knowledge_points: '配平与计算', difficulty: 'medium', source: 'manual',
  });
  check('manual 无题目级综合分', manual.audit_report.question_level === null, `status=${manual.overall_status}`);
  const ocr = await window.ChemAPI.importQuestion({
    content: '识别题目：配平化学方程式 $\\ce{2H2 + O2 -> 2H2O}$',
    options: [], answer: '', analysis: '',
    knowledge_points: '配平与计算', difficulty: 'medium', source: 'ocr',
  });
  check('ocr 只过方程式级', ocr.audit_report.question_level === null && ocr.overall_status === 'passed');

  // 7. Tab2/3/4 真实端点（题库/试卷/真题/班级/考试/知识点）
  const sets = await window.ChemAPI.getExamSets();
  check('Tab2 题库列表', Array.isArray(sets.items), `total=${sets.total}`);
  const papers = await window.ChemAPI.getPapers();
  check('Tab3 试卷树', Array.isArray(papers.tree));
  const hist = await window.ChemAPI.searchHistorical('全国');
  check('Tab3 真题搜索', Array.isArray(hist.items) && hist.items.length > 0, `total=${hist.total}`);
  const cls = await window.ChemAPI.getClasses();
  check('Tab4 班级列表', Array.isArray(cls.items), `count=${cls.items.length}`);
  const exams = await window.ChemAPI.getExams();
  check('Tab4 考试列表', Array.isArray(exams.items));
  const created = await window.ChemAPI.createExam({ name: 'B5联调-测试', class_id: cls.items[0].id });
  check('Tab4 创建考试', created.exam_id && created.status === 'draft', `status=${created.status}`);
  const kp = await window.ChemAPI.getKnowledge();
  check('知识点列表', kp.items && kp.items.length > 0);

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
