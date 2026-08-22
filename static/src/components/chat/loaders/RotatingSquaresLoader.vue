<template>
  <div class="rotating-squares-loader">
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
    <div class="square"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by ZacharyCrespin (modified)
</script>

<style scoped>
@keyframes square-animation {
  0% {
    left: 0;
    top: 0;
  }
  10.5% {
    left: 0;
    top: 0;
  }
  12.5% {
    left: 8px;
    top: 0;
  }
  23% {
    left: 8px;
    top: 0;
  }
  25% {
    left: 16px;
    top: 0;
  }
  35.5% {
    left: 16px;
    top: 0;
  }
  37.5% {
    left: 16px;
    top: 8px;
  }
  48% {
    left: 16px;
    top: 8px;
  }
  50% {
    left: 8px;
    top: 8px;
  }
  60.5% {
    left: 8px;
    top: 8px;
  }
  62.5% {
    left: 8px;
    top: 16px;
  }
  73% {
    left: 8px;
    top: 16px;
  }
  75% {
    left: 0;
    top: 16px;
  }
  85.5% {
    left: 0;
    top: 16px;
  }
  87.5% {
    left: 0;
    top: 8px;
  }
  98% {
    left: 0;
    top: 8px;
  }
  100% {
    left: 0;
    top: 0;
  }
}

.rotating-squares-loader {
  position: relative;
  width: 24px;
  height: 24px;
  transform: rotate(45deg);
}

.square {
  position: absolute;
  top: 0;
  left: 0;
  width: 3px;
  height: 3px;
  margin: 0.5px;
  background: var(--text-tertiary);
  animation: square-animation 10s ease-in-out infinite both;
}

.square:nth-of-type(1) {
  animation-delay: -1.43s;
}

.square:nth-of-type(2) {
  animation-delay: -2.86s;
}

.square:nth-of-type(3) {
  animation-delay: -4.29s;
}

.square:nth-of-type(4) {
  animation-delay: -5.71s;
}

.square:nth-of-type(5) {
  animation-delay: -7.14s;
}

.square:nth-of-type(6) {
  animation-delay: -8.57s;
}

.square:nth-of-type(7) {
  animation-delay: -10s;
}
</style>
