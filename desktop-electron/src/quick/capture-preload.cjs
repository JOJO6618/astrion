const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('capture', {
  select: (rect) => ipcRenderer.send('quick:selection', rect),
  cancel: () => ipcRenderer.send('quick:selection-cancel'),
  dismiss: () => ipcRenderer.send('quick:outside-click'),
  selectWindow: (id) => ipcRenderer.send('quick:window-selection', id),
  windows: (callback) => ipcRenderer.on('quick:windows', (_, snapshot) => callback(snapshot)),
  exclude: (callback) => ipcRenderer.on('quick:exclude', (_, bounds) => callback(bounds)),
  onHide: (callback) => ipcRenderer.on('quick:overlay-dismiss', (_, ticket) => callback(ticket)),
  dismissed: (ticket) => ipcRenderer.send('quick:overlay-dismissed', ticket)
});
