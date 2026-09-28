// static/src/chrome.ts - 桌面壳顶部对话标签条（chrome 独立 webview）入口
//
// 双 webview 架构（desktop/src-tauri/src/backend.rs）：本页运行在窗口顶部 46px
// 的独立 webview 中，与主页面同源（/chrome），fetch/CSRF/cookie 零改动复用。
// 标签数据单一写者 = 主页面；本页只做乐观镜像 + 意图派发
// （POST /api/desktop/chrome-dispatch → 壳控制桥 → eval 进主 webview），
// 并周期轮询 /api/conversation-tabs 收敛。
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ChromeApp from './components/chrome/ChromeApp.vue';
import './styles/chrome.scss';
import { installTheme } from './utils/theme';
import { installI18n } from './locales';

const app = createApp(ChromeApp);
const pinia = createPinia();

app.use(pinia);
installI18n(app);
installTheme();
app.mount('#chrome-app');
