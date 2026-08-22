<template>
  <div class="square-grid-loader">
    <div class="square" id="sq1"></div>
    <div class="square" id="sq2"></div>
    <div class="square" id="sq3"></div>
    <div class="square" id="sq4"></div>
    <div class="square" id="sq5"></div>
    <div class="square" id="sq6"></div>
    <div class="square" id="sq7"></div>
    <div class="square" id="sq8"></div>
    <div class="square" id="sq9"></div>
  </div>
</template>

<script setup lang="ts">
// From Uiverse.io by JkHuger
</script>

<style scoped>
@keyframes loader_5191 {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

.square-grid-loader {
  position: relative;
  width: 16px;
  height: 16px;
}

.square {
  background: var(--text-tertiary);
  width: 3px;
  height: 3px;
  position: absolute;
  top: 50%;
  left: 50%;
  margin-top: -1.5px;
  margin-left: -1.5px;
  border-radius: 0.5px;
}

#sq1 {
  margin-top: -7.5px;
  margin-left: -7.5px;
  animation: loader_5191 675ms ease-in-out 0s infinite alternate;
}

#sq2 {
  margin-top: -7.5px;
  animation: loader_5191 675ms ease-in-out 75ms infinite alternate;
}

#sq3 {
  margin-top: -7.5px;
  margin-left: 4.5px;
  animation: loader_5191 675ms ease-in-out 150ms infinite;
}

#sq4 {
  margin-left: -7.5px;
  animation: loader_5191 675ms ease-in-out 225ms infinite;
}

#sq5 {
  animation: loader_5191 675ms ease-in-out 300ms infinite;
}

#sq6 {
  margin-left: 4.5px;
  animation: loader_5191 675ms ease-in-out 375ms infinite;
}

#sq7 {
  margin-top: 4.5px;
  margin-left: -7.5px;
  animation: loader_5191 675ms ease-in-out 450ms infinite;
}

#sq8 {
  margin-top: 4.5px;
  animation: loader_5191 675ms ease-in-out 525ms infinite;
}

#sq9 {
  margin-top: 4.5px;
  margin-left: 4.5px;
  animation: loader_5191 675ms ease-in-out 600ms infinite;
}
</style>
