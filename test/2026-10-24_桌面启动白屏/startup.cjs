const assert = require('node:assert/strict');
const { compile, deferred, noop } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');

async function flush() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

function fixture({ routePending = false } = {}) {
  const route = deferred();
  const initial = deferred();
  const session = compile('static/src/app/methods/conversation/session.ts');
  const lifecycle = compile('static/src/app/lifecycle.ts', {
    '../stores/chatActions': { useChatActionStore: () => ({ registerDependencies: noop }) },
    '../stores/sandboxSetup': { useSandboxSetupStore: () => ({ autoCheck: noop }) },
    '../composables/useScrollControl': { normalizeScrollLock: noop },
    './bootstrap': {
      setupShowImageObserver: noop,
      teardownShowImageObserver: noop,
      setupImagePreviewDelegation: noop
    },
    './methods/common': { debugLog: noop }
  }, {
    window: { addEventListener: noop, removeEventListener: noop },
    document: { addEventListener: noop, removeEventListener: noop }
  });
  let restores = 0;
  let initialCalls = 0;
  const target = {
    _todoRefreshTimer: null,
    titleTypingTimer: null,
    workspaces: [],
    preferencesReady: false,
    bootstrapRoute: () => routePending ? route.promise : Promise.resolve(),
    async loadInitialData() {
      initialCalls++;
      const epoch = session.conversationViewEpoch(host);
      await initial.promise;
      // The production initializer uses this guard after status/policy/preferences.
      if (session.conversationViewEpoch(host) !== epoch) return;
      host.preferencesReady = true;
      host.workspaces = ['workspace'];
    },
    restoreConversationTabView() {
      restores++;
      session.beginConversationSession(host, 'conv_restored', 'workspace');
    },
    leaveConversationView() { session.leaveConversationSession(host); },
    $nextTick: (callback) => callback()
  };
  const host = new Proxy(target, {
    get: (object, key) => key in object ? object[key] : noop
  });
  return { lifecycle, host, initial, route, restores: () => restores,
    initialCalls: () => initialCalls };
}

async function main() {
  {
    const f = fixture();
    await f.lifecycle.mounted.call(f.host);
    assert.equal(f.restores(), 0, 'navigation must not invalidate pending initialization');
    f.initial.resolve();
    await flush();
    assert.equal(f.host.preferencesReady, true);
    assert.deepEqual(f.host.workspaces, ['workspace']);
    assert.equal(f.restores(), 1);
    console.log('PASS startup loads preferences and workspaces before navigation changes epoch');
  }
  {
    const f = fixture();
    await f.lifecycle.mounted.call(f.host);
    f.initial.reject(new Error('synthetic auxiliary failure'));
    await flush();
    assert.equal(f.restores(), 1, 'initialization failure must not strand navigation');
    console.log('PASS failed initialization still attaches desktop navigation');
  }
  {
    const f = fixture();
    await f.lifecycle.mounted.call(f.host);
    f.lifecycle.beforeUnmount.call(f.host);
    f.initial.resolve();
    await flush();
    assert.equal(f.restores(), 0, 'unmounted page must not restart navigation');
    console.log('PASS unmount during initialization prevents delayed navigation attachment');
  }
  {
    const f = fixture({ routePending: true });
    const mounting = f.lifecycle.mounted.call(f.host);
    f.lifecycle.beforeUnmount.call(f.host);
    f.route.resolve();
    await mounting;
    assert.equal(f.initialCalls(), 0);
    assert.equal(f.restores(), 0);
    console.log('PASS unmount during route bootstrap prevents late initialization');
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
