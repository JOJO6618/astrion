(() => {
  const application = document.querySelector('#app')?.__vue_app__;
  const stores = application?.config.globalProperties.$pinia?._s;
  const store = stores?.get('nativeBrowser');
  if (!store) return false;
  store.open = true;
  window.surfaceDiag = {
    ready: true,
    theme: theme => {
      document.documentElement.dataset.theme = theme;
      document.body.dataset.theme = theme;
      const color = getComputedStyle(document.documentElement).getPropertyValue('--surface-base').trim();
      window.astrionWindow.surface(color);
    },
    navigate: url => store.navigate(url),
    repaint: () => {
      const node = document.createElement('div');
      node.className = 'surface-diagnostic-repaint';
      node.textContent = '局部重绘诊断';
      node.style.cssText = 'position:fixed;left:30px;top:240px;color:var(--text-primary);z-index:1';
      document.body.appendChild(node);
      setTimeout(() => node.remove(), 250);
    },
    inspect: () => {
      const root = document.documentElement;
      const surface = document.querySelector('.nb-surface');
      const style = getComputedStyle(root);
      const navigation = stores.get('conversationTabs');
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
        url: store.active?.url,
        navigationStatus: navigation?.navigation?.status,
        activeTab: !!navigation?.activeTab,
        availableStores: [...stores.keys()]
      };
    }
  };
  return true;
})();
