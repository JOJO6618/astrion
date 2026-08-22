<template>
  <div class="ripple-loader">
    <div class="cell d-0"></div>
    <div class="cell d-1"></div>
    <div class="cell d-2"></div>
    <div class="cell d-1"></div>
    <div class="cell d-2"></div>
    <div class="cell d-2"></div>
    <div class="cell d-3"></div>
    <div class="cell d-3"></div>
    <div class="cell d-4"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by alexruix (modified)
</script>

<style scoped>
.ripple-loader {
  --cell-size: 3px;
  --cell-gap: 4px;
  display: grid;
  grid-template-columns: repeat(3, var(--cell-size));
  grid-template-rows: repeat(3, var(--cell-size));
  gap: var(--cell-gap);
  width: calc(3 * var(--cell-size) + 2 * var(--cell-gap));
  height: calc(3 * var(--cell-size) + 2 * var(--cell-gap));
}

.cell {
  width: var(--cell-size);
  height: var(--cell-size);
  background-color: transparent;
  animation: 1.5s ripple ease infinite;
}

.cell.d-1 {
  animation-delay: 100ms;
}

.cell.d-2 {
  animation-delay: 200ms;
}

.cell.d-3 {
  animation-delay: 300ms;
}

.cell.d-4 {
  animation-delay: 400ms;
}

.cell:nth-child(1),
.cell:nth-child(2),
.cell:nth-child(3),
.cell:nth-child(4),
.cell:nth-child(5),
.cell:nth-child(6),
.cell:nth-child(7),
.cell:nth-child(8),
.cell:nth-child(9) {
  --cell-color: var(--text-tertiary);
}

@keyframes ripple {
  0% {
    background-color: transparent;
  }

  30% {
    background-color: var(--cell-color);
  }

  60% {
    background-color: transparent;
  }

  100% {
    background-color: transparent;
  }
}
</style>
