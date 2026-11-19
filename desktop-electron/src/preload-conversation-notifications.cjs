const { contextBridge, ipcRenderer } = require('electron');

if (process.isMainFrame) {
  const subscribe = (channel, callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
  const initialTheme = ipcRenderer.sendSync('shell:theme-get');
  contextBridge.exposeInMainWorld('__ASTRION_INITIAL_THEME__', initialTheme);
  contextBridge.exposeInMainWorld('astrionWindow', {
    onThemeChange: (callback) => subscribe('shell:theme-changed', callback),
  });
  contextBridge.exposeInMainWorld('astrionConversationNotifications', {
    onBanners: (callback) => subscribe('conversation-notification:banners', callback),
    ready: () => ipcRenderer.send('conversation-notification:ready'),
    open: (id) => ipcRenderer.send('conversation-notification:open', id),
    dismiss: (id) => ipcRenderer.send('conversation-notification:dismiss', id),
  });
}
