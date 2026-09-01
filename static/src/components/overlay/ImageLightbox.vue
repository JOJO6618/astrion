<template>
  <Teleport to="body">
    <Transition name="lightbox-fade">
      <div
        v-if="preview"
        class="image-lightbox"
        role="dialog"
        aria-modal="true"
        :aria-label="preview.name || $t('overlay.imagePreview')"
        @click.self="close"
      >
        <CloseButton
          variant="bare"
          class="image-lightbox__close"
          :label="$t('overlay.closePreview')"
          @click="close"
        />
        <div class="image-lightbox__stage" @click.self="close">
          <img
            class="image-lightbox__img"
            :src="preview.url"
            :alt="preview.name || $t('overlay.imagePreview')"
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
import CloseButton from '@/components/common/CloseButton.vue';

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

/* bare 变体：按钮本体样式在 CloseButton 组件内，此处只负责灯箱场景的定位与配色覆盖。
   灯箱遮罩永远为深色，颜色不随主题变量；默认约 72% 透明度的白，hover 全亮。 */
.image-lightbox__close {
  position: absolute;
  top: calc(10px + env(safe-area-inset-top, 0px));
  right: calc(10px + env(safe-area-inset-right, 0px));
  z-index: 1;
  --close-btn-color: color-mix(in srgb, var(--lightbox-text) 72%, transparent);
  --close-btn-color-hover: var(--lightbox-text);
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
