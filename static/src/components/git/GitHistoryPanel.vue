<script setup lang="ts">
/**
 * 提交面板：挂在比较范围触发器下的弹层（UiPopover，宽 380）。
 * 提交记录视图 = 分支下拉 + ⋯（分支比较）→ 搜索 → 通栏提交行（hover 直角）；分支比较视图带返回行。
 * 分支下拉与 ⋯ 菜单作为子弹层叠在面板之上，不会把面板关掉（UiPopover 弹层链）。
 */
import { computed, ref, watch } from 'vue';
import { t } from '@/locales';
import UiIconButton from '@/components/ui/UiIconButton.vue';
import UiMenu from '@/components/ui/UiMenu.vue';
import UiPopover from '@/components/ui/UiPopover.vue';
import UiSearchField from '@/components/ui/UiSearchField.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import { shortDate, type Commit } from './api';
type Option = { value: string; label: string };
defineProps<{
  open: boolean;
  anchor?: HTMLElement | null;
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
  (event: 'close', restoreFocus: boolean): void;
  (event: 'select', commit: Commit): void;
  (event: 'history', append: boolean): void;
  (event: 'compare'): void;
}>();

const moreButton = ref<InstanceType<typeof UiIconButton> | null>(null);
const moreAnchor = computed(() => (moreButton.value?.$el as HTMLElement | undefined) || null);
const moreOpen = ref(false);
const moreItems = computed(() => [
  { value: 'compare', label: t('git.compareBranchesAction'), icon: 'git-branch' }
]);
function closeMore(restoreFocus: boolean) {
  moreOpen.value = false;
  if (restoreFocus && !branchMode.value) moreAnchor.value?.focus({ preventScroll: true });
}
// 清空搜索即回到完整列表；输入关键词后回车搜索
watch(search, (value, old) => {
  if (!value && old) emit('history', false);
});
</script>

<template>
  <UiPopover
    :open="open"
    :anchor="anchor"
    role="dialog"
    :label="$t('git.chooseCommit')"
    :min-width="380"
    :max-height="560"
    :padding="0"
    @close="emit('close', $event)"
  >
    <div v-if="!branchMode" class="git-history">
      <div class="git-history__tools">
        <UiSelect
          v-model="branch"
          icon="git-branch"
          :label="$t('git.currentBranch')"
          :options="branchOptions"
          @update:model-value="emit('history', false)"
        />
        <UiIconButton
          ref="moreButton"
          icon="ellipsis"
          :label="$t('common.moreActions')"
          aria-haspopup="menu"
          :aria-expanded="moreOpen"
          @click="moreOpen = !moreOpen"
        />
        <UiMenu
          actions
          align-end
          :open="moreOpen"
          :anchor="moreAnchor"
          :items="moreItems"
          :label="$t('common.moreActions')"
          @select="branchMode = true"
          @close="closeMore"
        />
      </div>
      <form class="git-history__search" @submit.prevent="emit('history', false)">
        <UiSearchField
          v-model="search"
          :placeholder="$t('git.searchCommits')"
          :clear-label="$t('git.clearSearch')"
        />
      </form>
      <div class="git-history__commits" role="listbox" :aria-label="$t('git.history')">
        <div v-if="loading && !commits.length" class="git-empty">{{ $t('common.loading') }}</div>
        <div v-else-if="!commits.length" class="git-empty">{{ $t('git.noCommits') }}</div>
        <button
          v-for="commit in commits"
          :key="commit.sha"
          type="button"
          role="option"
          class="git-history__commit"
          :aria-selected="selectedSha === commit.sha"
          :disabled="reviewing"
          data-popover-item
          @click="emit('select', commit)"
        >
          <span class="git-history__subject">{{ commit.subject || commit.sha.slice(0, 8) }}</span>
          <span class="git-history__meta"
            ><span class="git-history__author">{{ commit.author }}</span
            ><code>{{ commit.sha.slice(0, 8) }}</code
            ><time :datetime="commit.date">{{ shortDate(commit.date) }}</time></span
          >
        </button>
        <button
          v-if="hasMore"
          type="button"
          class="git-button git-button--ghost git-history__more"
          :disabled="loading"
          @click="emit('history', true)"
        >
          {{ loading ? $t('common.loading') : $t('git.more') }}
        </button>
      </div>
    </div>
    <form v-else class="git-compare" @submit.prevent="emit('compare')">
      <div class="git-compare__head">
        <UiIconButton
          icon="arrow-left"
          :label="$t('git.backToHistory')"
          @click="branchMode = false"
        />
        <span>{{ $t('git.compareBranches') }}</span>
      </div>
      <div class="git-compare__body">
        <div class="git-compare__field">
          <span>{{ $t('git.baseBranch') }}</span>
          <UiSelect
            v-model="baseBranch"
            :label="$t('git.baseBranch')"
            :placeholder="$t('git.chooseBranch')"
            :options="refOptions"
          />
        </div>
        <div class="git-compare__field">
          <span>{{ $t('git.headBranch') }}</span>
          <UiSelect
            v-model="headBranch"
            :label="$t('git.headBranch')"
            :placeholder="$t('git.chooseBranch')"
            :options="headOptions"
          />
        </div>
        <button
          type="submit"
          class="git-button git-button--primary"
          :disabled="!baseBranch || comparing"
        >
          {{ $t('git.compare') }}
        </button>
      </div>
    </form>
  </UiPopover>
</template>
