<template>
  <div class="chaotic-orbit-loader"></div>
</template>

<script setup lang="ts">
// Based on Uiverse (dovatgabriel), square-styled
</script>

<style scoped>
.chaotic-orbit-loader {
  --size: 24px;
  --speed: 1.5s;
  position: relative;
  width: var(--size);
  height: var(--size);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: rotate calc(var(--speed) * 1.667) linear infinite;
}

.chaotic-orbit-loader::before,
.chaotic-orbit-loader::after {
  content: '';
  position: absolute;
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
  will-change: transform;
}

.chaotic-orbit-loader::before {
  animation: orbit var(--speed) linear infinite;
}
.chaotic-orbit-loader::after {
  animation: orbit var(--speed) linear calc(var(--speed) / -2) infinite;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes orbit {
  0% {
    transform: translate(12px) scale(0.74);
    opacity: 0.65;
  }
  25% {
    transform: translate(0) scale(0.47);
    opacity: 0.3;
  }
  50% {
    transform: translate(-12px) scale(0.74);
    opacity: 0.65;
  }
  75% {
    transform: translate(0) scale(1);
    opacity: 1;
  }
  100% {
    transform: translate(12px) scale(0.74);
    opacity: 0.65;
  }
}
</style>
