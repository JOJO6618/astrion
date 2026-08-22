<template>
  <div class="leap-frog-loader">
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
  </div>
</template>

<script setup lang="ts">
// Based on Uiverse (dovatgabriel), square-styled
</script>

<style scoped>
.leap-frog-loader {
  --size: 24px;
  --speed: 2s;
  position: relative;
  width: var(--size);
  height: var(--size);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dot {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  animation: leap var(--speed) ease infinite;
}

.dot::before {
  content: '';
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
}

.dot:nth-child(2) {
  transform: translateX(calc(var(--size) * 0.4));
  animation-delay: calc(var(--speed) / -1.5);
}

.dot:nth-child(3) {
  transform: translateX(calc(var(--size) * 0.8));
  animation-delay: calc(var(--speed) / -3);
}

@keyframes leap {
  0% {
    transform: translateX(0) rotate(0deg);
  }
  33.333% {
    transform: translateX(0) rotate(180deg);
  }
  66.666% {
    transform: translateX(calc(var(--size) * -0.4)) rotate(180deg);
  }
  99.999% {
    transform: translateX(calc(var(--size) * -0.8)) rotate(180deg);
  }
  100% {
    transform: translateX(0) rotate(0deg);
  }
}
</style>
