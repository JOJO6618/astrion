<script setup lang="ts">
import { inject } from 'vue';

defineOptions({ name: 'FilesTab' });

/**
 * 共享上下文由 PersonalizationDrawer.vue 通过 provide 注入。
 * 解构出的名称与主文件 script 顶层绑定一致，模板可直接引用。
 */
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const {
  personalization,
  form,
  activeDropdown,
  floatingMenuStyle,
  imageCompressionLabel,
  imageCompressionOptions,
  selectImageCompression,
  toggleDropdown,
  activeTheme
} = ctx;
</script>

<template>
                    <section
                      class="settings-page"
                      data-tutorial="personal-page-image"
                    >
                      <div class="settings-select-row">
                        <span class="settings-row-copy"
                          ><span class="settings-row-title">{{ $t('personalization.imageCompressionTitle') }}</span
                          ><span class="settings-row-desc"
                            >{{ $t('personalization.imageCompressionDesc') }}</span
                          ></span
                        >
                        <div
                          class="settings-select-wrap"
                          :class="{ open: activeDropdown === 'image' }"
                          @click.stop
                        >
                          <button
                            type="button"
                            class="settings-select-button"
                            @click="toggleDropdown('image')"
                          >
                            {{ imageCompressionLabel }}
                            <span class="select-chevron" aria-hidden="true"></span>
                          </button>
                          <div
                            :class="['settings-floating-menu', { dark: activeTheme === 'dark' }]"
                            :style="activeDropdown ? floatingMenuStyle : undefined"
                          >
                            <button
                              v-for="option in imageCompressionOptions"
                              :key="option.id"
                              type="button"
                              class="settings-menu-option"
                              :class="{ selected: form.image_compression === option.id }"
                              @click="selectImageCompression(option.id)"
                            >
                              <strong>{{ $t(option.labelKey) }}</strong
                              ><span>{{ $t(option.descKey) }}</span
                              ><svg viewBox="0 0 24 24"><path d="M5 12.5 9.5 17 19 7" /></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </section>
</template>
