// Isolated-world DOM adapter; no API, IPC object or password is exposed globally.
const { ipcRenderer } = require('electron');
if (process.isMainFrame) {
  if (!['http:', 'https:'].includes(location.protocol)) return;
  const random = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  const documentToken = random(), groups = new Map(), containers = new WeakMap();
  let timer, lastSubmit = '', lastSubmitAt = 0;
  const visible = input => input.isConnected && !input.disabled && input.getClientRects().length > 0 && getComputedStyle(input).visibility !== 'hidden';
  function containerFor(input) {
    if (input.form) return input.form;
    // Keep separate form-less login panels separate whenever the page supplies a group.
    return input.closest('[role="dialog"], [role="form"], fieldset') || document;
  }
  function fields(container) {
    const inputs = [...container.querySelectorAll('input')].filter(visible);
    const passwords = inputs.filter(input => input.type === 'password' && !input.readOnly);
    const newPassword = passwords.find(input => input.autocomplete === 'new-password');
    const password = newPassword || passwords.find(input => input.autocomplete === 'current-password') || (passwords.length === 1 ? passwords[0] : null);
    if (!password) return null;
    const names = inputs.filter(input => ['text', 'email', 'tel'].includes(input.type));
    const preceding = names.filter(input => input.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING);
    const username = names.find(input => input.autocomplete === 'username') || preceding.find(input => input.type === 'email') || preceding.at(-1) || (names.length === 1 ? names[0] : null);
    return { password, username, allowFill: !newPassword && passwords.length === 1 };
  }
  function register(container) {
    const found = fields(container);
    if (!found) return null;
    let group = containers.get(container);
    if (!group) { group = { id: random(), container }; containers.set(container, group); groups.set(group.id, group); }
    Object.assign(group, found);
    return group;
  }
  function details(group) {
    const action = group.container instanceof HTMLFormElement ? group.container.action : location.href;
    return { documentToken, formToken: group.id, url: location.href, action, allowFill: group.allowFill,
      username: group.username?.value || '', hasPassword: !!group.password.value };
  }
  function announce(group, reason) {
    ipcRenderer.send('browser:password-form', { kind: 'form', ...details(group), reason });
  }
  function scan() {
    clearTimeout(timer);
    for (const group of groups.values()) if (!group.password.isConnected) { groups.delete(group.id); containers.delete(group.container); }
    const seen = new Set();
    for (const input of document.querySelectorAll('input[type="password"]')) {
      const container = containerFor(input);
      if (seen.has(container)) continue;
      seen.add(container);
      const known = containers.get(container), oldPassword = known?.password, oldUsername = known?.username;
      const group = register(container);
      if (group && (!known || group.password !== oldPassword || group.username !== oldUsername)) announce(group, 'detected');
    }
  }
  function capture(container, validate = false) {
    if (validate && container instanceof HTMLFormElement && !container.noValidate && !container.checkValidity()) return;
    const group = register(container);
    if (!group || !group.password.value || group.password.value.length > 16384) return;
    const username = group.username?.value || '';
    if (username.length > 4096) return;
    const signature = `${group.id}\n${username}\n${group.password.value}`;
    if (signature === lastSubmit && Date.now() - lastSubmitAt < 2000) return;
    lastSubmit = signature; lastSubmitAt = Date.now();
    clearTimeout(group.submitTimer);
    group.submitTimer = setTimeout(() => { lastSubmit = ''; }, 2000);
    ipcRenderer.send('browser:password-form', { kind: 'submitted', ...details(group), username, password: group.password.value });
  }
  document.addEventListener('focusin', event => {
    if (!(event.target instanceof HTMLInputElement)) return;
    const group = register(containerFor(event.target));
    if (group && [group.password, group.username].includes(event.target)) announce(group, 'focused');
  }, true);
  document.addEventListener('submit', event => { if (event.isTrusted) capture(event.target); }, true);
  document.addEventListener('click', event => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const button = event.target.closest('button, input[type="submit"], input[type="button"], input[type="image"], [role="button"]');
    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true' || button.type === 'reset') return;
    const container = button.form || button.closest('form, [role="dialog"], [role="form"], fieldset') || document;
    const nativeSubmit = ['submit', 'image'].includes(button.type);
    const label = `${button.textContent || ''} ${button.getAttribute('aria-label') || ''} ${button.getAttribute('title') || ''} ${button.value || ''}`;
    // Do not treat password-visibility, back or cancel controls as a login attempt.
    if (!nativeSubmit && /password|密码|显示|隐藏|取消|cancel|back|forgot|toggle/i.test(label) && !/log.?in|sign.?in|登录|登陆|继续|continue|注册|sign.?up/i.test(label)) return;
    capture(container, nativeSubmit && !button.formNoValidate);
  }, true);
  document.addEventListener('keydown', event => {
    if (event.isTrusted && event.key === 'Enter' && event.target instanceof HTMLInputElement) capture(containerFor(event.target));
  }, true);
  ipcRenderer.on('browser:password-fill', (_event, value) => {
    const group = groups.get(value?.formToken);
    if (!group || value.documentToken !== documentToken || value.origin !== location.origin || value.url !== location.href || !group.allowFill || !visible(group.password)) return;
    // Re-evaluate the DOM at delivery time; the same form token cannot bless a new field.
    const live = fields(group.container);
    if (!live || !live.allowFill || live.password !== group.password || live.username !== group.username) return;
    try { if (new URL(details(group).action, location.href).origin !== location.origin) return; } catch { return; }
    if (typeof value.username !== 'string' || typeof value.password !== 'string') return;
    if (!value.replace && (group.password.value || (group.username?.value && group.username.value !== value.username))) return;
    if (group.username?.readOnly && group.username.value !== value.username) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    const fill = (input, text) => {
      if (!input || !visible(input) || input.readOnly) return;
      setter.call(input, text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    fill(group.username, value.username); fill(group.password, value.password);
  });
  function start() {
    scan();
    new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 150); }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['type', 'autocomplete', 'disabled', 'readonly', 'style', 'class'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true }); else start();
}
