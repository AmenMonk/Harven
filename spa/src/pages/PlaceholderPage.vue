<template>
  <div class="placeholder-page">
    <BaseCard class="ph-card">
      <div class="ph-head">
        <DuckLogo :px="36" />
        <div>
          <h2 class="ph-title">{{ route.meta.title }}</h2>
          <p class="ph-sub">施工表编号 {{ route.meta.id }} · B0 骨架占位页</p>
        </div>
      </div>
      <div class="ph-note">
        <span class="ph-note-label">验收要点</span>
        <p>{{ route.meta.note }}</p>
      </div>
      <div class="ph-demo">
        <span class="ph-demo-label">组件速览</span>
        <div class="ph-demo-row">
          <ModeCapsule mode="cloud" text="外脑 · GLM-5.3" />
          <ModeCapsule mode="local" text="本地 · qwen2.5" />
          <ModeCapsule mode="vertical" text="垂直 · 华佗" />
          <StatusDot status="green" title="在线" />
          <StatusDot status="amber" title="忙碌" />
          <HIcon name="chat" /><HIcon name="gear" /><HIcon name="pulse" />
        </div>
        <SuggestionCard class="ph-sg"
          title="示例建议：开启夜间整理"
          description="这是 B16 建议卡的实时预览，四要素+三状态。"
          @accept="sgState='accepted'" @dismiss="sgState='dismissed'" :state="sgState" />
      </div>
      <p class="ph-tip">本页按《哈文全面制作总方案 v1》灰度原则开发：旧界面不受影响，本页通过验收后再逐页替换。</p>
    </BaseCard>
  </div>
</template>

<script setup>
// 16 页统一占位页 — 每页展示施工表验收要点 + 底座组件速览
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import BaseCard from '../components/BaseCard.vue'
import DuckLogo from '../components/DuckLogo.vue'
import ModeCapsule from '../components/ModeCapsule.vue'
import StatusDot from '../components/StatusDot.vue'
import SuggestionCard from '../components/SuggestionCard.vue'
import HIcon from '../components/HIcon.vue'

const route = useRoute()
const sgState = ref('pending')
</script>

<style scoped>
.placeholder-page { padding: 32px; max-width: 760px; margin: 0 auto; }
.ph-head { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; }
.ph-title { margin: 0; font-size: 20px; }
.ph-sub { margin: 2px 0 0; color: var(--c-text-2); font-size: 12px; }
.ph-note { background: var(--c-bg-soft); border-radius: 10px; padding: 12px 14px; }
.ph-note-label { font-size: 12px; font-weight: 700; color: var(--c-primary); }
.ph-note p { margin: 4px 0 0; font-size: 13px; color: var(--c-text-2); }
.ph-demo { margin-top: 16px; }
.ph-demo-label { font-size: 12px; font-weight: 700; color: var(--c-primary); }
.ph-demo-row { display: flex; align-items: center; gap: 12px; margin-top: 10px; color: var(--c-text); }
.ph-sg { margin-top: 14px; }
.ph-tip { color: var(--c-text-2); font-size: 12px; margin-top: 16px; }
</style>
