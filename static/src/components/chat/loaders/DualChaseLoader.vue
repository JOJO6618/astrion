<template>
  <div class="dual-chase-loader"></div>
</template>

<script setup lang="ts">
// From Uiverse.io by satyamchaudharydev (modified)
</script>

<style scoped>
.dual-chase-loader {
  --size: 6px;
  width: 16px;
  height: 16px;
  position: relative;
}

.dual-chase-loader::after,
.dual-chase-loader::before {
  box-sizing: border-box;
  position: absolute;
  content: '';
  width: var(--size);
  height: var(--size);
  top: 50%;
  animation: chase-up 2.4s cubic-bezier(0, 0, 0.24, 1.21) infinite;
  left: 50%;
  background: var(--text-tertiary);
}

.dual-chase-loader::after {
  top: calc(50% - var(--size));
  left: calc(50% - var(--size));
  animation: chase-down 2.4s cubic-bezier(0, 0, 0.24, 1.21) infinite;
}

@keyframes chase-down {
  0%,
  100% {
    transform: none;
  }

  25% {
    transform: translateX(100%);
  }

  50% {
    transform: translateX(100%) translateY(100%);
  }

  75% {
    transform: translateY(100%);
  }
}

@keyframes chase-up {
  0%,
  100% {
    transform: none;
  }

  25% {
    transform: translateX(-100%);
  }

  50% {
    transform: translateX(-100%) translateY(-100%);
  }

  75% {
    transform: translateY(-100%);
  }
}
</style>
