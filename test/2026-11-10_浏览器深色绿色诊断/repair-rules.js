// A/B experiment only: change the isolated diagnostic renderer's CSSOM, not source files.
(() => {
  const changed = [];
  const walk = rules => {
    for (const rule of rules) {
      if (rule.selectorText && rule.style) {
        const background = rule.style.getPropertyValue('background');
        const selector = rule.selectorText.replace(/\s+/g, '');
        if (selector === ':root[data-theme="dark"],body[data-theme="dark"]' && background.includes('--theme-surface-muted')) {
          const suffix = background.includes('--state-success') ? '--done' : background.includes('--accent') ? '--running' : '';
          const old = rule.selectorText;
          rule.selectorText = `:root[data-theme="dark"] .goal-mode-banner${suffix}, body[data-theme="dark"] .goal-mode-banner${suffix}`;
          changed.push({ before: old, after: rule.selectorText, background });
        }
      }
      if (rule.cssRules) walk(rule.cssRules);
    }
  };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch {} }
  return { changed, html: getComputedStyle(document.documentElement).backgroundColor };
})();
