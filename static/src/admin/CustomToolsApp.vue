<template>
  <div class="custom-tools-page" :class="{ 'editor-only': editorPageMode }">
    <SecondaryGate
      v-if="!secondaryVerified"
      :configured="secondaryConfigured"
      :loading="secondaryLoading"
      :error="secondaryError"
      :description="$t('adminCustomTools.gateDescription')"
      @verify="handleVerifySecondary"
      @recheck="checkSecondary"
    />
    <!-- 列表模式 -->
    <template v-if="!editorPageMode">
      <header class="page-header">
        <div>
          <h1>{{ $t('adminCustomTools.main.title') }}</h1>
          <p>{{ $t('adminCustomTools.main.subtitle') }}</p>
        </div>
        <div class="actions">
          <button type="button" class="primary" @click="openCreateModal">
            {{ $t('adminCustomTools.main.newTool') }}
          </button>
          <button type="button" class="ghost" @click="refresh" :disabled="loading">
            {{ loading ? $t('common.refreshing') : $t('adminCustomTools.main.refreshList') }}
          </button>
          <a class="ghost" href="/static/custom_tools/guide.html" target="_blank" rel="noopener"
            >{{ $t('adminCustomTools.main.viewGuide') }}</a
          >
          <a class="ghost" href="/admin/monitor" target="_blank" rel="noopener"
            >{{ $t('adminCustomTools.main.backToMonitor') }}</a
          >
          <a class="ghost" href="/admin/policy" target="_blank" rel="noopener"
            >{{ $t('adminCustomTools.main.policyConfig') }}</a
          >
        </div>
      </header>

      <section class="panel" v-if="error">
        <p class="error">{{ $t('adminCustomTools.loadFailedWith', { message: error }) }}</p>
      </section>

      <section class="panel" v-else>
        <div class="tool-list-header">
          <h2>{{ $t('adminCustomTools.main.toolList') }}</h2>
          <span class="muted">{{ $t('adminCustomTools.main.toolCount', { count: tools.length }) }}</span>
        </div>
        <div v-if="!tools.length" class="empty">{{ $t('adminCustomTools.main.emptyTools') }}</div>
        <div class="tool-grid">
          <div v-for="tool in tools" :key="tool.id" class="tool-card" @click="openEditorPage(tool)">
            <div class="tool-card-title">
              <strong>{{ tool.id }}</strong>
              <span class="tag">{{ tool.category || 'custom' }}</span>
            </div>
            <p class="desc">{{ tool.description || $t('adminCustomTools.main.noDescription') }}</p>
            <div class="meta">
              <span>{{ $t('adminCustomTools.main.params', { count: paramCount(tool) }) }}</span>
              <span>{{ $t('adminCustomTools.main.timeout', { seconds: tool.timeout || 30 }) }}</span>
            </div>
            <div class="files">
              <span>{{ tool.execution_file || 'execution.py' }}</span>
              <span>{{ tool.return_file || 'return.json' }}</span>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- 纯编辑页面 -->
    <section v-else class="editor-page">
      <div class="editor-page-header">
        <div class="breadcrumbs">
          <a class="ghost" href="/admin/custom-tools">{{ $t('adminCustomTools.backToList') }}</a>
        </div>
        <div class="status" v-if="loading">{{ $t('common.loading') }}</div>
        <div class="status error" v-else-if="error">{{ error }}</div>
      </div>

      <div v-if="activeTool" class="editor-panel">
        <header class="drawer-header">
          <div>
            <h3>{{ activeTool.id }}</h3>
            <p>{{ activeTool.description || $t('adminCustomTools.editor.noDescription') }}</p>
          </div>
          <div class="drawer-actions">
            <button type="button" class="primary" @click="saveAll" :disabled="saving">
              {{ saving ? $t('common.saving') : $t('common.save') }}
            </button>
            <button type="button" class="danger" @click="openDeleteConfirm()">
              {{ $t('common.delete') }}
            </button>
            <button type="button" class="ghost" @click="closeEditor">{{ $t('common.close') }}</button>
          </div>
        </header>

        <div class="tabs">
          <button :class="{ active: tab === 'definition' }" @click="tab = 'definition'">
            definition.json
          </button>
          <button :class="{ active: tab === 'execution' }" @click="tab = 'execution'">
            execution.py
          </button>
          <button :class="{ active: tab === 'return' }" @click="tab = 'return'">return.json</button>
          <button :class="{ active: tab === 'meta' }" @click="tab = 'meta'">meta.json</button>
        </div>

        <div class="editor">
          <textarea
            v-if="tab === 'definition'"
            v-model="buffers.definition"
            spellcheck="false"
          ></textarea>
          <textarea
            v-else-if="tab === 'execution'"
            v-model="buffers.execution"
            spellcheck="false"
          ></textarea>
          <textarea
            v-else-if="tab === 'return'"
            v-model="buffers.return"
            spellcheck="false"
          ></textarea>
          <textarea v-else v-model="buffers.meta" spellcheck="false"></textarea>
        </div>

        <div class="hint">
          <p>
            {{ $t('adminCustomTools.editor.hint1Pre') }}
            <code v-pre>{{ ... }}</code>
            {{ $t('adminCustomTools.editor.hint1Post') }}
          </p>
          <p>{{ $t('adminCustomTools.editor.hint2') }}</p>
        </div>
      </div>

      <div v-else-if="!error" class="empty">{{ $t('adminCustomTools.editor.loadingTool') }}</div>
    </section>

    <!-- 创建工具弹窗 -->
    <transition name="fade">
      <div v-if="createModal" class="modal-backdrop">
        <div class="modal">
          <h3>{{ $t('adminCustomTools.create.title') }}</h3>
          <label>{{ $t('adminCustomTools.create.toolIdLabel') }}<input v-model="createForm.id" /></label>
          <label>{{ $t('adminCustomTools.create.descriptionLabel') }}<input v-model="createForm.description" /></label>
          <div class="modal-actions">
            <button type="button" class="ghost" @click="createModal = false">
              {{ $t('common.cancel') }}
            </button>
            <button type="button" class="primary" @click="createTool" :disabled="creating">
              {{ creating ? $t('adminCustomTools.create.creating') : $t('adminCustomTools.create.create') }}
            </button>
          </div>
        </div>
      </div>
    </transition>

    <!-- 删除确认弹窗 -->
    <transition name="fade">
      <div v-if="confirmDeleteModal" class="modal-backdrop">
        <div class="modal">
          <h3>{{ $t('adminCustomTools.delete.title') }}</h3>
          <p class="delete-tip">
            {{ $t('adminCustomTools.delete.confirmPre') }}
            <strong>{{ deleteTargetId }}</strong>
            {{ $t('adminCustomTools.delete.confirmPost') }}
          </p>
          <div class="modal-actions">
            <button type="button" class="ghost" @click="confirmDeleteModal = false">
              {{ $t('common.cancel') }}
            </button>
            <button type="button" class="danger" @click="performDelete" :disabled="deleting">
              {{ deleting ? $t('adminCustomTools.delete.deleting') : $t('adminCustomTools.delete.title') }}
            </button>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue';
import { t } from '@/locales';
import { useSecondaryPass } from './useSecondaryPass';
import SecondaryGate from './SecondaryGate.vue';

interface CustomTool {
  id: string;
  description?: string;
  parameters?: any;
  category?: string;
  icon?: string | null;
  timeout?: number;
  execution_file?: string;
  return_file?: string;
}

const {
  verified: secondaryVerified,
  configured: secondaryConfigured,
  loading: secondaryLoading,
  error: secondaryError,
  check: checkSecondary,
  verify: verifySecondary
} = useSecondaryPass();

const tools = ref<CustomTool[]>([]);
const loading = ref(false);
const error = ref('');
const activeTool = ref<CustomTool | null>(null);
const pendingOpenId = ref('');
const editorPageMode = ref(false);
const tab = ref<'definition' | 'execution' | 'return' | 'meta'>('definition');
const saving = ref(false);
const createModal = ref(false);
const creating = ref(false);
const confirmDeleteModal = ref(false);
const deleting = ref(false);
const deleteTargetId = ref('');
const createForm = reactive({ id: '', description: '' });
const buffers = reactive({ definition: '', execution: '', return: '', meta: '' });

const refresh = async () => {
  if (!secondaryVerified.value) return;
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch('/api/admin/custom-tools', { credentials: 'same-origin' });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || t('common.loadFailed'));
    tools.value = (data.data || []).map((item: any) => ({
      ...item,
      execution_file: (item.execution && item.execution.file) || 'execution.py',
      return_file: item.return ? 'return.json' : t('adminCustomTools.main.noReturnLayer')
    }));
    if (pendingOpenId.value) {
      const target = tools.value.find((tool) => tool.id === pendingOpenId.value);
      if (target) {
        await openEditorInline(target);
      } else {
        error.value = t('adminCustomTools.main.toolNotFound', { id: pendingOpenId.value });
      }
      pendingOpenId.value = '';
    }
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
};

const openEditorInline = async (tool: CustomTool) => {
  activeTool.value = tool;
  tab.value = 'definition';
  await loadBuffers(tool.id);
};

const openEditorPage = (tool: CustomTool) => {
  const url = `/admin/custom-tools?tool=${encodeURIComponent(tool.id)}`;
  window.open(url, '_blank', 'noopener');
};

const loadBuffers = async (id: string) => {
  if (!secondaryVerified.value) return;
  try {
    buffers.definition = await fetchText(
      `/api/admin/custom-tools/file?id=${encodeURIComponent(id)}&name=definition.json`
    );
    buffers.execution = await fetchText(
      `/api/admin/custom-tools/file?id=${encodeURIComponent(id)}&name=execution.py`
    );
    buffers.return = await fetchText(
      `/api/admin/custom-tools/file?id=${encodeURIComponent(id)}&name=return.json`,
      ''
    );
    buffers.meta = await fetchText(
      `/api/admin/custom-tools/file?id=${encodeURIComponent(id)}&name=meta.json`,
      ''
    );
  } catch (e: any) {
    error.value = e.message;
  }
};

const fetchText = async (url: string, fallback = ''): Promise<string> => {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (res.status === 404) return fallback;
  const text = await res.text();
  return text || fallback;
};

const saveAll = async () => {
  if (!secondaryVerified.value) return;
  if (!activeTool.value) return;
  saving.value = true;
  try {
    await saveFile(activeTool.value.id, 'definition.json', buffers.definition);
    await saveFile(activeTool.value.id, 'execution.py', buffers.execution);
    await saveFile(activeTool.value.id, 'return.json', buffers.return);
    await saveFile(activeTool.value.id, 'meta.json', buffers.meta);
    await reloadRegistry();
    await refresh();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    saving.value = false;
  }
};

const saveFile = async (id: string, name: string, content: string) => {
  const res = await fetch('/api/admin/custom-tools/file', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, name, content })
  });
  const data = await res.json();
  if (!data.success) throw new Error(data.error || t('adminCustomTools.save.failed', { name }));
};

const reloadRegistry = async () => {
  await fetch('/api/admin/custom-tools/reload', { method: 'POST', credentials: 'same-origin' });
};

const openDeleteConfirm = (tool?: CustomTool) => {
  const target = tool || activeTool.value;
  if (!target) return;
  deleteTargetId.value = target.id;
  confirmDeleteModal.value = true;
};

const performDelete = async () => {
  if (!secondaryVerified.value) return;
  if (!deleteTargetId.value) return;
  deleting.value = true;
  try {
    const res = await fetch(
      `/api/admin/custom-tools?id=${encodeURIComponent(deleteTargetId.value)}`,
      { method: 'DELETE', credentials: 'same-origin' }
    );
    const data = await res.json();
    if (!data.success) throw new Error(data.error || t('adminCustomTools.delete.failed'));
    activeTool.value = null;
    confirmDeleteModal.value = false;
    deleteTargetId.value = '';
    await refresh();
  } catch (e: any) {
    error.value = e.message;
  }
  deleting.value = false;
};

const openCreateModal = () => {
  createForm.id = '';
  createForm.description = '';
  createModal.value = true;
};

const createTool = async () => {
  if (!secondaryVerified.value) return;
  const id = createForm.id.trim();
  if (!id) {
    error.value = t('adminCustomTools.create.idRequired');
    return;
  }
  const idPattern = /^[A-Za-z][A-Za-z0-9_-]*$/;
  if (!idPattern.test(id)) {
    error.value = t('adminCustomTools.create.idInvalid');
    return;
  }
  creating.value = true;
  try {
    const payload = {
      id,
      description: createForm.description,
      parameters: { type: 'object', properties: {}, required: [] },
      execution_code: "print('hello')\n",
      category: 'custom'
    };
    const res = await fetch('/api/admin/custom-tools', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || t('adminCustomTools.create.failed'));
    createModal.value = false;
    activeTool.value = null;
    await refresh();
    window.location.href = `/admin/custom-tools?tool=${encodeURIComponent(id)}`;
  } catch (e: any) {
    error.value = e.message;
  } finally {
    creating.value = false;
  }
};

const closeEditor = () => {
  window.location.href = '/admin/custom-tools';
};

const paramCount = (tool: CustomTool) => {
  const props = tool.parameters?.properties || {};
  return Object.keys(props).length;
};

const handleVerifySecondary = async (password: string) => {
  await verifySecondary(password);
  if (secondaryVerified.value) {
    await refresh();
  }
};

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  const toolId = params.get('tool');
  if (toolId) {
    pendingOpenId.value = toolId;
    editorPageMode.value = true;
  }
  await checkSecondary();
  if (secondaryVerified.value) {
    await refresh();
  }
});

watch(secondaryVerified, async (val) => {
  if (val) {
    await refresh();
  }
});
</script>

<style scoped>
/* 经典：米色+白色+深棕的柔和高对比配色 */
:global(html, body) {
  min-height: 100%;
  background:
    radial-gradient(140% 120% at 20% 20%, rgba(239, 229, 214, 0.9), transparent),
    radial-gradient(120% 120% at 80% 0%, rgba(255, 255, 255, 0.9), transparent), #f8f3e8;
}
:global(#custom-tools-app) {
  min-height: 100vh;
}
.custom-tools-page {
  --sand: #f8f3e8;
  --sand-strong: #efe5d6;
  --paper: #ffffff;
  --brown: #4b3626;
  --brown-strong: #2f2015;
  --ink: #1a120b;
  --muted: #6f635a;
  --border: #e3d8c8;
  --glow: 0 14px 40px rgba(47, 32, 21, 0.12);
  --radius-lg: 18px;
  --radius-md: 12px;

  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px;
  font-family:
    'Space Grotesk',
    'Noto Sans SC',
    'Inter',
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    'Segoe UI',
    sans-serif;
  color: var(--ink);
  background: transparent;
}
.custom-tools-page.editor-only {
  max-width: 1080px;
  width: min(1080px, 100%);
  padding: 20px 16px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.page-header h1 {
  margin: 0;
  letter-spacing: 0.4px;
  color: var(--brown-strong);
}
.page-header p {
  margin: 4px 0 0;
  color: var(--muted);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.panel {
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px;
  box-shadow: var(--glow);
}
.tool-list-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.tool-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px;
  cursor: pointer;
  transition:
    border-color 0.2s,
    box-shadow 0.2s,
    transform 0.1s,
    background 0.2s;
  background: linear-gradient(180deg, #fffdf8 0%, #ffffff 50%, #fbf6ed 100%);
}
.tool-card:hover {
  border-color: var(--brown);
  box-shadow: 0 12px 30px rgba(47, 32, 21, 0.12);
  transform: translateY(-1px);
}
.tool-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tool-card-title strong {
  word-break: break-all;
  color: var(--brown-strong);
}
.desc {
  margin: 6px 0;
  color: var(--muted);
  min-height: 32px;
  word-break: break-word;
}
.meta,
.files {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
  flex-wrap: wrap;
}
.files span {
  max-width: 100%;
  word-break: break-all;
}
.tag {
  background: rgba(75, 54, 38, 0.08);
  color: var(--brown-strong);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  white-space: nowrap;
}
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.drawer-header h3 {
  margin: 0;
}
.editor-panel {
  margin-top: 16px;
  background: var(--paper);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px;
  box-shadow: var(--glow);
  width: 100%;
  box-sizing: border-box;
}
.editor-page {
  margin-top: 12px;
}
.editor-page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.breadcrumbs {
  display: flex;
  gap: 8px;
  align-items: center;
}
.status {
  color: var(--muted);
}
.status.error {
  color: var(--state-danger);
}
.editor-only {
  max-width: 1080px;
}
.tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.tabs button {
  padding: 8px 12px;
  border: 1px solid var(--border);
  background: #fdf8ef;
  border-radius: 10px;
  cursor: pointer;
  color: var(--muted);
}
.tabs button.active {
  border-color: var(--brown);
  color: var(--brown-strong);
  background: #fff;
  box-shadow: inset 0 0 0 1px rgba(75, 54, 38, 0.04);
}
.editor {
  flex: 1;
  min-height: 320px;
  width: 100%;
  overflow: auto;
}
.editor textarea {
  width: 100%;
  height: 100%;
  min-height: 320px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
  outline: none;
  resize: vertical;
  background: #fdf9f1;
  color: var(--ink);
  line-height: 1.5;
  box-shadow: inset 0 2px 6px rgba(47, 32, 21, 0.04);
  box-sizing: border-box;
  max-width: 100%;
}
.hint {
  font-size: 12px;
  color: var(--muted);
}
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(26, 18, 11, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2100;
}
.modal {
  background: var(--paper);
  padding: 18px;
  border-radius: var(--radius-lg);
  width: 360px;
  max-width: min(420px, 90vw);
  box-sizing: border-box;
  box-shadow: 0 16px 40px rgba(47, 32, 21, 0.18);
  display: flex;
  flex-direction: column;
  gap: 12px;
  border: 1px solid var(--border);
}
.modal h3 {
  margin: 0;
  color: var(--brown-strong);
}
.modal input {
  width: 100%;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fdf8ef;
  box-sizing: border-box;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.delete-tip {
  margin: 4px 0 8px;
  color: var(--muted);
}
.error {
  color: var(--state-danger);
}
.empty {
  color: var(--muted);
  padding: 12px;
}

/* 按钮样式与主站一致风格 */
.primary,
.ghost,
.danger {
  font-family: inherit;
  font-weight: 600;
  font-size: 14px;
  line-height: 1.1;
  border-radius: 12px;
  padding: 10px 18px;
}
.primary {
  background: linear-gradient(
    135deg,
    var(--accent) 0%,
    var(--accent-strong) 100%
  );
  color: #fff;
  border: none;
  cursor: pointer;
  box-shadow: 0 10px 22px rgba(47, 32, 21, 0.15);
}
.primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.ghost {
  border: 1px solid var(--border);
  text-decoration: none;
  color: var(--brown-strong);
  background: #fffdf8;
}
.ghost:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.danger {
  background: linear-gradient(135deg, #c05621, #9c4221);
  color: #fff;
  border: none;
  cursor: pointer;
  box-shadow: 0 12px 28px rgba(156, 66, 33, 0.22);
}
.actions .primary,
.actions .ghost {
  text-decoration: none;
}

@media (max-width: 900px) {
  .drawer-content {
    max-width: 100%;
  }
}
@media (max-width: 640px) {
  .custom-tools-page {
    padding: 16px;
  }
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .actions {
    width: 100%;
  }
  .tool-grid {
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  }
  .drawer {
    width: 100%;
  }
  .drawer-content {
    padding: 12px;
  }
  .editor textarea {
    min-height: 220px;
  }
}

</style>
