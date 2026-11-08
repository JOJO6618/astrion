import { t } from '@/locales';
import type { BrowserTab, BrowserInternalSection } from './types';
export const internalPageIcons: Record<BrowserInternalSection, string> = {
  history: 'clock',
  downloads: 'download',
  settings: 'settings',
  passwords: 'keyboard',
  import: 'folderOpen',
  clear: 'trash'
};
export function browserTabLabel(tab: BrowserTab) {
  if (tab.section) return t(`browser.${tab.section === 'import' ? 'importTitle' : tab.section}`);
  return tab.title || (tab.url === 'about:blank' ? t('browser.newTab') : tab.url);
}
