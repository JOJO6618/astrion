import { createApp } from 'vue';
import { installI18n } from '@/locales';
import ApiAdminApp from './ApiAdminApp.vue';

const app = createApp(ApiAdminApp);
installI18n(app);
app.mount('#admin-api-app');
