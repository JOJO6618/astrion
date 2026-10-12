/** Runs inside the opaque-origin sandbox; generated scripts start only after commit. */
export const INLINE_UI_FRAME_RUNTIME = String.raw`
(() => {
  'use strict';
  const root = document.getElementById('astrion-inline-ui-root');
  const send = (type, extra = {}) => parent.postMessage({ channel: 'astrion-inline-ui', instance: INSTANCE, type, ...extra }, '*');
  let lastHeight = -1;
  let measureQueued = false;
  let sequence = -1;
  let committed = false;
  let failed = false;
  let generation = 0;
  function measure() {
    if (measureQueued) return;
    measureQueued = true;
    // A zero-height/offscreen frame may not receive animation frames yet.
    setTimeout(() => {
      measureQueued = false;
      const height = Math.ceil(root.getBoundingClientRect().height);
      if (height !== lastHeight) {
        lastHeight = height;
        send('height', { height });
      }
    }, 0);
  }
  function reportError() {
    if (failed) return;
    failed = true;
    root.inert = true;
    send('error');
  }
  function setEnvironment(env) {
    for (const [key, value] of Object.entries(env.variables)) document.documentElement.style.setProperty(key, value);
    document.documentElement.dataset.theme = env.theme;
    document.documentElement.style.colorScheme = env.colorScheme;
    document.documentElement.dataset.reducedMotion = String(env.reducedMotion);
    document.body.style.fontFamily = env.fontFamily;
    document.body.style.fontSize = env.fontSize;
    document.body.style.lineHeight = env.lineHeight;
    document.body.style.color = env.color;
    window.dispatchEvent(new CustomEvent('astrion-ui-environment-change', { detail: env }));
    measure();
  }
  function execute(script, owner) {
    return new Promise((resolve, reject) => {
      const element = document.createElement('script');
      for (const [key, value] of Object.entries(script.attributes)) element.setAttribute(key, value);
      element.async = false;
      element.textContent = script.content;
      const asynchronous = !!element.src || element.type === 'module';
      let timer;
      const finish = (error) => {
        clearTimeout(timer);
        element.onload = element.onerror = null;
        if (owner !== generation) return resolve();
        if (error) reject(error); else resolve();
      };
      if (asynchronous) {
        element.onload = () => finish();
        element.onerror = () => finish(new Error('script'));
        timer = setTimeout(() => { element.remove(); finish(new Error('timeout')); }, 15000);
      }
      document.head.appendChild(element);
      if (!asynchronous) finish();
    });
  }
  async function commit(data) {
    const owner = ++generation;
    committed = true;
    try {
      for (const handler of data.handlers) {
        const node = root.querySelector('[data-inline-ui-handler="' + handler.key + '"]');
        if (node) for (const [key, value] of Object.entries(handler.attributes)) node.setAttribute(key, value);
      }
      for (const script of data.scripts) {
        if (owner !== generation || failed) return;
        await execute(script, owner);
      }
      if (owner !== generation || failed) return;
      root.inert = false;
      root.removeAttribute('aria-busy');
      send('committed');
      measure();
    } catch (_) { reportError(); }
  }
  const receive = (event) => {
    const data = event.data;
    if (event.source !== parent || !data || data.channel !== 'astrion-inline-ui' || data.instance !== INSTANCE) return;
    if (data.type === 'environment') { setEnvironment(data.environment); return; }
    if (data.type !== 'content' || committed || data.sequence <= sequence) return;
    sequence = data.sequence;
    const template = document.createElement('template');
    template.innerHTML = data.html;
    patch(root, template.content);
    root.inert = true;
    root.setAttribute('aria-busy', 'true');
    measure();
    if (data.closed) commit(data);
  };
  window.addEventListener('message', receive);
  // Script errors leave the visible static content intact and report one concise failure.
  window.addEventListener('error', (event) => { if (committed && event.error) reportError(); });
  window.addEventListener('unhandledrejection', () => { if (committed) reportError(); });
  new ResizeObserver(measure).observe(root);
  document.addEventListener('load', measure, true);
  if (document.fonts) {
    document.fonts.ready.then(measure);
    document.fonts.addEventListener('loadingdone', measure);
  }
  if (typeof INITIAL_ENVIRONMENT !== 'undefined' && INITIAL_ENVIRONMENT) setEnvironment(INITIAL_ENVIRONMENT);
  send('ready');
  measure();
})();
`;
