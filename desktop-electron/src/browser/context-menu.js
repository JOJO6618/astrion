import { Menu, clipboard } from 'electron';
import { navigationURL, manualNavigationURL, isLocalFile, searchURL } from './policy.js';

const DEFAULT_LABELS = {
  back: 'Back', forward: 'Forward', reload: 'Reload', find: 'Find in page',
  undo: 'Undo', redo: 'Redo', cut: 'Cut', copy: 'Copy', paste: 'Paste', selectAll: 'Select all',
  openLinkNewTab: 'Open link in new tab', copyLink: 'Copy link address', saveLink: 'Save link',
  copyImage: 'Copy image', saveImage: 'Save image', copyImageAddress: 'Copy image address',
  copyMediaAddress: 'Copy media address', saveMedia: 'Save media', searchSelection: 'Search selected text'
};

export function contextMenuLabels(input) {
  return Object.fromEntries(Object.entries(DEFAULT_LABELS).map(([key, fallback]) => [key,
    typeof input?.[key] === 'string' && input[key].trim() && input[key].length <= 100 ? input[key] : fallback
  ]));
}

function downloadable(url) {
  try {
    const parsed = new URL(url);
    return ['http:', 'https:', 'blob:'].includes(parsed.protocol) || /^data:image\//i.test(url);
  } catch { return false; }
}

export class BrowserContextMenu {
  constructor(manager) { this.manager = manager; this.current = null; }
  close() {
    const current = this.current;
    this.current = null;
    if (current && !current.window.isDestroyed()) {
      try { current.menu.closePopup(current.window); } catch { /* A closing window may already have dismissed the menu. */ }
    }
  }
  closeStale() { if (this.current && !this.current.valid()) this.close(); }
  build(tab, params, host) {
    const manager = this.manager, wc = tab.view.webContents;
    const lease = host.lease, url = wc.getURL(), frame = params.frame;
    let frameURL;
    try { frameURL = frame?.url; } catch { /* A detached frame makes this menu invalid. */ }
    const valid = () => {
      try {
        return !manager.disposed && !host.window.isDestroyed() && manager.hostFor(tab.owner) === host && host.visible && host.lease === lease && host.active() === tab &&
          !tab.pendingURL && !wc.isDestroyed() && tab.view.getVisible() && wc.getURL() === url &&
          (!frame || (frameURL !== undefined && !frame.detached && frame.url === frameURL));
      } catch { return false; }
    };
    const labels = contextMenuLabels(host.menuLabels);
    const invoke = action => () => {
      if (!valid()) return;
      try { wc.focus(); action(); } catch (error) { host.message(error.message); }
    };
    const item = (id, action, enabled = true) => ({ id, label: labels[id], enabled, click: invoke(action) });
    const groups = [];
    if (params.isEditable) {
      const flags = params.editFlags || {};
      groups.push(['undo', 'redo'].map(action => item(action, () => wc[action](), flags[`can${action[0].toUpperCase()}${action.slice(1)}`] === true)));
      groups.push(['cut', 'copy', 'paste', 'selectAll'].map(action => item(action, () => wc[action](), flags[`can${action[0].toUpperCase()}${action.slice(1)}`] === true)));
    } else if (params.selectionText) {
      groups.push([
        item('copy', () => wc.copy(), params.editFlags?.canCopy !== false),
        item('searchSelection', () => manager.registry.create(searchURL(tab.profile.searchEngine(), params.selectionText), { owner: tab.owner, profile: tab.profile }))
      ]);
    }
    if (params.linkURL) {
      let navigable = false;
      const navigate = isLocalFile(wc.getURL()) ? manualNavigationURL : navigationURL;
      try { navigate(params.linkURL); navigable = true; } catch { /* Copy remains available for other URL schemes. */ }
      groups.push([
        item('openLinkNewTab', () => manager.registry.create(navigate(params.linkURL), { owner: tab.owner, profile: tab.profile }), navigable && [...manager.tabs.values()].filter(row => row.owner === tab.owner).length < 48),
        item('copyLink', () => clipboard.writeText(params.linkURL)),
        item('saveLink', () => wc.downloadURL(params.linkURL), downloadable(params.linkURL))
      ]);
    }
    if (params.mediaType === 'image' || params.mediaType === 'canvas') {
      groups.push([
        item('copyImage', () => wc.copyImageAt(params.x, params.y), params.hasImageContents === true || params.mediaType === 'canvas'),
        ...(params.srcURL ? [
          item('saveImage', () => wc.downloadURL(params.srcURL), downloadable(params.srcURL)),
          item('copyImageAddress', () => clipboard.writeText(params.srcURL))
        ] : [])
      ]);
    } else if (['audio', 'video'].includes(params.mediaType) && params.srcURL) {
      groups.push([
        item('copyMediaAddress', () => clipboard.writeText(params.srcURL)),
        item('saveMedia', () => wc.downloadURL(params.srcURL), downloadable(params.srcURL) && params.mediaFlags?.canSave !== false)
      ]);
    }
    groups.push([
      item('back', () => wc.navigationHistory.goBack(), wc.navigationHistory.canGoBack()),
      item('forward', () => wc.navigationHistory.goForward(), wc.navigationHistory.canGoForward()),
      item('reload', () => wc.reload()),
      item('find', () => host.send('browser:shortcut', { lease, key: 'f' }))
    ]);
    const template = groups.filter(group => group.length).flatMap((group, index) => index ? [{ type: 'separator' }, ...group] : group);
    return { menu: Menu.buildFromTemplate(template), valid, window: host.window };
  }
  show(tab, params) {
    const host = this.manager.hostFor(tab.owner);
    if (!host?.lease || tab !== host.active()) return;
    this.close();
    const current = this.build(tab, params, host);
    if (!current.valid()) return;
    this.current = current;
    try {
      current.menu.popup({ window: host.window, callback: () => { if (this.current === current) this.current = null; } });
    } catch (error) {
      if (this.current === current) this.current = null;
      host.message(error.message);
    }
  }
}
