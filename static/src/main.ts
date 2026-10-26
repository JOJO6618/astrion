import { createApp, defineAsyncComponent } from 'vue';
import { createPinia } from 'pinia';
import 'katex/dist/katex.min.css';
import 'prismjs/themes/prism.css';
import './styles/index.scss';
import { installTheme } from './utils/theme';
import { installI18n } from './locales';

// Select before importing/mounting Agent lifecycle or workspace resources.
const root = /^\/chat(?:\/|$)/.test(window.location.pathname)
  ? defineAsyncComponent(() => import('./components/chat-mode/ChatShell.vue'))
  : defineAsyncComponent(() => import('./App.vue'));
const app = createApp(root);
const pinia = createPinia();

app.use(pinia);
installI18n(app);
installTheme();
app.mount('#app');
