const TOKENS: Record<string, string> = {
  '--ui-text': '--text-primary',
  '--ui-text-muted': '--text-secondary',
  '--ui-surface': '--surface-soft',
  '--ui-border': '--border-default',
  '--ui-accent': '--accent',
  '--ui-success': '--state-success',
  '--ui-warning': '--state-warning',
  '--ui-danger': '--state-danger',
  '--ui-chat-bg': '--chat-surface-color'
};

export interface InlineUiEnvironment {
  theme: string;
  colorScheme: 'light' | 'dark';
  reducedMotion: boolean;
  variables: Record<string, string>;
  fontFamily: string;
  fontSize: string;
  lineHeight: string;
  color: string;
}

export function readInlineUiEnvironment(element: HTMLElement): InlineUiEnvironment {
  const style = getComputedStyle(element);
  const variables: Record<string, string> = {};
  for (const [name, token] of Object.entries(TOKENS)) {
    variables[name] = style.getPropertyValue(token).trim();
  }
  if (!variables['--ui-chat-bg']) variables['--ui-chat-bg'] = style.getPropertyValue('--surface-panel').trim();
  const theme = document.documentElement.getAttribute('data-theme') || 'classic';
  return {
    theme,
    colorScheme: theme === 'dark' ? 'dark' : 'light',
    reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    variables,
    fontFamily: style.fontFamily,
    fontSize: style.fontSize,
    lineHeight: style.lineHeight,
    color: style.color
  };
}

export function applyInlineUiEnvironment(element: HTMLElement, environment: InlineUiEnvironment): void {
  element.style.colorScheme = environment.colorScheme;
  for (const [key, value] of Object.entries(environment.variables)) element.style.setProperty(key, value);
}

const subscribers = new Set<() => void>();
let observer: MutationObserver | null = null;
let media: MediaQueryList | null = null;
let scheduled = false;
function notify() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    for (const subscriber of subscribers) subscriber();
  });
}

/** One theme observer for all mounted inline UIs, retired when the last one leaves. */
export function observeInlineUiEnvironment(callback: () => void): () => void {
  subscribers.add(callback);
  if (!observer) {
    observer = new MutationObserver(notify);
    for (const node of [document.documentElement, document.body]) {
      observer.observe(node, { attributes: true, attributeFilter: ['data-theme', 'style', 'class'] });
    }
    media = window.matchMedia('(prefers-reduced-motion: reduce)');
    media.addEventListener('change', notify);
  }
  return () => {
    subscribers.delete(callback);
    if (!subscribers.size) {
      observer?.disconnect();
      observer = null;
      media?.removeEventListener('change', notify);
      media = null;
    }
  };
}
