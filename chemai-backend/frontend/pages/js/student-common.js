/* 学生端共享：登录守卫、底部 4-tab 渲染、markdown/KaTeX 渲染、难度/等级标签、toast。
   依赖 js/auth.js（ChemAuth）、js/katex.js（ChemKaTeX）、vendor/marked.min.js。 */
(function () {
  'use strict';

  const TABS = [
    { id: 'ai-tutor', label: 'AI助教', href: 'ai-tutor.html',
      svg: '<path d="M12 2a7 7 0 0 1 7 7c0 2.5-1.3 4-2.5 5.2-.7.7-1.5 1.4-1.5 2.3V17H9v-.5c0-.9-.8-1.6-1.5-2.3C6.3 13 5 11.5 5 9a7 7 0 0 1 7-7z"/><path d="M9 21h6"/>' },
    { id: 'practice', label: '练习', href: 'practice.html',
      svg: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>' },
    { id: 'wrong', label: '错题', href: 'wrong.html',
      svg: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>' },
    { id: 'profile', label: '我的', href: 'profile.html',
      svg: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>' },
  ];

  // 净化 marked 输出，阻断存储型 XSS（题目/解析/学生答案经 v-html 注入）
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
      } catch (err) { /* fallthrough */ }
    }
    return sanitizeHtml(s.replace(/\n/g, '<br>'));
  }

  const DIFFICULTY = { easy: '基础难度', medium: '中等难度', hard: '较难', competition: '竞赛难度' };
  const LEVEL_TAG = {
    level1: { cls: 'tag-green', text: '首次学习' },
    level2: { cls: 'tag-blue', text: 'Level 2 · 3天后' },
    level3: { cls: 'tag-blue', text: 'Level 3 · 7天后' },
    level4: { cls: 'tag-orange', text: 'Level 4 · 14天后' },
    level5: { cls: 'tag-orange', text: 'Level 5 · 30天后' },
    level6: { cls: 'tag-green', text: '已掌握' },
  };
  const BARRIER = { concept: '概念理解型', reading: '审题障碍型', expression: '表述障碍型' };

  window.StudentApp = {
    requireStudent() {
      if (!window.ChemAuth.getToken()) {
        window.ChemAuth.redirectToLogin(true);  // 学生页无会话，回学生登录页
        return false;
      }
      if (!window.ChemAuth.isStudent()) {
        const target = window.ChemAuth.redirectAfterLogin();
        if (target) location.replace(target);
        else window.ChemAuth.redirectToLogin();
        return false;
      }
      if (!window.ChemAuth.getStudentId()) {
        // 登录态缺 student_id：清会话回登录页重登，不发业务请求（规格 student-auth）
        window.ChemAuth.logout();
        location.href = 'student-login.html';
        return false;
      }
      return true;
    },
    studentId() {
      return window.ChemAuth.getStudentId();
    },
    renderTabbar(activeId) {
      const el = document.getElementById('tabbar');
      if (!el) return;
      el.innerHTML = TABS.map((t) => {
        const inner = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + t.svg + '</svg><span>' + t.label + '</span>';
        return '<a class="tabbar-item' + (t.id === activeId ? ' active' : '') + '" href="' + t.href + '">' + inner + '</a>';
      }).join('');
    },
    // 渲染化学正文/选项（markdown → 净化 → KaTeX）；root 为该次渲染的容器
    renderChem(root) {
      if (root && window.ChemKaTeX) window.ChemKaTeX.render(root);
    },
    renderMarkdown,
    sanitizeHtml,
    difficultyLabel(d) {
      return DIFFICULTY[d] || d || '中等难度';
    },
    levelTag(level) {
      return LEVEL_TAG[level] || { cls: 'tag-muted', text: String(level) };
    },
    barrierLabel(b) {
      return BARRIER[b] || '';
    },
    escapeHtml(str) {
      return String(str || '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    },
    emptyState(el, message, icon) {
      if (!el) return;
      el.innerHTML = '<div class="empty"><div class="icon">' + (icon || '✓') + '</div><div>' + message + '</div></div>';
    },
    toast(msg, ms) {
      let t = document.getElementById('std-toast');
      if (!t) {
        t = document.createElement('div');
        t.id = 'std-toast';
        t.className = 'toast';
        document.body.appendChild(t);
      }
      t.textContent = msg;
      t.style.display = 'block';
      clearTimeout(t._timer);
      t._timer = setTimeout(() => { t.style.display = 'none'; }, ms || 2200);
    },
  };
})();
