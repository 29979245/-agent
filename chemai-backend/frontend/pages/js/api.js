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
      if (window.ChemAuth) {
        const isStudent = window.ChemAuth.isStudent();  // logout 前先读角色，避免清空后无法推断
        window.ChemAuth.logout();
        window.ChemAuth.redirectToLogin(isStudent);
      } else {
        location.href = 'login.html';
      }
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

  // multipart 上传（OCR 批量建批次）：FormData + Bearer，不设 Content-Type（浏览器自动带 boundary）
  async function requestMultipart(path, formData) {
    const opts = { method: 'POST' };
    const token = getToken();
    if (token) opts.headers = { Authorization: 'Bearer ' + token };

    let resp;
    try {
      opts.body = formData;
      resp = await fetch(baseURL + path, opts);
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

  // SSE 流解析：按空行切分事件块，解析 event:/data: 行（data 可多行合并，容错 \r\n）
  async function parseSSE(body, { onEvent }) {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    const emitBlock = (block) => {
      let event = '';
      const dataLines = [];
      block.split('\n').forEach((line) => {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
      });
      if (dataLines.length) onEvent(event, dataLines.join('\n'));
    };
    const flush = () => {
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        emitBlock(block);
      }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
      flush();
    }
    const rest = buffer.trim();
    if (rest) emitBlock(rest);  // EOF 尾部未以空行结尾的事件（如 done）也需消费
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
    getClassStudents(classId) {
      return request('/api/classes/' + classId + '/students');
    },

    // ---- 学情面板（/api/panel）----
    getClassPanel(classId) {
      return request('/api/panel/class/' + classId);
    },
    getPanelTrend(classId) {
      return request('/api/panel/class/' + classId + '/trend');
    },
    getGradeTrend(gradeId) {
      return request('/api/panel/grade/' + gradeId + '/trend');
    },
    getStudentDetail(classId, studentId) {
      return request('/api/panel/class/' + classId + '/student/' + studentId);
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

    // ---- OCR 批改（/api/ocr + /api/grading）----
    uploadOcrBatch(files) {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));
      return requestMultipart('/api/ocr/tasks/batch', formData);
    },
    getOcrBatchStatus(batchId) {
      return request('/api/ocr/tasks/batch/' + batchId);
    },
    retryOcrTask(taskId) {
      return request('/api/ocr/tasks/' + taskId + '/retry', { method: 'POST' });
    },
    getOcrBatches() {
      return request('/api/ocr/tasks');
    },
    getOcrServicesStatus() {
      return request('/api/ocr/services/status');
    },
    runGrading(payload) {
      return request('/api/grading/run', { method: 'POST', body: payload });
    },
    saveGrading(payload) {
      return request('/api/grading/save', { method: 'POST', body: payload });
    },
    getGradingResults(batchId) {
      return request('/api/grading/results/' + batchId);
    },

    // ---- 学生练习（/api/practice）----
    studentPracticeTasks(studentId) {
      return request('/api/practice/student/' + studentId + '/tasks');
    },
    generatePractice() {
      return request('/api/practice/generate', { method: 'POST' });
    },
    studentPracticeSubmit(practiceId, answers) {
      return request('/api/practice/submit', {
        method: 'POST',
        body: { practice_id: practiceId, answers },
      });
    },
    studentEffect(studentId) {
      return request('/api/practice/effect/' + studentId);
    },

    // ---- 学生复习（/api/review）----
    studentReviewTasks(studentId) {
      return request('/api/review/tasks/' + studentId);
    },
    studentReviewSubmit(reviewTaskId, passed) {
      return request('/api/review/submit', {
        method: 'POST',
        body: { review_task_id: reviewTaskId, passed },
      });
    },

    // ---- 学生错题（/api/wrong-questions）----
    studentWrongQuestions(studentId) {
      return request('/api/wrong-questions/' + studentId);
    },
    studentWrongVariants(questionId, count) {
      return request('/api/wrong-questions/variants', {
        method: 'POST',
        body: { question_id: questionId, count: count || 1 },
      });
    },
    studentWrongTrain(studentId, answers) {
      return request('/api/wrong-questions/train', {
        method: 'POST',
        body: { student_id: studentId, answers },
      });
    },
    studentWrongMastered(questionId, studentId) {
      return request('/api/wrong-questions/' + questionId + '/mastered', {
        method: 'POST',
        body: { student_id: studentId },
      });
    },

    // ---- 学生报告（/api/report）----
    getStudentReport(studentId) {
      return request('/api/report/student/' + studentId);
    },

    // ---- 家长绑定码（/api/student）----
    generateBindCode(studentId) {
      return request('/api/student/' + studentId + '/bind-code', { method: 'POST' });
    },

    // ---- 修改密码（/api/auth）----
    changePassword(payload) {
      return request('/api/auth/change-password', { method: 'POST', body: payload });
    },

    // ---- AI 助教对话（/api/agent/chat/langgraph/stream，SSE 流式）----
    // 返回 { cancel, done }：cancel() 中止连接；done 为 Promise，正常结束不 reject。
    // onError(data)：data.degradable=true 表示端点未实现可降级；kind ∈ not_implemented/network/stream/http/canceled。
    agentChatStream(payload, handlers = {}) {
      const { onPhase, onText, onToolCall, onToolResult, onDone, onError } = handlers;
      const controller = new AbortController();
      const cancel = () => controller.abort();

      const promise = (async () => {
        const opts = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
          signal: controller.signal,
        };
        const token = getToken();
        if (token) opts.headers.Authorization = 'Bearer ' + token;
        opts.body = JSON.stringify(payload);

        let resp;
        try {
          resp = await fetch(baseURL + '/api/agent/chat/langgraph/stream', opts);
        } catch (err) {
          if (onError) {
            onError(controller.signal.aborted
              ? { degradable: false, kind: 'canceled', message: '已取消' }
              : { degradable: false, kind: 'network', message: err.message });
          }
          return { canceled: controller.signal.aborted };
        }

        try {
          handleAuth(resp);
        } catch (err) {
          // 401 已由 handleAuth 登出并跳转，静默结束；403 未跳转，需回传错误让 UI 恢复（否则 busy 卡死、发送钮永久禁用）
          if (onError && err && err.status === 403) {
            onError({ degradable: false, kind: 'forbidden', message: err.message });
          }
          return { canceled: false };
        }

        if (resp.status === 404) {
          if (onError) onError({ degradable: true, kind: 'not_implemented', message: 'AI 助教后端尚未上线' });
          return { canceled: false };
        }
        if (!resp.ok) {
          let data = null;
          try { data = await resp.json(); } catch (err) { /* 非 JSON 错误体 */ }
          if (onError) onError({ degradable: false, kind: 'http', status: resp.status, message: extractDetail(data, resp.status) });
          return { canceled: false };
        }

        let doneReceived = false;
        try {
          await parseSSE(resp.body, {
            onEvent: (eventName, dataText) => {
              let dataObj = {};
              try { dataObj = JSON.parse(dataText); } catch (err) { dataObj = { content: dataText }; }
              const name = eventName || dataObj.type || '';
              if (name === 'text' || name === 'token') {
                if (onText) onText(dataObj.content || '');
              } else if (name === 'phase' || name === 'thinking') {
                if (onPhase) onPhase(dataObj.content || dataObj.phase || dataObj.state || name);
              } else if (name === 'tool_call') {
                if (onToolCall) onToolCall(dataObj);
              } else if (name === 'tool_result') {
                if (onToolResult) onToolResult(dataObj);
              } else if (name === 'done') {
                doneReceived = true;
                if (onDone) onDone(dataObj);
              } else if (name === 'error') {
                if (onError) onError({ degradable: false, kind: 'stream_error', message: dataObj.message || '对话出错' });
              } else if (name === 'planning' || name === 'executing' || name === 'reply' || name === 'awaiting_approval') {
                if (onPhase) onPhase(name);
              }
            },
          });
          // 流干净结束但未收到 done：视为连接中断（design D3），否则加载态悬挂
          if (!doneReceived && !controller.signal.aborted && onError) {
            onError({ degradable: false, kind: 'stream', message: '连接中断' });
          }
        } catch (err) {
          if (!controller.signal.aborted && onError) {
            onError({ degradable: false, kind: 'stream', message: err.message || '连接中断' });
          }
        }
        return { canceled: controller.signal.aborted };
      })();

      return { cancel, done: promise };
    },
  };
})();
