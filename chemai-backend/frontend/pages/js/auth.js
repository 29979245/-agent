/* 认证：登录/登出、localStorage token、角色门控（教师及以上放行工作台）。 */
(function () {
  'use strict';

  const TOKEN_KEY = 'chemai_token';
  const USER_KEY = 'chemai_user';
  const TEACHER_ROLES = ['teacher', 'admin', 'dept_admin', 'subject_lead'];

  window.ChemAuth = {
    getToken() {
      return localStorage.getItem(TOKEN_KEY);
    },
    getUser() {
      try {
        return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
      } catch (err) {
        return null;
      }
    },
    isTeacherLike() {
      const u = this.getUser();
      return !!u && TEACHER_ROLES.includes(u.role);
    },
    saveSession(token, user) {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, JSON.stringify(user));
    },
    logout() {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    },
    requireAuth() {
      if (!this.getToken()) {
        location.href = 'login.html';
        return false;
      }
      return true;
    },
    async login(username, password) {
      const data = await window.ChemAPI.login({ username, password });
      this.saveSession(data.access_token, {
        user_id: data.user_id,
        role: data.role,
        name: data.name,
        school_id: data.school_id,
      });
      return data;
    },
  };
})();
