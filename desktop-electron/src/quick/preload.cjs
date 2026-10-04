const { contextBridge, ipcRenderer } = require('electron');
let pointerFrame = 0;
window.addEventListener('mousemove', () => {
  if (pointerFrame) return;
  pointerFrame = requestAnimationFrame(() => { pointerFrame = 0; ipcRenderer.send('quick:pointer'); });
});
contextBridge.exposeInMainWorld('astrionQuick', {
  request: (route, method = 'GET', body, workspace = '') => ipcRenderer.invoke('quick:request', { route, method, body, workspace }),
  info: () => ipcRenderer.invoke('quick:info'),
  configure: (patch) => ipcRenderer.invoke('quick:configure', patch),
  hide: () => ipcRenderer.send('quick:hide'),
  hidden: () => ipcRenderer.send('quick:hidden'),
  layout: (regions, presentation) => ipcRenderer.send('quick:layout', regions, presentation),
  capturePermission: () => ipcRenderer.invoke('quick:capture-permission'),
  onCapture: (callback) => { const listener = (_, image) => callback(image); ipcRenderer.on('quick:capture', listener); return () => ipcRenderer.removeListener('quick:capture', listener); },
  onCaptureError: (callback) => { const listener = (_, message) => callback(message); ipcRenderer.on('quick:capture-error', listener); return () => ipcRenderer.removeListener('quick:capture-error', listener); },
  onShow: (callback) => { const listener = () => callback(); ipcRenderer.on('quick:show', listener); return () => ipcRenderer.removeListener('quick:show', listener); },
  onHide: (callback) => { const listener = () => callback(); ipcRenderer.on('quick:will-hide', listener); return () => ipcRenderer.removeListener('quick:will-hide', listener); },
  onPointer: (callback) => { const listener = (_, point) => callback(point); ipcRenderer.on('quick:pointer-position', listener); return () => ipcRenderer.removeListener('quick:pointer-position', listener); }
});
