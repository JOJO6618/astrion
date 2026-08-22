import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import 'katex/dist/katex.min.css';
import 'prismjs/themes/prism.css';
import './styles/index.scss';
import { installTheme } from './utils/theme';

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
installTheme();
app.mount('#app');
