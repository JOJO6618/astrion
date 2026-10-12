import { SHOW_HTML_IFRAME_CSP, SHOW_HTML_IFRAME_GUARD_SCRIPT } from '../showHtmlSandbox';
import { patchInlineUi } from './content';
import type { InlineUiEnvironment } from './environment';
import { INLINE_UI_FRAME_RUNTIME } from './frameRuntime';

export const INLINE_UI_BASE_STYLE = `
  *, *::before, *::after { box-sizing: border-box; }
  .inline-ui-content { display: flow-root; width: 100%; min-width: 0; }
  .inline-ui-content > :first-child { margin-top: 0; }
  .inline-ui-content > :last-child { margin-bottom: 0; }
  img, video, svg, canvas { max-width: 100%; }
  button, input, textarea, select { font: inherit; color: inherit; }
  button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, a:focus-visible {
    outline: 2px solid var(--ui-accent); outline-offset: 2px;
  }
`;

export function buildInlineUiSandbox(instance: string, environment?: InlineUiEnvironment): string {
  // Source is constant across stream updates; generated content travels through postMessage.
  const runtime = `const INSTANCE = ${JSON.stringify(instance)}; const INITIAL_ENVIRONMENT = ${JSON.stringify(environment || null)}; const patch = (${patchInlineUi.toString()});\n${INLINE_UI_FRAME_RUNTIME}`;
  const safeScript = (script: string) => script.replace(/<\/script/gi, '<\\/script');
  return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${SHOW_HTML_IFRAME_CSP}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>${safeScript(SHOW_HTML_IFRAME_GUARD_SCRIPT)}</script>
<style>
html { color-scheme: ${environment?.colorScheme === 'dark' ? 'dark' : 'light'}; }
html, body { margin: 0; padding: 0; width: 100%; min-height: 0; background: transparent; overflow-x: clip; }
body { color: var(--ui-text); }
${INLINE_UI_BASE_STYLE}
</style></head><body>
<div id="astrion-inline-ui-root" class="inline-ui-content" inert aria-busy="true"></div>
<script>${safeScript(runtime)}</script></body></html>`;
}
