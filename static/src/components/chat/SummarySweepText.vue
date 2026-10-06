<template>
  <span ref="root" class="summary-sweep-text" :class="{ live: animate }" :aria-label="text">
    <span ref="outgoing" class="summary-sweep-outgoing" aria-hidden="true"></span>
    <span ref="outgoingBeam" class="summary-sweep-outgoing summary-sweep-outgoing-beam" aria-hidden="true"></span>
    <span ref="content" class="summary-sweep-content" aria-hidden="true">
      <span ref="base" class="summary-sweep-base" :class="{ replaced: displayedReel }"></span>
      <span ref="incoming" class="summary-sweep-incoming"></span>
      <SummaryToolReel v-if="displayedReel" v-bind="displayedReel" />
    </span>
    <span ref="beam" class="summary-sweep-beam" aria-hidden="true"></span>
  </span>
</template>

<script setup lang="ts">
import { ref, shallowRef, watch } from 'vue';
import { useSummarySweep } from '@/composables/useSummarySweep';
import type { SummaryReelView } from '@/composables/useSummaryToolReel';
import type { SummarySweepInput } from './summarySweepController';
import SummaryToolReel from './SummaryToolReel.vue';

const props = withDefaults(defineProps<SummarySweepInput & { reelView?: SummaryReelView }>(), {
  ready: true,
  forceComplete: false
});
const emit = defineEmits<{ settled: [input: SummarySweepInput] }>();
const settledIdentity = ref('');
const displayedReel = shallowRef<SummaryReelView | null>(null);
const root = ref<HTMLElement | null>(null);
const content = ref<HTMLElement | null>(null);
const outgoing = ref<HTMLElement | null>(null);
const outgoingBeam = ref<HTMLElement | null>(null);
const base = ref<HTMLElement | null>(null);
const incoming = ref<HTMLElement | null>(null);
const beam = ref<HTMLElement | null>(null);

// The surface snapshots the visible reel frame before installing a new label.
// A new reel can only take over once its incoming text has fully appeared.
watch(() => props.reelView, (view) => {
  if (view && props.kind === 'tool' && props.ready !== false &&
      settledIdentity.value === props.identity) displayedReel.value = view;
}, { flush: 'post' });

useSummarySweep(root, content, base, incoming, beam, outgoing, outgoingBeam, () => props, (input) => {
  settledIdentity.value = input.identity;
  if (input.kind === 'tool' && input.identity === props.identity && props.reelView) {
    displayedReel.value = props.reelView;
  }
  emit('settled', input);
}, () => {
  settledIdentity.value = '';
  displayedReel.value = null;
});
</script>

<style scoped>
.summary-sweep-text {
  position: relative;
  display: block;
  height: 26px;
  line-height: 26px;
  overflow: hidden;
  color: inherit;
}

.summary-sweep-text.live {
  color: var(--summary-text);
}

.summary-sweep-content,
.summary-sweep-outgoing {
  position: absolute;
  inset: 0;
  height: 26px;
  mask-repeat: no-repeat;
  mask-position: 0 0;
  mask-mode: alpha;
}

.summary-sweep-outgoing {
  pointer-events: none;
}

.summary-sweep-outgoing-beam {
  color: var(--summary-sweep);
}

.summary-sweep-base.replaced {
  visibility: hidden;
}

.summary-sweep-base,
.summary-sweep-incoming,
.summary-sweep-beam {
  position: absolute;
  top: 0;
  left: 0;
  display: block;
  width: max-content;
  height: 26px;
  line-height: 26px;
  white-space: pre;
  mask-repeat: no-repeat;
  mask-position: 0 0;
  mask-mode: alpha;
  pointer-events: none;
}

.summary-sweep-beam {
  color: var(--summary-sweep);
  mask-image: linear-gradient(transparent, transparent);
  will-change: mask-image;
}
</style>
