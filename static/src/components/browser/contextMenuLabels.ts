import { t } from '@/locales';

export function browserContextMenuLabels() {
  const keys = [
    'back',
    'forward',
    'reload',
    'find',
    'undo',
    'redo',
    'cut',
    'copy',
    'paste',
    'selectAll',
    'openLinkNewTab',
    'copyLink',
    'saveLink',
    'copyImage',
    'saveImage',
    'copyImageAddress',
    'copyMediaAddress',
    'saveMedia',
    'searchSelection'
  ];
  return Object.fromEntries(keys.map((key) => [key, t(`browser.${key}`)]));
}
