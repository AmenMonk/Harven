import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import router from '../router'
import { tryNavigate } from '../navCommands'

// 聊天状态 — B1 首页三栏
// 防弄混四保险的数据基础：会话标签 / 新会话纯净 / 身份条会话名 / 回复方三色
export const useChatStore = defineStore('chat', () => {
  const conversations = ref([])
  const currentId = ref(null)
  const messages = ref([])          // {role:'user'|'assistant', content, replyBy?}
  const sending = ref(false)
  const thinking = ref('')          // "正在执行工具 (第1轮)..." 等过程提示
  const sourceNote = ref('')        // 最近一条 source 事件（回复方/知识库来源）
  // ★ 2026-09-15 用户反馈：工作台要有详细数据——保存最近一次 model_source 结构化详情
  const sourceDetail = ref(null)    // {model, label, isExternal, mode}
  const rightDockOpen = ref(true)

  // ★ 2026-10-10 #1053 生成停止（定版方案A·真停止）：生成中按钮变「⏹ 停止」
  const stopping = ref(false)          // 正在执行停止动作（防连点）
  let _abortCtrl = null                // 当前请求的 AbortController（超时强断用）

  // 查询模式：direct=外脑 / kb_only=本地（auto 已取消）
  // ★ #1247（2026-10-27 开发者）：用户选什么刷新/重启就是什么——localStorage 持久化 + 后端偏好同步
  const queryMode = ref(localStorage.getItem('harven_spa_query_mode') === 'kb_only' ? 'kb_only' : 'direct')
  function _persistMode() {
    try { localStorage.setItem('harven_spa_query_mode', queryMode.value) } catch (e) {}
    fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query_mode: queryMode.value }) }).catch(() => {})
  }

  // ★ 2026-09-14 C批：协作模式（外脑设计 → 本地执行）
  // 打开时强制 queryMode=kb_only（本地执行），发送体带 collab_mode；
  // 关闭发 'off' 保证按钮就是唯一真相（后端 collab_mode_override 优先于 config）
  // ★ 方案A：collabMode 支持 cloud_local（智能协作）/ local_dual（本地协作）
  const collabOn = ref(localStorage.getItem('harven_spa_collab_on') === '1')
  const collabMode = ref(localStorage.getItem('harven_spa_collab_mode') || 'cloud_local')
  function setCollab(on, mode) {
    collabOn.value = !!on
    localStorage.setItem('harven_spa_collab_on', collabOn.value ? '1' : '0')
    if (mode === 'cloud_local' || mode === 'local_dual') {
      collabMode.value = mode
      localStorage.setItem('harven_spa_collab_mode', mode)
    }
    if (collabOn.value) {
      queryMode.value = 'kb_only' // 协作 = 本地执行
      _persistMode()
      if (currentId.value) {
        convMeta.value[currentId.value] = { ...(convMeta.value[currentId.value] || {}), mode: 'collab' }
        saveConvMeta()
      }
    } else {
      // ★ #1247 关协作 → 恢复用户自己选的模式（不再留在协作强制态）
      queryMode.value = localStorage.getItem('harven_spa_query_mode') === 'kb_only' ? 'kb_only' : 'direct'
      _persistMode()
    }
  }

  const currentTitle = computed(() => {
    if (!currentId.value) return '新对话'
    const c = conversations.value.find(c => c.id === currentId.value)
    return c ? c.title : '对话'
  })

  // ★ 保险1：会话的模式/面板标签（SPA 内建立，存本地；历史会话缺省只显示面板标签）
  const _convMeta = JSON.parse(localStorage.getItem('harven_spa_conv_meta') || '{}')
  const convMeta = ref(_convMeta)
  function saveConvMeta() {
    localStorage.setItem('harven_spa_conv_meta', JSON.stringify(convMeta.value))
  }
  function convTags(id) {
    const meta = convMeta.value[id] || {}
    const conv = conversations.value.find(c => c.id === id)
    return { mode: meta.mode || '', panel: conv?.spec_name || '' }
  }

  async function loadConversations() {
    try {
      const r = await fetch('/api/conversations')
      conversations.value = await r.json()
    } catch (e) { console.warn('会话列表加载失败', e) }
  }

  /* ★ 2026-10-21 会话可见包：30s 自动刷新——手机发起的「📱 手机远程」会话电脑自动出现，
     不再需要手动刷新才知道手机说了啥（实测实锤反馈「电脑上看不到对话」） */
  let _convPollTimer = null
  function ensureConversationPoll() {
    if (_convPollTimer) return
    _convPollTimer = setInterval(() => {
      if (document.hidden) return   // 页面在后台不白刷
      loadConversations()
    }, 30000)
  }

  // ★ 保险2：＋新对话 = 纯净新会话（聊天白纸换新，面板数据不继承）
  // ★ #1247：查询模式跟用户走（不硬置 direct）——协作关闭由 setCollab(false) 恢复用户偏好
  function newConversation() {
    currentId.value = null
    messages.value = []
    sourceNote.value = ''
    thinking.value = ''
    setCollab(false)
  }

  async function openConversation(id) {
    currentId.value = id
    messages.value = []
    sourceNote.value = ''
    try {
      const r = await fetch(`/api/conversations/${id}/messages`)
      const list = await r.json()
      messages.value = (Array.isArray(list) ? list : []).map(m => {
        // ★ 2026-09-14 来源持久化：metadata.source 随消息存库，历史对话也能看到谁答的
        let meta = {}
        try { meta = typeof m.metadata === 'string' ? JSON.parse(m.metadata || '{}') : (m.metadata || {}) } catch (e) {}
        const src = meta.source || ''
        let replyBy = ''
        if (src) {
          if (/外脑|☁️/.test(src)) replyBy = 'cloud'
          else if (/垂直|🌡/.test(src)) replyBy = 'vertical'
          else if (/本地|🏠/.test(src)) replyBy = 'local'
        }
        return { role: m.role, content: m.content, replyBy, sourceLabel: src }
      })
    } catch (e) { console.warn('消息加载失败', e) }
  }

  async function deleteConversation(id) {
    try {
      await fetch(`/api/conversations/${id}`, { method: 'DELETE' })
      if (currentId.value === id) newConversation()
      await loadConversations()
    } catch (e) { console.warn('删除失败', e) }
  }

  /* ★ 2026-10-11 #1095 开发者需求：最近会话统一删除键——一键清空全部（后端 batch-delete 现成） */
  async function clearAllConversations() {
    try {
      const ids = (conversations.value || []).map(c => c.id)
      if (!ids.length) return
      await fetch('/api/conversations/batch-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      newConversation()   // 清完自动开新对话（当前会话必在删除集内）
      await loadConversations()
    } catch (e) { console.warn('清空会话失败', e) }
  }

  function setMode(m) {
    queryMode.value = m === 'kb_only' ? 'kb_only' : 'direct'
    _persistMode()   // ★ #1247 刷新/重启保持用户选择
    if (currentId.value) {
      convMeta.value[currentId.value] = { ...(convMeta.value[currentId.value] || {}), mode: queryMode.value }
      saveConvMeta()
    }
  }

  // 发送消息 — SSE 流式解析（/api/chat 与旧界面同一管线）
  // ★ 2026-09-15 A方案：opts.images = [{name, dataUrl}] 随消息发送，后端自动路由看图模型
  async function send(text, opts = {}) {
    const imgs = (opts.images || []).map(i => i.dataUrl).filter(Boolean)
    const content = (text || '').trim() || (imgs.length ? '请描述这张图片的内容。' : '')
    if ((!content && !imgs.length) || sending.value) return

    // ★ 2026-09-16 A路线：导航指令前端拦截（"打开家庭面板"→ 直接跳转，不进聊天）
    // 严格三闸：祈使词开头 + ≤24字无问号 + 目标精确匹配；不命中照常聊天
    if (!imgs.length) {
      try {
        const nav = await tryNavigate(content)
        if (nav && nav.handled) {
          messages.value.push({ role: 'user', content, replyBy: '' })
          messages.value.push({ role: 'assistant', content: nav.note, replyBy: '', sourceLabel: '🧭 页面导航' })
          if (nav.path) router.push(nav.path)
          return
        }
      } catch (e) { console.warn('导航拦截异常（降级为聊天）', e) }
    }

    sending.value = true
    messages.value.push({ role: 'user', content, images: imgs.length ? imgs : undefined, replyBy: '' })
    const reply = { role: 'assistant', content: '', replyBy: '', sourceLabel: '', toolLogs: [] }
    messages.value.push(reply)
    thinking.value = ''
    // ★ 2026-10-10 #1053: 每次发送新建 AbortController（停止按钮可强断流）
    _abortCtrl = new AbortController()
    // ★ 2026-09-27 #886 F 状态文案诚实化：发出去就显示「思考中」，如实累计已等秒数
    //   （不再显示「正在连接…」假装在连接——FC 首轮检测/模型加载期间就是单纯在等）
    const _t0 = Date.now()
    const _waitTimer = setInterval(() => {
      if (!sending.value) { clearInterval(_waitTimer); return }
      if (thinking.value && !thinking.value.startsWith('思考中…')) return // 后端有真状态（执行工具/流水线），不盖
      const s = Math.floor((Date.now() - _t0) / 1000)
      thinking.value = s >= 2 ? `思考中…已等 ${s} 秒` : '思考中…'
    }, 1000)

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: _abortCtrl ? _abortCtrl.signal : undefined,  // ★ #1053: 停止可断流
        body: JSON.stringify({
          message: content,
          conversation_id: currentId.value,
          query_mode: queryMode.value,
          // ★ 2026-09-13 反馈#3：联网开关（首页🌐签控制，true=强制搜索）
          web_search: !!opts.web_search,
          // ★ 2026-09-14 C批→方案A：协作模式（off/cloud_local/local_dual，按钮面板即真相）
          collab_mode: collabOn.value ? collabMode.value : 'off',
          // ★ 2026-09-15 A方案：图片附件（dataURL 列表，≤4张）
          images: imgs,
        }),
      })
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let navPath = ''   // ★ 2026-09-16 B路线：模型回复的跳转标记
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop()
        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data:')) continue
          let evt
          try { evt = JSON.parse(line.slice(5)) } catch { continue }
          if (evt.type === 'meta') {
            if (evt.conversation_id && evt.conversation_id !== currentId.value) {
              currentId.value = evt.conversation_id
              // ★ 保险1：新会话落库后补标签
              convMeta.value[evt.conversation_id] = { mode: queryMode.value }
              saveConvMeta()
              await loadConversations()
            }
          } else if (evt.type === 'text') {
            reply.content += evt.content || ''
          } else if (evt.type === 'tool_log') {
            // ★ 2026-09-21 #830：工具调用过程 → 独立收集，前端渲染成可折叠卡
            //   （不再混进正文；旧版本 engine 发 text 会直接拼进气泡）
            if (evt.content) reply.toolLogs.push(String(evt.content))
          } else if (evt.type === 'confirm_card') {
            // ★ #1212：Hub 安装确认卡（装前征求用户同意 + 风险告知四条）
            reply.confirmCard = evt.content || null
          } else if (evt.type === 'thinking') {
            thinking.value = evt.content || ''
          } else if (evt.type === 'pipeline_step') {
            // ★ V1 协作流水线：每站进度实时显示在思考区
            const ps = evt.content || {}
            thinking.value = ps.status === 'done'
              ? `✅ ${ps.name} 完成${ps.file ? '，文件已生成' : ''}`
              : (ps.message || `⛓️ ${ps.name} 运行中…`)
          } else if (evt.type === 'model_source') {
            // ★ 保险4（首选）：结构化回复方事件 {model,label,is_external,mode}
            const ms = evt.content || {}
            reply.replyBy = ms.is_external ? 'cloud' : (ms.mode === 'vision' ? 'vertical' : 'local')
            reply.sourceLabel = ms.label || ''  // ★ 2026-09-14 具体来源（DeepSeek/GLM/协作/Agent）
            sourceNote.value = ms.label || ''
            // ★ 2026-09-15 用户反馈：工作台详细数据（具体模型名/模式/内外脑）
            sourceDetail.value = {
              model: ms.model || '',
              label: ms.label || '',
              isExternal: !!ms.is_external,
              mode: ms.mode || '',
            }
          } else if (evt.type === 'source') {
            // ★ 保险4（兜底）：文本来源事件（☁️外脑 / 🏠本地 / 📚知识库…）
            sourceNote.value = evt.content || ''
            if (/外脑|☁️/.test(evt.content)) { reply.replyBy = 'cloud'; reply.sourceLabel = evt.content }
            else if (/本地|🏠/.test(evt.content)) { reply.replyBy = 'local'; reply.sourceLabel = evt.content }
            else if (/垂直|🌡/.test(evt.content)) { reply.replyBy = 'vertical'; reply.sourceLabel = evt.content }
          } else if (evt.type === 'error') {
            // ★ 2026-09-15 开发者抓到"石沉大海"：error 事件之前被静默丢弃，助手气泡永远空白
            reply.content += `\n❌ ${evt.content || '未知错误'}`
          } else if (evt.type === 'navigate') {
            // ★ 2026-09-16 B路线：后端从回复末尾提取的跳转标记 → 本地剥离+跳转
            navPath = evt.content || ''
          } else if (evt.type === 'done') {
            // 结束
          }
        }
      }
      // ★ B路线收尾：剥离文本里残留的标记并执行跳转（双保险，防 navigate 事件缺失）
      if (navPath) {
        reply.content = reply.content.replace(/\[NAVIGATE:[^\]]+\]\s*$/, '').trim()
        if (navPath.startsWith('/')) { try { router.push(navPath) } catch (e) { console.warn('跳转失败', e) } }
      }
    } catch (e) {
      // ★ 2026-10-10 #1053: 用户主动停止不算错误（后端已把半截回复标「（已停止）」入库）
      if (stopping.value || (e && e.name === 'AbortError')) {
        console.info('用户停止了生成')
      } else {
        reply.content += '\n[连接中断，请重试]'
        console.warn('发送失败', e)
      }
    } finally {
      clearInterval(_waitTimer)
      sending.value = false
      thinking.value = ''
    }
  }

  // ★ 2026-10-10 #1053 真停止：通知后端收笔（半截入库标「（已停止）」），
  // 最多等 5 秒优雅结束；后端卡在非流式阶段没反应时强断流兜底
  async function stopGeneration() {
    if (!sending.value || stopping.value) return
    stopping.value = true
    try {
      await fetch('/api/chat/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: currentId.value }),
      })
    } catch (e) { console.warn('停止请求失败', e) }
    const t0 = Date.now()
    while (sending.value && Date.now() - t0 < 5000) {
      await new Promise(r => setTimeout(r, 200))
    }
    if (sending.value && _abortCtrl) {
      try { _abortCtrl.abort() } catch (e) { console.warn('断流失败', e) }
    }
    stopping.value = false
  }

  return {
    conversations, currentId, messages, sending, thinking, sourceNote, sourceDetail,
    rightDockOpen, queryMode, currentTitle, collabOn, collabMode, stopping,
    loadConversations, newConversation, openConversation, deleteConversation, clearAllConversations,
    send, stopGeneration, setMode, setCollab, convTags, ensureConversationPoll,
  }
})
