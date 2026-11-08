export const INTERNAL_SECTIONS = ['history', 'downloads', 'settings', 'passwords', 'import', 'clear'];
export function internalURL(section) {
  if (!INTERNAL_SECTIONS.includes(section)) throw new Error('unknown_browser_internal_page');
  return `astrion://browser/${section}`;
}
export function internalSection(value) {
  return typeof value === 'string' ? INTERNAL_SECTIONS.find(section => value === internalURL(section) || value === `${internalURL(section)}/`) || '' : '';
}
export function internalSnapshot(tab) {
  return { id: tab.id, section: tab.section, url: tab.url, title: '', favicon: '', loading: false, pending: false, error: '', canGoBack: false, canGoForward: false, zoom: 100, find: null };
}
