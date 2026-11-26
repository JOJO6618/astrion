import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { t } from '@/locales';
import { useChat } from './useChat';
import { useChatDraft } from './useChatDraft';
import { command, operationId, uploadMedia } from './api';
import type { ChatMessage } from './types';
import { visibleHandoffs } from './handoff';
import { toDisplayMessages } from './display';
import { resolveAvatarStatus } from '@/utils/avatarStatus';
import { taskErrorToast } from '@/utils/taskErrorToast';
import { useConversationTabsStore } from '@/stores/conversationTabs';
import { useConversationStore } from '@/stores/conversation';
import { useUiStore } from '@/stores/ui';
import { usePersonalizationStore } from '@/stores/personalization';
import { useModelStore } from '@/stores/model';
import { startDesktopView, stopDesktopView } from '@/app/methods/ui/desktopView';
import { hasDesktopSidebar } from '@/components/chrome/desktopSidebarBridge';
import { filterModelGroups, groupModelOptions } from '@/utils/modelGroups';
import { reasoningCapabilityFor, type ReasoningChange } from '@/utils/reasoningStops';

export function useChatPage() {
  const ui = useUiStore();
  const chat = reactive(useChat((data) => ui.pushToast(taskErrorToast(data))));
  const draft = reactive(useChatDraft(chat.report));
  const tabs = useConversationTabsStore();
  const conversations = useConversationStore();
  const personalization = usePersonalizationStore();
  const models = useModelStore();
  const headerMenuOpen = ref(false);
  const modelMenuSearchQuery = ref('');
  const collapsedGroups = reactive(new Set<string>());
  const inputFocused = ref(false);
  const uploading = ref(false);
  const editing = ref<ChatMessage | null>(null);
  const saving = ref(false);
  const expandedBlocks = reactive(new Set<string>());
  const mobileOpen = ref(false);
  const isMobile = ref(!hasDesktopSidebar() && window.innerWidth <= 768);
  let alive = true;
  let searchTimer = 0;
  const displayIdentities = new Map<string, string>();
  watch(
    () => chat.cid,
    () => displayIdentities.clear(),
    { flush: 'sync' }
  );
  const messages = computed(() =>
    toDisplayMessages(
      chat.messages,
      chat.viewingLeaf ? [] : chat.display?.messages,
      displayIdentities
    )
  );
  const avatarStatus = computed(() =>
    resolveAvatarStatus({
      messages: messages.value,
      taskInProgress: chat.running,
      streamingMessage: chat.running && !!chat.display?.state.streaming,
      apiRequestPending: !!chat.display?.state.api_request_pending,
      stopRequested: chat.display?.task?.status === 'cancel_requested',
      toolIntentEnabled: !!personalization.form.tool_intent_enabled
    })
  );
  const title = computed(() => chat.document?.title || '');
  const modelOptions = computed(() =>
    models.visibleModels.map((model) => ({
      ...model,
      providerId: model.providerId ?? undefined,
      providerName: model.providerName ?? undefined
    }))
  );
  const currentModel = computed(() => models.allModels.find((m) => m.key === chat.model));
  const groups = computed(() =>
    filterModelGroups(
      groupModelOptions(modelOptions.value, { custom: t('appCore.modelGroupCustom') }),
      modelMenuSearchQuery.value
    )
  );
  // 顶栏模型弹窗推理滑块：最左「无」= 不思考，其余为思考强度
  const reasoning = computed(() => reasoningCapabilityFor(currentModel.value));
  const suggestions = computed(() => visibleHandoffs(chat.document, chat.path));
  const retryTurn = computed(() => {
    const turn = chat.document?.metadata.chat.turns.find(
      (turn) => turn.tip_id === chat.path[chat.path.length - 1]?.message_id
    );
    return turn &&
      ['failed', 'stopped'].includes(turn.status) &&
      !chat.messages.some((m) => m.metadata?.chat?.turn_id === turn.id && m.role === 'assistant')
      ? turn
      : null;
  });
  const contextTokens = computed(
    () => chat.document?.token_statistics?.current_context_tokens || 0
  );
  const preview = (id: string) => `/api/chat/media/${encodeURIComponent(id)}`;
  const images = computed(() =>
    draft.media.filter((m) => m.kind === 'image').map((m) => m.media_id)
  );
  const videos = computed(() =>
    draft.media.filter((m) => m.kind === 'video').map((m) => m.media_id)
  );
  const removeMedia = (id: string) => {
    draft.media = draft.media.filter((m) => m.media_id !== id);
  };
  async function run(action: () => Promise<unknown>) {
    try {
      return await action();
    } catch (cause) {
      chat.report(cause);
    }
  }
  const configure = (patch: Record<string, unknown>) => run(() => chat.configure(patch));
  const setReasoning = (change: ReasoningChange) =>
    configure({
      thinking_mode: change.thinking,
      ...(change.effort !== undefined ? { reasoning_effort: change.effort } : {})
    });
  function openSettings(section = 'chat') {
    const path = `/settings/${section}`;
    if (tabs.enabled) void run(() => tabs.openPage(path));
    else window.location.assign(`${path}?from=chat`);
  }
  const messagesById = computed(
    () => new Map(chat.messages.map((message) => [message.message_id, message]))
  );
  function messageForAction(action: { responseMessageId?: string }) {
    return action.responseMessageId ? messagesById.value.get(action.responseMessageId) : undefined;
  }
  function versions(message: ChatMessage) {
    return message.role === 'user'
      ? chat.siblings(message).map((m) => ({ id: m.message_id }))
      : chat.finalMessage(message)
        ? chat.turnVersions(message).map((turn) => ({ id: turn.tip_id || turn.parent_id }))
        : [];
  }
  function versionIndex(message: ChatMessage) {
    return Math.max(
      0,
      versions(message).findIndex((m) => m.id === message.message_id)
    );
  }
  async function load(id: string, owner: () => boolean = () => true) {
    editing.value = null;
    const accepted = await chat.load(id, owner);
    if (alive && accepted && owner() && !chat.incompatibleId)
      await draft.open(chat.settings?.username || '', chat.cid || 'new');
  }
  function refreshAfterConversationMigration() {
    if (!hasDesktopSidebar()) {
      void run(async () => {
        const query = conversations.searchQuery;
        await chat.list();
        if (query && conversations.searchQuery === query) await chat.list(query);
      });
    }
    if (chat.incompatibleId && !chat.loading) void run(() => load(chat.incompatibleId));
  }
  async function navigate(id: string) {
    if (tabs.enabled)
      await run(async () => {
        // 点击本视图已显示的对话：空操作
        if (id && id === chat.cid) return;
        if (id)
          await tabs.command('open', {
            conversation_kind: 'chat',
            conversation_id: id,
            workspace_id: ''
          });
        else await tabs.addNewTab({ conversationKind: 'chat' });
      });
    else
      await run(async () => {
        await load(id);
        window.history.pushState({}, '', id ? `/chat/${id.replace(/^conv_/, '')}` : '/chat/new');
      });
    mobileOpen.value = false;
  }
  async function send() {
    if (chat.incompatibleId || uploading.value || !draft.ready) return;
    const oldCid = chat.cid;
    const tabKey =
      tabs.viewTab?.kind === 'new' && tabs.viewTab.conversationKind === 'chat'
        ? tabs.viewTab.key
        : '';
    const owner = draft.identity;
    const text = draft.text;
    const media = JSON.stringify(draft.media);
    await run(async () => {
      const result = await chat.send(text, [...draft.media]);
      if (!result) return;
      if (!oldCid && tabs.enabled && tabKey)
        await tabs.bindConversation(tabKey, '', result.conversation_id, '');
      if (!result.ownsPage || draft.identity !== owner || chat.cid !== result.conversation_id)
        return;
      if (draft.text === text && JSON.stringify(draft.media) === media) draft.clear();
      if (!oldCid) await draft.open(chat.settings?.username || '', chat.cid);
    });
  }
  async function upload(files: FileList | File[] | null) {
    if (chat.incompatibleId || !files?.length || uploading.value) return;
    const owner = draft.identity;
    const selected = Array.from(files);
    if (selected.some((f) => !/^(image|video)\//.test(f.type))) {
      chat.report(t('chatMode.mediaOnly'));
      return;
    }
    uploading.value = true;
    try {
      for (const file of selected) {
        const media = await uploadMedia(file);
        if (!alive || owner !== draft.identity) return;
        if (!draft.media.some((m) => m.media_id === media.media_id))
          draft.media = [...draft.media, media];
      }
    } catch (cause) {
      if (alive && owner === draft.identity) chat.report(cause);
    } finally {
      uploading.value = false;
    }
  }
  async function saveEdit(text: string, sendAgain: boolean) {
    const message = editing.value;
    if (!message) return;
    const cid = chat.cid;
    saving.value = true;
    try {
      if (sendAgain) await chat.send(text, [], { edit_message_id: message.message_id });
      else await chat.save(message, text);
      if (chat.cid === cid && editing.value === message) editing.value = null;
    } catch (cause) {
      chat.report(cause);
    } finally {
      saving.value = false;
    }
  }
  async function duplicateConversation(id: string) {
    await run(async () => {
      const result = await command(`/conversations/${id}/duplicate`, {
        operation_id: operationId()
      });
      await chat.list(conversations.searchQuery);
      await navigate(result.conversation_id);
    });
  }
  async function deleteConversation(id: string) {
    const confirmed = await ui.requestConfirm({
      title: t('chatMode.delete'),
      message: t('chatMode.deleteConfirm'),
      confirmText: t('chatMode.confirmDelete'),
      confirmVariant: 'danger'
    });
    if (confirmed)
      await run(async () => {
        await command(`/conversations/${id}`, {}, 'DELETE');
        if (chat.cid === id) await navigate('');
        await chat.list(conversations.searchQuery);
      });
  }
  function search(value: string) {
    chat.cancelList();
    conversations.searchQuery = value;
    conversations.searchActive = !!value.trim();
    conversations.searchInProgress = true;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(
      () =>
        void run(async () => {
          await chat.list(value);
          if (conversations.searchQuery === value) conversations.searchInProgress = false;
        }),
      200
    );
  }
  watch(
    () => chat.history,
    (list) => {
      const items = list.map((item) => ({ ...item, updated_at: (item as any).updated_at || '' }));
      if (chat.historyQuery && chat.historyQuery === conversations.searchQuery)
        conversations.searchResults = items;
      else if (!chat.historyQuery) conversations.conversations = items;
      conversations.hasMoreConversations = false;
      conversations.searchMoreAvailable = false;
    }
  );
  watch(
    () => chat.incompatibleId || chat.cid,
    (id) => {
      conversations.currentConversationId = id || null;
      editing.value = null;
    }
  );
  watch(title, (value) => {
    conversations.currentConversationTitle = value;
    if (chat.cid && chat.document?.metadata.title_locked === true) {
      tabs.updateTitle(chat.cid, value, '');
    }
  });
  watch(
    () => chat.model,
    (key) => models.setModel(key)
  );
  watch(
    () => chat.error,
    (error) => {
      if (error) ui.pushToast({ message: error, type: 'error' });
    }
  );
  const desktopHost = {
    isChatMode: true,
    get currentConversationId() {
      return chat.cid;
    },
    leaveConversationView: chat.leave,
    isConversationIndependentRoute: () => false,
    persistComposerDraftNow: draft.save,
    uiPushToast: (value: { message: string }) => chat.report(value.message)
  };
  function pop() {
    const id = window.location.pathname.split('/')[2];
    void run(() => load(id && id !== 'new' ? `conv_${id.replace(/^conv_/, '')}` : ''));
  }
  function resize() {
    isMobile.value = !hasDesktopSidebar() && window.innerWidth <= 768;
  }
  onMounted(async () => {
    conversations.conversationsLoading = !hasDesktopSidebar();
    await run(async () => {
      await chat.initialize({ loadHistory: !hasDesktopSidebar() });
      personalization.applyPersonalizationData(chat.settings?.personalization || {});
      personalization.loaded = true;
      await models.fetchModels().catch(() => undefined);
      if (!alive) return;
      models.setModel(chat.model);
      const id = window.location.pathname.split('/')[2];
      await load(id && id !== 'new' ? `conv_${id.replace(/^conv_/, '')}` : '');
      if (tabs.enabled) await startDesktopView(desktopHost);
    });
    conversations.conversationsLoading = false;
    window.addEventListener('popstate', pop);
    window.addEventListener('resize', resize);
  });
  onBeforeUnmount(() => {
    alive = false;
    window.clearTimeout(searchTimer);
    window.removeEventListener('popstate', pop);
    window.removeEventListener('resize', resize);
    if (tabs.enabled) stopDesktopView(desktopHost);
  });
  return {
    chat,
    draft,
    ui,
    conversations,
    personalization,
    headerMenuOpen,
    modelMenuSearchQuery,
    collapsedGroups,
    inputFocused,
    uploading,
    editing,
    saving,
    expandedBlocks,
    mobileOpen,
    isMobile,
    messages,
    avatarStatus,
    title,
    modelOptions,
    currentModel,
    groups,
    reasoning,
    suggestions,
    retryTurn,
    contextTokens,
    preview,
    images,
    videos,
    removeMedia,
    run,
    configure,
    setReasoning,
    openSettings,
    messageForAction,
    versions,
    versionIndex,
    navigate,
    refreshAfterConversationMigration,
    send,
    upload,
    saveEdit,
    deleteConversation,
    duplicateConversation,
    search
  };
}
