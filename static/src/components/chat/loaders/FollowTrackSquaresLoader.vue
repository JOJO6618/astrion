<template>
  <div class="follow-track-loader">
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
    <div class="sq"></div>
  </div>
</template>

<script setup lang="ts">
// 16. 跟随轨迹（方块统一版）
</script>

<style scoped>
.follow-track-loader {
  width: 10px;
  height: 22px;
  position: relative;
}

.sq {
  width: 2px;
  height: 2px;
  position: absolute;
  left: 50%;
  margin-left: -1px;
  background: var(--text-tertiary);
  animation: follow-track 1.25s infinite;
}

.sq:nth-child(1) {
  top: 0;
  animation-delay: 0.15s;
}
.sq:nth-child(2) {
  top: 5px;
  animation-delay: 0.3s;
}
.sq:nth-child(3) {
  top: 10px;
  animation-delay: 0.45s;
}
.sq:nth-child(4) {
  top: 15px;
  animation-delay: 0.6s;
}
.sq:nth-child(5) {
  top: 20px;
  animation-delay: 0.75s;
}

@keyframes follow-track {
  0% {
    transform: rotate(0deg) translateY(-4px);
  }
  60%,
  100% {
    transform: rotate(360deg) translateY(-4px);
  }
}
</style>
