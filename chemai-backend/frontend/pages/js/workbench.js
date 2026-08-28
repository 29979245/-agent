/* 出题工作台 Vue 根组件（Vue 3 全局构建，模板在 exam-v2.html 内）。
   四 Tab：workbench / bank / history / exams；Tab 1 内三子模式 ai / manual / ocr。
   数据全部来自真实后端（exam-bank / exam / classes / historical）。 */
(function () {
  'use strict';

  const { createApp } = Vue;

  // 知识点切分：兼容中英文逗号、去空白与空项（Tab1 卡片与 Tab2/3 列表共用）
  window.splitKps = function splitKps(text) {
    return String(text || '')
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
  };

  const SAMPLE_AI_CONTENT =
    '配平并判断反应：$\\ce{2H2 + O2 -> 2H2O}$，该反应属于哪种基本反应类型？';

  const STATUS_TEXT = {
    passed: '通过',
    warning: '警告',
    blocked: '阻断',
    approved: '已入库',
  };

  const AUDIT_STATUS_TEXT = {
    passed: '已通过',
    warning: '有警告',
    blocked: '已阻断',
    approved: '已入库',
  };

  // 后端六态：draft / published / in_progress / grading / completed / archived
  const EXAM_STATUS_LABEL = {
    draft: '草稿',
    published: '已发布',
    in_progress: '进行中',
    grading: '阅卷中',
    completed: '已完成',
    archived: '已归档',
  };

  const DIFFICULTY_LABEL = {
    easy: '容易',
    medium: '中等',
    hard: '困难',
    competition: '竞赛',
  };

  // 净化 marked 输出的 HTML：移除 script/style/iframe 等危险标签与事件属性，
  // 阻断存储型 XSS（题目正文/学生答案经 v-html 注入）。KaTeX 公式不受影响：
  // v-katex 指令在 v-html 绑定后按文本节点扫描 $..$ 分隔符，净化仅作用于标记输出。
  function sanitizeHtml(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('script, style, iframe, object, embed, link, meta, form').forEach((n) => n.remove());
    doc.querySelectorAll('*').forEach((n) => {
      Array.from(n.attributes).forEach((attr) => {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on')) { n.removeAttribute(attr.name); return; }
        const val = attr.value.trim().toLowerCase();
        if ((name === 'href' || name === 'src') && (val.startsWith('javascript:') || val.startsWith('data:'))) {
          n.removeAttribute(attr.name);
        }
      });
    });
    return doc.body ? doc.body.innerHTML : '';
  }

  function renderMarkdown(text) {
    if (!text) return '';
    const s = String(text);
    if (window.marked) {
      try {
        const html = window.marked.parse ? window.marked.parse(s) : window.marked(s);
        return sanitizeHtml(html);
      } catch (err) {
        /* fallthrough */
      }
    }
    return sanitizeHtml(s.replace(/\n/g, '<br>'));
  }

  // 组装展示卡片：提交载荷 + 后端响应（generate 不返回内容，需用载荷补全）
  function makeCard(payload, resp) {
    const report = (resp && resp.audit_report) || {};
    return {
      question_id: resp && resp.question_id,
      content: payload.content || '',
      options: payload.options || [],
      answer: payload.answer || '',
      analysis: payload.analysis || '',
      knowledge_points: payload.knowledge_points || '',
      difficulty: payload.difficulty || 'medium',
      source: payload.source,
      audit_report: report,
      overall_status: (resp && resp.overall_status) || report.overall_status || 'passed',
      generation_failed: !!(resp && resp.generation_failed),
      approved: false,
      review_flag: false,
      trap_note: '',
      source_label: '',
    };
  }

  // 派生易错提示：取审核报告最严重（blocked/warning）维度的 evidence
  function deriveTrap(report) {
    const eq = (report && report.equation_level) || {};
    const dims = [];
    for (const key of Object.keys(eq)) {
      if (key === 'overall_status' || key === 'note') continue;
      const v = eq[key];
      if (v && v.status && v.evidence) dims.push({ label: key, status: v.status, evidence: v.evidence });
    }
    const worst = ['blocked', 'warning']
      .map((s) => dims.find((d) => d.status === s))
      .find(Boolean);
    return worst ? '易错：' + worst.label + ' — ' + worst.evidence : '';
  }

  const QuestionCard = {
    name: 'QuestionCard',
    props: { q: { type: Object, required: true } },
    emits: ['approve', 'regenerate'],
    computed: {
      eqDims() {
        const eq = (this.q.audit_report && this.q.audit_report.equation_level) || {};
        return Object.keys(eq)
          .filter((k) => k !== 'overall_status' && k !== 'note')
          .map((k) => ({ label: k, status: eq[k].status, evidence: eq[k].evidence }));
      },
      ql() {
        return (this.q.audit_report && this.q.audit_report.question_level) || null;
      },
      qlDims() {
        if (!this.ql) return [];
        return Object.keys(this.ql)
          .filter((k) => k !== 'composite' && k !== 'status' && k !== 'reason')
          .map((k) => ({ label: k, score: this.ql[k].score, evidence: this.ql[k].evidence }));
      },
      eqNote() {
        const eq = (this.q.audit_report && this.q.audit_report.equation_level) || {};
        return eq.note || '';
      },
      llmUnconfigured() {
        const meta = (this.q.audit_report && this.q.audit_report.meta) || {};
        return meta.review_note === 'llm_unconfigured';
      },
      attempts() {
        const meta = (this.q.audit_report && this.q.audit_report.meta) || {};
        return meta.regeneration_attempts || 0;
      },
      kpList() {
        return window.splitKps(this.q.knowledge_points);
      },
      showApprove() {
        return !this.q.approved && (this.q.overall_status === 'passed' || this.q.overall_status === 'warning');
      },
      showRegenerate() {
        return !this.q.approved && (this.q.overall_status === 'warning' || this.q.overall_status === 'blocked');
      },
    },
    methods: {
      renderMarkdown,
      statusTextOf(s) {
        return STATUS_TEXT[s] || s;
      },
      eqOverall() {
        const eq = (this.q.audit_report && this.q.audit_report.equation_level) || {};
        return STATUS_TEXT[eq.overall_status] || '';
      },
    },
    template: '#tpl-question-card',
  };

  const app = createApp({
    components: { 'question-card': QuestionCard },
    data() {
      return {
        tab: 'workbench',
        user: (window.ChemAuth && window.ChemAuth.getUser()) || {},
        // Tab 1
        mode: 'ai',
        aiKpQuery: '',
        ai: {
          types: [],
          difficulty: 'medium',
          kps: [],
          content: SAMPLE_AI_CONTENT,
          source_label: '',
        },
        manual: {
          content: '',
          optionsText: '',
          answer: '',
          analysis: '',
          kps: '',
          difficulty: 'medium',
        },
        ocr: { fileName: '', previewUrl: '' },
        knowledge: [],
        questions: [],
        generating: false,
        // Tab 1 保存到题库文件夹（批准入库时导入）
        bankSaveSetId: '',
        // Tab 2
        examSets: [],
        bankAllSets: [],
        selectedSetId: null,
        bankMeta: null,
        bankQuestions: [],
        selectedBank: [],
        bankRegion: '',
        bankYear: '',
        bankRegions: [],
        bankYears: [],
        bankSetPage: 1,
        bankSetPageSize: 20,
        bankSetTotal: 0,
        bankImportSetId: '',
        // Tab 3
        paperTree: [],
        openedRegions: {},
        openedYears: {},
        historyQuery: '',
        historyResults: [],
        // Tab 4
        classes: [],
        exams: [],
        newExam: { name: '', class_id: '' },
        examPage: 1,
        examPageSize: 20,
        examTotal: 0,
        edit: { show: false, exam: null, questions: [] },
        editBank: { set: '', sets: [], questions: [], selected: [] },
        editHist: { query: '', results: [], selected: [] },
        exportModal: { show: false, exam: null, format: 'docx', with_answers: false },
        resultsModal: { show: false, exam: null, data: null },
        picker: { show: false, ids: [], refs: [], exams: [] },
        // modal + toast
        regen: { show: false, question: null, content: '', answer: '', analysis: '', knowledge_points: '' },
        toastState: { msg: '', isErr: false, visible: false },
      };
    },
    computed: {
      typeChips() {
        return [
          { key: 'choice', label: '选择题' },
          { key: 'fill', label: '填空题' },
          { key: 'calc', label: '计算题' },
          { key: 'experiment', label: '实验题' },
          { key: 'infer', label: '推断题' },
        ];
      },
      filteredKps() {
        if (!this.aiKpQuery) return this.knowledge;
        return this.knowledge.filter((k) => k.name.includes(this.aiKpQuery));
      },
      ownSets() {
        return this.bankAllSets.filter((s) => !s.is_preset);
      },
    },
    mounted() {
      if (!window.ChemAuth.requireAuth()) return;
      if (!window.ChemAuth.isTeacherLike()) { location.href = 'forbidden.html?side=teacher'; return; }
      this.loadKnowledge();
      this.loadExamSets();
      this.loadPapers();
      this.loadClasses();
      this.loadExams();
    },
    methods: {
      renderMarkdown,
      examStatusLabel(s) {
        return EXAM_STATUS_LABEL[s] || s;
      },
      difficultyLabel(d) {
        return DIFFICULTY_LABEL[d] || d || '中等';
      },
      auditStatusText(s) {
        return AUDIT_STATUS_TEXT[s] || s;
      },
      // 题型推断：有选项→选择题，否则按题干关键词
      typeLabel(q) {
        if (q.options && q.options.length) return '选择题';
        const c = q.content || '';
        if (c.includes('配平') || c.includes('计算')) return '计算题';
        if (c.includes('实验')) return '实验题';
        return '填空题';
      },
      toast(msg, isErr) {
        this.toastState = { msg, isErr: !!isErr, visible: true };
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => {
          this.toastState.visible = false;
        }, 3200);
      },
      logout() {
        window.ChemAuth.logout();
        location.href = 'login.html';
      },

      // ---- Tab 1：三子模式 ----
      toggleType(key) {
        const i = this.ai.types.indexOf(key);
        if (i >= 0) this.ai.types.splice(i, 1);
        else this.ai.types.push(key);
      },
      toggleKp(name) {
        const i = this.ai.kps.indexOf(name);
        if (i >= 0) this.ai.kps.splice(i, 1);
        else this.ai.kps.push(name);
      },
      handleOcrFile(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        this.ocr.fileName = file.name;
        this.ocr.previewUrl = URL.createObjectURL(file);
      },

      async doGenerate(payload, extra) {
        this.generating = true;
        try {
          const resp = await window.ChemAPI.generate(payload);
          const card = makeCard(payload, resp);
          if (extra && extra.source_label) card.source_label = extra.source_label;
          card.trap_note = deriveTrap(card.audit_report);
          this.questions.unshift(card);
          this.toast('出题完成：' + (STATUS_TEXT[card.overall_status] || card.overall_status), false);
        } catch (err) {
          this.toast((err && err.message) || '出题失败', true);
        } finally {
          this.generating = false;
        }
      },
      async aiGenerate() {
        if (!this.ai.content.trim()) {
          this.toast('请填写题目内容', true);
          return;
        }
        const payload = {
          content: this.ai.content,
          options: [],
          answer: '',
          analysis: '',
          knowledge_points: this.ai.kps.join(','),
          difficulty: this.ai.difficulty,
          source: 'ai',
        };
        await this.doGenerate(payload, { source_label: this.ai.source_label });
        if (this.ai.source_label) this.ai.source_label = '';
      },
      async manualAdd() {
        if (!this.manual.content.trim()) {
          this.toast('请填写题干', true);
          return;
        }
        const options = this.manual.optionsText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean);
        const payload = {
          content: this.manual.content,
          options,
          answer: this.manual.answer,
          analysis: this.manual.analysis,
          knowledge_points: this.manual.kps,
          difficulty: this.manual.difficulty,
          source: 'manual',
        };
        await this.doGenerate(payload);
      },
      async ocrRecognize() {
        if (!this.ocr.fileName) {
          this.toast('请先上传题目图片', true);
          return;
        }
        // 演示：模拟识别出一道含方程式的题目，source=ocr 只过方程式级硬闸
        const payload = {
          content: '识别题目：配平化学方程式 $\\ce{2H2 + O2 -> 2H2O}$',
          options: [],
          answer: '',
          analysis: '',
          knowledge_points: '配平与计算',
          difficulty: 'medium',
          source: 'ocr',
        };
        await this.doGenerate(payload);
      },

      // ---- 卡片操作 ----
      async approveQuestion(q) {
        // 方案1：批准即入库——必须先选保存的题库文件夹，避免产生悬空题
        if (!this.bankSaveSetId) {
          this.toast('请先选择保存的题库文件夹（批准即入库）', true);
          return;
        }
        // 先入库（失败则题目仍可见可重试），再批准打标记：保证无"已批准但不可达"的题
        try {
          const imp = await window.ChemAPI.importQuestions(this.bankSaveSetId, [q.question_id]);
          const reason = (imp.skipped_reasons || {})[String(q.question_id)] || '';
          // 审核未通过的题不可批准（blocked 后端批准也会 400），终止并说明
          if (imp.added === 0 && reason.indexOf('审核未通过') === 0) {
            this.toast('该题未通过审核，未批准入库（' + reason + '）', true);
            return;
          }
          const resp = await window.ChemAPI.approve(q.question_id);
          q.approved = true;
          q.review_flag = !!resp.review_flag;
          await this.loadExamSets();
          const saved = imp.added > 0
            ? '，已保存到题库文件夹（新增 ' + imp.added + '）'
            : '（该题已在文件夹中）';
          this.toast(
            (q.review_flag ? '已批准入库（带复核标记）' : '已批准入库') + saved,
            false
          );
        } catch (err) {
          this.toast((err && err.message) || '批准失败', true);
        }
      },
      openRegen(q) {
        this.regen.show = true;
        this.regen.question = q;
        this.regen.content = q.content;
        this.regen.answer = q.answer;
        this.regen.analysis = q.analysis;
        this.regen.knowledge_points = q.knowledge_points;
      },
      async submitRegen() {
        const q = this.regen.question;
        if (!q) return;
        try {
          const resp = await window.ChemAPI.regenerate(q.question_id, {
            content: this.regen.content,
            answer: this.regen.answer,
            analysis: this.regen.analysis,
            knowledge_points: this.regen.knowledge_points,
          });
          const regenMeta = (resp.audit_report && resp.audit_report.meta) || {};
          Object.assign(q, {
            content: this.regen.content,
            answer: this.regen.answer,
            analysis: this.regen.analysis,
            knowledge_points: this.regen.knowledge_points,
            audit_report: resp.audit_report,
            overall_status: resp.overall_status,
            generation_failed: !!regenMeta.generation_failed,
          });
          q.trap_note = deriveTrap(q.audit_report);
          this.regen.show = false;
          this.toast('已重新生成并重新审核：' + (STATUS_TEXT[resp.overall_status] || resp.overall_status), false);
        } catch (err) {
          this.toast((err && err.message) || '重生成失败', true);
        }
      },

      // ---- Tab 2：题库管理 ----
      async loadKnowledge() {
        try {
          const d = await window.ChemAPI.getKnowledge();
          this.knowledge = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载知识点失败', true);
        }
      },
      // 目录筛选选项：一次性拉取当前教师的全部文件夹（预设置顶 + 我的）
      async loadBankFilters() {
        const d = await window.ChemAPI.getExamSets({
          teacher_id: this.user.user_id,
          page: 1,
          page_size: 100,
        });
        this.bankAllSets = (d && d.items) || [];
        this.bankRegions = Array.from(new Set(this.bankAllSets.map((s) => s.region).filter(Boolean))).sort();
        this.bankYears = Array.from(new Set(this.bankAllSets.map((s) => s.year).filter(Boolean))).sort().reverse();
      },
      async loadExamSets() {
        try {
          await this.loadBankFilters();
          const d = await window.ChemAPI.getExamSets({
            teacher_id: this.user.user_id,
            region: this.bankRegion,
            year: this.bankYear || undefined,
            page: this.bankSetPage,
            page_size: this.bankSetPageSize,
          });
          this.examSets = (d && d.items) || [];
          this.bankSetTotal = (d && d.total) || 0;
          if (this.selectedSetId == null) this.selectedSetId = this.examSets.length ? this.examSets[0].id : null;
          if (this.selectedSetId != null) await this.selectSet(this.selectedSetId);
        } catch (err) {
          this.toast((err && err.message) || '加载题库失败', true);
        }
      },
      setBankFilter(which, val) {
        if (which === 'region') this.bankRegion = val;
        if (which === 'year') this.bankYear = val;
        this.bankSetPage = 1;
        this.loadExamSets();
      },
      setBankPage(p) {
        this.bankSetPage = p;
        this.loadExamSets();
      },
      async createExamSet() {
        const name = window.prompt('新建题库名称：');
        if (!name || !name.trim()) return;
        try {
          await window.ChemAPI.createExamSet({ name: name.trim() });
          this.selectedSetId = null;
          await this.loadExamSets();
          this.toast('题库已创建', false);
        } catch (err) {
          this.toast((err && err.message) || '创建题库失败', true);
        }
      },
      async deleteExamSet(id) {
        if (id == null) return;
        if (!window.confirm('确认删除该题库？')) return;
        try {
          await window.ChemAPI.deleteExamSet(id);
          this.selectedSetId = null;
          this.bankQuestions = [];
          this.bankMeta = null;
          await this.loadExamSets();
          this.toast('题库已删除', false);
        } catch (err) {
          this.toast((err && err.message) || '删除题库失败', true);
        }
      },
      async selectSet(id) {
        this.selectedSetId = id;
        this.selectedBank = [];
        try {
          const d = await window.ChemAPI.getExamSetDetail(id);
          this.bankMeta = { id: d.id, name: d.name, region: d.region, year: d.year, question_count: d.question_count, is_preset: d.is_preset };
          this.bankQuestions = (d && d.questions) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载题目失败', true);
        }
      },
      toggleBank(q) {
        const i = this.selectedBank.indexOf(q.question_id);
        if (i >= 0) this.selectedBank.splice(i, 1);
        else this.selectedBank.push(q.question_id);
      },
      async batchImportToFolder() {
        if (!this.selectedBank.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        if (!this.bankImportSetId) {
          this.toast('请选择导入目标文件夹', true);
          return;
        }
        try {
          const res = await window.ChemAPI.importQuestions(this.bankImportSetId, this.selectedBank);
          const target = this.bankImportSetId;
          this.selectedBank = [];
          this.toast('已导入 ' + res.added + ' 题，跳过 ' + res.skipped + ' 题', false);
          await this.loadExamSets();
          if (String(target) === String(this.selectedSetId)) await this.selectSet(target);
        } catch (err) {
          this.toast((err && err.message) || '批量导入失败', true);
        }
      },
      async batchAddToExam() {
        if (!this.selectedBank.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        this.openPicker({ ids: this.selectedBank.slice() });
      },
      async batchRemoveFromBank() {
        if (!this.selectedBank.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        if (!window.confirm('确认从题库移除 ' + this.selectedBank.length + ' 道题？（题目实体保留，仅解除关联）')) return;
        const ids = this.selectedBank.slice();
        try {
          for (const qid of ids) {
            await window.ChemAPI.removeBankQuestion(this.selectedSetId, qid);
          }
          this.selectedBank = [];
          await this.selectSet(this.selectedSetId);
          await this.loadExamSets();
          this.toast('已从题库移除 ' + ids.length + ' 题', false);
        } catch (err) {
          this.toast((err && err.message) || '移除失败', true);
        }
      },

      // ---- Tab 3：历史真题库 ----
      async loadPapers() {
        try {
          const d = await window.ChemAPI.getPapers();
          this.paperTree = (d && d.tree) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载试卷目录失败', true);
        }
      },
      toggleRegion(r) {
        this.openedRegions[r.region] = !this.openedRegions[r.region];
      },
      toggleYear(r, y) {
        const key = r.region + '/' + y.year;
        this.openedYears[key] = !this.openedYears[key];
      },
      async searchHistory() {
        try {
          const d = await window.ChemAPI.searchHistorical({ keyword: this.historyQuery });
          this.historyResults = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '搜索失败', true);
        }
      },
      addVariantFromHistory(h) {
        this.mode = 'ai';
        this.ai.source_label = '基于 ' + h.paper + ' 变体生成';
        this.tab = 'workbench';
        this.toast('已设为变体蓝本，回到「出题工作台」生成变体题', false);
      },
      addHistoryToExam(h) {
        this.openPicker({ refs: [h.ref_id] });
      },

      // ---- 加入考试：目标考试选择弹窗 ----
      async openPicker({ ids = [], refs = [] }) {
        try {
          const d = await window.ChemAPI.getExams({ page: 1, page_size: 100 });
          const exams = (d && d.items) || [];
          this.picker.exams = exams.filter((e) => e.status === 'draft');
          this.picker.ids = ids;
          this.picker.refs = refs;
          this.picker.show = true;
        } catch (err) {
          this.toast((err && err.message) || '加载考试列表失败', true);
        }
      },
      async confirmPickExam(exam) {
        try {
          const ids = this.picker.ids.concat(this.picker.refs);
          const res = await window.ChemAPI.addExamQuestions(exam.exam_id, ids);
          this.picker.show = false;
          this.picker.ids = [];
          this.picker.refs = [];
          this.toast('已加入考试「' + exam.name + '」：新增 ' + res.added + ' 跳过 ' + res.skipped, false);
        } catch (err) {
          this.toast((err && err.message) || '加入考试失败', true);
        }
      },

      // ---- Tab 4：考试列表 ----
      async loadClasses() {
        try {
          const d = await window.ChemAPI.getClasses();
          this.classes = (d && d.items) || [];
          if (this.classes.length && this.newExam.class_id === '') this.newExam.class_id = this.classes[0].id;
        } catch (err) {
          this.toast((err && err.message) || '加载班级失败', true);
        }
      },
      async loadExams() {
        try {
          const d = await window.ChemAPI.getExams({ page: this.examPage, page_size: this.examPageSize });
          this.exams = (d && d.items) || [];
          this.examTotal = (d && d.total) || 0;
        } catch (err) {
          this.toast((err && err.message) || '加载考试列表失败', true);
        }
      },
      setExamPage(p) {
        this.examPage = p;
        this.loadExams();
      },
      async createExam() {
        if (!this.newExam.name.trim()) {
          this.toast('请填写考试名称', true);
          return;
        }
        try {
          await window.ChemAPI.createExam({
            name: this.newExam.name.trim(),
            class_id: this.newExam.class_id,
          });
          this.newExam = { name: '', class_id: this.classes.length ? this.classes[0].id : '' };
          await this.loadExams();
          this.toast('考试已创建', false);
        } catch (err) {
          this.toast((err && err.message) || '创建考试失败', true);
        }
      },
      // 每状态操作矩阵
      examActions(e) {
        const matrix = {
          draft: [
            { key: 'edit', label: '编辑' },
            { key: 'publish', label: '发布' },
            { key: 'delete', label: '删除' },
          ],
          published: [
            { key: 'grade', label: '开始阅卷' },
            { key: 'export', label: '导出' },
          ],
          in_progress: [
            { key: 'grade', label: '开始阅卷' },
            { key: 'export', label: '导出' },
          ],
          grading: [
            { key: 'finalize', label: '完成统计' },
            { key: 'export', label: '导出' },
          ],
          completed: [
            { key: 'archive', label: '归档' },
            { key: 'export', label: '导出' },
            { key: 'results', label: '看结果' },
          ],
          archived: [{ key: 'export', label: '导出' }],
        };
        return matrix[e.status] || [];
      },
      btnClassFor(a) {
        return a.key === 'delete' ? 'btn-danger-outline' : 'btn-outline';
      },
      async examAction(exam, key) {
        try {
          if (key === 'edit') {
            await this.openEditDrawer(exam);
            return;
          }
          if (key === 'export') {
            this.exportModal = { show: true, exam, format: 'docx', with_answers: false };
            return;
          }
          if (key === 'results') {
            await this.openResults(exam);
            return;
          }
          if (key === 'delete') {
            if (!window.confirm('确认删除考试「' + exam.name + '」？')) return;
            await window.ChemAPI.deleteExam(exam.exam_id);
            this.toast('考试已删除', false);
            await this.loadExams();
            return;
          }
          const calls = {
            publish: () => window.ChemAPI.publishExam(exam.exam_id),
            grade: () => window.ChemAPI.startGrading(exam.exam_id),
            finalize: () => window.ChemAPI.finalizeExam(exam.exam_id),
            archive: () => window.ChemAPI.archiveExam(exam.exam_id),
          };
          const resp = await calls[key]();
          this.toast('操作成功：' + (EXAM_STATUS_LABEL[resp.status] || resp.status), false);
          await this.loadExams();
        } catch (err) {
          this.toast((err && err.message) || '操作失败', true);
        }
      },

      // ---- 编辑抽屉 ----
      async openEditDrawer(exam) {
        this.edit = { show: true, exam, questions: [] };
        this.editBank = { set: '', sets: [], questions: [], selected: [] };
        this.editHist = { query: '', results: [], selected: [] };
        try {
          const d = await window.ChemAPI.getExamQuestions(exam.exam_id);
          this.edit.questions = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载考试题目失败', true);
        }
      },
      async removeEditQuestion(q) {
        if (!window.confirm('确认从考试移除该题？')) return;
        try {
          await window.ChemAPI.removeExamQuestion(this.edit.exam.exam_id, q.question_id);
          this.edit.questions = this.edit.questions.filter((x) => x.question_id !== q.question_id);
          this.toast('已移除', false);
        } catch (err) {
          this.toast((err && err.message) || '移除失败', true);
        }
      },
      // 从题库选入
      async loadEditBankSets() {
        try {
          const d = await window.ChemAPI.getExamSets({ teacher_id: this.user.user_id, page_size: 100 });
          this.editBank.sets = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载题库失败', true);
        }
      },
      async loadEditBankQuestions() {
        if (!this.editBank.set) return;
        try {
          const d = await window.ChemAPI.getExamSetDetail(this.editBank.set);
          this.editBank.questions = (d && d.questions) || [];
          this.editBank.selected = [];
        } catch (err) {
          this.toast((err && err.message) || '加载题库题目失败', true);
        }
      },
      toggleEditBankSel(q) {
        const i = this.editBank.selected.indexOf(q.question_id);
        if (i >= 0) this.editBank.selected.splice(i, 1);
        else this.editBank.selected.push(q.question_id);
      },
      toggleEditHistSel(h) {
        const i = this.editHist.selected.indexOf(h.ref_id);
        if (i >= 0) this.editHist.selected.splice(i, 1);
        else this.editHist.selected.push(h.ref_id);
      },
      skipReasonText(res, idPrefix) {
        const reasons = (res && res.skipped_reasons) || {};
        const parts = Object.entries(reasons).map(([id, reason]) => idPrefix + id + '：' + reason);
        return parts.length ? '（' + parts.join('；') + '）' : '';
      },
      // 从题库/真题选入题目到当前编辑考试（source: 'bank' | 'hist'）
      async addEditFrom(source) {
        const bank = source === 'bank';
        const sel = bank ? this.editBank.selected : this.editHist.selected;
        if (!sel.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        try {
          const res = await window.ChemAPI.addExamQuestions(this.edit.exam.exam_id, sel);
          const reasons = this.skipReasonText(res, bank ? '题' : '');
          this.toast(
            '已加入 ' + res.added + ' 题，跳过 ' + res.skipped + (res.skipped > 0 ? reasons : ''),
            res.added === 0 && res.skipped > 0
          );
          const d = await window.ChemAPI.getExamQuestions(this.edit.exam.exam_id);
          this.edit.questions = (d && d.items) || [];
          if (bank) this.editBank = { set: '', sets: [], questions: [], selected: [] };
          else this.editHist = { query: '', results: [], selected: [] };
        } catch (err) {
          this.toast((err && err.message) || '加入失败', true);
        }
      },
      // 从真题选入
      async searchEditHistory() {
        try {
          const d = await window.ChemAPI.searchHistorical({ keyword: this.editHist.query });
          this.editHist.results = (d && d.items) || [];
          this.editHist.selected = [];
        } catch (err) {
          this.toast((err && err.message) || '搜索真题失败', true);
        }
      },
      // ---- 导出 ----
      async doExport() {
        const { exam, format, with_answers } = this.exportModal;
        try {
          const blob = await window.ChemAPI.exportExam(exam.exam_id, { format, with_answers });
          const ext = format === 'pdf' ? 'pdf' : 'docx';
          const name = (exam.name || 'exam') + (with_answers ? '-含答案' : '-无答案') + '.' + ext;
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = name;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          this.exportModal.show = false;
          this.toast('试卷已下载：' + name, false);
        } catch (err) {
          this.toast((err && err.message) || '导出失败', true);
        }
      },

      // ---- 看结果 ----
      async openResults(exam) {
        this.resultsModal = { show: true, exam, data: null };
        try {
          const d = await window.ChemAPI.getExamResults(exam.exam_id);
          this.resultsModal.data = d;
        } catch (err) {
          this.toast((err && err.message) || '加载结果失败', true);
        }
      },
      pct(v) {
        return (Math.round((v || 0) * 10000) / 100) + '%';
      },
    },
  });

  // Vue 模板作用域内 window 不可见（编译后表达式不走全局），挂到 globalProperties 供 v-for 使用
  app.config.globalProperties.splitKps = window.splitKps;

  app.directive('katex', {
    mounted(el) {
      window.ChemKaTeX.render(el);
    },
    updated(el) {
      window.ChemKaTeX.render(el);
    },
  });

  app.mount('#app');
})();
