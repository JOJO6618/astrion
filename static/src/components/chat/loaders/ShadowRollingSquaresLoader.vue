<template>
  <div class="shadow-rolling-loader"></div>
</template>

<script setup lang="ts">
// Based on Uiverse (alexruix), square-styled
</script>

<style scoped>
.shadow-rolling-loader {
  --sq: 3px;
  width: var(--sq);
  height: var(--sq);
  position: relative;
  left: -10px;
  color: var(--text-tertiary);
  animation: roll 2s linear infinite;
}

@keyframes roll {
  0% {
    box-shadow:
      0 0 transparent,
      0 0 transparent,
      0 0 transparent,
      0 0 transparent;
  }
  12% {
    box-shadow:
      6px 0 currentColor,
      0 0 transparent,
      0 0 transparent,
      0 0 transparent;
  }
  25% {
    box-shadow:
      12px 0 currentColor,
      6px 0 currentColor,
      0 0 transparent,
      0 0 transparent;
  }
  36% {
    box-shadow:
      18px 0 currentColor,
      12px 0 currentColor,
      6px 0 currentColor,
      0 0 transparent;
  }
  50% {
    box-shadow:
      24px 0 currentColor,
      18px 0 currentColor,
      12px 0 currentColor,
      6px 0 currentColor;
  }
  62% {
    box-shadow:
      36px 0 transparent,
      24px 0 currentColor,
      18px 0 currentColor,
      12px 0 currentColor;
  }
  75% {
    box-shadow:
      36px 0 transparent,
      36px 0 transparent,
      24px 0 currentColor,
      18px 0 currentColor;
  }
  87% {
    box-shadow:
      36px 0 transparent,
      36px 0 transparent,
      36px 0 transparent,
      24px 0 currentColor;
  }
  100% {
    box-shadow:
      36px 0 transparent,
      36px 0 transparent,
      36px 0 transparent,
      36px 0 transparent;
  }
}
</style>
