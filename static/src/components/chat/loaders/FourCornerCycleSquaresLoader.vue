<template>
  <div class="four-corner-loader">
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
  </div>
</template>

<script setup lang="ts">
// 17. 四角循环（方块统一版）
</script>

<style scoped>
.four-corner-loader {
  width: 24px;
  height: 24px;
  position: relative;
}

.sq {
  width: 3px;
  height: 3px;
  position: absolute;
  top: 50%;
  left: 50%;
  margin-top: -1.5px;
  margin-left: -1.5px;
  background: var(--text-tertiary);
  animation: corner-cycle 1.8s ease-in-out infinite;
}

.sq:nth-child(1) {
  animation-delay: -0.45s;
}
.sq:nth-child(2) {
  animation-delay: -0.9s;
}
.sq:nth-child(3) {
  animation-delay: -1.35s;
}
.sq:nth-child(4) {
  animation-delay: -1.8s;
}

@keyframes corner-cycle {
  0%,
  100% {
    transform: translate(-6px, -6px);
  }
  25% {
    transform: translate(6px, -6px);
  }
  50% {
    transform: translate(6px, 6px);
  }
  75% {
    transform: translate(-6px, 6px);
  }
}
</style>
