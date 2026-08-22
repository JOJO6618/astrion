<template>
  <div class="wave-matrix-loader">
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
  </div>
</template>

<script setup lang="ts">
// 15. 波浪点阵（方块统一版）
</script>

<style scoped>
.wave-matrix-loader {
  --size: 28px;
  --speed: 0.7s;
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: var(--size);
  height: 16px;
}

.sq {
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
  animation: wave var(--speed) ease-in-out infinite;
}

.sq:nth-child(1) {
  animation-delay: calc(var(--speed) * -0.45);
}
.sq:nth-child(2) {
  animation-delay: calc(var(--speed) * -0.3);
}
.sq:nth-child(3) {
  animation-delay: calc(var(--speed) * -0.15);
}
.sq:nth-child(4) {
  animation-delay: 0s;
}

@keyframes wave {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-8px);
  }
}
</style>
