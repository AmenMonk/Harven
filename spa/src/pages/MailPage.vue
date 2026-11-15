<template>
  <PageShell :title="t('page.mail.t')" icon="mail" :sub="t('page.mail.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="load(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <div class="mail-grid">
      <!-- 配置向导 -->
      <section class="mbox big">
        <div class="mt2"><HIcon name="key" :size="17" class="ic" /> 第一步 · 配置邮箱</div>
        <p class="m-desc">填好下面三样，点保存。哈文会<b>当场试连一次</b>——连得上才存，连不上马上告诉你要改哪。</p>
        <label>邮箱地址<input v-model.trim="form.email" type="email" placeholder="例如 yourname@qq.com" /></label>
        <label>邮箱类型
          <select v-model="form.provider">
            <option value="qq">QQ 邮箱 ✅ 推荐</option>
            <option value="163">163 网易邮箱 ✅ 推荐</option>
            <option value="126">126 网易邮箱 ✅ 推荐</option>
            <option value="gmail">Gmail ⚠️ 不推荐</option>
            <option value="outlook">Outlook ⚠️ 不推荐</option>
            <option value="other">其他（IMAP/SMTP）</option>
          </select>
        </label>
        <label>授权码<input v-model.trim="form.token" type="password" placeholder="不是登录密码！按右边步骤去邮箱设置里生成" /></label>
        <BigButton :px="14" :loading="saving" @click="save">保存并试连</BigButton>
        <div class="m-note">💡 点「保存并试连」后等 3 秒左右：出现「已实测连通」就配好了；报错会直接告诉你是授权码不对还是网络不通</div>
      </section>

      <!-- 分服务商授权码指引 -->
      <section class="mbox big">
        <div class="mt2"><HIcon name="chat" :size="17" class="ic" /> 授权码怎么拿（跟着点就行）</div>
        <div v-for="(s, i) in guide.steps" :key="i" class="step"><span class="n">{{ i + 1 }}</span>{{ s }}</div>
        <div v-if="guide.warn" class="m-warn">{{ guide.warn }}</div>
        <div class="m-note">🔑 通用道理：授权码 ≠ 登录密码。国内邮箱都要先在<b>网页版邮箱设置</b>里开启「IMAP/SMTP 服务」（手机发短信验证），然后才给你一串 16 位字母授权码——只显示一次，复制存好。</div>
      </section>
    </div>

    <!-- 配好之后怎么用 -->
    <section class="mbox big" style="margin-top:14px;max-width:980px;">
      <div class="mt2"><HIcon name="chat" :size="17" class="ic" /> 第二步 · 聊天里直接用</div>
      <p class="m-desc">配置好后不需要打开任何页面，在聊天框说话就行：</p>
      <div class="say"><code>帮我看看今天有什么新邮件</code><span>→ 哈文会列出未读邮件摘要</span></div>
      <div class="say"><code>给 38552540@qq.com 发一封邮件，主题是会议提醒，内容写明天上午十点开会</code><span>→ 发送前哈文一定会先给你确认</span></div>
      <div class="say"><code>搜一下上个月王总发来的报价单</code><span>→ 按条件搜索历史邮件</span></div>
      <div class="m-warn">🛡️ 安全规则：哈文发邮件前必须经过你的确认（这是写死的安全底线，关不掉）</div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON } from '../api.js'

const form = reactive({ email: '', provider: 'qq', token: '' })
const saving = ref(false)
const msg = ref('')
const msgOk = ref(false)

// ★ #965-③ 分服务商授权码指引（讲透每一步，不再一行小字）
const GUIDES = {
  qq: { steps: [
    '电脑浏览器打开 mail.qq.com 并登录（手机版页面没有这个入口，要用电脑）',
    '点右上角「设置」→ 选「账户」这一页',
    '往下翻到「POP3/IMAP/SMTP/Exchange/CardDAV 服务」，找到「IMAP/SMTP 服务」，点「开启」',
    '按弹窗提示用绑定手机发一条短信验证',
    '验证后页面弹出一串 16 位字母授权码（只显示这一次，马上复制存好）',
    '把授权码填到左边，点「保存并试连」——3 秒内告诉您通没通',
  ], warn: '' },
  163: { steps: [
    '电脑浏览器打开 mail.163.com 并登录',
    '顶部「设置」→「POP3/SMTP/IMAP」（新版界面在「设置」左侧菜单里）',
    '开启「IMAP/SMTP 服务」，按提示用手机发短信验证',
    '验证后弹出一串 16 位字母授权码（只显示一次，复制存好）',
    '把授权码填到左边，点「保存并试连」',
    '哈文会自动向网易自报身份（这是网易的特殊要求，已内置）',
  ], warn: '⚠️ 163 有时会因「异地/新设备登录」再拦一次：试连报错时回邮箱网页把这个服务关掉重开一次，再试' },
  126: { steps: [
    '电脑浏览器打开 mail.126.com 并登录',
    '顶部「设置」→「POP3/SMTP/IMAP」',
    '开启「IMAP/SMTP 服务」，按提示用手机发短信验证',
    '验证后弹出一串 16 位字母授权码（只显示一次，复制存好）',
    '把授权码填到左边，点「保存并试连」',
  ], warn: '⚠️ 126 与 163 同属网易，规则一样：报错时回网页把服务关掉重开一次再试' },
  gmail: { steps: [
    'Gmail 需要「应用专用密码」（要先开两步验证）',
    '⚠️ 但国内网络基本连不上 Gmail 服务器，配了也白配',
  ], warn: '❌ 不推荐：需要稳定的国际网络 + 应用专用密码，门槛高。建议换 QQ/163/126' },
  outlook: { steps: [
    '微软在逐步关闭「授权码直连」这种旧登录方式',
    '部分账号还能生成应用密码：account.microsoft.com → 安全 → 高级安全选项',
  ], warn: '❌ 不推荐：微软收紧认证后经常配不通。建议换 QQ/163/126' },
  other: { steps: [
    '需要向你的邮箱服务商要两样东西：IMAP 服务器地址和 SMTP 服务器地址',
    '开启服务商的 IMAP 服务并生成授权码',
    '填地址 + 授权码，点「保存并试连」实测',
  ], warn: '' },
}
const guide = computed(() => GUIDES[form.provider] || GUIDES.other)

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 8000)
}

async function load(showErr = false) {
  try {
    const j = await getJSON('/api/email/setup')
    const cfg = j.config || j || {}
    if (cfg.email) form.email = cfg.email
    if (cfg.provider) form.provider = cfg.provider
  } catch (e) { if (showErr) flash('配置读取失败（可能还没配置过）：' + e.message, false) }
}

async function save() {
  if (!form.email || !form.token) { flash('邮箱地址和授权码都要填', false); return }
  saving.value = true
  try {
    const j = await postJSON('/api/email/setup', { ...form })
    // ★ #965-② 后端已实测：成功=真连通；失败=人话报错（授权码不对/网络不通/服务没开）
    if (j.success) flash('✅ 已实测连通，配置保存好了！去聊天框说「帮我看看新邮件」试试', true)
    else flash(j.error || '保存失败（原因不明，看哈文日志）', false)
  } catch (e) { flash('保存失败：' + e.message, false) }
  finally { saving.value = false }
}

onMounted(() => load(true))
</script>

<style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }

.mail-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; align-items: start; max-width: 980px; }
.mbox { border: 1px solid var(--line); border-radius: 14px; background: #fff; padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.mt2 { display: flex; align-items: center; gap: 7px; font-size: 14px; font-weight: 700; }
.mt2 .ic { color: var(--blue); }
.m-desc { font-size: 12.5px; color: var(--sub); margin: 0; line-height: 1.6; }
.mbox label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--sub); }
.mbox input, .mbox select { border: 1px solid var(--line); border-radius: 9px; padding: 9px 12px; font-size: 13px; outline: none; background: #fff; color: var(--ink); }
.mbox input:focus, .mbox select:focus { border-color: var(--blue); }
.m-note { font-size: 11.5px; color: var(--sub); background: var(--c-bg-soft); border-radius: 8px; padding: 8px 11px; line-height: 1.6; }
.step { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; line-height: 1.55; color: var(--ink); }
.step .n { flex: 0 0 18px; height: 18px; line-height: 18px; text-align: center; border-radius: 50%; background: var(--blue); color: #fff; font-size: 11px; font-weight: 700; transform: translateY(2px); }
.say { display: flex; flex-direction: column; gap: 2px; background: var(--c-bg-soft); border-radius: 9px; padding: 9px 12px; }
.say code { font-size: 12.5px; font-weight: 600; word-break: break-all; }
.say span { font-size: 11.5px; color: var(--sub); }
.m-warn { font-size: 12px; color: #9A6B1F; background: #FDF8EE; border-radius: 8px; padding: 8px 11px; }

@media (max-width: 900px) { .mail-grid { grid-template-columns: 1fr; } }
</style>
