<template>
  <div class="five-squares-loader">
    <div id="sq1"></div>
    <div id="sq2"></div>
    <div id="sq3"></div>
    <div id="sq4"></div>
    <div id="sq5"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by Nawsome (modified)
</script>

<style scoped>
.five-squares-loader {
  --square: 3px;
  --offset: 7.5px;
  width: calc(3 * var(--offset) + var(--square));
  height: calc(2 * var(--offset) + var(--square));
  position: relative;
}

.five-squares-loader div {
  display: inline-block;
  background: var(--text-tertiary);
  width: var(--square);
  height: var(--square);
  position: absolute;
}

.five-squares-loader #sq1 {
  left: 0;
  top: 0;
  animation: square1 2.4s 0.2s ease-in-out infinite;
}

.five-squares-loader #sq2 {
  left: 0;
  top: var(--offset);
  animation: square2 2.4s 0.2s ease-in-out infinite;
}

.five-squares-loader #sq3 {
  left: var(--offset);
  top: var(--offset);
  animation: square3 2.4s 0.2s ease-in-out infinite;
}

.five-squares-loader #sq4 {
  left: calc(2 * var(--offset));
  top: var(--offset);
  animation: square4 2.4s 0.2s ease-in-out infinite;
}

.five-squares-loader #sq5 {
  left: calc(3 * var(--offset));
  top: var(--offset);
  animation: square5 2.4s 0.2s ease-in-out infinite;
}

@keyframes square1 {
  0% {
    left: 0;
    top: 0;
  }
  8.33% {
    left: 0;
    top: var(--offset);
  }
  100% {
    left: 0;
    top: var(--offset);
  }
}

@keyframes square2 {
  0% {
    left: 0;
    top: var(--offset);
  }
  8.33% {
    left: 0;
    top: calc(2 * var(--offset));
  }
  16.67% {
    left: var(--offset);
    top: calc(2 * var(--offset));
  }
  25% {
    left: var(--offset);
    top: var(--offset);
  }
  83.33% {
    left: var(--offset);
    top: var(--offset);
  }
  91.67% {
    left: var(--offset);
    top: 0;
  }
  100% {
    left: 0;
    top: 0;
  }
}

@keyframes square3 {
  0%,
  100% {
    left: var(--offset);
    top: var(--offset);
  }
  16.67% {
    left: var(--offset);
    top: var(--offset);
  }
  25% {
    left: var(--offset);
    top: 0;
  }
  33.33% {
    left: calc(2 * var(--offset));
    top: 0;
  }
  41.67% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
  66.67% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
  75% {
    left: calc(2 * var(--offset));
    top: calc(2 * var(--offset));
  }
  83.33% {
    left: var(--offset);
    top: calc(2 * var(--offset));
  }
  91.67% {
    left: var(--offset);
    top: var(--offset);
  }
}

@keyframes square4 {
  0% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
  33.33% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
  41.67% {
    left: calc(2 * var(--offset));
    top: calc(2 * var(--offset));
  }
  50% {
    left: calc(3 * var(--offset));
    top: calc(2 * var(--offset));
  }
  58.33% {
    left: calc(3 * var(--offset));
    top: var(--offset);
  }
  100% {
    left: calc(3 * var(--offset));
    top: var(--offset);
  }
}

@keyframes square5 {
  0% {
    left: calc(3 * var(--offset));
    top: var(--offset);
  }
  50% {
    left: calc(3 * var(--offset));
    top: var(--offset);
  }
  58.33% {
    left: calc(3 * var(--offset));
    top: 0;
  }
  66.67% {
    left: calc(2 * var(--offset));
    top: 0;
  }
  75% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
  100% {
    left: calc(2 * var(--offset));
    top: var(--offset);
  }
}
</style>
