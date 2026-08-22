<template>
  <div class="jumping-seq-loader">
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
  </div>
</template>

<script setup lang="ts">
// Based on Uiverse (EddyBel), square-styled
</script>

<style scoped>
.jumping-seq-loader {
  position: relative;
  display: flex;
  gap: 5px;
}

.sq {
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
  animation: jump 2s ease infinite;
}

.sq:nth-child(2n) {
  animation-delay: 300ms;
}
.sq:nth-child(3n) {
  animation-delay: 600ms;
}

@keyframes jump {
  0%,
  100% {
    transform: translateY(0);
  }
  25% {
    transform: translateY(-10px) scale(0.66);
  }
  50% {
    transform: translateY(0);
  }
  75% {
    transform: translateY(3px) scale(0.9);
  }
}
</style>
