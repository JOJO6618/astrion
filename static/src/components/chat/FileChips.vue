<template>
  <div class="file-chip-row" role="list">
    <div
      v-for="file in normalizedFiles"
      :key="file.path"
      class="file-chip"
      :class="{
        'is-clickable': canPreview,
        'is-previewing': canPreview && quickDock.previewPath === file.path
      }"
      role="listitem"
      :title="file.name"
      @click="previewFile(file)"
    >
      <span class="file-chip-icon" :style="{ background: `var(--file-kind-${file.kind})` }">
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path :d="FILE_BODY_PATH" />
          <path d="M14 2v5a1 1 0 0 0 1 1h5" />
          <template v-if="file.kind === 'word'">
            <path d="M9 13h6" />
            <path d="M9 16.5h6" />
          </template>
          <template v-else-if="file.kind === 'excel'">
            <path d="M9 12h6v5H9z" />
            <path d="M9 14.5h6" />
            <path d="M12 12v5" />
          </template>
          <template v-else-if="file.kind === 'ppt'">
            <path d="M9.5 17v-3.5" />
            <path d="M12.5 17V12" />
            <path d="M15.5 17v-2" />
          </template>
          <template v-else-if="file.kind === 'pdf'">
            <path d="M12 11.5v6" />
            <path d="m9.5 15 2.5 2.5 2.5-2.5" />
          </template>
          <template v-else-if="file.kind === 'text'">
            <path d="M9 13h6" />
            <path d="M9 16.5h4" />
          </template>
          <template v-else-if="file.kind === 'code'">
            <path d="m10 12.5-2 2 2 2" />
            <path d="m14 12.5 2 2-2 2" />
          </template>
          <template v-else-if="file.kind === 'archive'">
            <path d="M9 12.5h6v4.5H9z" />
            <path d="M11 12.5V11h2v1.5" />
          </template>
        </svg>
      </span>
      <span class="file-chip-meta">
        <span class="file-chip-name">{{ file.name }}</span>
        <span class="file-chip-type">{{ $t(file.labelKey) }}</span>
      </span>
      <button
        v-if="removable"
        type="button"
        class="file-chip-remove"
        :aria-label="$t('chat.removeFile', { name: file.name })"
        @click.stop="$emit('remove', file.path)"
      >
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
          <path
            d="M2.5 2.5l5 5M7.5 2.5l-5 5"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
          />
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useQuickDockStore } from '@/stores/quickDock';
import { useUiStore } from '@/stores/ui';

interface FileChipEntry {
  path: string;
  name: string;
  kind: string;
  labelKey: string;
}

const FILE_BODY_PATH =
  'M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z';

// 类型识别表：扩展名 → { 类型键（对应 --file-kind-* token / 图标笔画）, 类型标签 key（模板 $t 解析） }
// 注意：labelKey 存文案 key（模块顶层禁止调 t()），模板里 $t(file.labelKey) 渲染时求值。
const FILE_KIND_MAP: Record<string, { kind: string; labelKey: string }> = {
  doc: { kind: 'word', labelKey: 'chat.fileTypeDoc' },
  docx: { kind: 'word', labelKey: 'chat.fileTypeDoc' },
  xls: { kind: 'excel', labelKey: 'chat.fileTypeSheet' },
  xlsx: { kind: 'excel', labelKey: 'chat.fileTypeSheet' },
  csv: { kind: 'excel', labelKey: 'chat.fileTypeSheet' },
  ppt: { kind: 'ppt', labelKey: 'chat.fileTypeSlides' },
  pptx: { kind: 'ppt', labelKey: 'chat.fileTypeSlides' },
  pdf: { kind: 'pdf', labelKey: 'chat.fileTypePdf' },
  txt: { kind: 'text', labelKey: 'chat.fileTypeText' },
  md: { kind: 'text', labelKey: 'chat.fileTypeText' },
  log: { kind: 'text', labelKey: 'chat.fileTypeText' },
  zip: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  tar: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  gz: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  tgz: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  bz2: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  xz: { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  '7z': { kind: 'archive', labelKey: 'chat.fileTypeArchive' },
  rar: { kind: 'archive', labelKey: 'chat.fileTypeArchive' }
};

const CODE_EXTENSIONS = new Set([
  'js', 'mjs', 'cjs', 'ts', 'jsx', 'tsx', 'vue', 'py', 'java', 'c', 'h', 'cpp', 'cc', 'hpp',
  'go', 'rs', 'rb', 'php', 'html', 'htm', 'css', 'scss', 'less', 'json', 'xml', 'yml', 'yaml',
  'toml', 'ini', 'cfg', 'sh', 'bash', 'zsh', 'sql', 'swift', 'kt', 'kts', 'lua', 'r', 'dart'
]);

const props = withDefaults(
  defineProps<{
    files?: Array<string | { path?: string; name?: string }>;
    removable?: boolean;
  }>(),
  { files: () => [], removable: false }
);

defineEmits<{ (e: 'remove', path: string): void }>();

const basename = (path: string): string => {
  const parts = String(path || '').split(/[/\\]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(path || '');
};

const resolveKind = (name: string): { kind: string; labelKey: string } => {
  const ext = (name.includes('.') ? name.split('.').pop() || '' : '').trim().toLowerCase();
  if (ext && FILE_KIND_MAP[ext]) {
    return FILE_KIND_MAP[ext];
  }
  if (ext && CODE_EXTENSIONS.has(ext)) {
    return { kind: 'code', labelKey: 'chat.fileTypeCode' };
  }
  return { kind: 'generic', labelKey: 'chat.fileTypeGeneric' };
};

const normalizedFiles = computed<FileChipEntry[]>(() => {
  return (props.files || [])
    .map((item) => {
      const path = typeof item === 'string' ? item : String(item?.path || '');
      if (!path) return null;
      const name =
        (typeof item === 'object' && item && typeof item.name === 'string' && item.name) ||
        basename(path);
      const { kind, labelKey } = resolveKind(name);
      return { path, name, kind, labelKey };
    })
    .filter((entry): entry is FileChipEntry => !!entry);
});

const quickDock = useQuickDockStore();
const uiStore = useUiStore();

// 预览面板移动端不渲染，移动端点击不生效
const canPreview = computed(() => !uiStore.isMobileViewport);

// 点击文件块 → 快捷窗口文件预览面板显示该文件。
// openPreview 对同路径是 toggle 关闭；文件块点击语义是「查看」，同路径保持打开。
const previewFile = (file: FileChipEntry) => {
  if (!canPreview.value) return;
  if (quickDock.previewPath === file.path) return;
  quickDock.openPreview(file.path);
};
</script>

<style scoped>
.file-chip-row {
  display: contents;
}

.file-chip {
  position: relative;
  flex: none;
  box-sizing: border-box;
  width: 196px;
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 12px 0 10px;
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  border-radius: 6px;
}

/* 深色：输入壳(.stadium-shell)背景为 --badge-bg(#2a2a2a)，
   --surface-base(#1a1a1a) 比壳更暗会沉成一坨近黑，
   这里以 --chip-bg 为基色提亮一级，并加强边框保证轮廓 */
body[data-theme='dark'] .file-chip {
  background: color-mix(in srgb, var(--chip-bg) 88%, white);
  border-color: var(--border-strong);
}

.file-chip.is-clickable {
  cursor: pointer;
}

.file-chip.is-clickable:hover,
.file-chip.is-previewing {
  border-color: var(--border-strong);
}

.file-chip-icon {
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--on-accent);
}

.file-chip-icon svg {
  display: block;
}

.file-chip-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.25;
}

.file-chip-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-chip-type {
  font-size: 11px;
  color: var(--text-secondary);
}

.file-chip-remove {
  position: absolute;
  top: -7px;
  right: -7px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--surface-base);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  display: none;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s ease;
}

body[data-theme='dark'] .file-chip-remove {
  background: color-mix(in srgb, var(--chip-bg) 88%, white);
  border-color: var(--border-strong);
}

.file-chip:hover .file-chip-remove {
  display: flex;
}

.file-chip-remove:hover {
  color: var(--state-danger);
}
</style>
