<template>
  <div class="bouncing-squares-loader">
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by mobinkakei (modified)
</script>

<style scoped>
.bouncing-squares-loader {
  --loader-scale: 0.45;
  --square-size: 7.6px; /* 7.6 * 0.45 ≈ 3.42px，略大一点 */
  --square-gap: 8.9px; /* 固定间距，缩放后约 4px */
  --start-x: 3px;
  position: relative;
  width: 40px;
  height: 20px;
  transform: scale(var(--loader-scale));
  transform-origin: center;
}

.square {
  position: absolute;
  width: var(--square-size);
  height: var(--square-size);
  left: 15%;
  background-color: var(--text-tertiary);
  transform-origin: 50%;
  animation: bounce-square 0.5s alternate infinite ease;
}

@keyframes bounce-square {
  0% {
    top: 20px;
    height: calc(var(--square-size) * 0.667);
    transform: scaleX(1.7);
  }
  40% {
    height: var(--square-size);
    transform: scaleX(1);
  }
  100% {
    top: 0%;
  }
}

.square:nth-child(1) {
  left: var(--start-x);
}

.square:nth-child(2) {
  left: calc(var(--start-x) + var(--square-size) + var(--square-gap));
  animation-delay: 0.2s;
}

.square:nth-child(3) {
  left: calc(var(--start-x) + (var(--square-size) + var(--square-gap)) * 2);
  animation-delay: 0.3s;
}
</style>
