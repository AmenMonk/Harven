<template>
  <!-- 运行时绑定：/static 由 Flask 托管，不参与 Vite 构建解析 -->
  <img :src="DUCK_SRC" alt="哈文" :style="{ width: sizeCss, height: sizeCss }" class="duck-logo" />
</template>

<script setup>
// 官方鸭子品牌组件 — 铁律 5：一律用 static/icon-duck.png，禁 emoji 代替
// ⚠️ Vue 的 :style 数字不会自动补 px（React 才会），不补会导致图片按原图尺寸渲染糊满全屏
import { computed } from 'vue'
const props = defineProps({ px: { type: [Number, String], default: 28 } })
const sizeCss = computed(() => (typeof props.px === 'number' ? `${props.px}px` : props.px))
const DUCK_SRC = '/static/icon-duck.png'
</script>

<style scoped>
/* ★ 2026-09-13 开发者令：icon-duck.png 源图已原生 22.5% 圆角（1024px 高清），
   此处不再做 CSS 裁切——双重裁切是小尺寸发糊的根因 */
.duck-logo {
  display: inline-block; vertical-align: middle;
}
</style>
