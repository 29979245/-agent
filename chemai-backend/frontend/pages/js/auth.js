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
    isStudent() {
      const u = this.getUser();
      return !!u && u.role === 'student';
    },
    // 学生业务实体 id（= Student.id，登录响应的 role_id），所有学生端点以它作路径参数
    getStudentId() {
      const u = this.getUser();
      return u ? u.role_id : null;
    },
    // 登录成功后的落点：学生进练习页，教师进工作台，其余角色无学生/教师端页面
    redirectAfterLogin() {
      if (this.isStudent()) return 'practice.html';
      if (this.isTeacherLike()) return 'exam-v2.html';
      return null;
    },
    // 未认证/过期跳转：logout 后 user 已清无法推断角色，需调用方传 forceStudent；
    // 无参时按当前 user 推断（保留历史行为）
    redirectToLogin(forceStudent) {
      const student = forceStudent !== undefined ? forceStudent : this.isStudent();
      location.href = student ? 'student-login.html' : 'login.html';
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
        this.redirectToLogin();
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
        role_id: data.role_id,
      });
      return data;
    },
  };
})();
