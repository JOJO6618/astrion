<template>
  <transition name="overlay-fade">
    <div v-if="open" class="image-picker-backdrop" @click.self="close">
      <div class="image-picker-panel">
        <div class="header">
          <div class="header-left">
            <div class="title">选择图片（最多9张）</div>
            <button type="button" class="local-btn" :disabled="uploading" @click="triggerLocal">
              {{ uploading ? '上传中...' : '从本地发送' }}
            </button>
            <input
              ref="localInput"
              type="file"
              class="file-input-hidden"
              accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
              multiple
              @change="onLocalChange"
            />
          </div>
          <button class="close-btn" @click="close">×</button>
        </div>
        <div class="body">
          <div v-if="loading" class="loading">加载中...</div>
          <div v-else-if="!images.length" class="empty">未找到图片文件</div>
          <div v-else class="grid">
            <div
              v-for="item in images"
              :key="item.path"
              class="card"
              :class="{ selected: selectedSet.has(item.path) }"
              @click="toggle(item.path)"
              :title="item.path"
            >
              <img :src="previewUrl(item.path)" :alt="item.name" />
              <div class="name">{{ item.name }}</div>
            </div>
          </div>
        </div>
        <div class="footer">
          <div class="count">已选 {{ selectedSet.size }} / 9</div>
          <div class="actions">
            <button type="button" class="btn secondary" @click="close">取消</button>
            <button
              type="button"
              class="btn primary"
              :disabled="!selectedSet.size"
              @click="confirm"
            >
              确认
            </button>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, withDefaults } from 'vue';

interface ImageEntry {
  name: string;
  path: string;
}

const props = withDefaults(
  defineProps<{
    open: boolean;
    entries: ImageEntry[];
    initialSelected: string[];
    loading: boolean;
    uploading?: boolean;
  }>(),
  {
    uploading: false
  }
);

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'confirm', list: string[]): void;
  (e: 'local-files', files: File[] | null): void;
}>();

const selectedSet = ref<Set<string>>(new Set(props.initialSelected || []));
const localInput = ref<HTMLInputElement | null>(null);

let ignoreBackdropClickUntil = 0;

watch(
  () => props.initialSelected,
  (val) => {
    selectedSet.value = new Set(val || []);
  }
);

const images = computed(() => props.entries || []);

const toggle = (path: string) => {
  if (!path) return;
  const set = new Set(selectedSet.value);
  if (set.has(path)) {
    set.delete(path);
  } else {
    if (set.size >= 9) return;
    set.add(path);
  }
  selectedSet.value = set;
};

const close = () => {
  if (Date.now() < ignoreBackdropClickUntil) return;
  emit('close');
};

const confirm = () => emit('confirm', Array.from(selectedSet.value));

const triggerLocal = () => {
  if (props.uploading) return;
  // 屏蔽接下来 1500ms 内的 backdrop click，防止系统文件选择器关闭后的 touch 穿透误关 overlay
  ignoreBackdropClickUntil = Date.now() + 1500;
  localInput.value?.click();
};

const onLocalChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  const rawFiles = target?.files;
  // 某些 WebView（尤其国产手机）在 input value 被清空后 FileList 会失效，先深拷贝
  const files = rawFiles && rawFiles.length ? Array.from(rawFiles) : null;
  if (!files || files.length === 0) {
    console.warn('[ImagePicker] change fired but files empty — likely WebView compat issue');
    emit('local-files', null);
  } else {
    emit('local-files', files);
  }
  // 延迟清空：国产手机 WebView 中 files 填充可能是异步的，立即清空会导致 files 被截断
  setTimeout(() => {
    if (target) target.value = '';
  }, 200);
};

const previewUrl = (path: string) => `/api/gui/files/download?path=${encodeURIComponent(path)}`;

onMounted(() => {
  selectedSet.value = new Set(props.initialSelected || []);
});
</script>

<style scoped>
.image-picker-backdrop {
  position: fixed;
  inset: 0;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
}
.image-picker-panel {
  width: min(980px, 92vw);
  max-height: 88vh;
  background: var(--surface-panel);
  color: var(--text-primary);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 16px 40px var(--shadow-color);
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-default);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title {
  font-weight: 600;
}
.local-btn {
  border: 1px solid var(--border-strong);
  padding: 4px 10px;
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}
.local-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.close-btn {
  background: transparent;
  color: var(--text-tertiary);
  border: none;
  font-size: 20px;
  cursor: pointer;
}
.body {
  padding: 12px 16px;
  overflow: auto;
  flex: 1;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
.card {
  border: 1px solid var(--border-default);
  border-radius: 10px;
  background: var(--surface-card);
  cursor: pointer;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.card img {
  width: 100%;
  height: 120px;
  object-fit: cover;
  background: var(--surface-base);
}
.card .name {
  padding: 8px 10px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card.selected {
  border-color: var(--state-info);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--state-info) 20%, transparent);
}
.loading,
.empty {
  padding: 40px 0;
  text-align: center;
  color: var(--text-tertiary);
}
.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-top: 1px solid var(--border-default);
}
.actions {
  display: flex;
  gap: 10px;
}
.btn {
  border: 1px solid var(--border-strong);
  padding: 8px 14px;
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-primary);
  cursor: pointer;
}
.btn.primary {
  background: var(--state-info);
  border-color: var(--state-info);
  color: var(--text-primary);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.count {
  font-size: 13px;
  color: var(--text-tertiary);
}
@media (max-width: 640px) {
  .grid {
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  }
  .card img {
    height: 90px;
  }
}
</style>
