<template>
  <nav class="nb-toolbar" :aria-label="$t('browser.title')">
    <div class="nb-navigation">
      <BrowserButton
        name="arrowLeft"
        :label="$t('browser.back')"
        :disabled="!store.active?.canGoBack"
        @click="store.command('back')"
      /><BrowserButton
        name="arrowLeft"
        class="nb-forward"
        :label="$t('browser.forward')"
        :disabled="!store.active?.canGoForward"
        @click="store.command('forward')"
      /><BrowserButton
        :name="store.active?.loading ? 'x' : 'refreshCw'"
        :label="store.active?.loading ? $t('browser.stop') : $t('browser.reload')"
        :disabled="!store.active || !!store.section"
        @click="store.command(store.active?.loading ? 'stop' : 'reload')"
      />
    </div>
    <form class="nb-address" @submit.prevent="navigate">
      <BrowserButton
        name="globe"
        :label="$t('browser.siteInfo')"
        :disabled="!!store.section"
        @click="toggleSite"
      /><input
        ref="address"
        v-model="addressText"
        :aria-label="$t('browser.address')"
        :placeholder="$t('browser.address')"
        :disabled="!store.snapshot"
        autocomplete="off"
        spellcheck="false"
        role="combobox"
        aria-autocomplete="list"
        aria-controls="nb-address-options"
        :aria-expanded="suggestionsOpen"
        :aria-activedescendant="
          suggestionsOpen && suggestionIndex >= 0
            ? `nb-address-option-${suggestionIndex}`
            : undefined
        "
        @focus="focusAddress"
        @click="reopenSuggestions"
        @blur="blurSuggestions"
        @input="inputSuggestions"
        @keydown="suggestionKeys"
      /><BrowserButton
        v-if="addressText"
        class="nb-clear-address"
        name="x"
        :label="$t('browser.dismiss')"
        @click="
          addressText = '';
          address?.focus();
          inputSuggestions();
        "
      />
      <div
        v-if="suggestionsOpen"
        id="nb-address-options"
        class="nb-address-suggestions"
        role="listbox"
        :aria-label="$t('browser.recentVisits')"
        @pointerdown.prevent
      >
        <button
          v-for="(row, index) in suggestions"
          :id="`nb-address-option-${index}`"
          :key="row.url"
          type="button"
          role="option"
          :aria-selected="suggestionIndex === index"
          @click="chooseAddress(row.url)"
        >
          <BrowserSiteIcon :src="row.favicon" :url="row.url" /><span
            ><strong>{{ row.title || row.url }}</strong
            ><small>{{ displayBrowserAddress(row.url) }}</small></span
          >
        </button>
      </div>
    </form>
    <BrowserButton
      v-if="followAiAvailable"
      name="crosshair"
      :active="store.followAi"
      :label="store.followAi ? $t('browser.followAiOn') : $t('browser.followAiOff')"
      :disabled="!store.snapshot"
      @click="store.toggleFollow()"
    />
    <BrowserButton
      name="download"
      class="nb-download-button"
      :label="$t('browser.downloads')"
      :disabled="!store.snapshot"
      :aria-expanded="downloadsOpen"
      aria-haspopup="dialog"
      @click="toggleDownloads"
    />
    <button
      class="nb-more"
      :aria-label="$t('browser.more')"
      aria-haspopup="menu"
      :aria-expanded="store.menu"
      :disabled="!store.snapshot"
      @click="toggleMenu"
    >
      ···
    </button>
  </nav>

  <div v-if="store.device && !store.section" class="nb-devicebar">
    <button class="nb-text-button" @click="device(false)">{{ $t('browser.desktop') }}</button
    ><label
      >{{ $t('browser.width') }}
      <input
        v-model.number="store.deviceWidth"
        inputmode="numeric"
        :aria-label="$t('browser.width')"
        @change="device(true)" /></label
    ><span>×</span
    ><label
      >{{ $t('browser.height') }}
      <input
        v-model.number="store.deviceHeight"
        inputmode="numeric"
        :aria-label="$t('browser.height')"
        @change="device(true)" /></label
    ><BrowserButton name="refreshCw" :label="$t('browser.rotate')" @click="rotate" /><BrowserButton
      name="x"
      :label="$t('browser.dismiss')"
      @click="device(false)"
    />
  </div>
</template>
<script setup lang="ts">
import { inject } from 'vue';
import { browserControlsKey } from './useBrowserControls';
import BrowserButton from './BrowserButton.vue';
import BrowserSiteIcon from './BrowserSiteIcon.vue';
import { displayBrowserAddress } from './address';
import { browserBridge } from './types';
// 只在本壳实现「跟随智能体」时显示按钮，避免其他壳出现按不动的开关。
const followAiAvailable = browserBridge()?.followAi === true;
const {
  store,
  address,
  addressText,
  addressSuggestions,
  focusAddress,
  chooseAddress,
  navigate,
  toggleMenu,
  toggleSite,
  toggleDownloads,
  downloadsOpen,
  device,
  rotate
} = inject(browserControlsKey)!;
const {
  items: suggestions,
  open: suggestionsOpen,
  selected: suggestionIndex,
  input: inputSuggestions,
  reopen: reopenSuggestions,
  blur: blurSuggestions,
  keydown: suggestionKeys
} = addressSuggestions;
</script>
<style src="./browser-address.scss"></style>
