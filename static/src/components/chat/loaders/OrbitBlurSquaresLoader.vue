<template>
  <div class="orbit-loader">
    <div class="orbe" style="--index: 0"></div>
    <div class="orbe" style="--index: 1"></div>
    <div class="orbe" style="--index: 2"></div>
    <div class="orbe" style="--index: 3"></div>
    <div class="orbe" style="--index: 4"></div>
  </div>
</template>

<script setup lang="ts">
// Based on Uiverse (krlozCJ), square + blur, no glow
</script>

<style scoped>
.orbit-loader {
  --size-loader: 24px;
  --size-orbe: 3px;
  width: var(--size-loader);
  height: var(--size-loader);
  position: relative;
  transform: rotate(45deg);
}

.orbe {
  position: absolute;
  width: 100%;
  height: 100%;
  --delay: calc(var(--index) * 0.1s);
  opacity: calc(1 - calc(0.18 * var(--index)));
  animation: orbit 1.5s var(--delay) ease-in-out infinite;
}

.orbe::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: var(--size-orbe);
  height: var(--size-orbe);
  background: var(--text-tertiary);
  filter: blur(0.35px);
}

@keyframes orbit {
  0% {
  }
  80% {
    transform: rotate(360deg);
  }
  100% {
    transform: rotate(360deg);
  }
}
</style>
