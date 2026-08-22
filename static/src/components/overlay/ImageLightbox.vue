<template>
  <Teleport to="body">
    <Transition name="lightbox-fade">
      <div
        v-if="preview"
        class="image-lightbox"
        role="dialog"
        aria-modal="true"
        :aria-label="preview.name || '图片预览'"
        @click.self="close"
      >
        <button
          type="button"
          class="image-lightbox__close"
          aria-label="关闭预览"
          @click.stop="close"
        >
          ×
        </button>
        <div class="image-lightbox__stage" @click.self="close">
          <img
            class="image-lightbox__img"
            :src="preview.url"
            :alt="preview.name || '图片预览'"
            draggable="false"
          />
          <div v-if="preview.name" class="image-lightbox__caption">{{ preview.name }}</div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue';
import { useUiStore } from '@/stores/ui';

const uiStore = useUiStore();
const preview = computed(() => uiStore.imagePreview);

const close = () => {
  uiStore.closeImagePreview();
};

const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && preview.value) {
    event.preventDefault();
    close();
  }
};

// 打开时锁定页面滚动并注册 ESC；关闭时恢复
const syncLock = (open: boolean) => {
  if (typeof document === 'undefined') return;
  if (open) {
    document.addEventListener('keydown', onKeydown);
    document.body.style.overflow = 'hidden';
  } else {
    document.removeEventListener('keydown', onKeydown);
    document.body.style.overflow = '';
  }
};

watch(preview, (value) => syncLock(!!value), { immediate: true });

onBeforeUnmount(() => syncLock(false));
</script>

<style scoped>
.image-lightbox {
  position: fixed;
  inset: 0;
  z-index: 4000;
  background: var(--overlay-scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  /* 移动端安全区适配 */
  padding:
    calc(12px + env(safe-area-inset-top, 0px))
    calc(12px + env(safe-area-inset-right, 0px))
    calc(12px + env(safe-area-inset-bottom, 0px))
    calc(12px + env(safe-area-inset-left, 0px));
  box-sizing: border-box;
}

.image-lightbox__stage {
  max-width: 100%;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 0;
}

.image-lightbox__img {
  /* 最大预览面积：桌面收敛在 1100x780 以内，再叠加视口上限，不铺满全屏 */
  max-width: min(1100px, 88vw);
  max-height: min(780px, 76vh);
  object-fit: contain;
  border-radius: 6px;
  user-select: none;
  -webkit-user-drag: none;
}

/* 移动端屏幕小，适度放宽比例 */
@media (max-width: 767px) {
  .image-lightbox__img {
    max-width: 94vw;
    max-height: 80vh;
  }
}

.image-lightbox__caption {
  flex: none;
  max-width: 80vw;
  font-size: 12px;
  line-height: 1.4;
  color: var(--lightbox-text);
  opacity: 0.85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.image-lightbox__close {
  position: absolute;
  top: calc(10px + env(safe-area-inset-top, 0px));
  right: calc(10px + env(safe-area-inset-right, 0px));
  z-index: 1;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: var(--lightbox-btn-bg);
  color: var(--lightbox-text);
  font-size: 20px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s ease;
}

.image-lightbox__close:hover {
  background: var(--lightbox-btn-bg-hover);
}

.lightbox-fade-enter-active,
.lightbox-fade-leave-active {
  transition: opacity 0.16s ease;
}

.lightbox-fade-enter-from,
.lightbox-fade-leave-to {
  opacity: 0;
}
</style>
