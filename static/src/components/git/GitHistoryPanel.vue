<script setup lang="ts">
import GitIcon from './GitIcon.vue';
import GitSelect from './GitSelect.vue';
import type { Commit } from './api';
type Option = { value: string; label: string };
defineProps<{
  commits: Commit[];
  selectedSha: string;
  loading: boolean;
  comparing: boolean;
  hasMore: boolean;
  reviewing: boolean;
  branchOptions: Option[];
  refOptions: Option[];
  headOptions: Option[];
}>();
const branch = defineModel<string>('branch', { required: true });
const baseBranch = defineModel<string>('baseBranch', { required: true });
const headBranch = defineModel<string>('headBranch', { required: true });
const search = defineModel<string>('search', { required: true });
const branchMode = defineModel<boolean>('branchMode', { required: true });
const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'select', commit: Commit): void;
  (event: 'history', append: boolean): void;
  (event: 'compare'): void;
}>();
function date(value: string) {
  return new Date(value).toLocaleString();
}
</script>

<template>
  <div class="git-shell__history-layer">
    <button
      type="button"
      class="git-shell__history-dismiss"
      :aria-label="$t('common.close')"
      tabindex="-1"
      @click="emit('close')"
    ></button>
    <aside id="git-history-panel" class="git-shell__history" :aria-label="$t('git.chooseCommit')">
      <header class="git-shell__history-heading">
        <div class="git-shell__tabs">
          <button type="button" :aria-pressed="!branchMode" @click="branchMode = false">
            {{ $t('git.history') }}
          </button>
          <button type="button" :aria-pressed="branchMode" @click="branchMode = true">
            {{ $t('git.compareBranches') }}
          </button>
        </div>
        <button
          type="button"
          class="git-icon-button"
          :aria-label="$t('common.close')"
          @click="emit('close')"
        >
          <GitIcon name="close" />
        </button>
      </header>
      <template v-if="!branchMode">
        <form class="git-shell__filters" @submit.prevent="emit('history', false)">
          <GitSelect
            v-model="branch"
            :label="$t('git.currentBranch')"
            :options="branchOptions"
            @update:model-value="emit('history', false)"
          />
          <div class="git-shell__search">
            <GitIcon name="search" /><input
              v-model="search"
              type="search"
              :placeholder="$t('git.searchCommits')"
              :aria-label="$t('git.searchCommits')"
            /><button type="submit" class="git-icon-button" :aria-label="$t('git.searchCommits')">
              ↵
            </button>
          </div>
        </form>
        <div class="git-shell__commit-list">
          <div v-if="loading && !commits.length" class="git-empty">{{ $t('common.loading') }}</div>
          <div v-else-if="!commits.length" class="git-empty">{{ $t('git.noCommits') }}</div>
          <button
            v-for="commit in commits"
            :key="commit.sha"
            type="button"
            class="git-shell__commit"
            :aria-pressed="selectedSha === commit.sha"
            :disabled="reviewing"
            @click="emit('select', commit)"
          >
            <span class="git-shell__subject">{{ commit.subject || commit.sha.slice(0, 8) }}</span>
            <span class="git-shell__commit-meta"
              ><span>{{ commit.author }}</span
              ><code>{{ commit.sha.slice(0, 8) }}</code></span
            >
            <time class="git-shell__commit-date" :datetime="commit.date">{{
              date(commit.date)
            }}</time>
          </button>
          <button
            v-if="hasMore"
            type="button"
            class="git-button git-shell__more"
            :disabled="loading"
            @click="emit('history', true)"
          >
            {{ loading ? $t('common.loading') : $t('git.more') }}
          </button>
        </div>
      </template>
      <form v-else class="git-shell__branch-form" @submit.prevent="emit('compare')">
        <label
          ><span>{{ $t('git.baseBranch') }}</span
          ><GitSelect v-model="baseBranch" :label="$t('git.baseBranch')" :options="refOptions"
        /></label>
        <label
          ><span>{{ $t('git.headBranch') }}</span
          ><GitSelect v-model="headBranch" :label="$t('git.headBranch')" :options="headOptions"
        /></label>
        <button type="submit" class="git-button" :disabled="!baseBranch || comparing">
          {{ $t('git.compare') }}
        </button>
      </form>
    </aside>
  </div>
</template>
