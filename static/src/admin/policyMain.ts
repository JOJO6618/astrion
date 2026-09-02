import { createApp } from 'vue';
import { installI18n } from '@/locales';
import PolicyApp from './PolicyApp.vue';

const app = createApp(PolicyApp);
installI18n(app);
app.mount('#admin-policy-app');
