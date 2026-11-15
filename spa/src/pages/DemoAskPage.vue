<template>
  <PageShell :title="t('page.demoAsk.t')" icon="bulb" :sub="t('page.demoAsk.s')">
    <template #actions>
      <BigButton :px="14" @click="resetAll">全部重来</BigButton>
    </template>

    <div class="demo-wrap">
      <div class="row-label">① 状态一：待选择（哈文主动反问）</div>
      <SuggestionCard
        question="你想让我把这份账单按月汇总，还是按分类汇总？"
        :options="['按月汇总', '按分类汇总']"
        state="ask"
        @pick="pick('A', $event)"
      />
      <div class="picked" v-if="picked.A">你选了：<b>{{ picked.A }}</b></div>

      <div class="row-label">② 状态二：已回答（收起成一行，可回看）</div>
      <SuggestionCard
        question="要顺便把今天的天气也查了吗？"
        :options="['查一下', '不用了']"
        state="answered"
        :answer="picked.B || '（还没选）'"
        @pick="pick('B', $event)"
      />

      <div class="row-label">③ 状态三：已忽略（淡显，可恢复）</div>
      <SuggestionCard
        question="要不要把这个习惯记进画像？"
        :options="['记住', '不用']"
        state="dismissed"
        @pick="pick('C', $event)"
        @restore="picked.C = ''"
      />
      <div class="picked" v-if="picked.C">你选了：<b>{{ picked.C }}</b></div>

      <div class="row-label">④ 四个可出现的位置（同一个组件复用）</div>
      <div class="pos-demo">
        <div class="pos-box">
          <span class="pos-tag">聊天流中</span>
          <SuggestionCard question="上面提到的 3 个省钱建议，要写进记账面板备注吗？" :options="['写进去', '不用']" state="ask" compact @pick="() => {}" />
        </div>
        <div class="pos-box">
          <span class="pos-tag">输入框上方</span>
          <SuggestionCard question="看你在看车，要不要帮你记一下途锐的保养里程？" :options="['帮我记', '不用']" state="ask" compact @pick="() => {}" />
        </div>
        <div class="pos-box">
          <span class="pos-tag">面板空态</span>
          <SuggestionCard question="记账面板还是空的，要我生成一个月度汇总表吗？" :options="['生成', '先不用']" state="ask" compact @pick="() => {}" />
        </div>
        <div class="pos-box">
          <span class="pos-tag">定时任务完成后</span>
          <SuggestionCard question="每天 8 点的天气播报跑完了，要改成 7 点半吗？" :options="['改时间', '保持 8 点']" state="ask" compact @pick="() => {}" />
        </div>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { reactive, ref } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import BigButton from '../components/BigButton.vue'
import SuggestionCard from '../components/SuggestionCard.vue'

const picked = reactive({ A: '', B: '', C: '' })
function pick(k, v) { picked[k] = v }
function resetAll() { picked.A = ''; picked.B = ''; picked.C = '' }
</script>

<style scoped>
.demo-wrap { max-width: 720px; display: flex; flex-direction: column; gap: 12px; }
.row-label { font-size: 13px; font-weight: 700; color: var(--sub); margin-top: 8px; }
.picked { font-size: 12.5px; color: var(--green); padding-left: 6px; }

.pos-demo { display: grid; grid-template-columns: repeat(2, 1fr); gap: 11px; }
.pos-box { background: var(--c-bg-soft); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 9px; }
.pos-tag { font-size: 11px; font-weight: 700; color: var(--blue); }

@media (max-width: 700px) { .pos-demo { grid-template-columns: 1fr; } }
</style>
