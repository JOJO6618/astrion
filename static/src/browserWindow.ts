// static/src/browserWindow.ts - 桌面壳（macOS）对话浏览器独立窗口入口
//
// 本页运行在主进程为某个对话创建的独立 BaseWindow 中（/browser-window，同源），
// 只挂载共享 BrowserPanel；网页本身是主进程挪进本窗口的原生 WebContentsView。
// 对话归属由主进程固定，URL 查询参数只用于显示对话标题。
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import BrowserWindowApp from './components/browser/BrowserWindowApp.vue';
import './styles/browser-window.scss';
import { installTheme } from './utils/theme';
import { installI18n } from './locales';

const app = createApp(BrowserWindowApp);
app.use(createPinia());
installI18n(app);
installTheme();
app.mount('#browser-window-app');
