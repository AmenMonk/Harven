<template>
  <button class="big-btn" :class="[`t-${type}`, { block }]" :disabled="disabled" @click="$emit('click')">
    <HIcon v-if="icon" :name="icon" :size="18" />
    <span><slot /></span>
  </button>
</template>

<script setup>
import HIcon from './HIcon.vue'
defineProps({
  type: { type: String, default: 'primary' },  // primary | soft | ghost | danger
  icon: { type: String, default: '' },
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['click'])
</script>

<style scoped>
.big-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 42px; padding: 0 22px; border-radius: var(--radius-btn);
  font-size: 14px; font-weight: 600; cursor: pointer; border: 1px solid transparent;
  transition: filter .15s, background .15s;
  white-space: nowrap; flex-shrink: 0; /* ★ #847 手机端窄屏不被挤压成一字一行 */
}
.big-btn:disabled { opacity: .5; cursor: not-allowed; }
.t-primary { background: var(--c-primary); color: #fff; }
.t-primary:hover:not(:disabled) { filter: brightness(1.08); }
.t-soft { background: var(--c-primary-weak); color: var(--c-primary); }
.t-ghost { background: transparent; border-color: var(--c-line); color: var(--c-text); }
.t-danger { background: var(--c-danger); color: #fff; }
.block { width: 100%; }
</style>
