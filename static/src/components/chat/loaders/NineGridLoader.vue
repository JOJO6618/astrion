<template>
  <div class="nine-grid-loader">
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
    <div class="box"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by Nawsome (modified)
</script>

<style scoped>
.nine-grid-loader {
  position: relative;
  width: 20px;
  height: 20px;
}

.box {
  float: left;
  position: relative;
  width: 3px;
  height: 3px;
  margin-right: 4px;
}

.box:before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  background: var(--text-tertiary);
}

.box:nth-child(3n) {
  margin-right: 0;
  margin-bottom: 4px;
}

.box:nth-child(1):before,
.box:nth-child(4):before {
  margin-left: 7px;
}

.box:nth-child(3):before {
  margin-top: 14px;
}

.box:last-child {
  margin-bottom: 0;
}

.box:nth-child(1) {
  animation: moveBox-1 4s infinite;
}
.box:nth-child(2) {
  animation: moveBox-2 4s infinite;
}
.box:nth-child(3) {
  animation: moveBox-3 4s infinite;
}
.box:nth-child(4) {
  animation: moveBox-4 4s infinite;
}
.box:nth-child(5) {
  animation: moveBox-5 4s infinite;
}
.box:nth-child(6) {
  animation: moveBox-6 4s infinite;
}
.box:nth-child(7) {
  animation: moveBox-7 4s infinite;
}
.box:nth-child(8) {
  animation: moveBox-8 4s infinite;
}
.box:nth-child(9) {
  animation: moveBox-9 4s infinite;
}

@keyframes moveBox-1 {
  9% {
    transform: translate(-7px, 0);
  }
  18% {
    transform: translate(0, 0);
  }
  27% {
    transform: translate(0, 0);
  }
  36% {
    transform: translate(7px, 0);
  }
  45% {
    transform: translate(7px, 7px);
  }
  54% {
    transform: translate(7px, 7px);
  }
  63% {
    transform: translate(7px, 7px);
  }
  72% {
    transform: translate(7px, 0);
  }
  81% {
    transform: translate(0, 0);
  }
  90% {
    transform: translate(-7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-2 {
  9% {
    transform: translate(0, 0);
  }
  18% {
    transform: translate(7px, 0);
  }
  27% {
    transform: translate(0, 0);
  }
  36% {
    transform: translate(7px, 0);
  }
  45% {
    transform: translate(7px, 7px);
  }
  54% {
    transform: translate(7px, 7px);
  }
  63% {
    transform: translate(7px, 7px);
  }
  72% {
    transform: translate(7px, 7px);
  }
  81% {
    transform: translate(0, 7px);
  }
  90% {
    transform: translate(0, 7px);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-3 {
  9% {
    transform: translate(-7px, 0);
  }
  18% {
    transform: translate(-7px, 0);
  }
  27% {
    transform: translate(0, 0);
  }
  36% {
    transform: translate(-7px, 0);
  }
  45% {
    transform: translate(-7px, 0);
  }
  54% {
    transform: translate(-7px, 0);
  }
  63% {
    transform: translate(-7px, 0);
  }
  72% {
    transform: translate(-7px, 0);
  }
  81% {
    transform: translate(-7px, -7px);
  }
  90% {
    transform: translate(0, -7px);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-4 {
  9% {
    transform: translate(-7px, 0);
  }
  18% {
    transform: translate(-7px, 0);
  }
  27% {
    transform: translate(-7px, -7px);
  }
  36% {
    transform: translate(0, -7px);
  }
  45% {
    transform: translate(0, 0);
  }
  54% {
    transform: translate(0, -7px);
  }
  63% {
    transform: translate(0, -7px);
  }
  72% {
    transform: translate(0, -7px);
  }
  81% {
    transform: translate(-7px, -7px);
  }
  90% {
    transform: translate(-7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-5 {
  9% {
    transform: translate(0, 0);
  }
  18% {
    transform: translate(0, 0);
  }
  27% {
    transform: translate(0, 0);
  }
  36% {
    transform: translate(7px, 0);
  }
  45% {
    transform: translate(7px, 0);
  }
  54% {
    transform: translate(7px, 0);
  }
  63% {
    transform: translate(7px, 0);
  }
  72% {
    transform: translate(7px, 0);
  }
  81% {
    transform: translate(7px, -7px);
  }
  90% {
    transform: translate(0, -7px);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-6 {
  9% {
    transform: translate(0, 0);
  }
  18% {
    transform: translate(-7px, 0);
  }
  27% {
    transform: translate(-7px, 0);
  }
  36% {
    transform: translate(0, 0);
  }
  45% {
    transform: translate(0, 0);
  }
  54% {
    transform: translate(0, 0);
  }
  63% {
    transform: translate(0, 0);
  }
  72% {
    transform: translate(0, 7px);
  }
  81% {
    transform: translate(-7px, 7px);
  }
  90% {
    transform: translate(-7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-7 {
  9% {
    transform: translate(7px, 0);
  }
  18% {
    transform: translate(7px, 0);
  }
  27% {
    transform: translate(7px, 0);
  }
  36% {
    transform: translate(0, 0);
  }
  45% {
    transform: translate(0, -7px);
  }
  54% {
    transform: translate(7px, -7px);
  }
  63% {
    transform: translate(0, -7px);
  }
  72% {
    transform: translate(0, -7px);
  }
  81% {
    transform: translate(0, 0);
  }
  90% {
    transform: translate(7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-8 {
  9% {
    transform: translate(0, 0);
  }
  18% {
    transform: translate(-7px, 0);
  }
  27% {
    transform: translate(-7px, -7px);
  }
  36% {
    transform: translate(0, -7px);
  }
  45% {
    transform: translate(0, -7px);
  }
  54% {
    transform: translate(0, -7px);
  }
  63% {
    transform: translate(0, -7px);
  }
  72% {
    transform: translate(0, -7px);
  }
  81% {
    transform: translate(7px, -7px);
  }
  90% {
    transform: translate(7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}

@keyframes moveBox-9 {
  9% {
    transform: translate(-7px, 0);
  }
  18% {
    transform: translate(-7px, 0);
  }
  27% {
    transform: translate(0, 0);
  }
  36% {
    transform: translate(-7px, 0);
  }
  45% {
    transform: translate(0, 0);
  }
  54% {
    transform: translate(0, 0);
  }
  63% {
    transform: translate(-7px, 0);
  }
  72% {
    transform: translate(-7px, 0);
  }
  81% {
    transform: translate(-14px, 0);
  }
  90% {
    transform: translate(-7px, 0);
  }
  100% {
    transform: translate(0, 0);
  }
}
</style>
