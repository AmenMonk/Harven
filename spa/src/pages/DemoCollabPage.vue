<template>
  <PageShell :title="t('page.demoCollab.t')" icon="users" :sub="t('page.demoCollab.s')">
    <template #actions>
      <BigButton :px="14" :loading="running" @click="replay"><HIcon name="send" :size="16" /> 重播一遍</BigButton>
    </template>

    <div class="demo-wrap">
      <div class="stage">
        <div class="bubble user">帮我看看家里的开支情况，给点省钱建议</div>

        <!-- 协作进度卡 -->
        <div v-if="started" class="collab-card" :class="{ collapsed: done }">
          <button class="cc-head" @click="expanded = !expanded">
            <StatusDot :status="done ? 'ok' : 'busy'" />
            <b>🤝 协作中 · {{ stageText }}</b>
            <span class="cc-pct">{{ pct }}%</span>
            <HIcon :name="expanded ? 'x' : 'dots'" :size="14" class="fold" />
          </button>
          <div v-if="expanded" class="cc-body">
            <div v-for="(s, i) in STEPS" :key="s" class="cc-step" :class="{ on: i < step, doing: i === step && !done }">
              <span class="cc-idx">{{ i < step ? '✓' : i + 1 }}</span>
              {{ s }}
            </div>
            <div class="cc-bar"><i :style="{ width: pct + '%' }" /></div>
            <div class="cc-note">本地数据不出门，只有你选好的内容才会发给外脑分析</div>
          </div>
        </div>

        <!-- 答案 -->
        <div v-if="done" class="bubble bot">
          🤝 协作产出 · 本地数据 + 外脑分析 + 本地整合<br /><br />
          你家这个月开支约 ¥8,240，其中餐饮 ¥2,900（35%）占比最高。三个建议：<br />
          ① 外卖频次每周 9 次 → 降到 5 次，约省 ¥600/月<br />
          ② 两个孩子兴趣班有重复项（美术 ×2），可合并<br />
          ③ 车险下月到期，提前比价约省 ¥800/年
        </div>
      </div>

      <div class="explain">
        <b>这张卡讲了什么：</b>
        <span>① 云地协作时，本地哈文先把家里记账面板的数据按隐私档位脱敏，发给外脑分析；</span>
        <span>② 外脑只负责出主意，最终整合仍由本地模型完成 —— 所以答案前有 🤝 标注；</span>
        <span>③ 跑完后进度卡自动收起成一行，点一下可以展开回看。</span>
      </div>
    </div>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import StatusDot from '../components/StatusDot.vue'

const STEPS = ['本地读取数据（记账面板）', '按隐私档位脱敏', '发给外脑分析', '外脑返回建议', '本地模型整合答案']
const stageTexts = ['准备数据…', '数据脱敏中…', '外脑思考中…', '收到建议', '整合答案…']

const started = ref(false)
const done = ref(false)
const expanded = ref(true)
const step = ref(0)
const running = ref(false)
let timer = null

const pct = computed(() => done.value ? 100 : Math.round((step.value / STEPS.length) * 100))
const stageText = computed(() => done.value ? '完成' : (stageTexts[Math.min(step.value, stageTexts.length - 1)]))

function replay() {
  running.value = true
  started.value = false
  done.value = false
  expanded.value = true
  step.value = 0
  setTimeout(() => { started.value = true }, 500)
  clearInterval(timer)
  timer = setInterval(() => {
    if (step.value < STEPS.length) step.value++
    else { done.value = true; running.value = false; clearInterval(timer) }
  }, 1100)
}

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.demo-wrap { max-width: 760px; display: flex; flex-direction: column; gap: 16px; }
.stage { background: var(--c-bg-soft); border-radius: 14px; padding: 18px; display: flex; flex-direction: column; gap: 12px; min-height: 320px; }
.bubble { max-width: 78%; padding: 11px 15px; border-radius: 14px; font-size: 13.5px; line-height: 1.7; }
.bubble.user { align-self: flex-end; background: var(--blue); color: #fff; border-bottom-right-radius: 4px; }
.bubble.bot { align-self: flex-start; background: #fff; border: 1px solid var(--line); border-bottom-left-radius: 4px; }

.collab-card { align-self: flex-start; width: min(420px, 92%); background: var(--mode-cloud-bg); border: 1px solid #DCCEF8; border-radius: 13px; overflow: hidden; }
.collab-card.collapsed { opacity: .85; }
.cc-head { width: 100%; display: flex; align-items: center; gap: 8px; background: transparent; border: none; padding: 11px 14px; cursor: pointer; font-size: 13px; }
.cc-head b { flex: 1; text-align: left; color: var(--mode-cloud); }
.cc-pct { font-size: 12px; font-weight: 700; color: var(--mode-cloud); }
.fold { color: var(--mode-cloud); }
.cc-body { padding: 4px 14px 12px; border-top: 1px dashed #DCCEF8; }
.cc-step { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--sub); padding: 4px 0; }
.cc-step.on { color: var(--ink); }
.cc-step.doing { color: var(--mode-cloud); font-weight: 700; }
.cc-idx { width: 18px; height: 18px; border-radius: 50%; background: #fff; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; }
.cc-step.on .cc-idx { background: var(--mode-cloud); color: #fff; }
.cc-bar { height: 7px; border-radius: 6px; background: #fff; overflow: hidden; margin-top: 7px; }
.cc-bar i { display: block; height: 100%; background: var(--mode-cloud); border-radius: 6px; transition: width .5s; }
.cc-note { font-size: 11px; color: var(--sub); margin-top: 7px; }

.explain { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 13px 16px; display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: var(--sub); }
.explain b { color: var(--ink); }
</style>
