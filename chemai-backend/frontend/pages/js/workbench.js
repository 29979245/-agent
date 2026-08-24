/* 出题工作台 Vue 根组件（Vue 3 全局构建，模板在 exam-v2.html 内）。
   四 Tab：workbench / bank / history / exams；Tab 1 内三子模式 ai / manual / ocr。 */
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

  const EXAM_STATUS_LABEL = {
    Draft: '草稿',
    AddingQuestions: '组卷中',
    Published: '已发布',
    InProgress: '进行中',
    Completed: '已完成',
  };

  function renderMarkdown(text) {
    if (!text) return '';
    const s = String(text);
    if (window.marked) {
      try {
        return window.marked.parse ? window.marked.parse(s) : window.marked(s);
      } catch (err) {
        /* fallthrough */
      }
    }
    return s.replace(/\n/g, '<br>');
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
        // Tab 2
        examSets: [],
        selectedSetId: null,
        bankQuestions: [],
        selectedBank: [],
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
    },
    mounted() {
      if (!window.ChemAuth.requireAuth()) return;
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
        try {
          const resp = await window.ChemAPI.approve(q.question_id);
          q.approved = true;
          q.review_flag = !!resp.review_flag;
          this.toast(q.review_flag ? '已批准入库（带复核标记）' : '已批准入库', false);
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
      async loadExamSets() {
        try {
          const d = await window.ChemAPI.getExamSets();
          this.examSets = (d && d.items) || [];
          if (this.examSets.length && this.selectedSetId == null) this.selectedSetId = this.examSets[0].id;
          if (this.selectedSetId != null) await this.selectSet(this.selectedSetId);
        } catch (err) {
          this.toast((err && err.message) || '加载题库失败', true);
        }
      },
      async createExamSet() {
        const name = window.prompt('新建题库名称：');
        if (!name || !name.trim()) return;
        try {
          const d = await window.ChemAPI.createExamSet(name.trim());
          const item = (d && d.item) || {};
          this.examSets.unshift(item);
          this.selectedSetId = item.id;
          this.toast('题库已创建（演示数据）', false);
        } catch (err) {
          this.toast((err && err.message) || '创建题库失败', true);
        }
      },
      async deleteExamSet(id) {
        if (id == null) return;
        if (!window.confirm('确认删除该题库？')) return;
        try {
          await window.ChemAPI.deleteExamSet(id);
          this.examSets = this.examSets.filter((s) => String(s.id) !== String(id));
          this.selectedSetId = this.examSets.length ? this.examSets[0].id : null;
          this.bankQuestions = [];
          if (this.selectedSetId != null) await this.selectSet(this.selectedSetId);
          this.toast('题库已删除（演示数据）', false);
        } catch (err) {
          this.toast((err && err.message) || '删除题库失败', true);
        }
      },
      async selectSet(id) {
        this.selectedSetId = id;
        this.selectedBank = [];
        try {
          const d = await window.ChemAPI.searchQuestions(id);
          this.bankQuestions = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载题目失败', true);
        }
      },
      toggleBank(q) {
        const i = this.selectedBank.indexOf(q.id);
        if (i >= 0) this.selectedBank.splice(i, 1);
        else this.selectedBank.push(q.id);
      },
      batchAddToExam() {
        if (!this.selectedBank.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        this.toast('已将 ' + this.selectedBank.length + ' 道题加入考试（演示数据）', false);
        this.selectedBank = [];
      },
      batchRemoveFromBank() {
        if (!this.selectedBank.length) {
          this.toast('请先勾选题目', true);
          return;
        }
        const ids = this.selectedBank.slice();
        this.bankQuestions = this.bankQuestions.filter((q) => ids.indexOf(q.id) < 0);
        this.selectedBank = [];
        this.toast('已从题库移除 ' + ids.length + ' 道题（演示数据）', false);
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
          const d = await window.ChemAPI.searchHistorical(this.historyQuery);
          this.historyResults = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '搜索失败', true);
        }
      },
      addVariantFromHistory(h) {
        this.mode = 'ai';
        this.ai.source_label = '基于 ' + h.source_paper + ' 变体生成 · 相似度 ' + h.similarity;
        this.tab = 'workbench';
        this.toast('已设为变体蓝本，回到「出题工作台」生成变体题', false);
      },
      addHistoryToExam(h) {
        this.toast('已加入考试（演示数据）：题 ' + h.id, false);
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
          const d = await window.ChemAPI.getExams();
          this.exams = (d && d.items) || [];
        } catch (err) {
          this.toast((err && err.message) || '加载考试列表失败', true);
        }
      },
      async createExam() {
        if (!this.newExam.name.trim()) {
          this.toast('请填写考试名称', true);
          return;
        }
        try {
          const d = await window.ChemAPI.createExam({
            name: this.newExam.name.trim(),
            class_id: this.newExam.class_id,
          });
          const item = (d && d.item) || d;
          this.exams.unshift(item);
          this.newExam = { name: '', class_id: this.classes.length ? this.classes[0].id : '' };
          this.toast('考试已创建（演示数据）', false);
        } catch (err) {
          this.toast((err && err.message) || '创建考试失败', true);
        }
      },
      async examAction(exam, action) {
        try {
          if (action === 'publish') {
            const d = await window.ChemAPI.updateExam(exam.id, { status: 'Published' });
            if (d && d.item) Object.assign(exam, d.item);
            this.toast('考试已发布（演示数据）', false);
          } else if (action === 'delete') {
            if (!window.confirm('确认删除考试「' + exam.name + '」？')) return;
            await window.ChemAPI.deleteExam(exam.id);
            this.exams = this.exams.filter((e) => String(e.id) !== String(exam.id));
            this.toast('考试已删除（演示数据）', false);
          } else if (action === 'edit') {
            this.toast('编辑为演示占位，请到真实后端落地后使用', true);
          } else if (action === 'export') {
            this.toast('导出为演示占位，请到真实后端落地后使用', true);
          }
        } catch (err) {
          this.toast((err && err.message) || '操作失败', true);
        }
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
