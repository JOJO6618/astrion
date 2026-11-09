const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('browserLab', {
  command: request => ipcRenderer.invoke('browser-lab:command', request),
  onState: listener => {
    const handler = (_event, state) => listener(state);
    ipcRenderer.on('browser-lab:state', handler);
    return () => ipcRenderer.removeListener('browser-lab:state', handler);
  },
});
