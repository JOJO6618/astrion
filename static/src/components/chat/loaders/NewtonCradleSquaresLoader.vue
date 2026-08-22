<template>
  <div class="newton-loader">
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
    <div class="dot"></div>
  </div>
</template>

<script setup lang="ts">
// Based on Uiverse (dovatgabriel), square-styled
</script>

<style scoped>
.newton-loader {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dot {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  width: 25%;
  transform-origin: center top;
}

.dot::after {
  content: '';
  position: absolute;
  top: 75%;
  width: 3px;
  height: 3px;
  background: var(--text-tertiary);
}

.dot:first-child {
  animation: swing 1.2s linear infinite;
}

.dot:last-child {
  animation: swing2 1.2s linear infinite;
}

@keyframes swing {
  0% {
    transform: rotate(0deg);
    animation-timing-function: ease-out;
  }
  25% {
    transform: rotate(70deg);
    animation-timing-function: ease-in;
  }
  50% {
    transform: rotate(0deg);
    animation-timing-function: linear;
  }
}

@keyframes swing2 {
  0% {
    transform: rotate(0deg);
    animation-timing-function: linear;
  }
  50% {
    transform: rotate(0deg);
    animation-timing-function: ease-out;
  }
  75% {
    transform: rotate(-70deg);
    animation-timing-function: ease-in;
  }
}
</style>
