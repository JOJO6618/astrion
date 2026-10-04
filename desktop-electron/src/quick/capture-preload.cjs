const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('capture', {
  select: (rect) => ipcRenderer.send('quick:selection', rect),
  cancel: () => ipcRenderer.send('quick:selection-cancel'),
  dismiss: () => ipcRenderer.send('quick:outside-click'),
  selectWindow: (id) => ipcRenderer.send('quick:window-selection', id),
  windows: (callback) => ipcRenderer.on('quick:windows', (_, snapshot) => callback(snapshot)),
  exclude: (callback) => ipcRenderer.on('quick:exclude', (_, bounds) => callback(bounds))
});
