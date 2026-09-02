import { createApp } from 'vue';
import { installI18n } from '@/locales';
import AdminDashboardApp from './AdminDashboardApp.vue';

const app = createApp(AdminDashboardApp);
installI18n(app);
app.mount('#admin-app');
