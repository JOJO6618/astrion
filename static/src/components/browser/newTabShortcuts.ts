import type { BrowserHistory, BrowserShortcut, BrowserShortcuts } from './types';

/** Tiles on the new-tab page: pinned first, then the most visited sites. */
export const MAX_SHORTCUTS = 10;
/** Only recent history counts; keeps the computation cheap on a 20k-entry history. */
const HISTORY_WINDOW = 5000;
const SEARCH_HOSTS = new Set(['google.com', 'cn.bing.com', 'bing.com', 'baidu.com']);

export interface ShortcutTile extends BrowserShortcut {
  key: string;
  pinned: boolean;
  favicon?: string;
}

/** Site identity used for hiding and de-duplication: host (with port) without "www.". */
export function shortcutKey(url: string): string {
  try {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol)) return '';
    return parsed.host.toLowerCase().replace(/^www\./, '');
  } catch {
    return '';
  }
}

/** Login/OAuth hops are recorded on every sign-in redirect; they are not places people go. */
const AUTH_LABELS = new Set([
  'accounts',
  'auth',
  'login',
  'signin',
  'sso',
  'passport',
  'oauth',
  'id'
]);
function isAuthHost(key: string) {
  return AUTH_LABELS.has(key.split('.')[0]);
}

function isSearchResult(url: URL, key: string) {
  return SEARCH_HOSTS.has(key) && (url.pathname === '/search' || url.pathname === '/s');
}

/** Most visited sites from history, newest-first input; excludes hidden and pinned sites. */
export function mostVisited(
  history: BrowserHistory[],
  shortcuts: BrowserShortcuts,
  limit: number
): ShortcutTile[] {
  if (limit <= 0) return [];
  const excluded = new Set([
    ...shortcuts.hidden,
    ...shortcuts.pinned.map((row) => shortcutKey(row.url))
  ]);
  const sites = new Map<
    string,
    { count: number; latest: number; url: string; title: string; favicon?: string }
  >();
  for (const row of history.slice(0, HISTORY_WINDOW)) {
    let url: URL;
    try {
      url = new URL(row.url);
    } catch {
      continue;
    }
    const key = shortcutKey(row.url);
    if (!key || excluded.has(key) || isSearchResult(url, key) || isAuthHost(key)) continue;
    const site = sites.get(key);
    if (site) {
      site.count++;
      if (!site.favicon && row.favicon) site.favicon = row.favicon;
      continue;
    }
    sites.set(key, {
      count: 1,
      latest: row.at,
      url: `${url.protocol}//${url.host}/`,
      title: key,
      favicon: row.favicon
    });
  }
  return [...sites.entries()]
    .sort(([, a], [, b]) => b.count - a.count || b.latest - a.latest)
    .slice(0, limit)
    .map(([key, site]) => ({
      key,
      url: site.url,
      title: site.title,
      favicon: site.favicon,
      pinned: false
    }));
}

export function shortcutTiles(
  history: BrowserHistory[],
  shortcuts: BrowserShortcuts
): ShortcutTile[] {
  const pinned = shortcuts.pinned.slice(0, MAX_SHORTCUTS).map((row) => ({
    ...row,
    key: shortcutKey(row.url),
    pinned: true
  }));
  return [...pinned, ...mostVisited(history, shortcuts, MAX_SHORTCUTS - pinned.length)];
}

/** User input → http(s) URL, or '' when it is not a usable address. */
export function shortcutURL(input: string): string {
  const value = input.trim();
  if (!value || /\s/.test(value)) return '';
  const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(value)
    ? value
    : `${/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(value) ? 'http' : 'https'}://${value}`;
  try {
    const url = new URL(candidate);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
    if (!url.hostname.includes('.') && !['localhost', '[::1]'].includes(url.hostname)) return '';
    return url.href;
  } catch {
    return '';
  }
}
