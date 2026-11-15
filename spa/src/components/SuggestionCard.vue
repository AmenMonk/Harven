<template>
  <div class="suggestion-card" :class="[`st-${normState}`, { compact }]">
    <!-- 选项问询模式：question + options -->
    <template v-if="isQuiz">
      <div class="sg-head">
        <HIcon name="bulb" :size="18" class="sg-icon" />
        <span class="sg-title">{{ question }}</span>
      </div>
      <div v-if="normState === 'answered'" class="sg-answer">
        <span class="sg-ans-label">你的选择：</span><b>{{ answer }}</b>
        <button v-if="answerable" class="sg-look" @click="expandAnswer = !expandAnswer">回看全部选项</button>
        <div v-if="expandAnswer" class="sg-opts">
          <button v-for="o in options" :key="o" class="sg-opt ghosted" @click="$emit('pick', o)">{{ o }}</button>
        </div>
      </div>
      <template v-else-if="normState !== 'dismissed'">
        <div class="sg-opts">
          <button v-for="o in options" :key="o" class="sg-opt" @click="$emit('pick', o)">{{ o }}</button>
        </div>
        <button class="sg-skip" @click="$emit('dismiss')">暂时不管</button>
      </template>
      <div v-else class="sg-answer dim">
        <span>已忽略</span>
        <button class="sg-look" @click="$emit('restore')">恢复</button>
      </div>
    </template>

    <!-- 建议模式（B0 原版，保持兼容）：title + description + 采纳/暂不 -->
    <template v-else>
      <div class="sg-head">
        <HIcon name="bulb" :size="18" class="sg-icon" />
        <span class="sg-title">{{ title }}</span>
        <button v-if="closable && normState !== 'dismissed'" class="sg-close" @click="$emit('dismiss')"><HIcon name="x" :size="14" /></button>
      </div>
      <p class="sg-desc">{{ description }}</p>
      <div class="sg-actions" v-if="normState === 'pending'">
        <BigButton type="primary" @click="$emit('accept')">{{ acceptText }}</BigButton>
        <BigButton type="ghost" @click="$emit('dismiss')">暂不</BigButton>
      </div>
      <p v-else-if="normState === 'accepted'" class="sg-answer"><b>✓ 已采纳</b></p>
      <p v-else class="sg-dismissed">已忽略该建议</p>
    </template>
  </div>
</template>

<script setup>
// 建议卡 / 询问提示卡 — B16 的地基，一套组件双模式：
// ① 建议模式：title + description + 采纳/暂不（B0 兼容）
// ② 问询模式：question + options[] + pick 事件
// 三状态（问询）：ask 待选 / answered 已回答 / dismissed 已忽略
import { computed, ref } from 'vue'
import HIcon from './HIcon.vue'
import BigButton from './BigButton.vue'

const props = defineProps({
  /* 建议模式 */
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  acceptText: { type: String, default: '采纳' },
  closable: { type: Boolean, default: true },
  /* 问询模式 */
  question: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  answer: { type: String, default: '' },
  answerable: { type: Boolean, default: true },
  /* 共用 */
  state: { type: String, default: 'ask' }, // ask|pending → 待处理；answered|accepted；dismissed
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['accept', 'dismiss', 'close', 'pick', 'restore'])

const isQuiz = computed(() => (props.options || []).length > 0 || !!props.question)
const normState = computed(() => {
  const s = props.state
  if (s === 'accepted') return 'answered'
  if (['ask', 'pending', 'answered', 'dismissed'].includes(s)) return s
  return 'ask'
})
const expandAnswer = ref(false)
</script>

<style scoped>
.suggestion-card {
  background: var(--c-card); border: 1px solid var(--c-line);
  border-radius: var(--radius-card); box-shadow: var(--shadow-card);
  padding: 14px 16px; max-width: 420px;
}
.suggestion-card.compact { padding: 11px 13px; max-width: none; }
.sg-head { display: flex; align-items: center; gap: 8px; }
.sg-icon { color: var(--mode-vertical); }
.sg-title { font-weight: 700; flex: 1; font-size: 13.5px; }
.sg-close { border: none; background: none; cursor: pointer; color: var(--c-text-2); padding: 2px; }
.sg-desc { color: var(--c-text-2); margin: 8px 0 12px; font-size: 13px; }
.sg-actions { display: flex; gap: 10px; }
.sg-actions :deep(.big-btn) { height: 34px; padding: 0 16px; }
.sg-dismissed { color: var(--c-text-2); font-size: 12px; margin: 4px 0 0; }

/* 问询模式 */
.sg-opts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.sg-opt { border: 1px solid var(--c-primary-weak); background: #F5F8FE; color: var(--c-primary); border-radius: 20px; padding: 6px 16px; font-size: 12.5px; cursor: pointer; font-weight: 600; }
.sg-opt:hover { background: var(--c-primary); color: #fff; }
.sg-opt.ghosted { opacity: .6; }
.sg-skip { margin-top: 8px; border: none; background: none; color: var(--c-text-2); font-size: 11.5px; cursor: pointer; padding: 0; }
.sg-skip:hover { color: var(--c-text); }
.sg-answer { margin-top: 9px; font-size: 12.5px; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.sg-answer.dim { color: var(--c-text-2); }
.sg-ans-label { color: var(--c-text-2); }
.sg-look { border: none; background: none; color: var(--c-primary); font-size: 11.5px; cursor: pointer; padding: 0; text-decoration: underline; }

/* 状态淡显 */
.st-dismissed { opacity: .55; }
.st-answered { border-color: var(--mode-local); }
</style>
