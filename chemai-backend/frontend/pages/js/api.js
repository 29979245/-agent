/* API 层：fetch 封装 + Bearer 注入 + 统一错误 + USE_MOCK 适配层。
   - 已落地端点（auth、question/generate/approve/regenerate）走真实请求。
   - Tab 2/3/4 端点（exam-bank、historical、exam、knowledge、classes）后端未落地，
     USE_MOCK=true 时由 js/mock.js 返回带「演示数据」标注的结果；后端落地后改 false 即切真实。 */
(function () {
  'use strict';

  const baseURL = 'http://localhost:8000';
  const USE_MOCK = true;

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.data = data;
    }
  }

  // 命中 mock 白名单的路径走 mock（无论真实请求是否已落地）
  const MOCK_ONLY =
    /^\/api\/(exam-bank|exam|question\/search|question\/historical|knowledge\/list|classes)/;

  function isMockable(path) {
    return USE_MOCK && MOCK_ONLY.test(path);
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

  async function request(path, { method = 'GET', body, query } = {}) {
    if (isMockable(path)) {
      return window.ChemMock.handle(method, path, body, query);
    }

    let url = baseURL + path;
    if (query) {
      const qs = new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '')
      ).toString();
      if (qs) url += '?' + qs;
    }

    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    const token = getToken();
    if (token) opts.headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) opts.body = JSON.stringify(body);

    let resp;
    try {
      resp = await fetch(url, opts);
    } catch (err) {
      throw new ApiError('无法连接后端，请确认后端已启动（' + baseURL + '）', 0);
    }

    if (resp.status === 401) {
      if (window.ChemAuth) window.ChemAuth.logout();
      location.href = 'login.html';
      throw new ApiError('登录已过期，请重新登录', 401);
    }
    if (resp.status === 403) {
      throw new ApiError('无权限：当前角色不能执行此操作', 403);
    }

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

  window.ChemAPI = {
    baseURL,
    USE_MOCK,
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

    // 知识库 / 题库 / 真题 / 考试（mock 适配）
    getKnowledge() {
      return request('/api/knowledge/list');
    },
    getExamSets() {
      return request('/api/exam-bank/exam-sets');
    },
    createExamSet(name) {
      return request('/api/exam-bank/exam-sets', { method: 'POST', body: { name } });
    },
    deleteExamSet(id) {
      return request('/api/exam-bank/exam-sets/' + id, { method: 'DELETE' });
    },
    searchQuestions(setId) {
      return request('/api/question/search', { query: { set_id: setId } });
    },
    getPapers() {
      return request('/api/exam-bank/papers');
    },
    searchHistorical(q) {
      return request('/api/question/historical', { query: { q } });
    },
    getClasses() {
      return request('/api/classes');
    },
    getExams() {
      return request('/api/exam/list');
    },
    createExam(payload) {
      return request('/api/exam/create', { method: 'POST', body: payload });
    },
    updateExam(id, payload) {
      return request('/api/exam/' + id, { method: 'PATCH', body: payload });
    },
    deleteExam(id) {
      return request('/api/exam/' + id, { method: 'DELETE' });
    },
  };
})();
