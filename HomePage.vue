<template>
  <div class="home-page">
    <!-- ★ 2026-10-20 第三批3.2: 即将到来（任务due/随身记/目标节点/生日 四源合一，未来7天） -->
    <div v-if="upcoming.length" class="up-strip">
      <span class="up-label">📅 即将到来</span>
      <div class="up-items">
        <span v-for="u in upcoming" :key="u.type + u.id + u.date" class="up-item" :title="u.title">
          <i class="up-d">{{ u.date.slice(5) }}</i>{{ u.type === 'birthday' ? '🎂' : u.type === 'goal' ? '🎯' : u.type === 'reminder' ? '⏰' : '📋' }} {{ u.title }}
        </span>
      </div>
    </div>
    <!-- ★ 2026-10-20 第二批2.2: 目标进度横条（可折叠，不挤工作台） -->
    <div v-if="goalCards.length" class="goal-strip">
      <button class="gs-toggle" @click="goalOpen = !goalOpen">
        🎯 目标（{{ goalCards.length }}）{{ goalOpen ? '▾' : '▸' }}
      </button>
      <div v-show="goalOpen" class="gs-cards">
        <div v-for="g in goalCards" :key="g.id" class="gs-card" @click="$router && $router.push('/goals')" :title="'点击进入目标页'">
          <span class="gs-light">{{ g.light }}</span>
          <b class="gs-name">{{ g.title }}</b>
          <div class="gs-bar"><i :style="{ width: g.progress + '%' }" /></div>
          <span class="gs-meta">{{ g.done }}/{{ g.total }}<template v-if="g.next_node"> · {{ g.next_node.name }} {{ g.next_node.due }}</template></span>
        </div>
      </div>
    </div>
    <!-- ★ 2026-09-13 对齐设计稿 v2 顶栏：会话名 + 本地/外脑/协作三段胶囊 + 带字状态签 + 格子按钮 -->
    <header class="identity-bar">
      <!-- ★ F批手机端：窄屏汉堡按钮（桌面隐藏） -->
      <button class="rail-toggle" title="菜单" @click="ui.railOpen = true">
        <i class="burger" /><i class="burger" /><i class="burger" />
      </button>
      <button class="dock-toggle" :title="chat.rightDockOpen ? '收起右栏' : '展开右栏'" @click="chat.rightDockOpen = !chat.rightDockOpen">
        <HIcon name="grid" :size="20" />
      </button>
      <!-- ★ 2026-09-13 用户反馈：模式按钮组左移到格子按钮旁、加大、选中态实色填充（不再浅色难辨认） -->
      <div class="mode-group" role="group" aria-label="查询模式">
        <button class="mg-seg mg-local" :class="{ on: modeKey === 'local' && !chat.collabOn }" @click="pickMode('local')">🏠 本地</button>
        <button class="mg-seg mg-cloud" :class="{ on: modeKey === 'cloud' && !chat.collabOn }" @click="pickMode('cloud')">🤖 外脑</button>
        <!-- ★ 2026-09-14 反馈⑩：垂直模型直答入口（跳转直答页，不改变聊天模式） -->
        <button class="mg-seg mg-vert" title="打开垂直大脑：画图/作曲/语音/医疗等专项模型，表单直答" @click="$router.push('/vertical')">🎨 垂直</button>
        <!-- ★ 2026-09-14 方案A：协作选项面板（模式二选一+协作外脑+隐私三档；点亮=面板里的选择） -->
        <button class="mg-seg mg-collab" :class="{ on: chat.collabOn }"
                :title="chat.collabOn ? '协作中，点这里调整模式/外脑/隐私档位' : '点这里打开协作面板：选模式、选外脑、选隐私档位'"
                @click="openCollabPanel">🦞 协作</button>
      </div>
      <span class="identity-name">{{ chat.currentTitle }}</span>
      <span class="status-tags">
        <!-- ★ 2026-09-13 用户反馈#3：联网签改成真开关——点它 = 下一条消息强制联网搜索 -->
        <button class="st-tag st-click" :class="webSearch ? 'net-on' : (internetOk ? 'net-ok' : 'net-bad')"
                :title="webSearch ? '已开启：下一条消息强制联网搜索（再点关闭）' : '点击开启联网搜索（下一条消息生效）'"
                @click="toggleWebSearch">
          🌐 联网{{ webSearch ? '·开' : '' }} <i class="st-dot" :class="webSearch ? 'g pulse' : (internetOk ? 'g' : 'r')" />
        </button>
        <span class="st-tag" :class="secFull ? 'sec-warn' : 'sec-ok'" :title="secFull ? '全权模式：命令拦截已放宽（需密码开启）' : '安全模式：危险命令拦截 + 路径限制生效中 · 切换去「设置 → 安全隐私」'">
          🛡️ {{ secFull ? '全权' : '安全' }} <i class="st-dot" :class="secFull ? 'a' : 'g'" />
        </span>
      </span>
    </header>

    <!-- ═══ ★ 2026-09-18 #748：今天要处理横条（待办中心·首页端，与鸭头角标同源 /api/assist/alerts） ═══ -->
    <div v-if="alerts.count" class="todo-bar">
      <span class="tb-title">📋 今天要处理 <b class="tb-n">{{ alerts.count }}</b> 件</span>
      <div class="tb-items">
        <div v-for="r in alerts.reminders.slice(0, 3)" :key="'r' + r.id" class="tb-item">
          <span class="tb-txt">⏰ {{ r.title }}<i v-if="r.remind_at" class="tb-time">{{ r.remind_at.slice(5, 16) }}</i></span>
          <button v-if="r.link" class="tb-btn tb-go" @click="goHandle(r)">去处理</button>
          <button class="tb-btn" @click="ackReminder(r)">知道了</button>
          <button class="tb-btn tb-del" @click="delReminder(r)">删</button>
        </div>
        <div v-if="alerts.pending" class="tb-item">
          <span class="tb-txt">💬 待确认记录 <b>{{ alerts.pending }}</b> 条（聊天里说的事等你拍板入库）</span>
          <button class="tb-btn tb-go" @click="$router.push('/panels')">去面板处理</button>
        </div>
      </div>
    </div>
    <!-- ★ 2026-09-19 易用性③：提醒空态引导（没有待办时教一句怎么建提醒，可关闭） -->
    <div v-else-if="!remTipOff" class="todo-bar todo-empty">
      <span class="tb-title">⏰ 提醒</span>
      <span class="te-txt">现在没有要处理的事。想建提醒？直接在下面聊天框说「明天9点提醒我取快递」，到点这条横栏会亮起来。</span>
      <button class="tb-btn" @click="closeRemTip">知道了</button>
    </div>

    <div class="home-body">
      <LeftRail />

      <!-- 中栏：聊天 -->
      <section class="chat-pane">
        <div class="msg-flow" ref="flowEl">
          <template v-if="chat.messages.length">
            <div v-for="(m, i) in chat.messages" :key="i"
                 class="msg-row" :class="m.role">
              <div class="bubble" :class="`b-${m.role}`">
                <!-- ★ 保险4：回复方三色标注（★ 2026-09-13 修复：来源事件未到时不再兜底显示"外脑"，
                     避免选了本地却先看到"外脑回答"再跳变"本地"的误导——等 model_source 到了才显示） -->
                <!-- ★ 2026-09-14：回复胶囊带具体来源（DeepSeek/GLM/协作/Agent），历史对话从 metadata.source 还原 -->
                <div v-if="m.role === 'assistant' && (m.replyBy || m.sourceLabel)" class="reply-by">
                  <ModeCapsule :mode="replyMode(m)" :text="m.sourceLabel || replyLabel(m.replyBy)" />
                </div>
                <!-- ★ 2026-09-21 #830：工具调用过程折叠卡（默认收起，点开看详情） -->
                <details v-if="m.role === 'assistant' && m.toolLogs && m.toolLogs.length" class="tool-log-card">
                  <summary>⚙️ 工具调用过程（{{ m.toolLogs.length }}）</summary>
                  <pre class="tool-log-body">{{ m.toolLogs.join('\n\n') }}</pre>
                </details>
                <!-- ★ 2026-09-15 A方案：用户消息里的图片缩略图 -->
                <!-- ★ #890（2026-09-26）：图片下加「存入面板」入口，根治发图不落库发完即丢 -->
                <div v-if="m.images && m.images.length" class="bubble-imgs">
                  <div v-for="(im, ii) in m.images" :key="ii" class="bubble-img-wrap">
                    <img :src="im" class="bubble-img" alt="图片" />
                    <button v-if="m.role === 'user'" class="img-save-btn" title="把这张图存进面板"
                            @click="openSaveImage(im)">📥 存入面板</button>
                  </div>
                </div>
                <!-- ★ 2026-09-27：助手回答里的图片内嵌展示（模型输出 ![照片](/api/media/knowledge/xx) 或裸知识库图片路径时自动渲染成可看的图） -->
                <div v-if="m.role === 'assistant' && msgImgs(m.content).length" class="bubble-imgs">
                  <div v-for="(iu, ii) in msgImgs(m.content)" :key="ii" class="bubble-img-wrap">
                    <img :src="iu" class="bubble-img bubble-img-view" alt="照片" @click="openImgViewer(iu)" />
                  </div>
                </div>
                <!-- ★ #926：聊天内跳转按钮（模型输出【打开:/photos】→ 渲染成可点的开门按钮） -->
                <div v-if="m.role === 'assistant' && msgJumps(m.content).length" class="bubble-jumps">
                  <button v-for="(jp, ji) in msgJumps(m.content)" :key="'j' + ji" class="jump-btn"
                          :title="'点击打开' + jp.label" @click="goJump(jp.path)">🚪 打开{{ jp.label }} →</button>
                </div>
                <p class="bubble-text">{{ m.role === 'assistant' ? stripMsgImgs(m.content) || '' : (m.content || (chat.sending && i === chat.messages.length - 1 ? '…' : '')) }}</p>
              </div>
            </div>
          </template>
          <div v-else class="chat-welcome">
            <DuckLogo :px="64" />
            <h2>你好，我是哈文</h2>
            <p>聊天、查知识库、跑技能、管面板，直接说就行。</p>
            <!-- ★ 2026-09-19 易用性①：直接跟我说引导卡（点击填入对话框） -->
            <div class="say-card">
              <b>💬 直接跟我说，就能记事/提醒：</b>
              <div class="say-chips">
                <button class="say-chip" @click="fillSay('买菜花了86块')">🛒 买菜花了86块</button>
                <button class="say-chip" @click="fillSay('数学考了85分')">📚 数学考了85分</button>
                <button class="say-chip" @click="fillSay('明天下午3点提醒我取快递')">⏰ 明天下午3点提醒我取快递</button>
                <button class="say-chip" @click="fillSay('爸今天血压有点高，人有点晕')">👴 爸今天血压有点高</button>
              </div>
              <span class="say-tip">点一句填进输入框试试——家里的事直接说，哈文自动分类记进对应模块；说「X点提醒我…」就能建提醒，到点首页「今天要处理」会亮。</span>
            </div>
            <div class="welcome-tips">
              <ModeCapsule mode="cloud" text="外脑 · 联网强推理" />
              <ModeCapsule mode="local" text="本地 · 隐私秒回" />
            </div>
          </div>
        </div>

        <!-- 常驻输入栏：＋菜单 + 发送（输入框规范：全部视图常驻） -->
        <div class="input-bar">
          <!-- ★ 2026-09-16 用户反馈：输入框可上下拖拽调大小（双击把手恢复自动高度） -->
          <div class="input-resizer" title="拖动调整输入框高度 · 双击恢复自动" @pointerdown="startResize" @dblclick="resetHeight"><span></span></div>
          <div v-if="chat.thinking" class="thinking-line">
            <StatusDot status="amber" /> {{ chat.thinking }}
          </div>
          <div v-if="menuOpen" class="plus-menu">
            <button @click="pickFile"><HIcon name="folder" :size="16" /> {{ uploading ? '读取中…' : '发文件给哈文' }}</button>
            <button @click="openInbox"><HIcon name="image" :size="16" /> 📝 随身记</button>
            <button @click="takePhoto"><HIcon name="image" :size="16" /> 拍照问哈文</button>
            <button @click="pickImage"><HIcon name="image" :size="16" /> 发图片给哈文看</button>
            <button @click="voiceMenuTap"><HIcon name="mic" :size="16" /> 语音输入</button>
          </div>
          <input ref="fileEl" type="file" multiple hidden @change="onFilePicked" />
          <input ref="imgEl" type="file" accept="image/*" multiple hidden @change="onImagePicked" />
          <!-- ★ #848 兜底：浏览器不支持直接录音时（如部分 WebView），调系统录音机 -->
          <input ref="audioEl" type="file" accept="audio/*" capture hidden @change="onAudioPicked" />
          <!-- ★ #848 按住说话：录音→传回电脑→本机转文字（APP 无需装任何模型） -->
          <!-- ★ #929-A3 微信式按住说话面板（波纹/秒数/上滑取消/转写状态/失败重试） -->
          <div v-if="recUI.state !== 'idle'" class="rec-overlay">
            <template v-if="recUI.state === 'error'">
              <span class="ro-msg bad">❌ {{ recUI.msg }}</span>
              <button class="rec-btn ok" @click="voiceRetry">重试</button>
              <button class="rec-btn no" @click="voiceGiveup">放弃</button>
            </template>
            <template v-else-if="recUI.state === 'busy'">
              <span class="rec-dot busy"></span>
              <b class="ro-msg">{{ recUI.msg || '正在转文字…' }}</b>
            </template>
            <template v-else>
              <span class="rec-waves" :class="{ cancel: recUI.cancel }"><i></i><i></i><i></i><i></i><i></i></span>
              <b class="ro-msg" :class="{ cancel: recUI.cancel }">{{ recUI.cancel ? '松开取消' : '正在听… ' + recUI.seconds + 's' }}</b>
              <span class="ro-hint">{{ recUI.cancel ? '抬手即取消，不会发送' : '松开发送 · 手指上滑取消' }}</span>
            </template>
          </div>
          <!-- ★ #929-A4 语音档案：转写成功后一键存档（12 秒内有效） -->
          <button v-if="voiceSave.show" class="voice-save-btn" @click="saveVoiceArchive" title="把这段话和录音存进语音档案">💾 存入语音档案</button>
          <!-- ★ 2026-09-14：当前回答方小字（补回删龙虾工作台丢掉的来源展示） -->
          <div v-if="chat.sourceNote && !chat.sending" class="src-note">
            <ModeCapsule :mode="sourceNoteMode" :text="chat.sourceNote" />
          </div>
          <!-- ★ 2026-09-15 A方案：待发送图片预览条 -->
          <div v-if="pendingImages.length" class="img-chips">
            <div v-for="(im, ii) in pendingImages" :key="ii" class="img-chip">
              <img :src="im.dataUrl" alt="" />
              <button class="img-chip-x" title="移除" @click="pendingImages.splice(ii, 1)">✕</button>
            </div>
          </div>
          <div class="input-row">
            <button class="plus-btn" title="＋菜单" @click="menuOpen = !menuOpen"><HIcon name="plus" :size="20" /></button>
            <!-- ★ #929-A3 按住说话：按住录、松开发送、上滑取消；正在录时再点一下=完成（菜单进入的路径） -->
            <button class="plus-btn mic-btn" :class="{ rec: recUI.state === 'rec' }" title="按住说话，松开转文字"
                    @pointerdown.prevent="micDown" @pointermove="micMove" @pointerup="micUp" @pointercancel="micUp"
                    @contextmenu.prevent>
              <HIcon name="mic" :size="18" />
            </button>
            <textarea ref="inputEl" v-model="draft" rows="1"
                      :placeholder="pendingImages.length ? '给图片配句话（可不填，直接发送也行）…' : (chat.collabOn ? '协作中：可用 / 分段接力，如 构思/生成/评价…' : '问点什么…（Enter 发送，Shift+Enter 换行，可直接 Ctrl+V 粘贴图片）')"
                      @keydown.enter.exact.prevent="doSend"
                      @paste="onPaste"
                      @drop="onDrop"
                      @dragover.prevent
                      @input="autoGrow" />
            <!-- ★ 2026-10-10 #1053: 生成中按钮变「⏹ 停止」（真停止：后端收笔+半截入库） -->
            <button v-if="chat.sending" class="send-btn stop-btn" @click="chat.stopGeneration()"
                    :disabled="chat.stopping" :title="chat.stopping ? '正在停止…' : '停止生成'">
              <HIcon name="stop" :size="16" />
            </button>
            <button v-else class="send-btn" :disabled="!draft.trim() && !pendingImages.length" @click="doSend" title="发送">
              <HIcon name="send" :size="18" />
            </button>
          </div>
        </div>
      </section>

      <RightDock />
    </div>

    <!-- ═══ ★ 2026-09-14 方案A：协作选项面板 ═══ -->
    <div v-if="showCollabPanel" class="collab-mask" @click.self="showCollabPanel = false">
      <div class="collab-box">
        <div class="cb-head">
          <b>🤝 协作模式</b>
          <button class="cb-close" @click="showCollabPanel = false">关闭</button>
        </div>
        <p class="cb-tip">协作 = 先让 AI 军师出主意，再让哈文本地干活。选好「怎么协作、谁来出主意、传多少数据」，点保存就行。</p>

        <!-- 模式（★ 2026-09-16 已定案：删"本地协作"——协作流水线是它的完全超集） -->
        <div class="cb-sec">
          <label class="cb-label">1 · 怎么协作</label>
          <div class="cb-modes">
            <button class="cb-mode" :class="{ on: collabForm.mode === 'cloud_local' }" @click="collabForm.mode = 'cloud_local'">
              <b>☁️ 智能协作<small>推荐</small></b>
              <span>流水线编排：每站任选外脑 / 本地 / 垂直生成，聪明又保隐私</span>
            </button>
            <button class="cb-mode" :class="{ on: collabForm.mode === 'off' }" @click="collabForm.mode = 'off'">
              <b>🚫 关闭协作</b>
              <span>不用协作，由当前模型直接回答</span>
            </button>
          </div>
        </div>

        <!-- ★ V1 流水线编排器：每一站任选外脑/本地/垂直生成 -->
        <div v-if="collabForm.mode === 'cloud_local'" class="cb-sec">
          <label class="cb-label">2 · 流水线编排（每一站随便换人）</label>
          <div class="cb-presets">
            <button v-for="ps in PIPELINE_PRESETS" :key="ps.name" class="cb-preset" @click="applyPreset(ps)">{{ ps.name }}</button>
          </div>
          <div class="cb-steps">
            <div v-for="(st, si) in collabPipeline" :key="si" class="cb-step">
              <span class="cb-step-no">{{ si + 1 }}</span>
              <!-- ★ 2026-09-16：站类型可直接切换（对话↔生成），生成站放第一站也行 -->
              <select :value="st.type" class="cb-kind" @change="switchStepType(si, $event.target.value)"
                      :title="st.type === 'chat' ? '对话站：出主意/分析/整合' : '生成站：画图/作曲/语音'">
                <option value="chat">💬 对话</option>
                <option value="tool">🎬 生成</option>
              </select>
              <select v-if="st.type === 'chat'" v-model="st.participant" class="cb-select">
                <option v-for="p in collabParts.chat" :key="p.id" :value="p.id">
                  {{ p.name }}{{ p.kind === 'external' && !p.has_key ? '（⚠️没钥匙）' : '' }}
                </option>
              </select>
              <select v-else v-model="st.model" class="cb-select">
                <option v-for="p in collabParts.tool" :key="p.id" :value="p.id">{{ p.icon }} {{ p.name }}（{{ p.estimate }}）</option>
              </select>
              <span class="cb-step-ops">
                <button class="cb-op" title="上移" :disabled="si === 0" @click="moveStep(si, -1)">↑</button>
                <button class="cb-op" title="下移" :disabled="si === collabPipeline.length - 1" @click="moveStep(si, 1)">↓</button>
                <button v-if="collabPipeline.length > 2" class="cb-op cb-op-del" title="删除这一站" @click="collabPipeline.splice(si, 1)">✕</button>
                <span v-else-if="si === collabPipeline.length - 1" class="cb-step-final">作答站</span>
              </span>
            </div>
          </div>
          <div v-if="collabPipeline.length < 4" class="cb-step-add">
            <button class="cb-addbtn" @click="addStep('chat')">＋ 加一站</button>
          </div>
          <p v-if="collabPipeline.length && collabPipeline[collabPipeline.length - 1].type !== 'chat'" class="cb-warn">
            ⚠️ 最后一站必须是对话站（它负责整合回答）
          </p>
          <p class="cb-hint">💬 对话站：出主意 / 分析 / 整合回答（🏠 可任选本地模型） · 🎬 生成站：自动把上游文字填进表单直接生成，产物传给下一站</p>
          <div class="cb-slash-tip">
            <b>💡 分段接力（推荐用法）</b>
            <span>发消息时每个站让它干什么，用 <code>/</code> 隔开，按顺序对应上面的站：</span>
            <span class="cb-slash-eg">出个乡村欢快钢琴曲的构思 <code>/</code> 按构思生成15秒钢琴曲 <code>/</code> 评价这首曲子</span>
            <span class="cb-slash-sub">打几段就跑几站；第 2 段落到 🎬 生成站就会真的生成音乐（约2~8分钟）。不打 / 就是一句话自动跑。</span>
          </div>
        </div>

        <!-- 隐私档位 -->
        <div v-if="collabForm.mode === 'cloud_local'" class="cb-sec">
          <label class="cb-label">3 · 传多少数据给外脑（隐私档位）</label>
          <div class="cb-privs">
            <button v-for="p in PRIVACY_OPTS" :key="p.id" class="cb-priv" :class="{ on: collabForm.privacy_level === p.id }" @click="collabForm.privacy_level = p.id">
              <b>{{ p.name }}</b>
              <span>{{ p.desc }}</span>
            </button>
          </div>
        </div>

        <div class="cb-actions">
          <!-- ★ 2026-09-16 用户反馈#1：编排自动保存 + 大保存按钮说明生效逻辑 -->
          <span v-if="collabSavedAt" class="cb-autosave">✓ 编排已自动保存 {{ collabSavedAt }}</span>
          <BigButton class="cb-savebig" :px="26" :loading="collabSaving" @click="saveCollab">
            <HIcon name="send" :size="16" /> 保存并生效
          </BigButton>
          <p class="cb-save-tip">编排改动会自动保存；点「保存并生效」后，下次发消息就按这条流水线跑。</p>
        </div>
      </div>
    </div>

    <!-- ★ #890（2026-09-26）：聊天发图「存入面板」弹层 -->
    <SaveImageCard :visible="!!saveImg" :image="saveImg || ''" @close="saveImg = ''" />
    <!-- ★ 2026-09-27 助手回答图片：点击看大图 -->
    <div v-if="imgViewer" class="img-viewer-overlay" @click="imgViewer = ''">
      <img :src="imgViewer" alt="大图查看" />
      <p class="img-viewer-tip">点击任意处关闭</p>
    </div>
  </div>
</template>

<script setup>
// B1 首页三栏：左=会话+7工具格 / 中=聊天 / 右=面板停靠+龙虾工作台
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { useChatStore } from '../stores/chat'
import { useUiStore } from '../stores/ui'
import LeftRail from '../components/LeftRail.vue'
import SaveImageCard from '../components/SaveImageCard.vue'
import RightDock from '../components/RightDock.vue'
import ModeCapsule from '../components/ModeCapsule.vue'
import StatusDot from '../components/StatusDot.vue'
import DuckLogo from '../components/DuckLogo.vue'
import HIcon from '../components/HIcon.vue'

const chat = useChatStore()
const ui = useUiStore()
const draft = ref('')
const menuOpen = ref(false)
const flowEl = ref(null)
const inputEl = ref(null)

const modeKey = ref(chat.queryMode === 'kb_only' ? 'local' : 'cloud')
watch(() => chat.queryMode, m => {
  modeKey.value = m === 'kb_only' ? 'local' : 'cloud'
})
function pickMode(m) {
  modeKey.value = m
  if (chat.collabOn) chat.setCollab(false) // 三段互斥：选本地/外脑 = 关协作
  chat.setMode(m === 'local' ? 'kb_only' : 'direct')
}
// ★ 2026-09-13 反馈#3：联网真开关（下一条消息强制搜索）
const webSearch = ref(false)
function toggleWebSearch() {
  webSearch.value = !webSearch.value
  chat.thinking = webSearch.value ? '🌐 已开启联网：下一条消息会先搜网络再回答' : '🌐 已关闭联网：下一条消息按当前模式正常回答'
  setTimeout(() => { if (chat.thinking.startsWith('🌐')) chat.thinking = '' }, 2500)
}

// ★ 2026-09-14 方案A：协作选项面板（替代单击直接开关）
// 数据全部来自现成后端接口：GET /api/collab/status · foreign-models · privacy-levels；POST /api/collab/config
const showCollabPanel = ref(false)
const collabSaving = ref(false)
const collabForm = ref({ mode: 'cloud_local', foreign_model: 'deepseek', privacy_level: 'full' })
const collabForeign = ref([])   // [{id,name,has_key,enabled}]
// ★ V1 协作流水线编排
const collabPipeline = ref([])  // [{type:'chat',participant} | {type:'tool',model}]
const collabParts = ref({ chat: [], tool: [] })  // GET /api/collab/participants
const PIPELINE_PRESETS = [
  { name: '⚡ 经典两步', steps: ['C1', 'L'] },
  { name: '🤝 双外脑会诊', steps: ['C1', 'C2', 'L'] },
  { name: '🏠 本地优先', steps: ['L', 'C1', 'L'] },
  { name: '🎬 创作流水线', steps: ['C1', 'T:stable-diffusion', 'T:musicgen-small', 'L'] },
]
function _presetStep(code) {
  const key = collabForeign.value.find(m => m.has_key)?.id || 'deepseek'
  const key2 = collabForeign.value.filter(m => m.has_key && m.id !== key)[0]?.id || key
  if (code === 'L') return { type: 'chat', participant: 'local' }
  if (code === 'C1') return { type: 'chat', participant: key }
  if (code === 'C2') return { type: 'chat', participant: key2 }
  if (code.startsWith('T:')) return { type: 'tool', model: code.slice(2) }
  return { type: 'chat', participant: 'local' }
}
function applyPreset(ps) {
  collabPipeline.value = ps.steps.map(_presetStep)
}
function addStep(t) {
  if (collabPipeline.value.length >= 4) return
  const last = collabPipeline.value[collabPipeline.value.length - 1]
  collabPipeline.value.splice(collabPipeline.value.length - 1, 0,
    t === 'tool'
      ? { type: 'tool', model: collabParts.value.tool[0]?.id || 'stable-diffusion' }
      : { type: 'chat', participant: collabForeign.value.find(m => m.has_key)?.id || 'local' })
  void last
}

// ★ 2026-09-16 已定案：站类型直接切换（对话↔生成）+ 上移下移排序（生成站可以放第一站）
function switchStepType(si, newType) {
  const st = collabPipeline.value[si]
  if (!st || st.type === newType) return
  if (newType === 'tool') {
    collabPipeline.value[si] = { type: 'tool', model: collabParts.value.tool[0]?.id || 'stable-diffusion' }
  } else {
    collabPipeline.value[si] = { type: 'chat', participant: 'local' }
  }
}
function moveStep(si, dir) {
  const j = si + dir
  if (j < 0 || j >= collabPipeline.value.length) return
  const arr = collabPipeline.value
  ;[arr[si], arr[j]] = [arr[j], arr[si]]
}

// ★ 2026-09-16 用户反馈#1：编排改动自动保存（防抖800ms），不用再记着点保存
const collabSavedAt = ref('')
let _autosaveTimer = null
let _suppressAutosave = false
watch(collabPipeline, () => {
  if (_suppressAutosave || !showCollabPanel.value || collabForm.value.mode !== 'cloud_local') return
  clearTimeout(_autosaveTimer)
  _autosaveTimer = setTimeout(async () => {
    // 最后一站不是对话站时先不自动保存（等用户调好再存）
    const arr = collabPipeline.value
    if (!arr.length || arr[arr.length - 1].type !== 'chat') return
    try {
      const pj = await postJSON('/api/collab/pipeline', { pipeline: arr })
      if (pj.success) {
        const t = new Date()
        collabSavedAt.value = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
      }
    } catch { /* 自动保存失败不打扰，手动保存兜底 */ }
  }, 800)
}, { deep: true })
const PRIVACY_OPTS = [
  { id: 'strict', name: '严格', desc: '不传用户数据，只发去敏后的问题摘要' },
  { id: 'balanced', name: '平衡', desc: '去掉姓名/电话/金额等敏感信息再传' },
  { id: 'full', name: '全量', desc: '数据全传，只限你完全信任的外脑（最快最准）' },
]
function openCollabPanel() {
  // 表单初始值：当前会话状态优先，再拉后台配置校准
  // ★ 2026-09-16：local_dual（本地协作）已删除，旧值映射为智能协作
  const _m = chat.collabOn ? (chat.collabMode || 'cloud_local') : 'off'
  collabForm.value = {
    mode: _m === 'local_dual' ? 'cloud_local' : _m,
    foreign_model: collabForeign.value.length ? collabForm.value.foreign_model : 'deepseek',
    privacy_level: collabForm.value.privacy_level || 'full',
  }
  showCollabPanel.value = true
  loadCollabConfig()
}
async function loadCollabConfig() {
  try {
    _suppressAutosave = true  // 装载期间的变动不算用户编辑，不触发自动保存
    const [st, fm, pp, pt] = await Promise.all([
      getJSON('/api/collab/status').catch(() => null),
      getJSON('/api/collab/foreign-models').catch(() => null),
      getJSON('/api/collab/pipeline').catch(() => null),
      getJSON('/api/collab/participants').catch(() => null),
    ])
    if (st?.success && st.data) {
      const d = st.data
      const _dm = (d.mode === 'local_dual') ? 'cloud_local' : d.mode  // 旧模式映射
      collabForm.value.mode = chat.collabOn ? (chat.collabMode === 'local_dual' ? 'cloud_local' : (chat.collabMode || _dm)) : _dm
      if (d.privacy_level) collabForm.value.privacy_level = d.privacy_level
    }
    if (fm?.success && Array.isArray(fm.data)) {
      collabForeign.value = fm.data
    }
    // ★ V1：流水线 + 参与者
    if (pp?.success && pp.data?.pipeline) {
      collabPipeline.value = pp.data.pipeline.map(s => ({ ...s }))
    }
    if (pt?.success && pt.data) {
      collabParts.value = { chat: pt.data.chat || [], tool: pt.data.tool || [] }
      // ★ 2026-09-16：清洗——参与者已不存在（没钥匙被下架等）→ 落回本地
      const validIds = new Set(collabParts.value.chat.map(p => p.id))
      for (const s of collabPipeline.value) {
        if (s.type === 'chat' && s.participant && !validIds.has(s.participant)) s.participant = 'local'
        if (s.type === 'tool' && !(collabParts.value.tool || []).some(t => t.id === s.model)) s.model = collabParts.value.tool[0]?.id || s.model
      }
    }
    // 编排为空时套默认预设（用第一个有钥匙的外脑）
    if (!collabPipeline.value.length) {
      collabPipeline.value = [_presetStep('C1'), _presetStep('L')]
    }
    // 兜底：一个有钥匙的外脑都没有 → 面板顶部提醒
    if (collabForeign.value.length && !collabForeign.value.some(m => m.has_key)) {
      chat.thinking = '⚠️ 一个有钥匙的外脑都没有——先去 设置 → 钥匙串 配一个 API Key'
      setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 4000)
    }
  } catch (e) { console.warn('协作配置加载失败', e) }
  finally { setTimeout(() => { _suppressAutosave = false }, 50) }
}
async function saveCollab() {
  const f = collabForm.value
  // ★ V1：智能协作时先校验并保存流水线编排
  if (f.mode === 'cloud_local') {
    if (!collabPipeline.value.length || collabPipeline.value[collabPipeline.value.length - 1].type !== 'chat') {
      chat.thinking = '⚠️ 最后一站必须是对话站（它负责整合回答）'
      setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 4000)
      return
    }
    try {
      const pj = await postJSON('/api/collab/pipeline', { pipeline: collabPipeline.value })
      if (!pj.success) throw new Error(pj.error || '流水线保存失败')
    } catch (e) {
      chat.thinking = '⚠️ 流水线保存失败：' + e.message
      setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 4000)
      return
    }
  }
  collabSaving.value = true
  try {
    const j = await postJSON('/api/collab/config', {
      mode: f.mode,
      privacy_level: f.privacy_level,
    })
    if (!j.success) throw new Error(j.error || '保存失败')
    // 会话内立即生效（按钮即真相；配置同时存进 config.yaml 给旧版界面/重启后用）
    chat.setCollab(f.mode !== 'off', f.mode === 'off' ? undefined : f.mode)
    showCollabPanel.value = false
    chat.thinking = f.mode === 'off'
      ? '🦞 协作已关闭：回到普通模式回答'
      : `🦞 流水线协作已开启：${collabPipeline.value.map(s => s.type === 'chat' ? (s.participant === 'local' ? '🏠本地' : (s.participant.startsWith('ollama:') ? '🏠' + s.participant.slice(7) : s.participant.startsWith('gguf:') ? '🏠垂直' : s.participant)) : '🎬生成').join(' → ')}`
    setTimeout(() => { if (chat.thinking.startsWith('🦞')) chat.thinking = '' }, 4200)
  } catch (e) {
    chat.thinking = '⚠️ 协作配置保存失败：' + e.message
    setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 4000)
  } finally { collabSaving.value = false }
}

// ★ 状态签真实化：安全模式 + 外网连接都实时拉取
const secFull = ref(false)
const internetOk = ref(true)
import { getJSON, postJSON } from '../api.js'
async function loadSec() {
  try {
    const d = await getJSON('/api/security/mode')
    secFull.value = (d.mode === 'full')
  } catch { /* 拉不到就保持安全默认 */ }
}
async function loadNet() {
  try { const d = await getJSON('/api/net/check'); internetOk.value = !!d.online }
  catch { internetOk.value = false }
}
loadSec(); loadNet()
setInterval(loadSec, 30000)
setInterval(loadNet, 60000)

/* ── ★ 2026-09-18 #748 今天要处理横条：与鸭头角标同源（/api/assist/alerts），60s 轮询 ── */
import { useRouter, useRoute } from 'vue-router'
import { delJSON } from '../api.js'
const router = useRouter()
const alerts = ref({ count: 0, reminders: [], pending: 0 })
async function loadAlerts() {
  try {
    const d = await getJSON('/api/assist/alerts')
    if (d.success) alerts.value = { count: d.count || 0, reminders: d.reminders || [], pending: d.pending || 0 }
  } catch { /* 拉不到就静默，别打扰聊天 */ }
}
function goHandle(r) {
  if (r.link) router.push(r.link)   // link 形如 /panel/kid_archive
}
function ackReminder(r) {
  postJSON(`/api/assist/alerts/reminder/${r.id}/ack`).catch(() => {})
  alerts.value.reminders = alerts.value.reminders.filter(x => x.id !== r.id)
  alerts.value.count = alerts.value.reminders.length + alerts.value.pending
}
async function delReminder(r) {
  if (!confirm(`确定删除提醒「${r.title}」？（重复提醒删了就不再响）`)) return
  try { await delJSON(`/api/assist/alerts/reminder/${r.id}`) } catch { /* 已删则忽略 */ }
  alerts.value.reminders = alerts.value.reminders.filter(x => x.id !== r.id)
  alerts.value.count = alerts.value.reminders.length + alerts.value.pending
}
loadAlerts()
setInterval(loadAlerts, 60000)

/* ★ 2026-09-19 易用性③：提醒空态引导（知道了就不再显示） */
const REM_TIP_KEY = 'harven_spa_remind_tip_off'
const remTipOff = ref(localStorage.getItem(REM_TIP_KEY) === '1')
function closeRemTip() { remTipOff.value = true; localStorage.setItem(REM_TIP_KEY, '1') }

function replyLabel(replyBy) {
  if (replyBy === 'local') return '本地回答'
  if (replyBy === 'vertical') return '垂直模型'
  return '外脑回答'
}

/* ★ 2026-09-14：来源配色——协作走琥珀，其余按外脑/本地 */
function replyMode(m) {
  if (m.sourceLabel && /协作/.test(m.sourceLabel)) return 'collab'
  return m.replyBy || 'cloud'
}
const sourceNoteMode = computed(() => {
  const s = chat.sourceNote || ''
  if (/协作/.test(s)) return 'collab'
  if (/本地|🏠/.test(s)) return 'local'
  if (/垂直|🌡/.test(s)) return 'vertical'
  return 'cloud'
})

var doSendRef = null   /* ★ 2026-10-22 autosend 桥接 */
function doSend() {
  doSendRef = doSend
  const text = draft.value.trim()
  if ((!text && !pendingImages.value.length) || chat.sending) return
  draft.value = ''
  const imgs = pendingImages.value.splice(0) // 取走并清空
  menuOpen.value = false
  if (inputEl.value) inputEl.value.style.height = 'auto'
  chat.send(text, { web_search: webSearch.value, images: imgs })
}

/* ★ 2026-09-19 易用性①：点示例话术填入对话框（不自动发送，让用户先看看怎么说话） */
function fillSay(text) {
  draft.value = text
  nextTick(() => inputEl.value?.focus({ preventScroll: true }))
}

function autoGrow(e) {
  const el = e.target
  el.style.height = 'auto'
  const h = Math.min(el.scrollHeight, manualH.value || 140)
  el.style.height = Math.max(h, manualH.value) + 'px'
}

/* ★ 2026-09-16 用户反馈：首页输入框可上下拖拽调大小——把手在输入栏顶部，
   上拖变高、下拖变矮，双击恢复自动；拖完记住（localStorage），下次打开还在 */
const RESIZE_KEY = 'harven_home_input_h'
const manualH = ref(0)
function startResize(e) {
  e.preventDefault()
  const el = inputEl.value
  if (!el) return
  const startY = e.clientY
  const startH = el.getBoundingClientRect().height
  const cap = Math.min(480, Math.round(window.innerHeight * 0.6))
  const move = ev => {
    const h = Math.round(Math.max(44, Math.min(startH + (startY - ev.clientY), cap)))
    manualH.value = h
    el.style.maxHeight = h + 'px'
    el.style.height = h + 'px'
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    try { localStorage.setItem(RESIZE_KEY, String(manualH.value)) } catch (_) { /* 静默 */ }
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
function resetHeight() {
  manualH.value = 0
  try { localStorage.removeItem(RESIZE_KEY) } catch (_) { /* 静默 */ }
  const el = inputEl.value
  if (el) { el.style.maxHeight = ''; autoGrow({ target: el }) }
}
function restoreInputH() {
  try {
    const h = parseInt(localStorage.getItem(RESIZE_KEY) || '0', 10)
    if (h >= 44 && inputEl.value) {
      manualH.value = h
      inputEl.value.style.maxHeight = h + 'px'
      inputEl.value.style.height = h + 'px'
    }
  } catch (_) { /* 静默 */ }
}
onMounted(restoreInputH)

/* ── ★ 2026-10-20 第二批2.2: 工作台目标进度横条（可折叠） ── */
const goalCards = ref([])
const goalOpen = ref(false)
async function loadGoalStrip() {
  try {
    const r = await fetch('/api/goals?status=active')
    const j = await r.json()
    goalCards.value = (j.goals || []).slice(0, 3)
  } catch { goalCards.value = [] }
}
/* ── ★ 2026-10-20 第三批3.2: 即将到来（四源合一） ── */
const upcoming = ref([])
async function loadUpcoming() {
  try {
    const r = await fetch('/api/home/upcoming?days=7')
    const j = await r.json()
    upcoming.value = j.items || []
  } catch { upcoming.value = [] }
}
onMounted(() => { loadGoalStrip(); loadUpcoming() })

/* ★ 2026-10-20 面板「和哈文聊目标」/讨论录入项 → 带主题到聊天页自动填入输入框 */
const homeRoute = useRoute()
onMounted(() => {
  const pre = homeRoute.query.prefill
  if (pre) {
    draft.value = String(pre)
    nextTick(() => inputEl.value && inputEl.value.focus())
    /* ★ 2026-10-22 autosend=1：「和哈文聊目标/讨论加项」点了直接开聊（填入+自动发送），不再让用户找发送键 */
    const auto = homeRoute.query.autosend === '1'
    try { window.history.replaceState(null, '', '#/') } catch (_) {}
    if (auto) setTimeout(() => { try { doSendRef && doSendRef() } catch (_) {} }, 350)
  }
  /* ★ 2026-10-21 会话可见包：30s 自动刷新会话列表（手机会话电脑自动出现） */
  try { chat.ensureConversationPoll() } catch (_) {}
})

// ★ 2026-09-15 A方案：发图片给哈文看 — 选图 → dataURL 预览 → 随消息发送（后端自动路由看图模型）
const imgEl = ref(null)
const pendingImages = ref([]) // [{name, dataUrl}]

// ★ #890（2026-09-26）：发图存入面板
const saveImg = ref('')
function openSaveImage(dataUrl) { saveImg.value = dataUrl || '' }

// ★ 2026-09-27 助手回答图片内嵌：提取/剥离回答中的图片引用，气泡内直接渲染可看的图
// 支持三种写法：Markdown ![照片](/api/images/file/9)（统一图库）、
// Markdown ![照片](/api/media/knowledge/xx.jpg)（知识库直链）、裸知识库路径
const _IMG_URL_RE = /!\[[^\]]*\]\((\/api\/(?:images\/file\/\d+[^)\s]*|media\/knowledge\/[^)\s]+))\)/g
const _IMG_PATH_RE = /(?:[A-Za-z]:[/\\][^\s"'）)】]*?data[/\\]knowledge[/\\]([A-Za-z0-9_\-.\u4e00-\u9fff]+\.(?:jpg|jpeg|png|gif|webp|bmp))|data[/\\]knowledge[/\\]([A-Za-z0-9_\-.\u4e00-\u9fff]+\.(?:jpg|jpeg|png|gif|webp|bmp)))/gi
function msgImgs(text) {
  const t = text || ''
  const urls = []
  let mm
  const re1 = new RegExp(_IMG_URL_RE.source, 'g')
  while ((mm = re1.exec(t))) urls.push(mm[1])
  const re2 = new RegExp(_IMG_PATH_RE.source, 'gi')
  while ((mm = re2.exec(t))) urls.push('/api/media/knowledge/' + encodeURIComponent(mm[1] || mm[2]))
  return [...new Set(urls)]
}
function stripMsgImgs(text) {
  const t = text || ''
  return t
    .replace(new RegExp(_IMG_URL_RE.source, 'g'), '')
    .replace(new RegExp(_IMG_PATH_RE.source, 'gi'), '')
    .replace(new RegExp(_JUMP_RE.source, 'g'), '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
const imgViewer = ref('')
function openImgViewer(url) { imgViewer.value = url }
// ★ #926（2026-09-27 已批准）：聊天内跳转通道——模型在回答里输出【打开:/photos】这类标记，
//   前端剥离标记并渲染成「打开 xx」按钮，点了直接切页。模型只认白名单路径，不认识的标记原样保留不跳。
const _JUMP_RE = /【打开:\s*(\/[^\】]+?)\s*\】/g
const _JUMP_LABELS = {
  '/photos': '图片库', '/memory': '记忆相册', '/panels': '面板列表', '/knowledge': '知识库', '/skills': '技能库',
  '/tasks': '任务', '/mail': '邮箱', '/models': '模型中心', '/settings': '设置',
  '/status': '运行状态', '/timeline': '时间轴', '/hub': '市场', '/profile': '画像',
  '/vertical': '垂直大脑', '/create': '创造区', '/studio': '创作工坊',
}
function msgJumps(text) {
  const out = []
  const re = new RegExp(_JUMP_RE.source, 'g')
  let m
  while ((m = re.exec(text || '')) !== null) {
    const path = m[1]
    let label = _JUMP_LABELS[path]
    if (!label && path.startsWith('/panel/')) label = '面板'
    if (label) out.push({ path, label })
  }
  return out
}
function goJump(path) {
  if (path) router.push(path)
}
const MAX_IMG_MB = 10
/* ★ 电脑「+」菜单随身记：唤起 RightDock 收件箱弹层（事件在 RightDock onMounted 监听） */
function openInbox() {
  menuOpen.value = false
  window.dispatchEvent(new CustomEvent('harven-open-inbox'))
}
function pickImage() {
  menuOpen.value = false
  imgEl.value?.click()
}
function onImagePicked(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  addImageFiles(files)
}

/* ★ 2026-09-15 用户反馈#2：Ctrl+V 粘贴图片 / 拖拽图片进输入框 —— 复用同一条待发预览 */
function addImageFiles(files) {
  for (const f of files) {
    if (!f.type.startsWith('image/')) continue
    if (f.size > MAX_IMG_MB * 1024 * 1024) {
      chat.thinking = `⚠️ 「${f.name}」超过 ${MAX_IMG_MB}MB，先压一压再发`
      setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 3500)
      continue
    }
    if (pendingImages.value.length >= 4) {
      chat.thinking = '⚠️ 一次最多发 4 张图'
      setTimeout(() => { if (chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 3500)
      break
    }
    const reader = new FileReader()
    reader.onload = () => pendingImages.value.push({ name: f.name || '粘贴图片.png', dataUrl: reader.result })
    reader.readAsDataURL(f)
  }
}
function onPaste(e) {
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'))
  if (files.length) {
    e.preventDefault()
    addImageFiles(files)
    chat.thinking = `📋 已粘贴 ${files.length} 张图片，配句话（可不填）直接发送`
    setTimeout(() => { if (chat.thinking.startsWith('📋')) chat.thinking = '' }, 2500)
  }
}
function onDrop(e) {
  const files = [...(e.dataTransfer?.files || [])].filter(f => f.type.startsWith('image/'))
  if (files.length) {
    e.preventDefault()
    addImageFiles(files)
  }
}

/* ★ 2026-09-15 用户反馈#3：打字没反应 = 焦点被右栏面板 iframe 抢走/没落回输入框
   —— 页面加载完自动聚焦输入框，每次发送结束把焦点还回来 */
function focusInput() {
  if (window.innerWidth > 900) inputEl.value?.focus({ preventScroll: true })
}
onMounted(focusInput)
watch(() => chat.sending, (now, was) => { if (was && !now) focusInput() })

function menuAction(name) {
  menuOpen.value = false
  chat.thinking = `「${name}」入口将在后续批次接入（拍照识别/语音输入）`
  setTimeout(() => { if (chat.thinking.startsWith('「')) chat.thinking = '' }, 2500)
}

// ═══ ★ D6/D7 2026-09-23 手机 APP 原生能力桥：拍照直调摄像头 + 安卓本地语音识别 ═══
// APP 壳（Capacitor）加载本页时桥仍在线 → window.Capacitor.Plugins 可用；纯浏览器里自动走旧路径
function capPlugin(name) {
  try { return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins[name]) || null }
  catch (_) { return null }
}

// ★ D6 拍照问哈文：Capacitor 相机插件直调后置摄像头 → 压缩图进待发预览，配句话一起发
async function takePhoto() {
  menuOpen.value = false
  const Cam = capPlugin('Camera')
  if (!Cam) {
    // 纯浏览器：退回文件选择（调起系统相机）
    try { imgEl.value?.setAttribute('capture', 'environment') } catch (_) {}
    imgEl.value?.click()
    return
  }
  try {
    const photo = await Cam.getPhoto({
      source: 'Camera', resultType: 'DataUrl',
      quality: 60, width: 1024, saveToGallery: false
    })
    if (!photo || !photo.dataUrl) return   // 用户取消
    const blob = await (await fetch(photo.dataUrl)).blob()
    addImageFiles([new File([blob], '拍照.jpg', { type: blob.type || 'image/jpeg' })])
  } catch (e) {
    const msg = String((e && e.message) || e || '')
    if (!/cancel/i.test(msg)) {
      chat.thinking = '拍照没成功：' + msg + '。也可以用「发图片给哈文看」选一张'
      setTimeout(() => { if (chat.thinking && chat.thinking.startsWith('拍照没成功')) chat.thinking = '' }, 5000)
    }
  }
}

// ═══ ★ #929-A3 微信式按住说话（2026-09-29 重做）：按住录、松开发送、上滑取消、失败自动重试2次 ═══
// ★ D7 保留：APP 里优先走安卓系统语音识别（SpeechRecognizer，华为/讯飞引擎，识别在手机本地完成，
//   文字先进输入框等确认再发送）；识别不可用时自动落回电脑端 Whisper 录音路径
const recSupported = !!(typeof navigator !== 'undefined' && navigator.mediaDevices && navigator.mediaDevices.getUserMedia)
// ★ #944：手机浏览器用 http+IP 打开时 = 非安全上下文，浏览器不开放麦克风给网页（APP 壳 https://localhost 不受影响）
const NO_MIC_MSG = '手机浏览器没把麦克风开放给这个网页（用 http 加 IP 打开时浏览器会这样）。推荐用哈文 APP 直接说；或点「重试」调系统录音机，录完选回来一样能转文字。'
const recUI = ref({ state: 'idle', seconds: 0, cancel: false, msg: '' })   // idle|rec|busy|error
const audioEl = ref(null)
const voiceSave = ref({ show: false, text: '', b64: '', ext: 'webm', timer: null })   // ★ #929-A4
let mediaStream = null, mediaRec = null, recChunks = [], recCancelled = false, recTick = null
let recStartY = 0, pendingBlob = null, pendingExt = 'webm'
let micClickMode = false   // ★ #963 鼠标=点一下开始/再点一下结束；触摸=按住说话（微信式）
function voiceMenuTap() {
  menuOpen.value = false
  if (recUI.value.state === 'rec') { recStop(false); return }   // 菜单进入的：再点一下=完成
  micDown()
}
function micDown(e) {
  if (recUI.value.state === 'busy') return
  if (recUI.value.state === 'error') voiceGiveup()
  const isMouse = !!(e && e.pointerType === 'mouse')
  // ★ #963 鼠标点一下=开始；正在录时再点一下=结束（原来按住式对鼠标=点一下就松=0 秒=「太短」）
  if (isMouse && recUI.value.state === 'rec') { micClickMode = false; recStop(recUI.value.cancel); return }
  micClickMode = isMouse
  try { e && e.currentTarget && e.currentTarget.setPointerCapture && e.setPointerCapture(e.pointerId) } catch (_) {}
  recStartY = (e && e.clientY) || 0
  const SR = capPlugin('SpeechRecognition')
  if (SR) { srStart(SR); return }
  // ★ #944：不再静默弹系统录音机（开发者 10-03 反馈「一点说话就跳录音机」）——先说明原因，点重试才调
  if (!recSupported) { recUI.value = { state: 'error', seconds: 0, cancel: false, msg: NO_MIC_MSG }; return }
  recStart()
}
function micMove(e) {
  if (micClickMode) return   // ★ #963 鼠标模式没有上滑取消
  if (recUI.value.state !== 'rec') return
  const dy = ((e && e.clientY) || 0) - recStartY
  recUI.value.cancel = dy < -60   // 上滑超 60px = 取消（微信同款）
}
function micUp(e) {
  // ★ #963 鼠标松开不结束——等再点一下（点击切换式）；触摸保持按住松开结束
  if (e && e.pointerType === 'mouse' && micClickMode) return
  if (recUI.value.state === 'rec') recStop(recUI.value.cancel)
}

// ── D7：安卓本地识别路径（同样的微信式面板，松开收字进输入框） ──
async function srStart(SR) {
  try {
    try { await SR.requestPermission() } catch (_) {}
    recUI.value = { state: 'rec', seconds: 0, cancel: false, msg: '' }
    const ts = Date.now()
    recTick = setInterval(() => {
      recUI.value.seconds = Math.round((Date.now() - ts) * 10) / 10
      if (recUI.value.seconds >= 60) recStop(false)
    }, 200)
    try { SR.removeAllListeners() } catch (_) {}
    // partialResults：边说边出字进输入框（用户确认后点发送）
    SR.addListener('partialResults', (data) => {
      const m = (data && (data.matches || data.value)) || []
      if (m && m[0]) draft.value = m[0]
    })
    SR.addListener('speechRecognitionResult', (data) => {
      const m = (data && (data.matches || data.value)) || []
      if (m && m[0]) draft.value = m[0]
    })
    await SR.start({ language: 'zh-CN', maxMatches: 1, partialResults: true, popup: false })
  } catch (e) {
    recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }
    // ★ 2026-09-23 用户反馈：本地识别失败原来静默回退，用户不知道换了路。现在明确提示再回退
    const why = (e && (e.message || e.reason)) || e || '未知原因'
    const notice = '手机本地识别没启动：' + why + '。已改用录音传电脑识别。'
    chat.thinking = notice
    setTimeout(() => { if (chat.thinking === notice) chat.thinking = '' }, 6000)
    if (recSupported) { recStart(); return }
  }
}
function srStop() {
  clearInterval(recTick)
  recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }
  const SR = capPlugin('SpeechRecognition')
  try { SR && SR.stop() } catch (_) {}
}

// ★ #944：录音格式探测（从手机页 #815 移植）——逐个试浏览器真支持的 mime，比硬编码稳
function pickMime() {
  const list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
  for (const m of list) { try { if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m } catch (_) {} }
  return ''
}

async function recStart() {
  recCancelled = false
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (e) {
    chat.thinking = e && e.name === 'NotAllowedError'
      ? '拿不到麦克风：权限被拒了。请在手机系统设置里允许哈文 APP 使用麦克风，再试一次'
      : '拿不到麦克风：' + (e && e.message || e)
    setTimeout(() => { if (chat.thinking && chat.thinking.startsWith('拿不到麦克风')) chat.thinking = '' }, 4000)
    return
  }
  recChunks = []
  const mime = pickMime()
  try { mediaRec = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined) }
  catch (_) { mediaRec = new MediaRecorder(mediaStream) }
  mediaRec.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data) }
  mediaRec.onstop = onRecStop
  mediaRec.start()
  recUI.value = { state: 'rec', seconds: 0, cancel: false, msg: '' }
  const ts = Date.now()
  recTick = setInterval(() => {
    recUI.value.seconds = Math.round((Date.now() - ts) * 10) / 10
    if (recUI.value.seconds >= 60) recStop(false)   // 最长 60 秒自动收
  }, 200)
}
function recStop(cancel) {
  // ★ D7：安卓本地识别路径的收尾
  if (capPlugin('SpeechRecognition') && !mediaRec) { srStop(); return }
  if (recUI.value.state !== 'rec') return
  recCancelled = !!cancel
  clearInterval(recTick)
  recUI.value = { state: 'busy', seconds: recUI.value.seconds, cancel: false, msg: '正在转文字…' }
  try { mediaRec.stop() } catch (_) { recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' } }
}
function onRecStop() {
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop())
  const chunks = recChunks; const mime = (mediaRec && mediaRec.mimeType) || 'audio/webm'
  if (recCancelled || !chunks.length) { recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }; return }
  const blob = new Blob(chunks, { type: mime })
  // ★ #963 碎录音不发后端（服务器只会回含糊报错）——当场教正确用法
  if (blob.size < 300) {
    recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }
    chat.thinking = '录音太短：' + (micClickMode ? '点一下话筒开始说，说完再点一下结束' : '按住话筒说，说完再松开')
    setTimeout(() => { if (chat.thinking && chat.thinking.startsWith('录音太短')) chat.thinking = '' }, 4000)
    return
  }
  pendingBlob = blob
  pendingExt = mime.includes('mp4') ? 'mp4' : 'webm'
  runTranscribe(pendingBlob, pendingExt)
}

// ★ #929-A3 转写统一入口：自动重试 2 次（手机→网络抖动/504 大多一retry就好）
// ★ #954: 新增 origName 参数——系统录音机选回的文件必须用真名上传（含 .amr 等真实后缀），
//   后端按真名做魔数比对；不再硬编码 speech.webm 导致 AMR 被误判
async function transcribeWithRetry(blob, ext, onTry, origName) {
  let lastErr = null
  for (let i = 0; i < 3; i++) {
    if (onTry) onTry(i)
    try {
      const fd = new FormData()
      fd.append('audio', blob, origName || (ext.includes('mp4') ? 'speech.mp4' : 'speech.webm'))
      const r = await fetch('/api/audio/transcribe', { method: 'POST', body: fd })
      const j = await r.json()
      if (j.error) throw new Error(j.error)
      return j
    } catch (e) { lastErr = e }
  }
  throw lastErr || new Error('未知原因')
}
async function runTranscribe(blob, ext, origName) {
  recUI.value = { state: 'busy', seconds: 0, cancel: false, msg: '正在转文字…' }
  try {
    const j = await transcribeWithRetry(blob, ext, (i) => {
      recUI.value.msg = i === 0 ? '正在转文字…' : `网络抖了一下，自动重试中（第 ${i} 次）…`
    }, origName)
    const txt = (j.text || '').trim()
    if (!txt) {
      recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }
      // ★ #979 空结果 = 八成麦克风根本没进声（whisper 静音幻觉防线拦下）——给电脑端排查三步
      chat.thinking = '没听清 —— 麦克风排查三步：① Win 设置→系统→声音→输入：选对麦克风、对着说话看音量条动不动；② Win 设置→隐私和安全性→麦克风：允许应用访问；③ 浏览器地址栏左侧锁图标→网站权限→麦克风：允许。然后点重试'
      setTimeout(() => { if (chat.thinking && chat.thinking.startsWith('没听清')) chat.thinking = '' }, 10000)
      return
    }
    draft.value = draft.value ? draft.value + ' ' + txt : txt
    recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' }
    showVoiceSave(txt, blob, ext)   // ★ #929-A4 转写成功 → 亮出 💾 存档
    nextTick(focusInput)
  } catch (e) {
    recUI.value = { state: 'error', seconds: 0, cancel: false, msg: '转文字没成功：' + (e.message || e) }
    setTimeout(() => { if (recUI.value.state === 'error') voiceGiveup() }, 15000)
  }
}
function voiceRetry() {
  if (pendingBlob) { runTranscribe(pendingBlob, pendingExt, pendingBlob.name || undefined); return }
  // ★ #944：无录音可重试时，「重试」= 明确调系统录音机（兜底通道保留，不再偷袭）
  voiceGiveup()
  audioEl.value?.click()
}
function voiceGiveup() { pendingBlob = null; recUI.value = { state: 'idle', seconds: 0, cancel: false, msg: '' } }

// ── ★ #929-A4 语音档案：💾 一键把「文字 + 录音」存进档案（12 秒内有效，默认不存） ──
function showVoiceSave(text, blob, ext) {
  if (voiceSave.value.timer) clearTimeout(voiceSave.value.timer)
  const toB64 = blob ? new Promise((res) => {
    const fr = new FileReader()
    fr.onload = () => res(String(fr.result).split(',')[1] || '')
    fr.onerror = () => res('')
    fr.readAsDataURL(blob)
  }) : Promise.resolve('')
  toB64.then((b64) => {
    voiceSave.value = { show: true, text, b64, ext: ext || 'webm', timer: null }
    voiceSave.value.timer = setTimeout(() => { voiceSave.value.show = false }, 12000)
  })
}
async function saveVoiceArchive() {
  const v = voiceSave.value
  if (!v.text) return
  try {
    const r = await fetch('/api/voice/archive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: v.text, audio_b64: v.b64, ext: v.ext, source: 'chat' }),
    })
    const j = await r.json()
    chat.thinking = j.success ? '💾 已存入语音档案（说「看看我的语音记录」能找回来）'
                              : '存档没成功：' + (j.error || '未知原因')
  } catch (e) { chat.thinking = '存档没成功：' + (e.message || e) }
  voiceSave.value.show = false
  setTimeout(() => { if (chat.thinking && (chat.thinking.startsWith('💾') || chat.thinking.startsWith('存档没成功'))) chat.thinking = '' }, 4000)
}

// 兜底路径：系统录音机选好的音频文件直接转
function onAudioPicked(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!f) return
  pendingBlob = f
  // ★ #954: 认真实后缀（.amr/.m4a/.wav…），不再一律猜 webm；上传用原始文件名
  const _nm = (f.name || '').toLowerCase()
  const _dot = _nm.lastIndexOf('.')
  pendingExt = _dot >= 0 ? _nm.slice(_dot + 1) : 'webm'
  runTranscribe(f, pendingExt, f.name)
}

// ★ 2026-09-24 定版拍板：「发文件给哈文」——文件内容直接进对话发给当前模型（本地/外脑都行），
//   不落知识库（知识库上传只留知识库页面自己的通道）。
//   图片文件 → 走现有看图链路；文本/pdf/docx → 后端解析成文字填进输入框，确认后发送。
const fileEl = ref(null)
const uploading = ref(false)
function pickFile() {
  menuOpen.value = false
  fileEl.value?.click()
}
async function onFilePicked(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return

  // 图片 → 看图链路（与粘贴/拖拽同一条待发预览）
  const imgs = files.filter(f => f.type && f.type.startsWith('image/'))
  const docs = files.filter(f => !(f.type && f.type.startsWith('image/')))
  if (imgs.length) addImageFiles(imgs)
  if (!docs.length) return

  uploading.value = true
  const parts = []
  let failed = 0
  for (const f of docs) {
    chat.thinking = `正在读「${f.name}」…`
    try {
      const fd = new FormData()
      fd.append('file', f)
      const res = await fetch('/api/chat/read_file', { method: 'POST', body: fd })
      const d = await res.json()
      if (d.ok) {
        parts.push(`【文件：${f.name}${d.truncated ? '，内容过长已截断' : ''}】\n${d.text}`)
      } else {
        failed++
        parts.push(`【文件：${f.name}——读取失败：${d.message || '未知原因'}】`)
      }
    } catch (err) {
      failed++
      parts.push(`【文件：${f.name}——读取失败：${err.message}】`)
    }
  }
  chat.thinking = ''
  if (parts.length) {
    const block = parts.join('\n\n')
    draft.value = draft.value ? draft.value + '\n\n' + block : block
    autoGrow()
    // 给个轻提示（不弹窗，用来源小字位置）
    chat.thinking = failed ? `⚠️ 已读入 ${parts.length - failed} 个文件，${failed} 个失败。检查内容后点发送` : `✅ 已读入 ${parts.length} 个文件内容，确认后点发送`
    setTimeout(() => { if (chat.thinking.startsWith('✅') || chat.thinking.startsWith('⚠️')) chat.thinking = '' }, 4000)
  }
  uploading.value = false
}

watch(() => chat.messages.length, async () => {
  await nextTick()
  if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
})
watch(() => chat.messages[chat.messages.length - 1]?.content, async () => {
  await nextTick()
  if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
})
</script>

<style scoped>
.home-page { height: 100%; display: flex; flex-direction: column; }
.identity-bar {
  display: flex; align-items: center; gap: 14px;
  padding: 8px 16px; background: var(--c-card);
  border-bottom: 1px solid var(--c-line); flex-shrink: 0;
}
.dock-toggle { border: none; background: none; cursor: pointer; color: var(--c-text-2); padding: 4px; }
.identity-name { flex: 1; font-weight: 800; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* 三段模式胶囊组 ★ 2026-09-13：左移 + 加大 + 选中实色（用户反馈：太靠右/太小/颜色浅看不出选中） */
.mode-group {
  display: flex; border: 1.5px solid var(--c-line); border-radius: 999px;
  overflow: hidden; flex-shrink: 0; background: var(--c-bg);
}
.mg-seg {
  border: none; background: none; cursor: pointer; padding: 0 18px;
  height: 34px; font-size: 13.5px; font-weight: 800; color: var(--c-text-2);
  transition: background .15s, color .15s; white-space: nowrap;
}
.mg-seg + .mg-seg { border-left: 1.5px solid var(--c-line); }
.mg-seg:hover { background: var(--c-bg-soft); }
/* 选中 = 实色填充白字，一眼可辨 */
.mg-seg.on.mg-local { background: var(--mode-local); color: #fff; }
.mg-seg.on.mg-cloud { background: var(--mode-cloud); color: #fff; }
.mg-seg.mg-collab { color: #B08A4F; opacity: .72; }
.mg-seg.mg-collab:hover { opacity: 1; }
.mg-seg.on.mg-collab { background: #B08A4F; color: #fff; opacity: 1; } /* ★ C批：协作选中态（琥珀实色） */
.mg-seg.mg-vert { color: #B05FC0; opacity: .75; } /* ★ 反馈⑩：垂直入口（紫粉，点击跳直答页） */
.mg-seg.mg-vert:hover { opacity: 1; background: rgba(176,95,192,.1); }
/* 带字状态签 ★ 同步统一：联网绿/安全琥珀与模式色板同族，描边加强 */
.status-tags { display: flex; gap: 8px; flex-shrink: 0; }
.st-tag {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12.5px; font-weight: 700; padding: 5px 12px; border-radius: 999px;
  border: 1.5px solid transparent; white-space: nowrap;
}
.st-tag.net-ok { color: #14713F; background: #EAF7F1; border-color: #BFE5D4; }
.st-tag.net-bad { color: #B42323; background: #FDECEC; border-color: #F3C1C1; }
.st-tag.sec-ok { color: #14713F; background: #EAF7F1; border-color: #BFE5D4; }
.st-tag.sec-warn { color: #92500A; background: #FDF3E3; border-color: #EFD9AC; }
/* ★ #3 联网开关：可点 + 开启态高亮 */
.st-tag.st-click { cursor: pointer; transition: transform .12s, box-shadow .12s; }
.st-tag.st-click:hover { transform: translateY(-1px); }
.st-tag.net-on { color: #fff; background: #1E8A54; border-color: #1E8A54; box-shadow: 0 1px 6px rgba(30,138,84,.35); }
.st-dot.pulse { animation: stpulse 1.6s ease-in-out infinite; }
@keyframes stpulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
.st-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.st-dot.g { background: #22A55E; }
.st-dot.r { background: #E5484D; }
.st-dot.a { background: #F59E0B; }
.home-body { flex: 1; display: flex; overflow: hidden; }

/* ═══ ★ 2026-09-18 #748 今天要处理横条 ═══ */
.todo-bar {
  flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  padding: 7px 16px; background: #FFF9EE; border-bottom: 1px solid #F0E2C4;
  overflow-x: auto; scrollbar-width: none;
}
.todo-bar::-webkit-scrollbar { display: none; }
.tb-title { flex-shrink: 0; font-size: 12.5px; font-weight: 800; color: #92500A; white-space: nowrap; }
.tb-n {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; border-radius: 9px; padding: 0 5px;
  background: #E8853D; color: #fff; font-size: 11px;
}
.tb-items { display: flex; gap: 8px; align-items: center; }
.tb-item {
  flex-shrink: 0; display: flex; align-items: center; gap: 6px;
  background: #fff; border: 1px solid #F0E2C4; border-radius: 999px;
  padding: 4px 6px 4px 12px; font-size: 12.5px; max-width: 520px;
}
.tb-txt { color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 340px; }
.tb-time { font-style: normal; color: var(--c-text-2); margin-left: 6px; font-size: 11px; }
.tb-btn {
  flex-shrink: 0; border: 1px solid var(--c-line); background: var(--c-bg, #f6f7f9);
  border-radius: 999px; font-size: 11.5px; padding: 3px 10px; cursor: pointer; color: var(--c-text-2);
}
.tb-btn:hover { color: var(--c-primary); border-color: var(--c-primary); }
.tb-btn.tb-go { background: #E8853D; border-color: #E8853D; color: #fff; font-weight: 700; }
.tb-btn.tb-go:hover { background: #d4742e; color: #fff; }
.tb-btn.tb-del:hover { color: #D9534F; border-color: #D9534F; }
@media (max-width: 900px) { .todo-bar { padding: 6px 10px; } .tb-txt { max-width: 180px; } }
/* ★ 2026-09-19 易用性③：提醒空态引导条 */
.todo-empty { background: #F7FAFF; border-bottom: 1px solid #E3ECF9; }
.todo-empty .te-txt { flex: 1; font-size: 12px; color: var(--c-text-2); line-height: 1.6; min-width: 0; }
.todo-empty .tb-title { color: #3E6DB5; }

.chat-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.msg-flow { flex: 1; overflow-y: auto; padding: 20px 24px; }
.msg-row { display: flex; margin-bottom: 14px; }
.msg-row.user { justify-content: flex-end; }
.bubble { max-width: 72%; border-radius: 14px; padding: 10px 14px; }
.b-user { background: var(--c-primary); color: #fff; border-bottom-right-radius: 4px; }
.b-assistant { background: var(--c-card); border: 1px solid var(--c-line); border-bottom-left-radius: 4px; }
.reply-by { margin-bottom: 6px; }
.bubble-text { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 14px; }
/* ★ 2026-09-21 #830：工具调用过程折叠卡 */
.tool-log-card {
  margin: 0 0 8px; border: 1px solid var(--c-line); border-radius: 10px;
  background: var(--c-card); font-size: 13px; overflow: hidden;
}
.tool-log-card summary {
  cursor: pointer; padding: 6px 10px; color: var(--c-text-2);
  user-select: none; font-size: 12px;
}
.tool-log-card summary:hover { color: var(--c-text); }
.tool-log-card[open] summary { border-bottom: 1px dashed var(--c-line); }
.tool-log-body {
  margin: 0; padding: 8px 10px; max-height: 220px; overflow: auto;
  white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  color: var(--c-text-2);
}
.chat-welcome { text-align: center; margin-top: 12vh; color: var(--c-text-2); }
.chat-welcome h2 { margin: 14px 0 4px; color: var(--c-text); }
.chat-welcome p { margin: 0 0 16px; }
.welcome-tips { display: flex; justify-content: center; gap: 10px; }
/* ★ 2026-09-19 易用性①：直接跟我说引导卡 */
.say-card {
  display: inline-flex; flex-direction: column; align-items: center; gap: 10px;
  border: 1px dashed var(--c-line); background: var(--c-card);
  border-radius: 14px; padding: 14px 20px; margin: 0 auto 14px; max-width: 520px;
}
.say-card > b { font-size: 13px; color: var(--c-text); }
.say-chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.say-chip {
  border: 1px solid var(--c-line); background: #fff; border-radius: 999px;
  font-size: 12.5px; padding: 6px 14px; cursor: pointer; color: var(--c-text);
  transition: border-color .15s, transform .12s;
}
.say-chip:hover { border-color: var(--c-primary); color: var(--c-primary); transform: translateY(-1px); }
.say-tip { font-size: 11.5px; color: var(--c-text-2); line-height: 1.7; max-width: 440px; text-align: left; }

.input-bar { flex-shrink: 0; padding: 10px 24px 14px; position: relative; }
.thinking-line {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--c-text-2); margin-bottom: 6px;
}
.plus-menu {
  position: absolute; bottom: 78px; left: 36px;
  background: var(--c-card); border: 1px solid var(--c-line);
  border-radius: 12px; box-shadow: var(--shadow-card);
  padding: 6px; display: flex; flex-direction: column; z-index: 5;
}
.plus-menu button {
  display: flex; align-items: center; gap: 8px;
  border: none; background: none; cursor: pointer;
  padding: 8px 14px; border-radius: 8px; font-size: 13px; color: var(--c-text);
}
.plus-menu button:hover { background: var(--c-bg-soft); }
.input-row {
  display: flex; align-items: center; gap: 8px;
  background: var(--c-card); border: 1px solid var(--c-line);
  border-radius: 16px; padding: 8px 10px; box-shadow: var(--shadow-card);
}
/* ★ 2026-09-14：输入框上方「当前回答方」小字 */
.src-note { display: flex; justify-content: flex-end; padding: 0 4px 6px; opacity: .85; }

/* ★ 2026-09-15 A方案：图片消息与待发预览条 */
.bubble-imgs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 4px; }
/* ★ #926 聊天内跳转按钮 */
.bubble-jumps { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.jump-btn { border: none; border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer;
  background: #2f6f4f; color: #fff; transition: filter .15s; }
.jump-btn:hover { filter: brightness(1.12); }
/* ★ 2026-09-27 助手回答内嵌图 + 大图查看器 */
.bubble-img-view { cursor: zoom-in; }
.img-viewer-overlay { position: fixed; inset: 0; z-index: 9999; background: rgba(0,0,0,.78); display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: zoom-out; }
.img-viewer-overlay img { max-width: 92vw; max-height: 84vh; border-radius: 8px; box-shadow: 0 8px 40px rgba(0,0,0,.5); }
.img-viewer-tip { color: rgba(255,255,255,.75); font-size: 12px; margin-top: 10px; }
.bubble-img {
  max-width: 180px; max-height: 140px; object-fit: cover;
  border-radius: 10px; border: 1px solid var(--c-line); display: block;
}
/* ★ #890（2026-09-26）：发图存入面板按钮（hover 图片时浮现） */
.bubble-img-wrap { position: relative; display: inline-block; }
.img-save-btn {
  position: absolute; left: 6px; bottom: 6px; padding: 3px 8px;
  border: none; border-radius: 999px; font-size: 11px; cursor: pointer;
  background: rgba(0,0,0,.62); color: #fff; opacity: 0; transition: opacity .15s;
  white-space: nowrap;
}
.bubble-img-wrap:hover .img-save-btn { opacity: 1; }
.img-save-btn:hover { background: rgba(0,0,0,.82); }
.img-chips { display: flex; gap: 8px; flex-wrap: wrap; padding: 0 4px 6px; }
.img-chip {
  position: relative; width: 64px; height: 64px; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--c-line); background: #fff; flex-shrink: 0;
}
.img-chip img { width: 100%; height: 100%; object-fit: cover; display: block; }
.img-chip-x {
  position: absolute; top: 2px; right: 2px; width: 18px; height: 18px;
  border: none; border-radius: 50%; background: rgba(0,0,0,.55); color: #fff;
  font-size: 10px; line-height: 18px; padding: 0; cursor: pointer; text-align: center;
}
.img-chip-x:hover { background: rgba(0,0,0,.8); }

/* ★ 2026-09-14 方案A：协作选项面板 */
.collab-mask { position: fixed; inset: 0; background: rgba(20, 24, 34, .42); z-index: 95; display: flex; align-items: center; justify-content: center; }
.collab-box { width: 460px; max-width: calc(100vw - 40px); max-height: 84vh; overflow-y: auto; background: #fff; border-radius: 16px; padding: 18px 20px; box-shadow: 0 18px 50px rgba(0,0,0,.18); }
.cb-head { display: flex; justify-content: space-between; align-items: center; }
.cb-head b { font-size: 15px; }
.cb-close { border: 1px solid var(--line); background: #fff; border-radius: 8px; font-size: 12px; padding: 4px 11px; cursor: pointer; color: var(--ink); }
.cb-tip { font-size: 12px; color: var(--sub); line-height: 1.7; margin: 8px 0 4px; }
.cb-sec { margin-top: 13px; }
.cb-label { display: block; font-size: 12.5px; font-weight: 700; color: var(--c-text); margin-bottom: 7px; }
.cb-modes { display: flex; flex-direction: column; gap: 7px; }
.cb-mode { text-align: left; border: 1px solid var(--line); border-radius: 11px; background: #fff; padding: 9px 12px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.cb-mode b { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.cb-mode b small { font-size: 10px; font-weight: 800; border-radius: 6px; padding: 0 6px; background: var(--mode-local-bg); color: var(--mode-local); }
.cb-mode span { font-size: 11.5px; color: var(--sub); }
.cb-mode.on { border-color: #B08A4F; background: #FDF9F2; }
.cb-select { width: 100%; border: 1px solid var(--line); border-radius: 10px; padding: 8px 11px; font-size: 13px; background: #fff; }
.cb-select:focus { outline: none; border-color: var(--blue); }
.cb-warn { font-size: 11.5px; color: var(--red); margin: 6px 0 0; }
/* ★ V1 流水线编排器 */
.cb-presets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 9px; }
.cb-preset { border: 1px solid var(--line); background: #fff; border-radius: 999px; font-size: 11.5px; padding: 4px 11px; cursor: pointer; color: var(--ink); }
.cb-preset:hover { border-color: var(--blue); color: var(--blue); }
.cb-steps { display: flex; flex-direction: column; gap: 7px; }
.cb-step { display: flex; align-items: center; gap: 7px; }
.cb-step-no { flex-shrink: 0; width: 20px; height: 20px; border-radius: 50%; background: var(--blue); color: #fff; font-size: 11.5px; display: inline-flex; align-items: center; justify-content: center; }
.cb-step-kind { flex-shrink: 0; font-size: 14px; }
/* ★ 2026-09-16：站类型切换下拉 + 上移/下移/删除操作组 */
.cb-kind { flex-shrink: 0; width: 84px; font-size: 12px; padding: 3px 4px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.cb-step-ops { flex-shrink: 0; display: inline-flex; align-items: center; gap: 2px; }
.cb-op { border: none; background: none; cursor: pointer; font-size: 12.5px; color: var(--ink-2, #555); padding: 3px 5px; border-radius: 6px; line-height: 1; }
.cb-op:hover:not(:disabled) { background: var(--c-bg-soft, #f2f4f7); color: var(--blue); }
.cb-op:disabled { opacity: .3; cursor: default; }
.cb-op-del { color: var(--red); }
.cb-step .cb-select { flex: 1; min-width: 0; }
.cb-step-del { flex-shrink: 0; border: none; background: none; color: var(--red); cursor: pointer; font-size: 13px; padding: 4px; }
.cb-step-final { flex-shrink: 0; font-size: 10.5px; color: var(--blue); background: rgba(91,141,239,.1); border-radius: 999px; padding: 2px 8px; }
.cb-step-add { display: flex; gap: 7px; margin-top: 8px; }
.cb-addbtn { flex: 1; border: 1px dashed var(--line); background: none; border-radius: 9px; font-size: 12px; padding: 6px; cursor: pointer; color: var(--ink-2, #555); }
.cb-addbtn:hover { border-color: var(--blue); color: var(--blue); }
.cb-hint { font-size: 11px; color: var(--ink-3, #8b949e); margin: 8px 0 0; line-height: 1.6; }
.cb-slash-tip { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; padding: 10px 12px; border: 1px dashed var(--c-line, #d8dce3); border-radius: 10px; background: var(--c-bg-soft, #f7f8fa); font-size: 12px; line-height: 1.6; }
.cb-slash-tip b { font-size: 12px; }
.cb-slash-tip code { background: #eef1f5; border-radius: 4px; padding: 1px 5px; font-weight: 600; color: #185FA5; }
.cb-slash-eg { color: var(--c-text, #24292f); background: #fff; border: 1px solid var(--c-line, #d8dce3); border-radius: 8px; padding: 6px 8px; }
.cb-slash-sub { color: var(--ink-3, #8b949e); font-size: 11px; }
.cb-privs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.cb-priv { border: 1px solid var(--line); border-radius: 11px; background: #fff; padding: 8px 9px; cursor: pointer; text-align: left; display: flex; flex-direction: column; gap: 2px; }
.cb-priv b { font-size: 12.5px; }
.cb-priv span { font-size: 10.5px; color: var(--sub); line-height: 1.5; }
.cb-priv.on { border-color: var(--blue); background: #F5F9FF; }
.cb-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; margin-top: 15px; }
/* ★ 2026-09-16 用户反馈#1：自动保存状态 + 大保存按钮 + 生效说明 */
.cb-autosave { font-size: 11.5px; color: #1a9a5c; }
.cb-savebig { font-size: 14.5px; }
.cb-save-tip { width: 100%; text-align: right; font-size: 11px; color: var(--ink-3, #8b949e); margin: 0; line-height: 1.5; }

@media (max-width: 700px) { .cb-privs { grid-template-columns: 1fr; } }

.plus-btn, .send-btn {
  border: none; background: none; cursor: pointer; color: var(--c-text-2);
  padding: 6px; border-radius: 10px; flex-shrink: 0;
  /* ★ 2026-09-15 用户反馈：＋/输入框/发送对齐——input-row 改 align-items:center 后
     按钮随行垂直居中，删掉旧的 margin-bottom 魔法数字（单行时三者精确一条线，
     多行时按钮居中在整框中部，与微信输入框一致） */
}
.plus-btn:hover { background: var(--c-bg-soft); color: var(--c-text); }
/* ★ #848 按住说话：话筒按钮 + 录音状态条 */
.mic-btn { touch-action: none; -webkit-user-select: none; user-select: none; }
.mic-btn.rec { background: #FDEBEC; color: #D64545; animation: mic-pulse 1.1s ease-in-out infinite; }
@keyframes mic-pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.12); } }
/* ★ #929-A3 微信式按住说话面板（替代旧 rec-bar） */
.rec-overlay {
  display: flex; align-items: center; gap: 10px; padding: 10px 14px; margin-bottom: 6px;
  border: 1px solid #F2C6C6; background: #FFF7F7; border-radius: 12px; font-size: 13.5px;
  touch-action: none; -webkit-user-select: none; user-select: none;
}
.rec-overlay .ro-msg { flex: 1; min-width: 0; color: #B23434; font-weight: 700; }
.rec-overlay .ro-msg.cancel { color: #D64545; }
.rec-overlay .ro-hint { flex-shrink: 0; font-size: 11.5px; color: var(--ink-3, #8b949e); font-weight: 400; }
.rec-overlay .ro-msg.bad { font-weight: 400; }
.rec-dot { width: 10px; height: 10px; border-radius: 50%; background: #D64545; flex-shrink: 0; animation: rec-blink 1s step-end infinite; }
.rec-dot.busy { background: #E8A23D; animation: none; }
@keyframes rec-blink { 50% { opacity: .25; } }
/* 波纹（录音中跳动的声浪条；上滑取消时变灰） */
.rec-waves { display: inline-flex; align-items: center; gap: 3px; height: 20px; flex-shrink: 0; }
.rec-waves i { width: 3.5px; border-radius: 2px; background: #D64545; animation: wave-jump .9s ease-in-out infinite; }
.rec-waves i:nth-child(1) { height: 8px; animation-delay: 0s; }
.rec-waves i:nth-child(2) { height: 14px; animation-delay: .12s; }
.rec-waves i:nth-child(3) { height: 20px; animation-delay: .24s; }
.rec-waves i:nth-child(4) { height: 14px; animation-delay: .36s; }
.rec-waves i:nth-child(5) { height: 8px; animation-delay: .48s; }
.rec-waves.cancel i { background: #c9ccd2; animation-play-state: paused; }
@keyframes wave-jump { 0%, 100% { transform: scaleY(.45); } 50% { transform: scaleY(1); } }
.rec-btn { border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 5px 14px; font-size: 13px; cursor: pointer; flex-shrink: 0; }
.rec-btn.ok { border-color: var(--c-primary, #4472E3); color: var(--c-primary, #4472E3); font-weight: 600; }
.rec-btn.no { color: var(--c-text-2); }
/* ★ #929-A4 语音档案存档按钮 */
.voice-save-btn {
  align-self: flex-start; border: 1px solid #F2D9A8; background: #FDF6E7; color: #8A6116;
  border-radius: 999px; padding: 4px 13px; font-size: 12.5px; cursor: pointer; margin-bottom: 6px;
}
.voice-save-btn:hover { background: #FAEBCB; }
.send-btn { color: var(--c-primary); }
.send-btn:disabled { opacity: .4; cursor: not-allowed; }
/* ★ 2026-10-10 #1053: 停止按钮——生成中替代发送键，红色醒目 */
.stop-btn { color: #e5484d; }
.stop-btn:disabled { opacity: .6; cursor: wait; }
textarea {
  flex: 1; border: none; outline: none; resize: none;
  font: inherit; color: var(--c-text); background: none;
  max-height: 140px; line-height: 1.5;
  padding: 0; /* ★ 2026-09-15：归零浏览器默认内边距，单行时与＋/发送按钮精确同线 */
}

/* ★ 2026-09-16 输入框拖拽把手：输入栏顶部一条小横杠，上下拖调高度，双击恢复 */
.input-resizer { height: 12px; display: flex; align-items: center; justify-content: center;
  cursor: ns-resize; touch-action: none; margin-bottom: 2px; }
.input-resizer span { width: 44px; height: 4px; border-radius: 2px;
  background: var(--line, #d8dee6); transition: background .15s ease, width .15s ease; }
.input-resizer:hover span, .input-resizer:active span { background: var(--blue, #4a8fe7); width: 64px; }

/* ═══ F批手机端：<900px 左栏抽屉化 + 顶栏压缩 + 触控优化 ═══ */
/* 汉堡按钮：桌面隐藏 */
.rail-toggle { display: none; }
.rail-toggle .burger {
  display: block; width: 18px; height: 2px; background: currentColor;
  margin: 2.5px auto; border-radius: 2px;
}
/* 遮罩：桌面永不显示 */
.rail-mask { display: none; }

@media (max-width: 900px) {
  /* 顶栏：汉堡登场，格子按钮/会话名/安全签退场（联网签保留可点） */
  .rail-toggle { display: block; border: none; background: none; cursor: pointer; color: var(--c-text); padding: 6px 8px; border-radius: 8px; }
  .rail-toggle:active { background: var(--c-bg-soft); }
  .dock-toggle { display: none; }
  .identity-bar { padding: 8px 10px; gap: 8px; flex-wrap: wrap; }
  .identity-name { display: none; }
  .status-tags .st-tag:nth-child(2) { display: none; } /* 安全签收起（切换在设置页） */
  /* ★ D2 2026-09-23 手机适配：模式条装不下时横向滑动，不再挤压/溢出 */
  .mode-group { overflow-x: auto; max-width: 100%; scrollbar-width: none; -webkit-overflow-scrolling: touch; }
  .mode-group::-webkit-scrollbar { display: none; }
  .mg-seg { padding: 0 11px; height: 32px; font-size: 12.5px; }

  /* 左栏 → 抽屉 */
  .home-body { position: relative; }
  .rail-mask.show {
    display: block; position: absolute; inset: 0; z-index: 30;
    background: rgba(20, 25, 35, .38); opacity: 1;
  }
  :deep(.left-rail) {
    position: absolute; left: 0; top: 0; bottom: 0; z-index: 31;
    transform: translateX(-105%); transition: transform .22s ease;
    box-shadow: 4px 0 24px rgba(0, 0, 0, .14); width: 264px;
  }
  :deep(.left-rail.rail-open) { transform: translateX(0); }

  /* 聊天区：气泡放宽、留出手机底部安全区（iPhone 小横条） */
  .msg-flow { padding: 12px 12px 8px; }
  .bubble { max-width: 86%; }
  .chat-welcome { margin-top: 8vh; }
  .input-bar { padding: 8px 12px calc(10px + env(safe-area-inset-bottom)); }
}

/* 超窄屏（<380px）：模式组允许横向滑动不挤爆 */
@media (max-width: 380px) {
  .mode-group { overflow-x: auto; max-width: calc(100vw - 60px); scrollbar-width: none; }
  .mode-group::-webkit-scrollbar { display: none; }
}

/* ★ 2026-10-20 目标进度横条 */
.goal-strip { padding: 6px 14px 0; }
/* ★ 2026-10-20 即将到来 */
.up-strip { display: flex; align-items: center; gap: 10px; padding: 6px 14px 0; overflow: hidden; }
.up-label { font-size: 12px; color: var(--sub, #888); flex: none; }
.up-items { display: flex; gap: 10px; overflow-x: auto; }
.up-item { flex: none; font-size: 12px; padding: 3px 10px; border-radius: 12px; background: var(--c-bg-soft, #f5f6f8); color: var(--ink, #333); cursor: default; max-width: 260px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.up-d { font-style: normal; color: var(--blue, #4a7dff); margin-right: 4px; }
.gs-toggle { border: 1px solid var(--line, #e5e5e5); border-radius: 16px; padding: 4px 12px; font-size: 12px; background: var(--bg2, #fff); cursor: pointer; color: var(--ink, #333); }
.gs-cards { display: flex; gap: 8px; margin-top: 8px; overflow-x: auto; }
.gs-card { flex: none; min-width: 200px; max-width: 260px; border: 1px solid var(--line, #e5e5e5); border-radius: 12px; padding: 8px 12px; cursor: pointer; background: var(--bg2, #fff); display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; align-items: center; }
.gs-light { font-size: 14px; }
.gs-name { font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.gs-bar { grid-column: 1 / -1; height: 5px; border-radius: 3px; background: var(--line, #eee); overflow: hidden; }
.gs-bar i { display: block; height: 100%; background: #22a06b; border-radius: 3px; }
.gs-meta { grid-column: 1 / -1; font-size: 11px; color: var(--tx2, #999); }
</style>
