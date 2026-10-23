// static/src/chrome.ts - 桌面壳顶部对话标签条（chrome 独立 webview）入口
//
// 双 webview 架构（desktop/src-tauri/src/backend.rs）：本页运行在窗口顶部 46px
// 的独立 webview 中，与主页面同源（/chrome），fetch/CSRF/cookie 零改动复用。
// 标签唯一写者在后端 modules/desktop_navigation；两个页面均提交幂等命令，
// 并消费 GET /api/conversation-tabs 的版本快照。本页不修改结构或执行主页面 JS。
// 主页面协调器收到目标后，复用既有 bootstrap 并确认提交显示。
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
