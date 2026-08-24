/* Mock 适配层：Tab 2/3/4 后端端点未落地，返回带「演示数据」标注的假数据。
   契约字段对齐 doc 25/40 的 API 形状；后端端点落地后把 api.js 的 USE_MOCK 置 false 即切真实请求。 */
(function () {
  'use strict';

  const _mark = { _mock: true, _label: '演示数据' };

  const KNOWLEDGE = [
    '氧化还原反应', '离子反应', '化学计量', '原子结构', '化学键',
    '化学反应速率', '化学平衡', '电解质溶液', '电化学', '金属元素',
    '非金属元素', '有机化学基础', '烃', '烃的衍生物', '化学实验基本操作',
    '物质的量', '元素周期律', '配平与计算',
  ];

  const CLASSES = [
    { id: 1, name: '高一（3）班' },
    { id: 2, name: '高三（1）班' },
    { id: 3, name: '高二（5）班' },
  ];

  let _sets = [
    { id: 1, name: '高一·化学反应原理', question_count: 24, created_at: '2026-08-01' },
    { id: 2, name: '高三·一轮复习·离子反应', question_count: 36, created_at: '2026-08-10' },
    { id: 3, name: '实验专题·气体制备', question_count: 12, created_at: '2026-08-18' },
  ];
  let _setSeq = 100;

  const BANK_QUESTIONS = [
    {
      id: 101,
      content: '配平并书写化学方程式：$\\ce{H2 + O2 -> H2O}$',
      options: ['A. $\\ce{2H2 + O2 -> 2H2O}$', 'B. $\\ce{H2 + O2 -> H2O}$', 'C. $\\ce{3H2 + O2 -> H2O}$', 'D. $\\ce{2H2 + 2O2 -> 2H2O}$'],
      answer: 'A',
      analysis: '氢气与氧气反应生成水，化学计量数配平为 2、1、2。',
      knowledge_points: '氧化还原反应,配平与计算',
      difficulty: 'easy',
    },
    {
      id: 102,
      content: '下列物质中既能与盐酸反应又能与氢氧化钠溶液反应的是（ ）。',
      options: ['A. Al', 'B. MgO', 'C. Na2CO3', 'D. SiO2'],
      answer: 'A',
      analysis: '铝是两性金属，既能与酸反应又能与碱反应。',
      knowledge_points: '金属元素,离子反应',
      difficulty: 'medium',
    },
    {
      id: 103,
      content: '实验室制取氯气：$\\ce{MnO2 + 4HCl(浓) -> MnCl2 + Cl2 ^ + 2H2O}$，该反应中氧化剂是（ ）。',
      options: ['A. MnO2', 'B. HCl', 'C. Cl2', 'D. MnCl2'],
      answer: 'A',
      analysis: 'MnO2 中 Mn 化合价降低，作氧化剂。',
      knowledge_points: '非金属元素,化学实验基本操作',
      difficulty: 'medium',
    },
  ];

  const PAPERS = [
    {
      region: '全国',
      years: [
        { year: '2024', papers: [{ id: 'g24', name: '2024 全国甲卷', questions: 20 }, { id: 'g24b', name: '2024 全国乙卷', questions: 22 }] },
        { year: '2023', papers: [{ id: 'g23', name: '2023 全国卷', questions: 21 }] },
      ],
    },
    { region: '北京', years: [{ year: '2024', papers: [{ id: 'bj24', name: '2024 北京卷', questions: 19 }] }] },
    { region: '上海', years: [{ year: '2023', papers: [{ id: 'sh23', name: '2023 上海卷', questions: 20 }] }] },
  ];

  const HISTORICAL = [
    {
      id: 201,
      content: '（2024 全国甲卷）某反应：$\\ce{Fe + 2HCl -> FeCl2 + H2 ^}$，下列说法正确的是（ ）。',
      options: ['A. Fe 被还原', 'B. HCl 作氧化剂', 'C. 该反应是置换反应', 'D. FeCl2 是氧化产物'],
      answer: 'C',
      knowledge_points: '氧化还原反应,离子反应',
      source_paper: '2024 全国甲卷',
      similarity: 0.85,
    },
    {
      id: 202,
      content: '（2023 北京卷）用 NaOH 溶液吸收 CO2：$\\ce{CO2 + 2NaOH -> Na2CO3 + H2O}$，下列离子方程式书写正确的是（ ）。',
      options: [
        'A. $\\ce{CO2 + 2OH- -> CO3^2- + H2O}$',
        'B. $\\ce{CO2 + OH- -> HCO3-}$',
        'C. $\\ce{CO2 + 2H+ -> CO3^2-}$',
        'D. $\\ce{CO2 + OH- -> CO3^2- + H2O}$',
      ],
      answer: 'A',
      knowledge_points: '离子反应,非金属元素',
      source_paper: '2023 北京卷',
      similarity: 0.78,
    },
  ];

  let _exams = [
    { id: 301, name: '高一·期中考试·化学', class_name: '高一（3）班', status: 'Draft', question_count: 0, created_at: '2026-08-20' },
    { id: 302, name: '高三·一轮复习周测', class_name: '高三（1）班', status: 'AddingQuestions', question_count: 15, created_at: '2026-08-22' },
    { id: 303, name: '高二·化学平衡单元测', class_name: '高二（5）班', status: 'Published', question_count: 20, created_at: '2026-08-15' },
    { id: 304, name: '高一·氧化还原随堂测', class_name: '高一（3）班', status: 'InProgress', question_count: 10, created_at: '2026-08-18' },
  ];
  let _examSeq = 400;

  function _ok(extra) {
    return Object.assign({}, _mark, extra);
  }

  function _matchList(items, query, fields) {
    const q = (query && query.q) || '';
    if (!q) return items;
    return items.filter((it) => fields.some((f) => String(it[f]).includes(q)));
  }

  window.ChemMock = {
    handle(method, path, body, query) {
      const p = path.replace(/^\/api\//, '');

      if (p === 'knowledge/list') {
        return _ok({ items: KNOWLEDGE.map((name) => ({ name })) });
      }

      if (p === 'classes') {
        return _ok({ items: CLASSES.slice() });
      }

      if (p === 'exam-bank/exam-sets') {
        if (method === 'GET') return _ok({ items: _sets.slice() });
        if (method === 'POST') {
          const set = { id: _setSeq++, name: (body && body.name) || '未命名题库', question_count: 0, created_at: '2026-08-24' };
          _sets.unshift(set);
          return _ok({ item: set });
        }
      }
      const delSet = p.match(/^exam-bank\/exam-sets\/(\d+)$/);
      if (delSet && method === 'DELETE') {
        const idx = _sets.findIndex((s) => String(s.id) === delSet[1]);
        if (idx >= 0) _sets.splice(idx, 1);
        return _ok({ deleted: true });
      }

      if (p === 'question/search') {
        return _ok({ items: BANK_QUESTIONS.slice() });
      }

      if (p === 'exam-bank/papers') {
        return _ok({ tree: PAPERS.slice() });
      }

      if (p === 'question/historical') {
        const items = _matchList(HISTORICAL, query, ['content', 'knowledge_points', 'source_paper']);
        return _ok({ items });
      }

      if (p === 'exam/list') {
        return _ok({ items: _exams.slice() });
      }
      if (p === 'exam/create' && method === 'POST') {
        const cls = CLASSES.find((c) => String(c.id) === String(body && body.class_id));
        const exam = {
          id: _examSeq++,
          name: (body && body.name) || '未命名考试',
          class_name: cls ? cls.name : '',
          status: 'Draft',
          question_count: 0,
          created_at: '2026-08-24',
        };
        _exams.unshift(exam);
        return _ok({ item: exam });
      }
      const examAct = p.match(/^exam\/(\d+)$/);
      if (examAct) {
        const exam = _exams.find((e) => String(e.id) === examAct[1]);
        if (!exam) return _ok({ detail: '演示数据：考试不存在', error_code: 'NOT_FOUND' });
        if (method === 'DELETE') {
          const idx = _exams.indexOf(exam);
          if (idx >= 0) _exams.splice(idx, 1);
          return _ok({ deleted: true });
        }
        if (method === 'PATCH' && body) {
          if (body.status) exam.status = body.status;
          if (body.name) exam.name = body.name;
          return _ok({ item: exam });
        }
      }

      return _ok({ detail: '演示数据：未匹配 mock 端点 ' + path, error_code: 'MOCK_UNMATCHED' });
    },
  };
})();
