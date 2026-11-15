import { createApp, h, nextTick } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import '@/styles/base/_tokens.scss';
import { displayBrowserAddress } from '@/components/browser/address';
import { installI18n } from '@/locales';
import NativeBrowserHost from '@/components/browser/NativeBrowserHost.vue';
import { browserScopeForNavigation } from '@/components/browser/browserScope';
import { useNativeBrowserStore } from '@/stores/nativeBrowser';
import { useConversationTabsStore } from '@/stores/conversationTabs';

const pinia = createPinia();
setActivePinia(pinia);
const app = createApp({ render: () => h(NativeBrowserHost) });
app.use(pinia);
installI18n(app);
const errors: string[] = [];
app.config.errorHandler = (error) => errors.push(String(error));
const browser = useNativeBrowserStore();
const tabs = useConversationTabsStore();
const original = {
  key: 'tab-no-model', kind: 'new' as const, conversationKind: 'agent' as const,
  workspaceId: 'fixture-workspace', workspaceLabel: 'Fixture', conversationId: '',
  title: '', error: '尚未配置任何模型', running: false, awaitingHumanApproval: false
};
app.mount('#app');

Object.assign(window, {
  browserRegression: {
    address: displayBrowserAddress,
    async state(kind: string) {
      browser.open = true;
      // v3：视图固定绑定一个标签；无 pending/commit。pending 等价于「尚未 attach」。
      const target = { ...original, key: kind === 'committed' ? 'tab-committed' : original.key,
        placement: 'main' as const, pageType: '' as const, route: '', openerKey: '' };
      const page = kind === 'settings';
      tabs.$patch({
        hydrated: true, enabled: true, viewKey: target.key,
        attached: kind !== 'unattached' && kind !== 'pending',
        renderers: { [target.key]: kind === 'foreign' ? 'another-renderer' : tabs.localRendererId },
        tabs: [page ? { ...target, kind: 'page' as const, pageType: 'settings' as const, route: '/settings' } : target],
        activeKey: target.key
      });
      await nextTick();
    },
    async pollNavigation() {
      tabs.applyServerSnapshot({
        revision: Math.max(0, tabs.revision) + 1,
        active_key: tabs.activeKey,
        renderers: JSON.parse(JSON.stringify(tabs.renderers)),
        tabs: tabs.tabs.map(tab => ({ ...tab, conversation_kind: tab.conversationKind,
          conversation_id: tab.conversationId, workspace_id: tab.workspaceId,
          workspace_label: tab.workspaceLabel, page_type: tab.pageType, opener_key: tab.openerKey }))
      }, tabs.responseSequence + 1);
      await nextTick();
    },
    editAddress(value: string) {
      const input = document.querySelector<HTMLInputElement>('.nb-address input')!;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    },
    findDraft() { browser.findOpen = true; browser.findText = 'steady-find-draft'; },
    theme(value: string) { document.documentElement.setAttribute('data-theme', value); document.body.setAttribute('data-theme', value); },
    async floating(find: boolean, error: string) { browser.findOpen = find; browser.error = error; await nextTick(); if (find) document.querySelector<HTMLInputElement>('.nb-findbar input')?.focus(); },
    history() { browser.showSection('history'); },
    async closeTabs() { for (const tab of [...browser.tabs]) await browser.command('close', undefined, tab.id); },
    guards() {
      const state = { hydrated: true, ownsView: true, viewTab: original };
      return {
        rootNew: !!browserScopeForNavigation(state, '/'),
        settingsRoute: browserScopeForNavigation(state, '/settings') === null,
        closedTarget: browserScopeForNavigation({ ...state, viewTab: null }, '/new') === null,
        foreignRenderer: browserScopeForNavigation({ ...state, ownsView: false }, '/new') === null,
        pageTab: browserScopeForNavigation({ ...state, viewTab: { ...original, kind: 'page' as const } }, '/new') === null
      };
    },
    inspect() {
      return {
        panel: !!document.querySelector('.native-browser-panel'),
        tabbar: !!document.querySelector('.nb-tabbar'),
        address: !!document.querySelector('.nb-address input'),
        newtab: !!document.querySelector('.nb-newtab'),
        blank: !!document.querySelector('.nb-navigation-blank'),
        downloads: !!document.querySelector('.nb-recent-downloads'),
        menu: !!document.querySelector('.nb-menu'),
        lease: browser.snapshot?.lease || '',
        activeUrl: browser.active?.url || '',
        addressValue: document.querySelector<HTMLInputElement>('.nb-address input')?.value || '',
        findOpen: browser.findOpen, findText: browser.findText,
        menuShadow: document.querySelector('.nb-menu') ? getComputedStyle(document.querySelector('.nb-menu')!).boxShadow : '',
        error: browser.error, errors: [...errors]
      };
    },
    async navigate(url: string) {
      const input = document.querySelector<HTMLInputElement>('.nb-address input')!;
      input.value = url;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await nextTick();
      document.querySelector<HTMLFormElement>('.nb-address')!.requestSubmit();
    },
    menu() { document.querySelector<HTMLButtonElement>('.nb-more')!.click(); },
    downloads() { document.querySelector<HTMLButtonElement>('.nb-download-button')!.click(); },
    async close() { browser.hide(); await nextTick(); }
  }
});
