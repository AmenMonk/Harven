<template>
  <div class="base-card"><slot /></div>
</template>

<script setup>
</script>

<style scoped>
.base-card {
  background: var(--c-card);
  border: 1px solid var(--c-line);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  padding: 16px;
}
</style>
