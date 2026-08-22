import { useUiStore } from '@/stores/ui';

let overlayRoot: HTMLElement | null = null;

export function useEasterEgg() {
  const uiStore = useUiStore();

  const registerOverlayRoot = (el: HTMLElement | null) => {
    overlayRoot = el;
    if (!el) {
      finishCleanup();
    }
  };

  const finishCleanup = () => {
    const state = uiStore.easterEgg;
    if (state.cleanupTimer) {
      clearTimeout(state.cleanupTimer);
    }
    const root = overlayRoot;
    if (root) {
      root.innerHTML = '';
    }
    uiStore.clearEasterEgg();
  };

  const destroyEffect = (forceImmediate = false) => {
    const state = uiStore.easterEgg;
    if (state.cleanupTimer) {
      clearTimeout(state.cleanupTimer);
      uiStore.setEasterEggState({ cleanupTimer: null });
    }
    const instance = state.instance;
    if (!instance) {
      finishCleanup();
      return Promise.resolve();
    }
    if (state.destroying) {
      return state.destroyPromise || Promise.resolve();
    }
    uiStore.setEasterEggState({ destroying: true });
    let result: unknown;
    try {
      result = instance.destroy({
        immediate: forceImmediate,
        payload: state.payload,
        root: overlayRoot
      });
    } catch (error) {
      console.warn('销毁彩蛋时发生错误:', error);
      uiStore.setEasterEggState({ destroying: false });
      finishCleanup();
      return Promise.resolve();
    }
    const finalize = () => {
      uiStore.setEasterEggState({ destroyPromise: null, destroying: false });
      finishCleanup();
    };
    if (result && typeof (result as Promise<void>).then === 'function') {
      const promise = (result as Promise<void>)
        .then(() => {
          finalize();
        })
        .catch((error) => {
          console.warn('彩蛋清理失败:', error);
          finalize();
        });
      uiStore.setEasterEggState({ destroyPromise: promise });
      return promise;
    }
    finalize();
    return Promise.resolve();
  };

  const startEffect = async (effectName: string, payload: any = {}, app?: any) => {
    const registry = (window as any).EasterEggRegistry;
    if (!registry) {
      console.warn('EasterEggRegistry 尚未加载，无法播放彩蛋');
      return;
    }
    if (!registry.has(effectName)) {
      console.warn('未注册的彩蛋 effect:', effectName);
      await destroyEffect(true);
      return;
    }
    const root = overlayRoot;
    if (!root) {
      console.warn('未找到彩蛋根节点');
      return;
    }
    await destroyEffect(true);
    uiStore.setEasterEggState({
      active: true,
      effect: effectName,
      payload
    });
    const instance = registry.start(effectName, {
      root,
      payload,
      app
    });
    if (!instance) {
      finishCleanup();
      return;
    }
    uiStore.setEasterEggState({
      instance,
      destroyPromise: null,
      destroying: false
    });
    if (uiStore.easterEgg.cleanupTimer) {
      clearTimeout(uiStore.easterEgg.cleanupTimer);
    }
    const durationSeconds = Math.max(8, Number(payload?.duration_seconds) || 45);
    const timer = setTimeout(() => {
      const cleanup = destroyEffect(false);
      if (cleanup && typeof cleanup.catch === 'function') {
        cleanup.catch(() => {});
      }
    }, durationSeconds * 1000);
    uiStore.setEasterEggState({ cleanupTimer: timer });
    if (payload?.message) {
    }
  };

  const handlePayload = async (payload: any, app?: any) => {
    let parsed = payload;
    if (typeof payload === 'string') {
      try {
        parsed = JSON.parse(payload);
      } catch (error) {
        console.warn('无法解析彩蛋结果:', payload);
        return;
      }
    }
    if (!parsed || typeof parsed !== 'object') {
      return;
    }
    if (!parsed.success) {
      if (parsed.error) {
        console.warn('彩蛋触发失败:', parsed.error);
      }
      await destroyEffect(true);
      return;
    }
    const effectName = (parsed.effect || '').toLowerCase();
    if (!effectName) {
      console.warn('彩蛋结果缺少 effect 字段');
      return;
    }
    await startEffect(effectName, parsed, app);
  };

  return {
    registerOverlayRoot,
    handlePayload,
    startEffect,
    destroyEffect,
    finishCleanup
  };
}
