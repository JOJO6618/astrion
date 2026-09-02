import { createApp } from 'vue';
import { installI18n } from '@/locales';
import CustomToolsGuideApp from './CustomToolsGuideApp.vue';

const app = createApp(CustomToolsGuideApp);
installI18n(app);
app.mount('#custom-tools-guide');
