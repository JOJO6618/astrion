import { createApp, defineComponent, h, onMounted } from 'vue';
import { createPinia } from 'pinia';
import BrowserPanel from '@/components/browser/BrowserPanel.vue';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { browserBridge } from '@/components/browser/types';
import { installI18n } from '@/locales';
import { useTheme } from '@/utils/theme';
import '@/styles/index.scss';
import './fixture.css';

const Root = defineComponent({
  setup() {
    const store = useNativeBrowserStore();
    const { setTheme } = useTheme();
    const theme = (new URLSearchParams(location.search).get('theme') || 'dark') as 'classic' | 'light' | 'dark';
    setTheme(theme);
    store.width = 580;
    store.open = true;
    browserBridge()!.onState(snapshot => store.apply(snapshot));
    const diagnostic = {
      ready: false,
      theme: setTheme,
      navigate: (url: string) => store.navigate(url),
      inspect: () => {
        const root = document.documentElement;
        const surface = document.querySelector('.nb-surface') as HTMLElement;
        const style = getComputedStyle(root);
        return {
          theme: root.dataset.theme,
          scheme: style.colorScheme,
          color: style.getPropertyValue('--surface-base').trim(),
          html: style.backgroundColor,
          body: getComputedStyle(document.body).backgroundColor,
          overlay: root.classList.contains('nb-native-overlay'),
          surface: surface ? getComputedStyle(surface).backgroundColor : '',
          rect: surface?.getBoundingClientRect().toJSON(),
          panelWidth: store.width,
          resizing: store.resizing,
          lease: store.snapshot?.lease,
          pending: store.active?.pending,
          error: store.error,
          url: store.active?.url
        };
      }
    };
    (window as any).surfaceDiag = diagnostic;
    onMounted(async () => {
      await store.connect({ mode: 'agent', workspace: 'surface-fixture', conversation: 'surface-fixture' });
      diagnostic.ready = true;
    });
    return () => h('div', { class: 'app-root' }, [
      h('main', { class: 'fixture-copy' }, [
        h('h1', 'Astrion 原生背景诊断'),
        h('p', '独立资料与本地页面。右侧使用当前源码的真实浏览器组件和原生网页。')
      ]),
      h(BrowserPanel)
    ]);
  }
});
const application = createApp(Root);
application.use(createPinia());
installI18n(application);
application.mount('#app');
