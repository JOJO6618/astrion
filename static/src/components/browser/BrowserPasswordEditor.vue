<template>
  <form class="nb-credential-editor" autocomplete="off" @submit.prevent="$emit('save')">
    <label
      >{{ $t('browser.website')
      }}<input
        :value="value.origin"
        type="url"
        required
        :disabled="busy"
        autocomplete="off"
        @input="change('origin', $event)"
    /></label>
    <label
      >{{ $t('browser.username')
      }}<input
        :value="value.username"
        :disabled="busy"
        autocomplete="off"
        @input="change('username', $event)"
    /></label>
    <label
      >{{ $t('browser.password')
      }}<input
        :value="value.password"
        type="password"
        :required="!value.id"
        :placeholder="value.id ? $t('browser.keepPassword') : ''"
        :disabled="busy"
        autocomplete="new-password"
        @input="change('password', $event)"
    /></label>
    <div v-if="value.id" class="nb-password-reveal">
      <output>{{ revealed || '••••••••' }}</output>
      <button class="nb-text-button" type="button" :disabled="busy" @click="$emit('reveal')">
        {{ $t(revealed ? 'browser.hidePassword' : 'browser.showPassword') }}
      </button>
      <button class="nb-text-button" type="button" :disabled="busy" @click="$emit('copy')">
        {{ $t('browser.copy') }}
      </button>
    </div>
    <p v-if="copied" class="nb-credential-note" role="status">{{ $t('browser.passwordCopied') }}</p>
    <div class="nb-credential-actions">
      <button class="nb-text-button" type="button" :disabled="busy" @click="$emit('close')">
        {{ $t('browser.cancel') }}
      </button>
      <button class="nb-text-button primary" type="submit" :disabled="busy">
        {{ $t('browser.savePassword') }}
      </button>
    </div>
  </form>
</template>
<script setup lang="ts">
interface Draft {
  id: string;
  origin: string;
  username: string;
  password: string;
}
const props = defineProps<{ value: Draft; busy: boolean; revealed: string; copied: boolean }>();
const emit = defineEmits<{
  change: [value: Draft];
  save: [];
  close: [];
  reveal: [];
  copy: [];
}>();
function change(key: 'origin' | 'username' | 'password', event: Event) {
  emit('change', { ...props.value, [key]: (event.target as HTMLInputElement).value });
}
</script>
