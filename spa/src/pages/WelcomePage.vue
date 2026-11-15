<template>
  <div class="page welcome">
    <div class="w-card">
      <div class="w-logo"><DuckLogo :px="60" /></div>
      <h1>你好，我是{{ instName }} 👋</h1>
      <p class="w-sub">你的本地 AI 助手 —— 装在你自己的电脑上，数据不出门</p>

      <!-- ★ 2026-10-09 强制改密卡：初始密码 harven2026 是公开的，登录后必须换掉（不可跳过） -->
      <div class="w-login" v-if="needChange">
        <div class="w-forced-title">🔒 请先把密码换成你自己的</div>
        <p class="w-forced-sub">现在的密码 <code>harven2026</code> 是所有新装用户的默认密码（公开的），<br/>手机远程、保险箱、面板确认都用它——不换等于大门钥匙人人有</p>
        <input v-model.trim="chgNew" type="password" placeholder="新密码（至少 6 位）" autocomplete="new-password" />
        <input v-model.trim="chgNew2" type="password" placeholder="再输一遍新密码" autocomplete="new-password" @keyup.enter="finishChange" />
        <BigButton :px="15" :loading="chgBusy" @click="finishChange"><HIcon name="send" :size="16" /> 保存并进入</BigButton>
        <div v-if="chgErr" class="w-err">{{ chgErr }}</div>
      </div>

      <!-- 登录 -->
      <div class="w-login" v-else-if="!entered && mode !== 'forgot'">
        <input v-model.trim="password" type="password" placeholder="输入管理密码进入" @keyup.enter="enter" />
        <BigButton :px="15" @click="enter"><HIcon name="send" :size="16" /> 进入哈文</BigButton>
        <div class="w-hint">第一次用？默认密码是 <code>harven2026</code>，进去后会让你换成自己的</div>
        <button class="w-forgot-link" @click="openForgot">忘记密码？</button>
        <div v-if="loginErr" class="w-err">{{ loginErr }}</div>
      </div>

      <!-- ★ #1362（2026-11-02）忘记密码恢复：安全问题 / 恢复码 双通道 -->
      <div class="w-login" v-else-if="mode === 'forgot'">
        <div class="w-forced-title">🔑 找回密码</div>
        <template v-if="forgotSet">
          <div class="w-forgot-tabs">
            <button :class="{ on: forgotTab === 'qa' }" @click="forgotTab = 'qa'">答安全问题</button>
            <button :class="{ on: forgotTab === 'code' }" @click="forgotTab = 'code'">用恢复码</button>
          </div>
          <template v-if="forgotTab === 'qa'">
            <div class="w-forgot-q">{{ forgotQ1 }}</div>
            <input v-model.trim="fA1" type="text" placeholder="你的答案" />
            <div class="w-forgot-q">{{ forgotQ2 }}</div>
            <input v-model.trim="fA2" type="text" placeholder="你的答案" />
          </template>
          <template v-else>
            <div class="w-forgot-q">输入你保存的恢复码（形如 XXXX-XXXX-XXXX-XXXX）</div>
            <input v-model.trim="fCode" type="text" placeholder="XXXX-XXXX-XXXX-XXXX" />
          </template>
          <input v-model.trim="fNew" type="password" placeholder="新密码（至少 6 位）" />
          <input v-model.trim="fNew2" type="password" placeholder="再输一遍新密码" />
          <BigButton :px="15" :loading="fBusy" @click="doReset"><HIcon name="send" :size="16" /> 重置密码</BigButton>
        </template>
        <p v-else class="w-forced-sub">这台哈文还没有设置密码找回方式。<br/>自救办法：打开哈文文件夹里的 <code>config.yaml</code> 文件，<br/>找到 <code>admin_password</code> 那一行，冒号后面就是当前密码。<br/>下次登录后记得在 设置 → 安全 里设置安全问题。</p>
        <div v-if="fErr" class="w-err">{{ fErr }}</div>
        <button class="w-forgot-link" @click="mode = ''">返回登录</button>
      </div>

      <!-- 进入后：硬件概览 + 入口 -->
      <template v-else>
        <div class="hw-grid">
          <div class="hw-item"><span>显卡</span><b>{{ hw?.gpu_name || '检测中…' }}</b></div>
          <div class="hw-item"><span>显存</span><b>{{ hw?.vram_gb ?? '—' }} GB</b></div>
          <div class="hw-item"><span>内存</span><b>{{ hw?.ram_gb ?? '—' }} GB</b></div>
        </div>
        <div class="w-tip">
          你的电脑跑 7B~14B 的本地对话模型很轻松；更大的模型可以去「模型安装」页按标注挑选。
        </div>
        <div class="w-btns">
          <BigButton :px="15" @click="$router.push('/')"><HIcon name="chat" :size="16" /> 开始聊天</BigButton>
          <BigButton variant="ghost" :px="15" @click="$router.push('/models/install')"><HIcon name="download" :size="16" /> 挑选模型</BigButton>
          <BigButton variant="ghost" :px="15" @click="$router.push('/settings')"><HIcon name="gear" :size="16" /> 设置</BigButton>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import DuckLogo from '../components/DuckLogo.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const password = ref('')
const entered = ref(false)
const loginErr = ref('')
const hw = ref(null)
/* ★ 2026-10-09 强制改密：初始密码 harven2026 是公开的，登录成功后必须换掉才能用 */
const needChange = ref(false)
const chgNew = ref('')
const chgNew2 = ref('')
const chgErr = ref('')
const chgBusy = ref(false)
/* ★ 2026-09-22 (#851): 实例名，拉不到就用默认「哈文」 */
const instName = ref('哈文')
/* ★ 2026-09-22 (#853): 电脑设备令牌（登录时登记，设置页可看到/改名/移除） */
const DEV_KEY = 'harven_device_token'

async function enter() {
  if (!password.value) { loginErr.value = '先输入密码'; return }
  try {
    let devTok = ''
    try { devTok = localStorage.getItem(DEV_KEY) || '' } catch (_) {}
    const j = await postJSON('/api/security/verify-password', { password: password.value, register_device: true, device_token: devTok })
    if (j.success || j.valid) {
      loginErr.value = ''
      try { if (j.device_token) localStorage.setItem(DEV_KEY, j.device_token) } catch (_) {}
      if (password.value === 'harven2026') { needChange.value = true; chgNew.value = ''; chgNew2.value = '' }
      else entered.value = true
    }
    else loginErr.value = '密码不对，再试试（默认 harven2026）'
  } catch (e) { loginErr.value = '验证失败：' + e.message }
}

/* ★ 2026-10-09 强制改密提交（后端 change_admin_password 会同步远程锁/保险箱/面板，一处改全局生效） */
async function finishChange() {
  if (!chgNew.value || !chgNew2.value) { chgErr.value = '两行都要填'; return }
  if (chgNew.value.length < 6) { chgErr.value = '新密码至少 6 位'; return }
  if (chgNew.value !== chgNew2.value) { chgErr.value = '两次输入的不一样'; return }
  if (chgNew.value === 'harven2026') { chgErr.value = '新密码不能还是默认密码'; return }
  chgBusy.value = true; chgErr.value = ''
  try {
    const j = await postJSON('/api/security/change-password', { old_password: 'harven2026', new_password: chgNew.value })
    if (j.success) { needChange.value = false; entered.value = true }
    else chgErr.value = '没改成：' + (j.error || '再试试')
  } catch (e) { chgErr.value = '没改成：' + e.message }
  finally { chgBusy.value = false }
}

/* ★ #1362（2026-11-02）忘记密码恢复 */
const mode = ref('')            // ''=登录 / 'forgot'=找回
const forgotSet = ref(false)
const forgotQ1 = ref('')
const forgotQ2 = ref('')
const forgotTab = ref('qa')
const fA1 = ref('')
const fA2 = ref('')
const fCode = ref('')
const fNew = ref('')
const fNew2 = ref('')
const fErr = ref('')
const fBusy = ref(false)

async function openForgot() {
  mode.value = 'forgot'
  fErr.value = ''
  try {
    const h = await getJSON('/api/security/recovery/hints')
    forgotSet.value = !!h.set
    forgotQ1.value = h.q1 || ''
    forgotQ2.value = h.q2 || ''
  } catch (_) { forgotSet.value = false }
}

async function doReset() {
  if (!fNew.value || fNew.value.length < 6) { fErr.value = '新密码至少 6 位'; return }
  if (fNew.value !== fNew2.value) { fErr.value = '两次输入的新密码不一样'; return }
  fBusy.value = true; fErr.value = ''
  try {
    const j = forgotTab.value === 'qa'
      ? await postJSON('/api/security/recovery/reset-by-answers', { a1: fA1.value, a2: fA2.value, new_password: fNew.value })
      : await postJSON('/api/security/recovery/reset-by-code', { code: fCode.value, new_password: fNew.value })
    if (j.success) {
      mode.value = ''
      password.value = ''
      loginErr.value = ''
      fErr.value = ''
      window.alert('密码已重置，请用新密码登录')
    }
    else fErr.value = '没改成：' + (j.error || '再试试')
  } catch (e) { fErr.value = '没改成：' + e.message }
  finally { fBusy.value = false }
}

onMounted(async () => {
  try { hw.value = await getJSON('/api/models/installer/hardware') } catch (_) {}
  try { const si = await getJSON('/api/system/info'); const n = (si.instance_name || '').trim(); if (n) instName.value = n } catch (_) {}
})
</script>

<style scoped>
.welcome { height: 100%; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 24px; }
.w-card { width: min(520px, 94vw); background: #fff; border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow-card); padding: 34px 34px 28px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.w-logo { margin-bottom: 4px; }
h1 { font-size: 23px; margin: 0; }
.w-sub { font-size: 13px; color: var(--sub); margin: 0 0 8px; }

.w-login { display: flex; flex-direction: column; gap: 10px; width: 100%; max-width: 340px; }
.w-login input { border: 1px solid var(--line); border-radius: 11px; padding: 11px 14px; font-size: 14px; outline: none; text-align: center; }
.w-login input:focus { border-color: var(--blue); }
.w-hint { font-size: 11.5px; color: var(--sub); }
.w-hint code { background: var(--c-bg-soft); border-radius: 6px; padding: 1px 7px; font-weight: 700; }
.w-err { font-size: 12px; color: var(--red); }
.w-forced-title { font-size: 16px; font-weight: 700; }
.w-forced-sub { font-size: 12px; color: var(--sub); line-height: 1.7; margin: 0; }
.w-forced-sub code { background: #FFF3E0; border-radius: 6px; padding: 1px 7px; font-weight: 700; color: #C76A00; }

.w-forgot-link { background: none; border: none; color: var(--blue); font-size: 12px; cursor: pointer; padding: 0; }
.w-forgot-link:hover { text-decoration: underline; }
.w-forgot-tabs { display: flex; gap: 6px; }
.w-forgot-tabs button { flex: 1; border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 7px 0; font-size: 12.5px; cursor: pointer; color: var(--sub); }
.w-forgot-tabs button.on { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F0F6FF; }
.w-forgot-q { font-size: 12.5px; color: var(--sub); text-align: left; line-height: 1.5; }
.w-forgot-q code { background: var(--c-bg-soft); border-radius: 6px; padding: 1px 6px; }

.hw-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; width: 100%; }
.hw-item { background: var(--c-bg-soft); border-radius: 11px; padding: 11px 8px; display: flex; flex-direction: column; gap: 2px; }
.hw-item span { font-size: 11px; color: var(--sub); }
.hw-item b { font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.w-tip { font-size: 12px; color: var(--sub); background: #F6F8FB; border-radius: 10px; padding: 9px 13px; line-height: 1.6; }
.w-btns { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-top: 4px; }
</style>
