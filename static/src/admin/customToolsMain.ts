import { createApp } from 'vue';
import { installI18n } from '@/locales';
import CustomToolsApp from './CustomToolsApp.vue';

const app = createApp(CustomToolsApp);
installI18n(app);
app.mount('#custom-tools-app');
