<template><BrowserPanel v-if="available && store.open && conversationPage" ref="panel" /></template>
<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useNativeBrowserStore, nativeBrowserAvailable } from '@/stores/nativeBrowser';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { browserBridge } from './types';
import { browserConversationPage, browserScopeForNavigation } from './browserScope';
import BrowserPanel from './BrowserPanel.vue';
const available = nativeBrowserAvailable();
const store = useNativeBrowserStore(),
  navigation = useConversationTabsStore();
const panel = ref<InstanceType<typeof BrowserPanel> | null>(null);
const conversationPage = computed(() =>
  browserConversationPage(window.location.pathname, navigation.navigation)
);
const scope = computed(() =>
  browserScopeForNavigation(navigation, window.location.pathname, window.location.search)
);
watch(
  [() => store.open, () => JSON.stringify(scope.value)],
  () => {
    if (!available) return;
    if (store.open && scope.value) void store.connect(scope.value);
    else store.suspend();
  },
  // Consume a complete Pinia navigation patch rather than mixing old and new fields.
  { immediate: true, flush: 'post' }
);
const subscriptions: Array<() => void> = [];
onMounted(() => {
  const bridge = browserBridge();
  if (!bridge) return;
  subscriptions.push(bridge.onState((snapshot) => store.apply(snapshot)));
  subscriptions.push(
    bridge.onShortcut((event) => {
      if (event.lease === store.snapshot?.lease) panel.value?.shortcut(event);
    })
  );
  subscriptions.push(
    bridge.onContextMenu((event) => {
      if (event.lease === store.snapshot?.lease) panel.value?.pageContext(event);
    })
  );
  subscriptions.push(
    bridge.onMessage((event) => {
      if (event.lease === store.snapshot?.lease) store.error = event.message || '';
    })
  );
});
onBeforeUnmount(() => {
  store.suspend();
  subscriptions.forEach((unsubscribe) => unsubscribe());
});
</script>
