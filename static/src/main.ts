import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import 'katex/dist/katex.min.css';
import 'prismjs/themes/prism.css';
import './styles/index.scss';
import { installTheme } from './utils/theme';
import { installI18n } from './locales';

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
installI18n(app);
installTheme();
app.mount('#app');
