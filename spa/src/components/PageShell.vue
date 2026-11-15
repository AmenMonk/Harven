<template>
  <div class="page pshell">
    <header class="page-head">
      <div class="head-l">
        <!-- ★ 2026-09-13 用户反馈：子页面没有哈文头像不知道怎么回首页 → 头像可点回首页 -->
        <button class="home-link" title="返回首页" @click="router.push('/')">
          <DuckLogo :px="34" />
        </button>
        <button class="back" title="返回" @click="goBack"><HIcon name="x" :size="15" /></button>
        <h1><HIcon :name="icon" :size="22" /> {{ title }}</h1>
      </div>
      <div class="head-actions"><slot name="actions" /></div>
    </header>
    <p v-if="sub" class="sub">{{ sub }}</p>
    <div class="pshell-body" :class="{ flush }"><slot /></div>
  </div>
</template>

<script setup>
import HIcon from './HIcon.vue'
import DuckLogo from './DuckLogo.vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' },
  icon: { type: String, default: 'grid' },
  flush: { type: Boolean, default: false }, // true = 内容区自己管 padding（如全出血卡墙）
})
const router = useRouter()

function goBack() {
  // ⚠️ 不能只看 history.length —— 从百度等外部页面进来时 length>1，
  // router.back() 会直接退出哈文回到外部网页（"点一下就回不去了"）。
  // vue-router 4 会在 history.state.back 记录站内上一跳，只有它存在才后退。
  const st = router.options.history.state
  if (st && st.back) router.back()
  else router.push('/')
}
</script>

<style scoped>
.pshell { display: flex; flex-direction: column; height: 100%; overflow: auto; }
.page-head { display: flex; justify-content: space-between; align-items: center; padding: 18px 26px 0; }
.head-l { display: flex; align-items: center; gap: 10px; }
.home-link {
  border: none; background: none; cursor: pointer; padding: 0;
  border-radius: 10px; line-height: 0; transition: transform .15s;
}
.home-link:hover { transform: scale(1.08); }
.back {
  width: 30px; height: 30px; border-radius: 9px; border: 1px solid var(--line);
  background: #fff; color: var(--sub); cursor: pointer; display: flex; align-items: center; justify-content: center;
}
.back:hover { color: var(--blue); border-color: var(--blue); }
.page-head h1 { display: flex; align-items: center; gap: 9px; font-size: 20px; margin: 0; }
.head-actions { display: flex; align-items: center; gap: 12px; }
.sub { font-size: 13px; color: var(--sub); margin: 5px 26px 0 66px; }
.pshell-body { flex: 1; padding: 16px 26px 26px; min-height: 0; }
.pshell-body.flush { padding: 16px 0 26px; }
/* ═══ #847 方案A 移动端全面适配：顶栏两行化（标题横排 + 按钮横滑，根治竖排字） ═══
   19 个页面全走本组件，这里一处改 = 全站手机端顶栏修复 */
@media (max-width: 760px) {
  .page-head { flex-wrap: wrap; row-gap: 8px; padding: 10px 12px 0; }
  .head-l { flex: 1 1 100%; min-width: 0; }
  .home-link { flex-shrink: 0; }
  .back { width: 34px; height: 34px; flex-shrink: 0; }
  .page-head h1 { flex: 1; min-width: 0; font-size: 17px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .head-actions { flex: 1 1 100%; justify-content: flex-start; gap: 8px; overflow-x: auto; padding-bottom: 2px; -webkit-overflow-scrolling: touch; }
  .head-actions :deep(.big-btn) { flex-shrink: 0; height: 38px; padding: 0 16px; font-size: 13px; }
  .sub { margin: 4px 12px 0; }
  .pshell-body { padding: 10px 12px calc(14px + env(safe-area-inset-bottom)); }
  .pshell-body.flush { padding: 10px 0 calc(14px + env(safe-area-inset-bottom)); }
}
</style>
