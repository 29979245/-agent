/* KaTeX + mhchem 自动渲染初始化。
   依赖 <head> 中按序加载 katex.min.css / katex.min.js / mhchem.min.js / auto-render.min.js。
   化学式统一用 $\ce{...}$ / $$...$$ 分隔符；渲染失败保留原文不阻塞页面。 */
(function () {
  'use strict';

  // auto-render UMD 挂到 window.renderMathInElement（部分版本同时挂 katex.renderMathInElement）
  const renderMathInElement =
    (window.katex && window.katex.renderMathInElement) || window.renderMathInElement;

  window.ChemKaTeX = {
    render(el) {
      if (!el || typeof renderMathInElement !== 'function') return;
      try {
        renderMathInElement(el, {
          delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '$', right: '$', display: false },
            { left: '\\(', right: '\\)', display: false },
            { left: '\\[', right: '\\]', display: true },
          ],
          throwOnError: false,
          // 中文化学式内嵌 浓/稀/加热 等汉字属正常用法，strict 关闭避免 unicodeTextInMathMode 噪音
          strict: false,
        });
      } catch (err) {
        // 渲染失败保留原始文本
      }
    },
  };
})();
