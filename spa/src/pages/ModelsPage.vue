<template>
  <PageShell :title="t('page.models.t')" icon="bot" :sub="t('page.models.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
      <a href="#/models/install"><BigButton :px="14"><HIcon name="download" :size="16" /> 装新模型</BigButton></a>
      <a href="#/vision"><BigButton variant="ghost" :px="14"><HIcon name="image" :size="16" /> 视觉引擎</BigButton></a>
      <BigButton variant="ghost" :px="14" :loading="checkupBusy" @click="runCheckup"><HIcon name="dots" :size="16" /> 🔍 模型体检</BigButton>
    </template>

    <!-- #1557 模型体检报告 -->
    <div v-if="checkupReport" class="checkup">
      <div class="ck-head">
        <b>🔍 模型体检报告</b>
        <span class="ck-sum">✅ 已接入 {{ checkupReport.summary?.integrated || 0 }} · ⚠️ 未登记 {{ checkupReport.summary?.known || 0 }} · ❌ 不认识 {{ checkupReport.summary?.unknown || 0 }}</span>
        <button class="ck-x" @click="checkupReport = null">✕</button>
      </div>
      <div v-for="(it, ii) in checkupReport.items" :key="ii" class="ck-row" :class="it.status">
        <span class="ck-st">{{ it.status === 'integrated' ? '✅' : it.status === 'known' ? '⚠️' : '❌' }}</span>
        <div class="ck-body">
          <b>{{ it.name }}</b>
          <span class="ck-hint">{{ it.hint }}</span>
        </div>
      </div>
    </div>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- ★ D1 #1564 识图引导（模型中立：只告知能力位，不推任何品牌） -->
    <div class="vision-guide">
      <b>💡 想让哈文能看图？</b>
      <span>挑模型时认准徽标：<b class="vg-vis">👁️ 支持识图</b> 的模型才能看懂图片；标着 <b class="vg-novis">📄 仅文字对话</b> 的只能打字聊天，看不了图。外脑和本地都一样——外脑配好钥匙就能用，本地装好后零费用。发照片、拍照记账、识别东西，都得用带 👁️ 的模型。</span>
    </div>

    <!-- ═══ 分区一：外脑 ═══ -->
    <h2 class="sec"><span class="dot cloud" /> 外脑 · 云端大脑 <small>聪明但按量花钱，得配钥匙（API Key）</small></h2>
    <div class="foreign-note">
      当前外脑：<b>{{ currentForeign ? provName(currentForeign) : '未选外脑（聊天用本地模型）' }}</b>
      <template v-if="currentForeign && currentModelName"> · 型号：<b>{{ currentModelName }}</b></template>
      <!-- ★ #1326：当前对话走本地时明说，免得误以为外脑选错 -->
      <span v-if="activeLocal" class="fn-note">💬 当前对话走的是本地模型（这里报的是外脑槽位，切回外脑用对话区 🤖按钮）</span>
      <!-- ★ 2026-10-23 设置页瘦身：API Keys 标签撤除，钥匙状态摘要收在这儿 -->
      <span class="fn-note">🔑 钥匙：{{ keysConfigured }}/{{ foreignList.length }} 家已配置（点外脑卡片可填/换/删）</span>
      <span class="fn-note">选了谁就用谁：外脑出问题如实报错，不会偷偷换本地；本地出问题也一样，两边互不干扰。</span>
      <!-- ★ #929-A6 语音利弊提示（2026-09-29） -->
      <span v-if="voiceCap.note" class="fn-note">🔊 {{ voiceCap.note }}</span>
    </div>
    <div class="m-grid">
      <div v-for="p in foreignShown" :key="p.id" class="fcard" :class="{ on: currentForeign === p.id, open: openId === p.id }" @click="toggleOpen(p.id)">
        <div class="f-head">
          <b>{{ provName(p) }}</b>
          <span class="f-head-badges">
            <span v-if="p.is_custom" class="f-badge cus">自定义</span>
            <!-- ★ D1 #1564 识图能力徽标（模型中立，未标注不显示） -->
            <span v-if="p.vision === true" class="f-badge vis">👁️ 支持识图</span>
            <span v-else-if="p.vision === false" class="f-badge novis">📄 仅文字对话</span>
            <span class="f-badge" :class="p.configured ? 'ok' : ''">{{ p.configured ? '钥匙已配' : '未配钥匙' }}</span>
          </span>
        </div>
        <div class="f-masked">{{ p.masked || 'sk-…' }}</div>
        <div v-if="p.model_name" class="f-model">型号：{{ p.model_name }}</div>
        <div v-if="openId === p.id" class="f-detail" @click.stop>
          <input class="key-input" v-model.trim="keyInput" :placeholder="'粘贴 ' + provName(p) + ' 的 API Key'" spellcheck="false" />
          <div class="f-btns">
            <BigButton :px="13" :loading="busyId === p.id + ':save'" @click="saveKey(p)">保存钥匙</BigButton>
            <BigButton variant="ghost" :px="13" :loading="busyId === p.id + ':use'" @click="useForeign(p)">设为当前外脑</BigButton>
            <BigButton v-if="p.configured" variant="ghost" :px="13" :loading="busyId === p.id + ':del'" @click="deleteKey(p)">删除钥匙</BigButton>
            <!-- ★ #1573 自定义外脑可整家删除（内置外脑只能删钥匙） -->
            <BigButton v-if="p.is_custom" variant="ghost" :px="13" :loading="busyId === p.id + ':delc'" @click="deleteCustom(p)">删除这家外脑</BigButton>
          </div>
          <!-- ★ #1573 claude 官方协议不兼容，提前说清避免白折腾 -->
          <div v-if="p.id === 'claude'" class="f-hint warn">⚠️ Claude 官方接口不是通用格式，直接填官方地址会报错——需填 OpenAI 兼容格式的中转地址才能用</div>
          <!-- ★ 2026-09-13 用户反馈#9：外脑型号设置 -->
          <div class="model-name-row">
            <label class="mn-label">型号</label>
            <input class="key-input mn-input" v-model.trim="modelInputs[p.id]" :placeholder="MODEL_HINT[p.id] || '例如 official-model-name'" spellcheck="false" @keyup.enter="saveModelName(p)" />
            <BigButton :px="13" :loading="busyId === p.id + ':mn'" @click="saveModelName(p)">保存型号</BigButton>
          </div>
          <div class="f-hint">型号 = 这家外脑的具体大脑版本（{{ MODEL_HINT[p.id] || '留空则用官方默认' }}）。填错型号聊天会报错，填对才能发挥实力</div>
        </div>
      </div>
    </div>
    <!-- ★ #1576 外脑折叠：默认只露常用 8 家+已配钥匙的+自定义，25 家全量点了才展开（老谭 11-27 拍板：全量太咋眼） -->
    <div class="foreign-toggle">
      <button class="mini" @click="showAllForeign = !showAllForeign">
        {{ showAllForeign ? '▲ 收起，只看常用外脑' : `▼ 显示全部 ${foreignList.length} 家外脑（还有 ${foreignHiddenCount} 家没配钥匙的）` }}
      </button>
      <span class="ft-hint">常用 {{ CORE_FOREIGN.length }} 家常驻；其余配了钥匙的也常驻，不用翻</span>
    </div>

    <!-- ★ #1573 外脑全家福：自定义外脑添加表单（任意 OpenAI 兼容服务商一步接入） -->
    <div class="add-cp">
      <div class="add-cp-head">
        <b>➕ 添加自定义外脑</b>
        <span>任何兼容 OpenAI 格式的服务商都能接：中转站、聚合平台（OpenRouter/硅基流动）、公司自建服务等。Claude 官方地址不兼容，需填 OpenAI 格式中转地址。</span>
        <button class="mini" @click="showAddForm = !showAddForm">{{ showAddForm ? '收起表单' : '展开表单' }}</button>
      </div>
      <div v-if="showAddForm" class="add-cp-form">
        <div class="cp-row"><label>显示名称</label><input class="key-input" v-model.trim="cpForm.name" placeholder="例如：我的中转站" /></div>
        <div class="cp-row"><label>接口地址</label><input class="key-input" v-model.trim="cpForm.base_url" placeholder="例如：https://api.xxx.com/v1（一般以 /v1 结尾）" spellcheck="false" /></div>
        <div class="cp-row"><label>API Key</label><input class="key-input" v-model.trim="cpForm.api_key" placeholder="服务商给的密钥，sk- 开头居多" spellcheck="false" /></div>
        <div class="cp-row"><label>型号</label><input class="key-input" v-model.trim="cpForm.model_name" placeholder="服务商的具体型号名，例如 gpt-4o-mini" spellcheck="false" /></div>
        <div class="f-btns">
          <BigButton :px="13" :loading="busyId === 'cp:test'" @click="testCustom">先测连通</BigButton>
          <BigButton :px="13" :loading="busyId === 'cp:add'" @click="addCustom">保存并启用</BigButton>
        </div>
        <div class="f-hint">「先测连通」用上面填的地址和钥匙现场试一发，不用先保存；测试通过再点「保存并启用」，保存后会自动出现在上方外脑列表</div>
      </div>
    </div>

    <!-- ═══ 分区二：本地通用 ═══ -->
    <h2 class="sec"><span class="dot local" /> 本地 · 通用大脑 <small>免费、离线、隐私最好，点「设为当前」即可切换；带 👁️ 的能看图</small></h2>
    <div v-if="!localLLM.length" class="empty">没找到本地通用模型 —— 去「装新模型」页挑一个合适的</div>
    <div class="m-grid">
      <div v-for="m in localLLM" :key="m.id" class="lcard">
        <span class="l-icon">{{ m.icon }}</span>
        <div class="l-info">
          <b>{{ m.label || m.name }}</b>
          <span class="l-meta">{{ m.source }} · {{ m.size_gb != null ? m.size_gb + 'GB' : (m.desc || '本机模型') }}</span>
          <!-- ★ #1577 视觉专用模型就地标注（不再另开分区，老谭 11-27 拍板） -->
          <span v-if="m.vision_only" class="l-vis vis">👁️ 看图专用</span>
          <span v-else-if="m.vision === true" class="l-vis vis">👁️ 支持识图</span>
          <span v-else-if="m.vision === false" class="l-vis novis">📄 仅文字对话</span>
          <span v-if="m.vision_only && visionPinned === m.id" class="l-vis vis">✅ 当前看图大脑</span>
          <span v-if="isAlsoVertical(m)" class="l-vert-tag">🎯 垂直大脑也可用</span>
          <!-- ★ #1578 组合卡：写清由哪些模型组成、怎么调用、失败怎么办 -->
          <template v-if="m.source === '组合' && (m.steps || []).length">
            <span class="l-chain">🔗 {{ chainLine(m) }}</span>
            <span class="l-chain-meta">{{ chainRule(m) }}</span>
          </template>
          <span v-else-if="m.source === '组合'" class="l-chain-meta">多环节接力流水线 · 组合即本地大脑</span>
        </div>
        <!-- ★ #1577 看图专用模型：按钮走钉选（设为看图大脑），不走通用切换 -->
        <button v-if="m.vision_only" class="mini" :class="{ cur: visionPinned === m.id }" :disabled="visionPinned === m.id || busyId === m.id + ':pin'" @click="pinVisionModel(m)">
          {{ visionPinned === m.id ? '看图使用中' : '设为看图大脑' }}
        </button>
        <button v-else class="mini" :class="{ cur: currentLocalId === m.id }" :disabled="currentLocalId === m.id" @click="setCurrent(m)">
          {{ currentLocalId === m.id ? '当前使用' : '设为当前' }}
        </button>
      </div>
    </div>
    <!-- ★ #1577 视觉分区撤除后的轻量说明行（钉选状态+清除入口收在这） -->
    <div class="vision-note-row">
      <b>👁️ 看图大脑：</b>
      <template v-if="visionPinned">
        <span>已钉选「{{ visionPinnedLabel }}」，发照片/拍照记账固定用它</span>
        <button class="mini" @click="pinVisionModel(null)">清除钉选（回到自动顶上逻辑）</button>
      </template>
      <span v-else>没单独钉选——当前聊天外脑带 👁️ 徽标就自动顶上看图；本地「看图专用」模型可在上方卡片钉选</span>
    </div>

    <!-- ★ #1577 视觉大脑专用分区已撤除（老谭 11-27：很多余）——看图模型归位通用区就地标注，外脑靠 👁️ 徽标 -->

    <!-- ═══ 分区三：本地垂直 ═══ -->
    <h2 class="sec"><span class="dot vert" /> 本地 · 专项大脑（垂直模型）<small>点卡片下方按钮跳去对应专项面板使用（垂直模型在面板里选，不走聊天框）</small></h2>
    <div v-if="!verticals.length" class="empty">还没装专项大脑 —— 音乐工坊、图片生成等模型会出现在这里</div>
    <div class="v-grid">
      <div v-for="v in verticals" :key="v.id || v.name" class="vcard">
        <b class="v-name">{{ v.name || v.model_id || v.id }}</b>
        <span class="v-type">{{ VTYPE_TEXT[v.canvas_type] || v.canvas_type || '专项' }}</span>
        <span class="v-desc">{{ v.capability?.can_do?.[0] || v.description || '' }}</span>
        <span class="v-where">💡 在「{{ VTYPE_PANEL[v.canvas_type] || '更多 → 专项大脑面板' }}」里选用这个模型</span>
        <!-- ★ C #1567：垂直模型加「去用」路径——直答页所有专项模型都能当场试 -->
        <div class="voice-btns"><a href="#/vertical"><button class="mini">🚀 去直答页用</button></a></div>
      </div>
    </div>
    <!-- ═══ 分区三·补：底座部件（#1561 模型中心合流：registry 户口在这也看得见） ═══ -->
    <h2 v-if="baseParts.length" class="sec"><span class="dot vert" /> ⚙️ 本地底座部件<small>组合工坊的零件：快分类/定位/查重等，在「工坊」里组合流水线时选用</small></h2>
    <div v-if="baseParts.length" class="v-grid">
      <div v-for="b in baseParts" :key="b.id" class="vcard">
        <b class="v-name">{{ b.label || b.id }}</b>
        <span class="v-type">{{ (b.capabilities || []).join('/') }} · {{ b.vram_gb }}GB</span>
        <span class="v-desc">{{ b.package ? '📦 接入包导入的模型' : b.builtin ? '✅ 哈文预置部件' : '🔌 已登记部件' }}</span>
        <!-- ★ A #1567：底座部件说清「在哪服役、怎么触发」——不再只给看不给用 -->
        <span class="v-where">💡 拍照记账、照片自动归位、查重时<b>毫秒级自动服役</b>，不用手动选；在「工坊」建流水线时也可从环节下拉里选它</span>
        <div class="voice-btns">
          <a href="#/photos"><button class="mini">📍 去图片库看效果</button></a>
          <a href="#/workshop"><button class="mini">🧩 去工坊组合</button></a>
        </div>
      </div>
    </div>

    <!-- ═══ 分区四：听音模型（★ 2026-10-23 自设置页「语音识别」标签迁入：什么模型去哪管） ═══ -->
    <h2 class="sec"><span class="dot voice" /> 🎤 听音模型（Whisper）<small>按住说话、手机喊哈文、音频转文字全靠它在本机听音——装好后不用联网也能用</small></h2>
    <div class="voice-row">
      <div class="row-main"><b>当前使用：{{ voiceModels.active || '—' }}</b><span>跟着「常用」档走；常用档没装时哈文会自动先用别的档顶着，不会哑</span></div>
    </div>
    <div class="v-grid" style="margin-bottom:10px">
      <div v-for="m in voiceModels.models" :key="m.name" class="vcard">
        <b class="v-name">{{ m.label }} · {{ m.size }}</b>
        <span class="v-type">{{ m.installed ? '已安装' : '未安装' }}</span>
        <span class="v-desc">{{ m.note }}</span>
        <div class="voice-btns">
          <span v-if="m.preferred" class="pill ok">常用</span>
          <span v-if="m.active" class="pill ok">使用中</span>
          <template v-else-if="m.installed">
            <button v-if="!m.preferred" class="mini" @click="preferVoice(m.name)">设为常用</button>
            <button class="mini danger" @click="deleteVoice(m.name, m.label)">删除</button>
          </template>
          <template v-else>
            <button class="mini" :disabled="voiceDl.state === 'downloading'" @click="downloadVoice(m.name)">下载</button>
            <button v-if="m.name !== (voiceModels.preferred || 'small')" class="mini" @click="preferVoice(m.name)">设为常用</button>
          </template>
        </div>
      </div>
    </div>
    <div v-if="voiceDl.state === 'downloading'" class="tip ok">⏳ 正在下载「{{ voiceDl.name }}」…{{ voiceDl.msg }}下载完成后自动生效，可点右上角「刷新」看进度</div>
    <div v-else-if="voiceDl.msg" class="tip" :class="voiceDl.state === 'error' ? 'bad' : 'ok'">{{ voiceDl.state === 'error' ? '❌ ' : '✅ ' }}{{ voiceDl.msg }}</div>
    <div class="voice-row">
      <div class="row-main"><b>装哪个好？</b><span>日常说指令、记一句话：装「增强 small」（约 480MB）就够，反应快识别准（默认常用档）；要听长段落/会议录音再上「旗舰 large-v3-turbo」（约 1.5GB）</span></div>
    </div>
    <div class="voice-row">
      <div class="row-main"><b>🔊 外脑听语音</b><span>{{ voiceCap.note || '（读取中…）' }}</span></div>
      <button v-if="voiceCap.has_audio" class="mini" @click="toggleAudioDirect">{{ voiceCap.direct_enabled ? '关闭直通' : '开启直通' }}</button>
    </div>
    <div v-if="voiceCap.has_audio" class="tip">直通 = 语音原声直接发给外脑听（语气语调更活，但按量花钱、语音档案存不了）；关着 = 哈文先在家转文字再发（免费秒转，记录可存可搜）。两种都只影响语音，不打扰别的功能</div>
  </PageShell>
</template>

<script setup>
import { ref, computed } from 'vue'
import PageShell from '../components/PageShell.vue'
import { t } from '../i18n'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import { getJSON, postJSON, delJSON } from '../api.js'

// ★ #1573 外脑全家福：25 家内置全量中文映射（后端 get_api_keys 已放全家福，前端名字跟上）
const FOREIGN_NAME = {
  deepseek: 'DeepSeek', doubao: '豆包', qwen: '通义千问', kimi: 'Kimi',
  zhipu: '智谱 GLM', yi: '零一万物', baichuan: '百川', stepfun: '阶跃星辰',
  openai: 'OpenAI（GPT）', claude: 'Claude', gemini: 'Gemini', groq: 'Groq',
  mistral: 'Mistral', cohere: 'Cohere', moonshot: 'Kimi 国际版', siliconflow: '硅基流动',
  openrouter: 'OpenRouter', hunyuan: '腾讯混元', minimax: 'MiniMax', ernie: '文心一言',
  spark: '讯飞星火', huggingface: 'HuggingFace', xai: 'xAI（Grok）', perplexity: 'Perplexity',
  together: 'Together AI',
}
const VTYPE_TEXT = { image_generation: '🎨 图片', music_generation: '🎵 音乐', speech: '🗣 语音', code: '💻 代码', translation: '🌐 翻译', medical: '🏥 医疗', data_analysis: '📊 数据' }
// ★ 2026-09-13 用户反馈#10：垂直模型"去哪儿用"指引
const VTYPE_PANEL = { medical: '华佗医疗面板', music_generation: '音乐工坊面板', image_generation: '图片生成面板', speech: '语音面板', code: '代码面板', translation: '翻译面板', data_analysis: '数据分析面板' }
// ★ 2026-09-13 用户反馈#9：外脑型号提示（各家常型号示例）
// ★ #1573 型号提示补全 25 家
const MODEL_HINT = {
  deepseek: 'deepseek-chat 或 deepseek-reasoner',
  zhipu: 'glm-4-flash / glm-4-plus',
  qwen: 'qwen-max / qwen-plus',
  kimi: 'moonshot-v1-8k / kimi-k2',
  doubao: 'doubao-种子系列型号',
  yi: 'yi-large 等',
  baichuan: 'baichuan2 系列',
  stepfun: 'step-1 系列',
  openai: 'gpt-4o / gpt-4o-mini',
  claude: 'claude-sonnet-4 等（官方接口需中转，见卡片内提示）',
  gemini: 'gemini-2.0-flash / gemini-1.5-pro',
  groq: 'llama-3.3-70b-versatile',
  mistral: 'mistral-large-latest',
  cohere: 'command-r-plus',
  moonshot: 'moonshot-v1-8k / kimi-k2 等',
  siliconflow: 'deepseek-ai/DeepSeek-V3 等',
  openrouter: 'openai/gpt-4o-mini 等（带厂商前缀）',
  hunyuan: 'hunyuan-turbo / hunyuan-lite',
  minimax: 'abab6.5s-chat',
  ernie: 'ernie-4.0 / ernie-speed',
  spark: 'generalv3.5 / lite',
  huggingface: 'HuggingFace 路由型号',
  xai: 'grok-2 / grok-beta',
  perplexity: 'sonar / sonar-pro',
  together: 'meta-llama/Llama-3 系列',
}

const keys = ref({})
const currentForeign = ref('')
const activeLocal = ref(false)   // ★ #1326 当前对话是否走本地
const localAll = ref([])
const verticals = ref([])
const baseParts = ref([])   // #1561 底座部件（registry builtin/接入包）
const openId = ref('')
const keyInput = ref('')
const busyId = ref('')
const currentLocalId = ref('')
const modelInputs = ref({})   // ★ #9 外脑型号输入框（按 provider 存）
const msg = ref('')
const msgOk = ref(false)
const voiceCap = ref({ note: '', has_audio: false, direct_enabled: false })   // ★ #929-A6

const keysConfigured = computed(() => Object.values(keys.value).filter(v => v.configured).length)

async function loadVoiceCap() {
  try {
    const j = await getJSON('/api/voice/model_capability')
    voiceCap.value = { has_audio: !!j.has_audio, direct_enabled: !!j.direct_enabled, note: j.note || '' }
  } catch (_) { voiceCap.value = { has_audio: false, direct_enabled: false, note: '（后端版本较旧，暂不支持查询）' } }
}
loadVoiceCap()

/* ── ★ 2026-10-23 听音模型区（自 SettingsPage 迁入）── */
const voiceModels = ref({ models: [], installed: [], active: null, preferred: 'small' })
const voiceDl = ref({ name: null, state: 'idle', msg: '' })
let voicePoll = null

async function loadVoiceModels() {
  try {
    const j = await getJSON('/api/voice/models')
    voiceModels.value = { models: j.models || [], installed: j.installed || [], active: j.active, preferred: j.preferred || 'small' }
    voiceDl.value = j.download || { state: 'idle', msg: '' }
    if (voiceDl.value.state === 'downloading' && !voicePoll) {
      voicePoll = setInterval(async () => {
        try {
          const s = await getJSON('/api/voice/models')
          voiceModels.value = { models: s.models || [], installed: s.installed || [], active: s.active, preferred: s.preferred || 'small' }
          voiceDl.value = s.download || { state: 'idle', msg: '' }
          if (voiceDl.value.state !== 'downloading') { clearInterval(voicePoll); voicePoll = null }
        } catch (_) {}
      }, 3000)
    }
  } catch (_) { /* 语音路由不可用时静默（老版本后端） */ }
}
async function downloadVoice(name) {
  try {
    const j = await postJSON('/api/voice/models/download', { name })
    if (j.success) { voiceDl.value = { name, state: 'downloading', msg: '已开始，正在从国内源下载…' }; loadVoiceModels() }
    else flash(j.error || '下载启动失败', false)
  } catch (e) { flash('下载启动失败：' + e.message, false) }
}
async function deleteVoice(name, label) {
  if (!confirm(`删除「${label}」这个听音模型？删掉后语音识别会换用还装着的模型（也可以随时重新下载）`)) return
  try {
    const j = await postJSON('/api/voice/models/delete', { name })
    if (j.success) flash(j.msg || '已删除')
    else flash(j.error || '删除失败', false)
    await loadVoiceModels()
  } catch (e) { flash('删除失败：' + e.message, false) }
}
async function preferVoice(name) {
  try {
    const j = await postJSON('/api/voice/models/prefer', { name })
    if (j.success) flash(j.msg || '已设为常用')
    else flash(j.error || '设置失败', false)
    await loadVoiceModels()
  } catch (e) { flash('设置失败：' + e.message, false) }
}
async function toggleAudioDirect() {
  try {
    const j = await postJSON('/api/voice/audio_direct', { enabled: !voiceCap.value.direct_enabled })
    if (j.success) { voiceCap.value.direct_enabled = !voiceCap.value.direct_enabled; flash(j.msg || '已切换') }
    else flash(j.error || '切换失败', false)
  } catch (e) { flash('切换失败：' + e.message, false) }
}
loadVoiceModels()

const foreignList = computed(() => Object.entries(keys.value).map(([id, v]) => ({ id, ...v })))

/* ── ★ #1576 外脑折叠：默认常用 8 家+已配钥匙+自定义，避免 25 家一屏晡不过来 ── */
const CORE_FOREIGN = ['deepseek', 'doubao', 'qwen', 'kimi', 'zhipu', 'yi', 'baichuan', 'stepfun']
const showAllForeign = ref(false)
const foreignShown = computed(() => {
  if (showAllForeign.value) return foreignList.value
  return foreignList.value.filter(p => CORE_FOREIGN.includes(p.id) || p.configured || p.is_custom)
})
const foreignHiddenCount = computed(() => foreignList.value.length - foreignShown.value.length)

/* ── ★ #1573 外脑全家福：名字统一走 provName（自定义外脑显示保存的名称，内置走中文映射） ── */
function provName(x) {
  if (typeof x === 'string') {
    const hit = foreignList.value.find(p => p.id === x)
    return (hit && hit.name) || FOREIGN_NAME[x] || x
  }
  return (x && x.name) || FOREIGN_NAME[x && x.id] || (x && x.id) || ''
}

/* ── ★ #1573 自定义外脑：添加表单 + 连通测试 + 整家删除 ── */
const showAddForm = ref(false)
const cpForm = ref({ name: '', base_url: '', api_key: '', model_name: '' })

async function testCustom() {
  if (!cpForm.value.base_url) { flash('先填接口地址再测', false); return }
  busyId.value = 'cp:test'
  try {
    const j = await postJSON('/api/models/test-connection', {
      provider_id: 'custom-preview',
      base_url: cpForm.value.base_url,
      api_key: cpForm.value.api_key,
      model_name: cpForm.value.model_name,
    })
    if (j.success) flash('✅ 连通成功！这个地址和钥匙能用，点「保存并启用」收进外脑列表')
    else flash('连通失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('测试失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function addCustom() {
  const f = cpForm.value
  if (!f.name || !f.base_url) { flash('显示名称和接口地址都得填', false); return }
  busyId.value = 'cp:add'
  try {
    const pid = 'custom_' + Date.now().toString(36)
    const j = await postJSON('/api/models/custom-provider', { provider_id: pid, name: f.name, base_url: f.base_url, api_key: f.api_key, model_name: f.model_name })
    if (j.success) {
      flash(`外脑「${f.name}」已添加，点它的卡片可改钥匙/型号或设为当前外脑`)
      cpForm.value = { name: '', base_url: '', api_key: '', model_name: '' }
      showAddForm.value = false
      await loadAll()
    } else flash('添加失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('添加失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function deleteCustom(p) {
  if (!confirm(`彻底删除自定义外脑「${provName(p)}」？钥匙和型号一起清掉，以后要用得重新添加`)) return
  busyId.value = p.id + ':delc'
  try {
    const j = await delJSON(`/api/models/custom-provider/${p.id}`)
    if (j.success) {
      flash(`已删除「${provName(p)}」`)
      if (currentForeign.value === p.id) flash(`已删除。当前外脑已置空，请到列表里另选一家设为当前`, false)
      await loadAll()
    } else flash(j.error || '删除失败', false)
  } catch (e) { flash('删除失败：' + e.message, false) }
  finally { busyId.value = '' }
}
const currentModelName = computed(() => foreignList.value.find(p => p.id === currentForeign.value)?.model_name || '')
const localLLM = computed(() => localAll.value.filter(m => m.type === 'llm'))

// ★ 2026-09-14 用户反馈#3：通用区标注"垂直大脑也可用"（该模型同时出现在垂直清单里，如 minicpm-v 视觉）
function isAlsoVertical(m) {
  const hay = `${m.id || ''} ${m.name || ''}`.toLowerCase()
  return verticals.value.some(v => {
    const vm = `${v.ollama_model || ''} ${v.id || ''} ${v.name || ''}`.toLowerCase()
    return vm.includes(hay) || hay.includes(v.ollama_model || '\u0000')
  })
}

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 5000)
}

function toggleOpen(id) {
  openId.value = openId.value === id ? '' : id
  keyInput.value = ''
  if (openId.value) {
    // ★ #9：展开时把已存型号填进输入框
    const p = foreignList.value.find(x => x.id === id)
    modelInputs.value[id] = p?.model_name || ''
  }
}

async function loadAll(showErr = false) {
  try {
    const [k, cf, la, vm, cm] = await Promise.all([
      getJSON('/api/models/api-keys'),
      getJSON('/api/models/current-foreign'),
      getJSON('/api/models/local-all').catch(() => ({ models: [] })),
      getJSON('/api/vertical/models').catch(() => ({ models: [] })),
      getJSON('/api/chains/models').catch(() => ({ models: [] })),   // #1561 底座部件户口
    ])
    keys.value = k.keys || {}
    currentForeign.value = (cf.model || '').trim()
    activeLocal.value = !!(cf.active_is_local)
    localAll.value = la.models || []
    verticals.value = vm.models || []
    // #1561: 只展示 mnv4/locate 这类「部件」条目（内置+接入包），qwen3-vl/ollama 已在别处分区出现
    baseParts.value = (cm.models || []).filter(m => m.builtin || m.package)
    // ★ #10 修复：当前本地模型从后端初始化（之前只在点击后才记，刷新页面"当前使用"标记就丢了）
    if (la.current) currentLocalId.value = la.current
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) { flash('加载失败：' + e.message, false) } }
}

async function saveKey(p) {
  if (!keyInput.value) { flash('先粘贴钥匙再保存', false); return }
  busyId.value = p.id + ':save'
  try {
    await postJSON('/api/models/api-keys', { keys: { [p.id]: keyInput.value } })
    flash(`${provName(p)} 的钥匙已保存`)
    keyInput.value = ''
    await loadAll()
  } catch (e) { flash('保存失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function useForeign(p) {
  if (!p.configured) { flash(`${provName(p)} 还没配钥匙，先保存钥匙再切换（否则聊天会报 401 无效）`, false); return }
  busyId.value = p.id + ':use'
  try {
    await postJSON('/api/models/switch-foreign', { model: p.id })
    flash(`已切换外脑到 ${provName(p)}`)
    await loadAll()
  } catch (e) { flash('切换失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function deleteKey(p) {
  if (!confirm(`删除 ${provName(p)} 已保存的钥匙？删除后需重新粘贴才能用这家外脑`)) return
  busyId.value = p.id + ':del'
  try {
    await postJSON('/api/models/api-keys', { delete: [p.id], keys: {} })
    flash(`${provName(p)} 的钥匙已删除`)
    if (currentForeign.value === p.id) flash(`钥匙已删除。注意当前外脑还是 ${provName(p)}，建议另选一家设为当前`, false)
    await loadAll()
  } catch (e) { flash('删除失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function saveModelName(p) {
  const name = (modelInputs.value[p.id] || '').trim()
  busyId.value = p.id + ':mn'
  try {
    if (!name) { flash('型号留空 = 用官方默认。要填就填全名，比如 deepseek-chat', false); busyId.value = ''; return }
    await postJSON('/api/models/foreign-model-name', { provider: p.id, model_name: name })
    flash(`${provName(p)} 型号已设为 ${name}`)
    await loadAll()
  } catch (e) { flash('保存型号失败：' + e.message, false) }
  finally { busyId.value = '' }
}

async function setCurrent(m) {
  try {
    const j = await postJSON('/api/models/set-current', { model_id: m.id })
    // ★ R4 2026-09-23 显存换班提示：大模型加载要几十秒，提前说清避免以为卡死
    if (j.success) { currentLocalId.value = m.id; flash(`当前通用大脑已换成 ${m.name}，正在加载到显存（约需 30 秒），期间第一次回复会稍慢，不是卡死`) }
    else flash('切换失败：' + (j.error || '未知错误'), false)
  } catch (e) { flash('切换失败：' + e.message, false) }
}

/* ── ★ #1577 视觉分区撤除：只保留钉选状态（看图模型已归位通用区就地标注）── */
const visionPinned = ref('')
const visionPinnedLabel = computed(() => {
  if (!visionPinned.value) return ''
  const hit = localAll.value.find(m => m.id === visionPinned.value)
  return (hit && (hit.label || hit.name)) || visionPinned.value
})
async function loadVision() {
  try {
    const j = await getJSON('/api/vision/models')
    if (j.success) visionPinned.value = j.pinned || ''
  } catch (_) { /* 旧后端无此路由时静默（钉选状态拿不到就不显示） */ }
}
async function pinVisionModel(m) {
  const id = m ? m.id : ''
  if (!m && !confirm('清除看图大脑钉选？之后看图走「当前外脑支持识图就自动用」的自动逻辑')) return
  busyId.value = (m ? m.id : 'clear') + ':pin'
  try {
    const j = await postJSON('/api/vision/models/pin', { model_id: id })
    if (j.success) {
      flash(m ? `看图大脑已换成「${m.label || m.name}」，发照片/拍照记账即刻生效` : '已清除钉选，看图回到自动逻辑')
      await loadVision()
    } else flash(j.error || '钉选失败', false)
  } catch (e) { flash('钉选失败：' + e.message, false) }
  finally { busyId.value = '' }
}
loadVision()

/* ── ★ #1578 组合卡：组成模型链 + 调用规则（人话渲染）── */
/* ★ #1580a 修复：CAP_LABEL 上一版漏定义 → 渲染组合卡时 ReferenceError 整页白屏（与 WorkshopPage 同一套映射） */
const CAP_LABEL = { dedup: '查重', detect: '目标检测', face: '人脸', embed: '向量/语义', tag: '打标', locate: '定位', classify: '快速分类', vision_understand: '看图理解', text_gen: '文字生成', music_generation: '音乐生成', image_generation: '图片生成', video_generation: '视频生成', speech_synthesis: '语音合成' }
function chainLine(m) {
  return (m.steps || []).map(s => {
    const label = s.model_label
      || (s.binding === 'dynamic_follow' ? '跟用户所选' : (CAP_LABEL[s.capability] || s.capability))
    return label + (s.optional ? '（支线可跳）' : '')
  }).join(' → ')
}
function chainRule(m) {
  const inT = m.input_type === 'image_path' ? '吃图片' : '吃文字'
  const outT = m.output_type === 'text' ? '出文字结论' : (m.output_type === 'image_path' ? '出图片' : `出 ${m.output_type}`)
  const fail = { abort: '失败即中止', skip_optional: '支线坏了自动跳过', use_backup: '失败自动换备选模型' }[m.on_step_fail] || m.on_step_fail
  return `${inT} → ${outT} · ${fail}`
}

/* #1557 模型体检 */
const checkupBusy = ref(false)
const checkupReport = ref(null)
async function runCheckup() {
  checkupBusy.value = true
  try {
    checkupReport.value = await getJSON('/api/vertical/checkup')
  } catch (e) {
    flash('体检失败：' + e.message, false)
  } finally {
    checkupBusy.value = false
  }
}

loadAll(true)
</script>

<style scoped>
/* #1557 体检报告 */
.checkup { border: 1px solid var(--line); background: #fff; border-radius: 12px; padding: 14px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 8px; }
.ck-head { display: flex; align-items: center; gap: 10px; }
.ck-head b { font-size: 14px; color: var(--ink); }
.ck-sum { font-size: 12px; color: var(--ink-3, #8b949e); }
.ck-x { margin-left: auto; border: none; background: none; cursor: pointer; color: var(--ink-3, #8b949e); font-size: 14px; }
.ck-row { display: flex; gap: 9px; align-items: baseline; padding: 7px 10px; border-radius: 8px; background: rgba(46,160,67,.05); }
.ck-row.known { background: rgba(255,212,0,.08); }
.ck-row.unknown { background: rgba(229,83,75,.06); }
.ck-st { flex-shrink: 0; }
.ck-body { display: flex; flex-direction: column; gap: 2px; }
.ck-body b { font-size: 12.5px; color: var(--ink); }
.ck-hint { font-size: 11.5px; color: var(--ink-3, #8b949e); line-height: 1.5; }
</style><style scoped>
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }

.sec { display: flex; align-items: baseline; gap: 8px; font-size: 15px; margin: 20px 0 11px; }
.sec small { font-size: 12px; color: var(--sub); font-weight: 400; }
.dot { width: 9px; height: 9px; border-radius: 50%; align-self: center; }
.dot.cloud { background: var(--mode-cloud); }
.dot.local { background: var(--mode-local); }
.dot.vert { background: var(--mode-vertical); }
.dot.voice { background: #8B72E0; }

/* ★ 2026-10-23 听音模型区 */
.voice-row { display: flex; align-items: center; gap: 10px; padding: 7px 2px; }
.voice-row .row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.voice-row .row-main b { font-size: 13px; }
.voice-row .row-main span { font-size: 12px; color: var(--sub); }
.voice-btns { display: flex; align-items: center; gap: 7px; margin-top: 7px; flex-wrap: wrap; }
.voice-btns .pill { font-size: 11px; padding: 2px 9px; border-radius: 20px; }
.mini.danger:hover { border-color: #D9534F; color: #D9534F; }

.foreign-note { font-size: 12.5px; color: var(--sub); background: var(--mode-cloud-bg); border-radius: 9px; padding: 7px 12px; margin-bottom: 11px; }
/* ★ D1 #1564 识图引导条 */
.vision-guide { font-size: 12.5px; color: var(--sub); background: #FFF9E6; border: 0.5px solid #F0E2B0; border-radius: 10px; padding: 9px 13px; margin-bottom: 12px; line-height: 1.6; }
.vision-guide b { color: var(--ink); }
.vg-vis { color: #1B7F3B; }
.vg-novis { color: #8B949E; }
.f-head-badges { display: flex; align-items: center; gap: 5px; }
.f-badge.vis, .l-vis.vis { background: #E8F5EC; color: #1B7F3B; font-weight: 700; }
.f-badge.novis, .l-vis.novis { background: #F4F5F7; color: #8B949E; }
.l-vis { font-size: 10.5px; padding: 1px 7px; border-radius: 6px; width: fit-content; font-weight: 700; }
.foreign-note b { color: var(--mode-cloud); }
.foreign-note .fn-note { display: block; margin-top: 3px; color: var(--sub); }

.m-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.fcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; cursor: pointer; }
.fcard.on { border-color: var(--mode-cloud); box-shadow: 0 0 0 1px var(--mode-cloud); }
.f-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.f-badge { font-size: 11px; padding: 2px 8px; border-radius: 20px; background: #FDF2F2; color: #B4443C; }
.f-badge.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.f-masked { font-size: 12px; color: var(--sub); margin-top: 5px; font-family: monospace; }
.f-model { font-size: 11.5px; color: var(--mode-cloud); margin-top: 3px; font-weight: 700; }
.model-name-row { display: flex; align-items: center; gap: 8px; }
.mn-label { font-size: 12px; font-weight: 700; color: var(--ink); flex-shrink: 0; }
.mn-input { flex: 1; min-width: 0; }
.f-detail { margin-top: 9px; border-top: 1px dashed var(--line); padding-top: 9px; display: flex; flex-direction: column; gap: 8px; }
.key-input { border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 12.5px; outline: none; background: #fff; color: var(--ink); }
.key-input:focus { border-color: var(--blue); }
.f-btns { display: flex; gap: 8px; }
.f-hint { font-size: 11px; color: var(--sub); }

/* ★ #1577 视觉钉选轻量说明行 + 组合卡组成链 */
.vision-note-row { display: flex; align-items: baseline; gap: 9px; padding: 8px 2px; flex-wrap: wrap; }
.vision-note-row b { font-size: 12.5px; color: var(--ink); flex-shrink: 0; }
.vision-note-row span { font-size: 12px; color: var(--sub); }
.l-chain { font-size: 11.5px; color: var(--mode-vertical); font-weight: 700; line-height: 1.5; }
.l-chain-meta { font-size: 11px; color: var(--sub); }

/* ★ #1576 外脑折叠切换条 */
.foreign-toggle { display: flex; align-items: center; gap: 10px; margin-top: 9px; }
.foreign-toggle .ft-hint { font-size: 11.5px; color: var(--sub); }

/* ★ #1573 自定义外脑徽标 + 添加表单 */
.f-badge.cus { background: #EDE7F6; color: #6A4FB6; font-weight: 700; }
.f-hint.warn { color: #9A6B00; background: #FFF9E6; border-radius: 8px; padding: 6px 9px; }
.add-cp { border: 1px dashed var(--line); border-radius: 12px; padding: 11px 14px; margin-top: 11px; background: var(--c-bg-soft); }
.add-cp-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.add-cp-head b { font-size: 13.5px; color: var(--ink); }
.add-cp-head span { flex: 1; min-width: 200px; font-size: 12px; color: var(--sub); line-height: 1.5; }
.add-cp-form { margin-top: 9px; display: flex; flex-direction: column; gap: 8px; }
.cp-row { display: flex; align-items: center; gap: 8px; }
.cp-row label { font-size: 12px; font-weight: 700; color: var(--ink); flex-shrink: 0; width: 58px; }
.cp-row .key-input { flex: 1; min-width: 0; }

.lcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 11px; }
.l-icon { font-size: 22px; }
.l-info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.l-info b { font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.l-meta { font-size: 11.5px; color: var(--sub); }
.l-vert-tag { font-size: 10.5px; color: var(--mode-vertical); font-weight: 700; border: 0.5px solid var(--mode-vertical); border-radius: 6px; padding: 1px 6px; width: fit-content; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 5px 12px; cursor: pointer; color: var(--ink); }
.mini:hover:not(:disabled) { border-color: var(--blue); color: var(--blue); }
.mini.cur, .mini:disabled { border-color: var(--green); color: var(--green); cursor: default; font-weight: 700; }

.v-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 11px; }
.vcard { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.v-where { font-size: 11.5px; color: var(--sub); background: var(--c-bg-soft); border-radius: 8px; padding: 5px 8px; margin-top: 3px; line-height: 1.5; }
.v-name { font-size: 13.5px; }
.v-type { font-size: 11.5px; color: var(--mode-vertical); font-weight: 700; }
.v-desc { font-size: 11.5px; color: var(--sub); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.empty { padding: 24px; text-align: center; color: var(--sub); background: var(--c-bg-soft); border-radius: 12px; font-size: 13px; margin-bottom: 10px; }

@media (max-width: 1000px) { .m-grid, .v-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 660px) { .m-grid, .v-grid { grid-template-columns: 1fr; } }
</style>
