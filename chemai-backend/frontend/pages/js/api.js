/* API 层：fetch 封装 + Bearer 注入 + 统一错误。
   全部路由指向真实后端；考试/题库/真题/导出端点已按后端对账（doc 47）。
   知识点无后端端点，由前端内置静态列表（任务 5.1）。 */
(function () {
  'use strict';

  const baseURL = 'http://localhost:8000';

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.data = data;
    }
  }

  function getToken() {
    return localStorage.getItem('chemai_token');
  }

  function extractDetail(data, status) {
    if (!data) return '请求失败 (' + status + ')';
    if (typeof data.detail === 'string') return data.detail;
    if (data.detail && typeof data.detail === 'object') {
      return data.detail.message || data.detail.msg || JSON.stringify(data.detail);
    }
    return data.message || data.error_code || '请求失败 (' + status + ')';
  }

  function handleAuth(resp) {
    if (resp.status === 401) {
      if (window.ChemAuth) window.ChemAuth.logout();
      location.href = 'login.html';
      throw new ApiError('登录已过期，请重新登录', 401);
    }
    if (resp.status === 403) {
      throw new ApiError('无权限：当前角色不能执行此操作', 403);
    }
  }

  function buildUrl(path, query) {
    let url = baseURL + path;
    if (query) {
      const qs = new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '')
      ).toString();
      if (qs) url += '?' + qs;
    }
    return url;
  }

  async function request(path, { method = 'GET', body, query } = {}) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    const token = getToken();
    if (token) opts.headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) opts.body = JSON.stringify(body);

    let resp;
    try {
      resp = await fetch(buildUrl(path, query), opts);
    } catch (err) {
      throw new ApiError('无法连接后端，请确认后端已启动（' + baseURL + '）', 0);
    }
    handleAuth(resp);

    let data = null;
    try {
      data = await resp.json();
    } catch (err) {
      /* 非 JSON 响应 */
    }
    if (!resp.ok) {
      throw new ApiError(extractDetail(data, resp.status), resp.status, data);
    }
    return data;
  }

  // 二进制响应（试卷导出 docx/pdf）：下载 blob，失败时解析 JSON 错误体
  async function fetchBlob(path, { query } = {}) {
    const opts = { method: 'GET' };
    const token = getToken();
    if (token) opts.headers = { Authorization: 'Bearer ' + token };

    let resp;
    try {
      resp = await fetch(buildUrl(path, query), opts);
    } catch (err) {
      throw new ApiError('无法连接后端，请确认后端已启动（' + baseURL + '）', 0);
    }
    handleAuth(resp);
    if (!resp.ok) {
      let data = null;
      try {
        data = await resp.json();
      } catch (err) {
        /* 非 JSON 错误体 */
      }
      throw new ApiError(extractDetail(data, resp.status), resp.status, data);
    }
    return resp.blob();
  }

  // 知识点静态列表：后端无 /api/knowledge 端点，前端内置（Tab 1 知识云）
  const STATIC_KNOWLEDGE = [
    '氧化还原反应',
    '离子反应',
    '化学计量',
    '元素周期律',
    '原子结构与性质',
    '化学键',
    '化学反应速率',
    '化学平衡',
    '电解质溶液',
    '电化学',
    '金属及其化合物',
    '非金属及其化合物',
    '有机化学基础',
    '化学实验基础',
    '物质的量',
    '配平与计算',
    '物质的分离提纯',
    '常见气体的制备',
  ].map((name) => ({ name }));

  window.ChemAPI = {
    baseURL,
    ApiError,
    request,

    // 认证（真实端点）
    login(payload) {
      return request('/api/auth/login', { method: 'POST', body: payload });
    },

    // 出题与审核（真实端点）
    generate(payload) {
      return request('/api/question/generate', { method: 'POST', body: payload });
    },
    approve(questionId) {
      return request('/api/question/' + questionId + '/approve', { method: 'POST' });
    },
    regenerate(questionId, payload) {
      return request('/api/question/' + questionId + '/regenerate', {
        method: 'POST',
        body: payload,
      });
    },

    // 知识点云：前端静态列表
    getKnowledge() {
      return Promise.resolve({ items: STATIC_KNOWLEDGE });
    },

    // ---- 题库管理（/api/exam-bank）----
    getExamSets(query) {
      return request('/api/exam-bank/exam-sets', { query });
    },
    createExamSet(payload) {
      return request('/api/exam-bank/exam-sets', { method: 'POST', body: payload });
    },
    deleteExamSet(id) {
      return request('/api/exam-bank/exam-sets/' + id, { method: 'DELETE' });
    },
    getExamSetDetail(setId) {
      return request('/api/exam-bank/exam-sets/' + setId);
    },
    importQuestions(setId, questionIds) {
      return request('/api/exam-bank/exam-sets/' + setId + '/import-questions', {
        method: 'POST',
        body: { question_ids: questionIds },
      });
    },
    removeBankQuestion(setId, questionId) {
      return request(
        '/api/exam-bank/exam-sets/' + setId + '/questions/' + questionId,
        { method: 'DELETE' }
      );
    },

    // ---- 历史真题库（/api/exam-bank）----
    getPapers() {
      return request('/api/exam-bank/papers');
    },
    searchHistorical(query) {
      return request('/api/exam-bank/historical', { query });
    },

    // ---- 班级（/api/classes）----
    getClasses() {
      return request('/api/classes');
    },

    // ---- 考试生命周期（/api/exam）----
    getExams(query) {
      return request('/api/exam', { query });
    },
    createExam(payload) {
      return request('/api/exam/create', { method: 'POST', body: payload });
    },
    getExamQuestions(examId) {
      return request('/api/exam/' + examId + '/questions');
    },
    addExamQuestions(examId, questionIds) {
      return request('/api/exam/' + examId + '/questions', {
        method: 'POST',
        body: { question_ids: questionIds },
      });
    },
    removeExamQuestion(examId, questionId) {
      return request('/api/exam/' + examId + '/questions/' + questionId, { method: 'DELETE' });
    },
    publishExam(examId) {
      return request('/api/exam/' + examId + '/publish', { method: 'POST' });
    },
    startGrading(examId) {
      return request('/api/exam/' + examId + '/start-grading', { method: 'POST' });
    },
    finalizeExam(examId) {
      return request('/api/exam/' + examId + '/finalize', { method: 'POST' });
    },
    archiveExam(examId) {
      return request('/api/exam/' + examId + '/archive', { method: 'POST' });
    },
    getExamResults(examId) {
      return request('/api/exam/' + examId + '/results');
    },
    deleteExam(examId) {
      return request('/api/exam/' + examId, { method: 'DELETE' });
    },

    // 试卷导出：docx/pdf 二进制下载
    exportExam(examId, { format = 'docx', with_answers = false } = {}) {
      return fetchBlob('/api/question/export/' + examId, { query: { format, with_answers } });
    },
  };
})();
