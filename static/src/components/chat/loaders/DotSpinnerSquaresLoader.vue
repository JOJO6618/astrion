<template>
  <div class="dot-spinner-loader">
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
  </div>
</template>

<script setup lang="ts">
// Based on Uiverse (abrahamcalsin), square-styled
</script>

<style scoped>
.dot-spinner-loader {
  --size: 22px;
  --speed: 0.9s;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  width: var(--size);
  height: var(--size);
}

.dot {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  width: 100%;
  height: 100%;
}

.dot::before {
  content: '';
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
  transform: scale(0);
  opacity: 0.5;
  animation: pulse calc(var(--speed) * 1.111) ease-in-out infinite;
}

.dot:nth-child(2) {
  transform: rotate(45deg);
}
.dot:nth-child(2)::before {
  animation-delay: calc(var(--speed) * -0.875);
}
.dot:nth-child(3) {
  transform: rotate(90deg);
}
.dot:nth-child(3)::before {
  animation-delay: calc(var(--speed) * -0.75);
}
.dot:nth-child(4) {
  transform: rotate(135deg);
}
.dot:nth-child(4)::before {
  animation-delay: calc(var(--speed) * -0.625);
}
.dot:nth-child(5) {
  transform: rotate(180deg);
}
.dot:nth-child(5)::before {
  animation-delay: calc(var(--speed) * -0.5);
}
.dot:nth-child(6) {
  transform: rotate(225deg);
}
.dot:nth-child(6)::before {
  animation-delay: calc(var(--speed) * -0.375);
}
.dot:nth-child(7) {
  transform: rotate(270deg);
}
.dot:nth-child(7)::before {
  animation-delay: calc(var(--speed) * -0.25);
}
.dot:nth-child(8) {
  transform: rotate(315deg);
}
.dot:nth-child(8)::before {
  animation-delay: calc(var(--speed) * -0.125);
}

@keyframes pulse {
  0%,
  100% {
    transform: scale(0);
    opacity: 0.5;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
