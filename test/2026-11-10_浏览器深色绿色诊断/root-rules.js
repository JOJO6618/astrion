(() => {
  const element = document.documentElement;
  const matches = [];
  const walk = (rules, href, conditions = []) => {
    for (const rule of rules) {
      if (rule.selectorText && rule.style) {
        const background = rule.style.getPropertyValue('background');
        const color = rule.style.getPropertyValue('background-color');
        if ((background || color) && element.matches(rule.selectorText)) matches.push({
          selector: rule.selectorText, background, color,
          important: rule.style.getPropertyPriority('background') || rule.style.getPropertyPriority('background-color'),
          href, conditions
        });
      }
      if (rule.cssRules) walk(rule.cssRules, href, rule.conditionText ? [...conditions, rule.conditionText] : conditions);
    }
  };
  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules, sheet.href); } catch (error) { matches.push({ error: String(error), href: sheet.href }); }
  }
  return { matches, inlineBackground: element.style.background, inlineColor: element.style.backgroundColor };
})();
