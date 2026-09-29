// main 视图 preload：桌面壳环境标记，页面在任意脚本执行前可读到
// （登录页据此自动免登录；App.vue 的桌面端链接拦截/软件更新入口同据此判定）。
// contextIsolation 下页面主世界读不到 preload 变量，必须走 contextBridge。
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('__ASTRION_DESKTOP__', true);
contextBridge.exposeInMainWorld('__ASTRION_PLATFORM__', process.platform);
