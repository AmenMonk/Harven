<template>
  <PageShell :title="t('page.settings.t')" icon="gear" :sub="t('page.settings.s')">
    <template #actions>
      <BigButton variant="ghost" :px="14" @click="loadAll(true)"><HIcon name="dots" :size="16" /> 刷新</BigButton>
    </template>

    <div v-if="msg" class="tip" :class="msgOk ? 'ok' : 'bad'">{{ msg }}</div>

    <!-- 锚点导航 -->
    <div class="anchors">
      <button v-for="s in SECTIONS" :key="s.id" :class="{ on: anchor === s.id }" @click="anchor = s.id">
        <HIcon :name="s.icon" :size="15" /> {{ s.name }}
      </button>
    </div>

    <!-- 模型 -->
    <section v-show="anchor === 'models'" class="sec-body">
      <div class="row">
        <div class="row-main"><b>{{ t('set.modelsCenter') }}</b><span>{{ t('set.modelsCenterD') }}</span></div>
        <a href="#/models"><BigButton :px="13" variant="ghost">{{ t('set.go') }}</BigButton></a>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('set.installModel') }}</b><span>{{ t('set.installModelD') }}</span></div>
        <a href="#/models/install"><BigButton :px="13" variant="ghost">{{ t('set.go') }}</BigButton></a>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('set.currentForeign') }}</b><span>{{ currentForeign ? currentForeign : t('set.noForeign') }}</span></div>
        <span class="pill" :class="currentForeign ? 'ok' : ''">{{ currentForeign ? t('set.configured') : t('set.notConfigured') }}</span>
      </div>
    </section>

    <!-- ★ 2026-10-23 设置页瘦身：语音识别并入「模型」页底部（ModelsPage），本标签撤除 -->

    <!-- 安全隐私 -->
    <section v-show="anchor === 'security'" class="sec-body">
      <div class="mode-cards">
        <div class="mcard" :class="{ on: mode === 'safe' }">
          <b>{{ t('set.safeMode') }}</b>
          <p>{{ t('set.safeModeD') }}</p>
          <BigButton v-if="mode === 'full'" variant="ghost" :px="13" :loading="busy" @click="setSafe">{{ t('set.backSafe') }}</BigButton>
          <span v-else class="cur-mark">{{ t('set.curMode') }}</span>
        </div>
        <div class="mcard" :class="{ on: mode === 'full' }">
          <b>{{ t('set.fullMode') }}</b>
          <p>{{ t('set.fullModeD') }}</p>
          <template v-if="mode !== 'full'">
            <input class="key-input" type="password" v-model.trim="pwd" :placeholder="t('set.pwdPlaceholder')" @keyup.enter="setFull" />
            <BigButton :px="13" :loading="busy" @click="setFull">{{ t('set.enableFull') }}</BigButton>
          </template>
          <span v-else class="cur-mark warn">{{ t('set.curModeFull') }}</span>
        </div>
      </div>
      <div class="row"><div class="row-main"><b>{{ t('set.dangerCmds') }}</b><span>{{ secInfo.dangerous_commands?.join('、') || '—' }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('set.allowedPaths') }}</b><span>{{ (secInfo.allowed_paths || []).join('　') || '—' }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('set.forbiddenPaths') }}</b><span>{{ (secInfo.forbidden_paths || []).join('　') || '—' }}</span></div></div>

      <!-- ★ 2026-09-17 密码补齐：管理员改密（同时是保险箱/面板解锁密码） -->
      <div class="row pwd-row">
        <div class="row-main">
          <b>{{ t('set.changePwd') }}</b>
          <span>{{ t('set.changePwdD') }}</span>
          <div class="pwd-fields">
            <input class="key-input" type="password" v-model.trim="oldPwd" :placeholder="t('set.oldPwd')" autocomplete="current-password" />
            <input class="key-input" type="password" v-model.trim="newPwd" :placeholder="t('set.newPwd')" autocomplete="new-password" />
            <input class="key-input" type="password" v-model.trim="newPwd2" :placeholder="t('set.newPwd2')" autocomplete="new-password" @keyup.enter="changePwd" />
          </div>
          <div class="pwd-hint">💡 {{ t('set.oldPwdHint') }}</div>
        </div>
        <BigButton :px="13" :loading="pwdBusy" @click="changePwd">{{ t('set.doChange') }}</BigButton>
      </div>
      <div v-if="pwdMsg" class="tip" :class="pwdOk ? 'ok' : 'bad'">{{ pwdMsg }}</div>

      <!-- ★ #1362（2026-11-02）密码恢复：安全问题 + 恢复盘 -->
      <div class="row pwd-row">
        <div class="row-main">
          <b>密码找回（强烈建议设置）</b>
          <span v-if="recStatus.set">已设置安全问题（{{ recStatus.questions[0] }} / {{ recStatus.questions[1] }}），忘记密码时可在登录页自救</span>
          <span v-else>还没设置。设置后忘记密码时，在登录页答对两个问题或用恢复码就能重置，不用翻配置文件</span>
          <template v-if="recEditing">
            <div class="pwd-fields">
              <select class="key-input" v-model="recQ1">
                <option value="">— 选第一个问题 —</option>
                <option v-for="q in recBank" :key="q" :value="q">{{ q }}</option>
                <option value="__custom">自定义问题…</option>
              </select>
              <input v-if="recQ1 === '__custom'" class="key-input" v-model.trim="recQ1c" placeholder="写一个只有你知道的问题" />
              <input class="key-input" v-model.trim="recA1" type="text" placeholder="答案 1（不用精确，自己记得住就行）" />
              <select class="key-input" v-model="recQ2">
                <option value="">— 选第二个问题（不能和第一个一样）—</option>
                <option v-for="q in recBank" :key="q" :value="q">{{ q }}</option>
                <option value="__custom">自定义问题…</option>
              </select>
              <input v-if="recQ2 === '__custom'" class="key-input" v-model.trim="recQ2c" placeholder="写一个只有你知道的问题" />
              <input class="key-input" v-model.trim="recA2" type="text" placeholder="答案 2" />
              <input class="key-input" type="password" v-model.trim="recPwd" placeholder="当前管理密码（验证是你本人）" />
            </div>
          </template>
          <div v-if="recCode" class="pwd-hint">✅ 已生成恢复码：<code>{{ recCode }}</code>　<a href="#" @click.prevent="downloadRecDisk">下载恢复盘（txt）</a></div>
        </div>
        <template v-if="recEditing">
          <BigButton :px="13" :loading="recBusy" @click="saveRecovery">保存</BigButton>
        </template>
        <template v-else>
          <BigButton variant="ghost" :px="13" @click="startRecEdit">{{ recStatus.set ? '查看/修改' : '去设置' }}</BigButton>
        </template>
      </div>
      <div v-if="recMsg" class="tip" :class="recOk ? 'ok' : 'bad'">{{ recMsg }}</div>
    </section>

    <!-- ★ 2026-10-20 权限中心（第二批2.1）：四类权限三选 + 联网三层开关 -->
    <section v-show="anchor === 'perm'" class="sec-body">
      <div class="perm-hint">哈文替你做事前，先按这里的规矩来。拿不准的保持默认就好。</div>
      <div v-for="p in permData.perms" :key="p.key" class="row perm-row">
        <div class="row-main">
          <b>{{ p.label }}</b>
          <span>{{ p.desc }}</span>
        </div>
        <select class="perm-select" :value="p.value" :disabled="p.key === 'perm_readonly' && false" @change="setPerm(p.key, $event.target.value)">
          <option v-for="c in p.choices" :key="c.value" :value="c.value">{{ c.label }}</option>
        </select>
      </div>
      <h3 class="about-sec" style="margin-top:18px">🌐 联网（总闸-分闸，上级关下级不可用）</h3>
      <div v-for="n in permData.net" :key="n.key" class="row perm-row">
        <div class="row-main">
          <b>{{ n.label }}</b>
          <span>{{ n.desc }}</span>
        </div>
        <label class="perm-switch" :class="{ off: !n.enabled }">
          <input type="checkbox" :checked="n.value" :disabled="!n.enabled" @change="setPerm(n.key, $event.target.checked)" />
          <span class="slider"></span>
        </label>
      </div>
      <div v-if="permMsg" class="tip">{{ permMsg }}</div>
    </section>

    <!-- 通用 -->
    <section v-show="anchor === 'general'" class="sec-body">
      <!-- ★ 2026-10-23 页内分两小节（标签不拆）：偏好 / 自动化 -->
      <h3 class="gs-title">⚙️ 偏好</h3>
      <!-- ★ 2026-09-17 #738 界面语言切换 -->
      <div class="row">
        <div class="row-main"><b>{{ t('set.langTitle') }}</b><span>{{ t('set.langD') }}</span></div>
        <div class="lang-switch">
          <button :class="{ on: lang === 'zh' }" @click="switchLang('zh')">{{ t('set.langZh') }}</button>
          <button :class="{ on: lang === 'en' }" @click="switchLang('en')">{{ t('set.langEn') }}</button>
        </div>
      </div>
      <!-- ★ 2026-09-22 (#851) 实例名：多台电脑/多个哈文时区分谁是谁 -->
      <div class="row pwd-row">
        <div class="row-main"><b>🏷️ 实例名</b><span>给这台哈文起个名字（比如「哈文1号」「哈文2号」），左上角和登录页都会显示；家里两台电脑各跑一个哈文时，一眼分清谁是谁</span></div>
        <div class="pwd-fields">
          <input class="key-input" type="text" v-model.trim="instNameInput" placeholder="哈文" maxlength="20" @keyup.enter="saveInstName" />
          <BigButton :px="13" :loading="instNameBusy" @click="saveInstName">保存</BigButton>
        </div>
      </div>
      <div v-if="instNameMsg" class="tip" :class="instNameOk ? 'ok' : 'bad'">{{ instNameMsg }}</div>
      <div class="row">
        <div class="row-main"><b>🩺 运行状态</b><span>电脑资源、模型、Token 花销、定时提醒，一眼看清</span></div>
        <a href="#/status"><BigButton :px="13" variant="ghost">查看</BigButton></a>
      </div>

      <!-- ★ 2026-09-18 #751 哈文学习词典（叫法映射可视化）+ 对话即录入省token开关 -->
      <div class="row">
        <div class="row-main"><b>📖 哈文学习词典</b><span>确认卡上点过「📌 以后都这样记」的叫法都存在这里，命中就不再花 AI 的钱；鼠标悬芯片看命中次数，点 ✕ 删除</span></div>
        <BigButton :px="13" variant="ghost" @click="loadLexicon">刷新</BigButton>
      </div>
      <!-- ★ 2026-10-23 词典改分组+芯片流：按面板分组、一行多枚，词汇多了不再一条一行往下排 -->
      <div v-if="lexicon.length" class="lex-groups">
        <div v-for="grp in lexGroups" :key="grp.panel" class="lex-group">
          <div class="lex-g-head">{{ grp.name }} <i>{{ grp.items.length }}</i></div>
          <div class="lex-chips">
            <span v-for="it in grp.items" :key="it.id" class="lex-chip"
                  :title="`已命中 ${it.hits} 次 · ${(it.created_at || '').slice(0, 10)}`">
              「{{ it.pattern }}」→ {{ lexLabel(it) }}<button class="lex-x" @click="delLexicon(it)">✕</button>
            </span>
          </div>
        </div>
      </div>
      <div v-else class="row"><div class="row-main"><b>词典还是空的</b><span>在哈文助手的确认卡上点「📌 以后都这样记」，哈文就学会你的叫法（比如「背单词=学习」）</span></div></div>
      <div class="row">
        <div class="row-main"><b>记录抽取走外脑</b><span>开=抽取准（花一点 API 额度，两段式很省）；关=纯本地模型（免费、隐私最严）</span></div>
        <select class="lex-select" v-model="assist.extract_allow_cloud" @change="saveAssist">
          <option value="true">开启（推荐）</option>
          <option value="false">关闭</option>
        </select>
      </div>
      <div class="row">
        <div class="row-main"><b>档案问答走外脑</b><span>问答上下文大、耗 token 多，默认关（本地模型免费答）</span></div>
        <select class="lex-select" v-model="assist.answer_allow_cloud" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <!-- ★ #1155（2026-10-16 已定案）看图用谁：选谁给谁、不行如实报、不兜底不代选 -->
      <div class="row">
        <div class="row-main"><b>👁️ 看图用谁</b><span>拍照识菜/照片打标签等看图场景用哪个模型。跟随当前模式=你选外脑就外脑看、选本地就本地看；看不了会如实告诉你，哈文不会偷偷换模型（更不会擅自花你的 API 额度）</span></div>
        <select class="lex-select" v-model="assist.vision_routing" @change="saveAssist">
          <option value="auto">跟随当前模式（推荐）</option>
          <option value="local">固定用本地模型</option>
          <option value="cloud">固定用当前外脑</option>
        </select>
      </div>
      <h3 class="gs-title" style="margin-top:18px">🤖 自动化</h3>
      <!-- ★ 2026-09-27 定版拍板：定时汇总三件套各配开关，默认全关；跟随用户选的模型模式，隐私板块只建议本地不设限 -->
      <div class="row">
        <div class="row-main"><b>定时 · 梦境周总结</b><span>每周一早上自动把一周梦境放一起看情绪线索。梦境较隐私，<b>建议用本地模型</b>（只是建议，你选什么模式就跟着什么跑）；开了才跑</span></div>
        <select class="lex-select" v-model="assist.summary_dream_weekly" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <div class="row">
        <div class="row-main"><b>定时 · 记账月报</b><span>每月1号早上自动汇总上个月的花销和变化。跟着你当前选的模型跑（外脑分析更细致也行），开了才跑</span></div>
        <select class="lex-select" v-model="assist.summary_accounting_monthly" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <div class="row">
        <div class="row-main"><b>定时 · 孩子成长月报</b><span>每月1号早上自动汇总孩子上个月的成长变化。孩子记录较隐私，<b>建议用本地模型</b>（只是建议，你选什么模式就跟着什么跑）；开了才跑</span></div>
        <select class="lex-select" v-model="assist.summary_kid_monthly" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <!-- ★ 2026-10-05 #1011 冰箱临期每日提醒（第四件，默认关） -->
      <div class="row">
        <div class="row-main"><b>定时 · 冰箱临期提醒</b><span>每天早上扫一遍冰箱台账，快过期的列清单提醒你先吃，跟着你当前选的模型出建议（没有临期就不吵你）；开了才跑</span></div>
        <select class="lex-select" v-model="assist.summary_fridge_expiry" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <!-- ★ 2026-10-10 #1063 B1 饮食周报（第五件，默认关） -->
      <div class="row">
        <div class="row-main"><b>定时 · 饮食周报</b><span>每周一早上把一周吃饭记录放一起看：外卖花了多少、吃辣几次、给点饮食建议。本地先算好数字，模型只负责说人话，跟着你当前选的模型跑；开了才跑</span></div>
        <select class="lex-select" v-model="assist.summary_meal_weekly" @change="saveAssist">
          <option value="false">关闭（默认）</option>
          <option value="true">开启</option>
        </select>
      </div>
      <div v-if="assistMsg" class="tip" :class="assistOk ? 'ok' : 'bad'">{{ assistMsg }}</div>
      <!-- ★ #1379（2026-11-02）误识别黑名单：手机对话说「以后别识别 X」进来的词，这里查看/移除 -->
      <div class="row" style="flex-direction: column; align-items: stretch">
        <div class="row-main"><b>🚫 误识别黑名单</b><span>
          拍照记账时如果哈文把聊天误当成账单，你在对话里回「以后别识别 XX」就能把词拉黑，以后不再自动识别；
          说「别记」只作废当次，词照常识别。这里可查看和恢复：</span></div>
        <div style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px">
          <span v-for="w in blocklist" :key="w"
                style="display:inline-flex;align-items:center;gap:4px;background:var(--bg-soft,#f3f4f6);border-radius:12px;padding:3px 10px;font-size:13px">
            {{ w }}<a style="cursor:pointer;color:#e05252;font-weight:bold" @click="delBlockWord(w)">✕</a>
          </span>
          <span v-if="!blocklist.length" style="color:#999;font-size:13px">（空的，没有拉黑任何词）</span>
        </div>
      </div>
      <!-- ★ #1509（2026-11-13）自定义口令：你的高频口令→哈文直接执行/回复，0 token 不等模型 -->
      <div class="row" style="flex-direction: column; align-items: stretch">
        <div class="row-main"><b>⚡ 自定义口令</b><span>
          把你的口头禅变成哈文的即时动作：说中就立刻办（不经过大模型、不花积分）。
          例：说「记一笔」直接打开记账；说「工作文件」直接打开 D 盘文件夹。只追加不改内置行为；
          疑问句（带吗/怎么/？）不会误触发，保存后立即生效：</span></div>
        <div style="margin-top: 8px; display: flex; flex-direction: column; gap: 6px">
          <div v-for="(k, ki) in ckwItems" :key="ki"
               style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;background:var(--bg-soft,#f7f8fa);border-radius:10px;padding:6px 10px;font-size:13px">
            <b style="color:var(--blue,#3b82f6)">「{{ k.trigger }}」</b>
            <span>{{ k.remind ? '→ 追加为提醒触发词' : k.open ? '→ 打开 ' + k.open : '→ ' + (k.reply || '') }}</span>
            <a style="cursor:pointer;color:#e05252;font-weight:bold;margin-left:auto" @click="delCkw(ki)">✕ 删除</a>
          </div>
          <span v-if="!ckwItems.length" style="color:#999;font-size:13px">（还没添加自定义口令）</span>
        </div>
        <div style="margin-top: 8px; display:flex; gap:6px; flex-wrap:wrap; align-items:center">
          <input class="key-input" style="width:130px" v-model.trim="ckwNew.trigger" placeholder="触发词，如：记一笔" maxlength="30" />
          <select class="lex-select" style="width:120px" v-model="ckwNew.type">
            <option value="reply">回复一句话</option>
            <option value="open">打开路径</option>
            <option value="remind">提醒触发词</option>
          </select>
          <input class="key-input" style="flex:1;min-width:160px" v-model.trim="ckwNew.value"
                 :placeholder="ckwNew.type === 'open' ? '如 D:\\工作' : ckwNew.type === 'remind' ? '选填，留空即可' : '哈文要回复的话'" maxlength="200" />
          <BigButton :px="13" :loading="ckwBusy" @click="addCkw">添加</BigButton>
        </div>
        <div v-if="ckwMsg" class="tip" :class="ckwOk ? 'ok' : 'bad'">{{ ckwMsg }}</div>
      </div>
      <!-- ★ 2026-09-21 (#824) 删除旧的「数据备份」纯文字行：与「关于」页备份与恢复区重合且无实际功能，备份入口统一收在关于页 -->
    </section>

    <!-- ★ 2026-09-20 (#804) 手机与云端：云端通道开关 + 配对码绑定手机 + 已绑设备管理 -->
    <!-- ★ 2026-09-20 (#808) 用户反馈：入口散、小白不懂 → 改成向导式：先选场景（在家/出门），三步图文一看就懂 -->
    <section v-show="anchor === 'mobile'" class="sec-body">
      <!-- ★ #952 三块重排：🏠在家 / 🚄出门 / ▸高级折叠。浏览器「添加到主屏幕」老方案整段删除（有 APP 后冗余） -->
      <div class="mg-card">
        <div class="mg-head"><b>🏠 在家用</b><span class="mg-tag free">连家里 WiFi · 不费流量 · 全功能</span></div>
        <div class="home-qr-row">
          <div ref="lanQrBox" class="pair-qr" title="手机扫码直达"></div>
          <div class="home-qr-txt">
            <b>手机连上家里 WiFi，用相机扫这个码就能用</b>
            <span>扫不出码也可在手机浏览器直接输：<code>http://{{ clLanHint }}:7000</code></span>
            <span>推荐装哈文手机 APP（可从哈文网站下载），连过一次自动记住，出门也能用</span>
          </div>
        </div>
      </div>

      <!-- ★ #947：Hub 运营公告（广播给在线哈文，7 天内有效） -->
      <div v-if="clStatus.broadcast && clStatus.broadcast.message" class="row" style="background:#FFF9E6;border-radius:10px;padding:10px 14px;margin-bottom:10px">
        <div class="row-main">
          <b>📢 平台公告（{{ clStatus.broadcast.ts }}）</b>
          <span>{{ clStatus.broadcast.message }}</span>
        </div>
      </div>

      <!-- ★ #952 出门用：就两步（开通道 → 扫码绑定），绑定完这区不用再看 -->
      <div class="mg-card" style="margin-top:10px">
        <div class="mg-head"><b>🚄 出门用</b><span class="mg-tag cloud">4G/5G 也能连回家 · 只需设置一次</span></div>
        <div class="row" style="border:none;padding:6px 0 2px">
          <div class="row-main">
            <b>☁️ 云端通道</b>
            <span>出门用手机的开关，开一次就行。全程加密，密码和文件永远留在你电脑上。状态：{{ clStatus.connected ? '已连上云端' : (clStatus.enabled ? '连接中/未连上' : '未开启') }}</span>
          </div>
          <template v-if="clStatus.enabled">
            <span class="pill" :class="clStatus.connected ? 'ok' : ''">{{ clStatus.connected ? '已连接' : '未连接' }}</span>
            <BigButton :px="13" variant="ghost" @click="clToggle(false)">关闭</BigButton>
          </template>
          <template v-else>
            <BigButton :px="13" @click="clToggle(true)">开启云端通道</BigButton>
          </template>
        </div>
      <div v-if="clToggleNeedPwd" class="row pwd-row">
        <div class="row-main">
          <span>开启/关闭云端通道需要管理员密码（就是你登录哈文用的那把）：</span>
          <div class="pwd-fields">
            <input class="key-input" type="password" v-model.trim="clPwd" placeholder="管理员密码" @keyup.enter="clToggle(clToggleTarget)" />
          </div>
        </div>
        <BigButton :px="13" :loading="clBusy" @click="clToggle(clToggleTarget)">确认</BigButton>
      </div>
      <div v-if="clMsg" class="tip" :class="clOk ? 'ok' : 'bad'">{{ clMsg }}</div>
      <!-- ★ #957 一键连接新手机：自动开通道 → 等连上 → 出码+二维码，全程不用翻高级设置 -->
      <div class="row" style="border:none;padding:6px 0 2px">
        <div class="row-main">
          <b>📱 连接新手机</b>
          <span>一键搞定：自动开通道 → 等连上 → 出配对码。手机相机「扫一扫」下面的二维码，点一下绑定即可</span>
        </div>
        <BigButton :px="13" :loading="ocBusy" @click="oneClickPair">一键连接新手机</BigButton>
      </div>
      <div v-if="ocTip" class="tip" :class="pairCode ? 'ok' : ''">{{ ocTip }}</div>
      <div v-if="pairCode" class="pair-box">
        <div ref="qrBox" class="pair-qr" title="手机扫一扫"></div>
        <b class="pair-code">{{ pairCode }}</b>
        <span>📱 用手机相机或「扫一扫」扫上面的二维码，自动填码，点一下绑定即可</span>
        <span>5 分钟内有效 · 一码只用一次</span>
      </div>
      </div><!-- /🚄出门用 -->

      <!-- ★ #952 高级折叠：中转服务器 / 已绑手机 / 已绑电脑，默认收起，新手零干扰 -->
      <div class="row adv-toggle" @click="advOpen = !advOpen">
        <div class="row-main"><b>{{ advOpen ? '▾' : '▸' }} 高级设置</b><span>中转服务器 · 已绑定的手机 · 已绑定的电脑（点开才显示）</span></div>
      </div>
      <template v-if="advOpen">
      <!-- ★ #951 中转服务器可配置：自建 HarvenRelay Lite 用户改这里，默认官方 -->
      <div class="row">
        <div class="row-main">
          <b>🛰️ 中转服务器</b>
          <span>出门在外时手机和电脑通过哪台服务器传话。默认用官方中转（harvenhub.com）；自己租了服务器、跑过 HarvenRelay Lite 一键包的用户，点「更换」填自己的地址</span>
          <span>当前：{{ isOfficialServer ? '官方中转 harvenhub.com' : clStatus.server_url }}</span>
        </div>
        <BigButton :px="13" variant="ghost" @click="clServerEdit = !clServerEdit">{{ clServerEdit ? '收起' : '更换' }}</BigButton>
      </div>
      <div v-if="clServerEdit" class="row pwd-row">
        <div class="row-main">
          <div class="pwd-fields">
            <input class="key-input" type="text" v-model.trim="clServerUrl" placeholder="https://你的服务器地址" @keyup.enter="clSaveServer(false)" style="max-width: 300px;" />
            <input class="key-input" type="password" v-model.trim="clPwd" placeholder="管理员密码" @keyup.enter="clSaveServer(false)" style="max-width: 200px;" />
          </div>
          <span>填自建服务器完整地址（https:// 或 http:// 开头）；密码填登录用的那把。留空地址 = 恢复官方中转。两个框都要填</span>
        </div>
        <div class="pc-ops">
          <button class="lex-del" @click="clSaveServer(false)">保存</button>
          <button class="lex-del" @click="clSaveServer(true)">恢复官方</button>
        </div>
      </div>

      <div class="row">
        <div class="row-main"><b>📱 绑定手机（手动）</b>
          <span v-if="clStatus.connected">点「生成配对码」→ 手机「扫一扫」扫二维码即可（5 分钟内有效，用一次作废）。也可以手动打开 <code>harvenhub.com/phone</code> 输码</span>
          <span v-else style="color:#B8860B">云端通道还没连上，所以按钮是灰的——用上面「📱 一键连接新手机」最省事，或先开通道等连上再回来</span>
        </div>
        <BigButton :px="13" :loading="clPairing" :disabled="!clStatus.connected" @click="genPairCode">生成配对码</BigButton>
      </div>

      <div class="row">
        <div class="row-main"><b>🔑 已绑定的手机（{{ clDevices.length }}）</b><span>换手机 / 手机丢了 / 员工离职 → 点解绑，那台手机立刻进不来</span></div>
        <BigButton :px="13" variant="ghost" @click="loadCl">刷新</BigButton>
      </div>
      <div v-for="d in clDevices" :key="d.id" class="row lex-row">
        <div class="row-main">
          <b>📱 {{ d.name }}</b>
          <span>绑定于 {{ d.added }}</span>
        </div>
        <button class="lex-del" @click="unbindDevice(d)">解绑</button>
      </div>
      <div v-if="!clDevices.length" class="row"><div class="row-main"><b>还没有绑定手机</b><span>开启云端通道后点「生成配对码」，在手机上输入即可绑定</span></div></div>

      <!-- ★ 2026-09-22 (#853) 登录过的电脑：登记 + 改名 + 移除 -->
      <div class="row">
        <div class="row-main"><b>💻 登录过的电脑（{{ pcDevices.length }}）</b><span>每台登录过哈文的电脑都会记在这里；点「改名」起个好认的名字（比如「烟台-客厅」「杭州-公司」），多台电脑一眼分清谁是谁</span></div>
        <BigButton :px="13" variant="ghost" @click="loadPcDevices">刷新</BigButton>
      </div>
      <div v-for="d in pcDevices" :key="d.id" class="row lex-row">
        <div class="row-main">
          <template v-if="pcEditId === d.id">
            <input class="key-input" type="text" v-model.trim="pcEditName" maxlength="20" placeholder="起个好认的名字" @keyup.enter="savePcName(d)" style="max-width: 240px;" />
          </template>
          <template v-else>
            <b>💻 {{ d.name }}</b>
            <span>最近登录 {{ d.last_seen || '—' }} · 首次 {{ d.added || '—' }}</span>
          </template>
        </div>
        <div class="pc-ops">
          <template v-if="pcEditId === d.id">
            <button class="lex-del" @click="savePcName(d)">保存</button>
            <button class="lex-del" @click="pcEditId = ''">取消</button>
          </template>
          <template v-else>
            <button class="lex-del" @click="startPcEdit(d)">改名</button>
            <button class="lex-del" @click="askRevokePc(d)">移除</button>
          </template>
        </div>
      </div>
      <div v-if="!pcDevices.length" class="row"><div class="row-main"><b>还没有电脑登录记录</b><span>在电脑浏览器里输密码登录一次哈文，这里就会出现这台电脑</span></div></div>
      <div v-if="pcRevokeDev" class="row pwd-row">
        <div class="row-main">
          <span>移除「{{ pcRevokeDev.name }}」需要管理员密码（移除后那台电脑下次要重新输密码登记）：</span>
          <div class="pwd-fields">
            <input class="key-input" type="password" v-model.trim="pcPwd" placeholder="管理员密码" @keyup.enter="doRevokePc" />
          </div>
        </div>
        <BigButton :px="13" :loading="pcBusy" @click="doRevokePc">确认移除</BigButton>
      </div>
      <div v-if="pcMsg" class="tip" :class="pcOk ? 'ok' : 'bad'">{{ pcMsg }}</div>
      </template><!-- /高级折叠 -->

    </section>

    <!-- 钥匙串 -->
    <!-- ★ 2026-10-23 设置页瘦身：API Keys 本就是只读查看器（填钥匙在「模型」页），标签撤除；
         钥匙状态摘要已加到 ModelsPage 外脑区头部 -->

    <!-- 关于（★ 2026-09-17 重做：检查更新/系统信息/制作者联系/协议隐私/一键备份/更新日志） -->
    <section v-show="anchor === 'about'" class="sec-body">
      <!-- 品牌区 -->
      <div class="about-hero">
        <DuckLogo :px="56" class="about-duck" />
        <div class="hero-main">
          <b>Harven AI 哈文 <span class="ver-pill">v{{ APP_VERSION }}</span></b>
          <span>{{ t('about.tagline') }}</span>
        </div>
        <BigButton :px="14" :loading="checking" @click="checkUpdate"><HIcon name="download" :size="15" /> {{ t('about.checkUpdate') }}</BigButton>
      </div>

      <!-- 检查更新结果 -->
      <div v-if="update" class="update-box" :class="update.has_update ? 'warn' : 'ok'">
        <template v-if="update.error">
          <b>{{ t('about.checkFail') }}{{ update.error }}</b>
          <span>{{ t('about.checkFailHint') }} <a href="https://github.com/AmenMonk/Harven/releases" target="_blank" rel="noreferrer">GitHub Releases</a></span>
        </template>
        <template v-else-if="update.has_update">
          <b>{{ t('about.hasUpdate', { cur: update.current, latest: update.latest }) }}</b>
          <pre v-if="update.changelog" class="changelog">{{ update.changelog }}</pre>
          <div class="upd-actions">
            <a v-if="(update.download_url || update.url) " :href="update.download_url || update.url" target="_blank" rel="noreferrer"><BigButton :px="13">{{ t('about.download') }}</BigButton></a>
            <BigButton v-if="(update.download_url || update.url || (update.update_channel && update.update_channel.url)) && !oneUpdDone" :px="13" :loading="oneUpdating" @click="oneClickUpdate"><HIcon name="download" :size="14" /> 一键更新<span v-if="update.update_channel && update.update_channel.url" class="delta-badge">增量</span></BigButton>
            <BigButton v-if="oneUpdDone" :px="13" @click="restartHarven"><HIcon name="download" :size="14" /> 重启哈文生效</BigButton>
          </div>
          <div v-if="oneUpdating" class="one-upd-bar"><div class="bar" :style="{ width: oneUpdPct + '%' }"></div></div>
          <div v-if="oneUpdMsg" class="one-upd-msg" :class="oneUpdOk ? 'ok' : 'err'">{{ oneUpdMsg }}</div>
        </template>
        <template v-else>
          <b>{{ t('about.upToDate', { v: update.latest || update.current }) }}</b>
        </template>
      </div>

      <!-- 系统信息 -->
      <h3 class="about-sec">{{ t('about.sysInfo') }}</h3>
      <div class="row"><div class="row-main"><b>{{ t('about.cpu') }}</b><span>{{ sysInfo.cpu_percent >= 0 ? sysInfo.cpu_percent + '%' : t('about.cpuNa') }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.memory') }}</b><span>{{ sysInfo.memory?.total ? `${sysInfo.memory.used}GB / ${sysInfo.memory.total}GB（${sysInfo.memory.percent}%）` : '—' }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.gpu') }}</b><span>{{ sysInfo.gpu?.name || '—' }}{{ sysInfo.gpu?.memory_total ? ` · ${t('about.vram', { n: sysInfo.gpu.memory_total })}` : '' }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.disk') }}</b><span>{{ sysInfo.disk?.total ? t('about.diskFree', { free: sysInfo.disk.free, total: sysInfo.disk.total }) : '—' }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.os') }}</b><span>{{ sysInfo.os || '—' }} {{ sysInfo.arch || '' }}</span></div></div>

      <!-- 制作者与联系 -->
      <h3 class="about-sec">{{ t('about.maker') }}</h3>
      <div class="row">
        <div class="row-main"><b>{{ t('about.maintainer') }}</b><span>{{ t('about.maintainerD') }}</span></div>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('about.contact') }}</b><span>{{ t('about.contactD') }}</span></div>
        <a href="mailto:38552540@qq.com"><BigButton :px="13" variant="ghost">{{ t('about.sendMail') }}</BigButton></a>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('about.repo') }}</b><span>{{ t('about.repoD') }}</span></div>
        <a href="https://github.com/AmenMonk/Harven" target="_blank" rel="noreferrer"><BigButton :px="13" variant="ghost">{{ t('set.go') }}</BigButton></a>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('about.hub') }}</b><span>{{ t('about.hubD') }}</span></div>
        <span class="hub-links">
          <a href="#/hub"><BigButton :px="13" variant="ghost">{{ t('about.openMarket') }}</BigButton></a>
          <a href="https://harvenhub.com" target="_blank" rel="noreferrer"><BigButton :px="13" variant="ghost">{{ t('about.openSite') }}</BigButton></a>
        </span>
      </div>

      <!-- 协议与隐私 -->
      <h3 class="about-sec">{{ t('about.license') }}</h3>
      <div class="row"><div class="row-main"><b>{{ t('about.licenseT') }}</b><span>{{ t('about.licenseD') }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.privacyT') }}</b><span>{{ t('about.privacyD') }}</span></div></div>
      <div class="row"><div class="row-main"><b>{{ t('about.hubConn') }}</b><span>{{ hubOk ? t('about.hubOk') : t('about.hubBad') }}</span></div></div>

      <!-- 备份与恢复 -->
      <h3 class="about-sec">{{ t('about.backupSec') }}</h3>
      <div class="row">
        <div class="row-main"><b>{{ t('about.backupT') }}</b><span>{{ t('about.backupD') }}</span></div>
        <BigButton :px="13" :loading="backing" @click="doBackup">{{ t('about.doBackup') }}</BigButton>
      </div>
      <div v-if="backupMsg" class="update-box" :class="backupOk ? 'ok' : 'bad'">
        <template v-if="backupOk">
          <b>{{ t('about.backupDone', { name: backupMsg.filename, size: backupMsg.size_mb }) }}</b>
          <a :href="backupMsg.download_url" download><BigButton :px="13" variant="ghost">{{ t('about.downloadBackup') }}</BigButton></a>
        </template>
        <template v-else><b>❌ {{ backupMsg }}</b></template>
      </div>
      <div class="row">
        <div class="row-main"><b>{{ t('about.restoreT') }}</b><span>{{ t('about.restoreD') }}</span></div>
        <input type="file" ref="restoreInput" accept=".zip" hidden @change="doRestore" />
        <BigButton :px="13" variant="ghost" @click="$refs.restoreInput.click()">{{ t('about.pickBackup') }}</BigButton>
      </div>

      <!-- 更新日志 -->
      <h3 class="about-sec">{{ t('about.log') }}</h3>
      <div class="changelog-card" v-for="c in CHANGELOG" :key="c.ver">
        <div class="cl-head"><b>{{ c.ver }}</b><span v-if="c.cur" class="cur-pill">{{ t('about.curVer') }}</span><span class="cl-date">{{ c.date }}</span></div>
        <ul><li v-for="item in c.items" :key="item">{{ item }}</li></ul>
      </div>
    </section>
  </PageShell>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import PageShell from '../components/PageShell.vue'
import HIcon from '../components/HIcon.vue'
import BigButton from '../components/BigButton.vue'
import DuckLogo from '../components/DuckLogo.vue'
import { getJSON, postJSON, delJSON } from '../api.js'
import { t, lang, setLang } from '../i18n'

function switchLang(l) {
  setLang(l)
  flash(l === 'en' ? t('set.langSavedEn') : t('set.langSaved'))
}

const SECTIONS = computed(() => [
  { id: 'models', name: t('set.sec.models'), icon: 'bot' },
  { id: 'security', name: t('set.sec.security'), icon: 'shield' },
  { id: 'perm', name: '权限中心', icon: 'lock' },
  { id: 'general', name: t('set.sec.general'), icon: 'gear' },
  { id: 'mobile', name: '手机与云端', icon: 'globe' },
  { id: 'about', name: t('set.sec.about'), icon: 'info' },
])

/* ── ★ 2026-10-20 权限中心（第二批2.1）── */
const permData = ref({ perms: [], net: [] })
const permMsg = ref('')

async function loadPerm() {
  try {
    const r = await fetch('/api/permissions')
    permData.value = await r.json()
  } catch { permData.value = { perms: [], net: [] } }
}

async function setPerm(key, value) {
  permMsg.value = ''
  try {
    const j2 = await fetch('/api/permissions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    }).then(r => r.json())
    // ★ 2026-10-21 修「ok」露底：后端 message='ok' 时不显示原始值
    if (j2.ok) permMsg.value = '已保存'
    else permMsg.value = (j2.message && j2.message !== 'ok') ? j2.message : '保存失败'
    await loadPerm()
    setTimeout(() => { permMsg.value = '' }, 2000)
  } catch (e) { permMsg.value = '网络错误' }
}
const FOREIGN_NAME = { deepseek: 'DeepSeek', doubao: '豆包', qwen: '通义千问', kimi: 'Kimi', zhipu: '智谱 GLM', yi: '零一万物', baichuan: '百川', stepfun: '阶跃星辰' }

const anchor = ref('models')
const mode = ref('safe')
const secInfo = ref({})
/* ★ 2026-10-23 设置页瘦身：keys 只读展示已撤（钥匙管理全在「模型」页），currentForeign 保留供模型区状态显示 */
const currentForeign = ref('')
const hubOk = ref(false)
const pwd = ref('')
const busy = ref(false)
const msg = ref('')
const msgOk = ref(false)

/* ── ★ 2026-09-20 (#804) 手机与云端 ── */
const clStatus = ref({ enabled: false, connected: false, devices: [] })
const clDevices = computed(() => clStatus.value.devices || [])
const clBusy = ref(false)
const clMsg = ref('')
const clOk = ref(false)
const clToggleNeedPwd = ref(false)
const clToggleTarget = ref(false)
const clPwd = ref('')
const clPairing = ref(false)
const pairCode = ref('')
const qrBox = ref(null)
const lanQrBox = ref(null)
const advOpen = ref(false)   /* ★ #952 高级折叠 */
const clLanHint = ref('家里电脑的IP')
/* ★ #951 中转服务器可配置 */
const clServerEdit = ref(false)
const clServerUrl = ref('')

const isOfficialServer = computed(() => {
  const u = String(clStatus.value.server_url || 'https://harvenhub.com')
  return !u || u === 'https://harvenhub.com'
})

async function clSaveServer(resetOfficial) {
  clBusy.value = true; clMsg.value = ''
  try {
    const payload = { server_url: resetOfficial ? '' : clServerUrl.value, password: clPwd.value }
    const j = await postJSON('/api/cloud_link/server', payload)
    if (j.success) {
      clOk.value = true
      clServerEdit.value = false
      clPwd.value = ''
      clMsg.value = resetOfficial
        ? '✅ 已恢复官方中转 harvenhub.com' + (clStatus.value.enabled ? '，正在重连…' : '')
        : '✅ 中转服务器已换成 ' + j.server_url + (clStatus.value.enabled ? '，正在重连…' : '（云端通道未开启，开启后生效）')
      loadCl()
    } else if ((j.error || '').includes('密码')) {
      clOk.value = false; clMsg.value = '❌ 请先填管理员密码'
    } else { clOk.value = false; clMsg.value = '❌ ' + (j.error || '保存失败') }
  } catch (e) {
    clOk.value = false; clMsg.value = '❌ ' + (e && e.message ? e.message : String(e))
  } finally { clBusy.value = false }
}

/* ★ 扫码配对（#812）：把配对链接生成二维码，手机相机一扫自动填码 */
function renderPairQR(code) {
  const load = () => new Promise((res, rej) => {
    if (window.QRCode) return res()
    const s = document.createElement('script')
    s.src = 'qrcode.min.js'  // 相对 SPA 根（/static/spa/），vite public 目录产物
    s.onload = res; s.onerror = () => rej(new Error('qr-lib'))
    document.head.appendChild(s)
  })
  load().then(() => {
    if (!qrBox.value) return
    qrBox.value.innerHTML = ''
    /* eslint-disable no-new */
    new window.QRCode(qrBox.value, { text: 'https://harvenhub.com/phone?code=' + code, width: 128, height: 128, correctLevel: window.QRCode.CorrectLevel.M })
  }).catch(() => { /* 库加载失败时只显示数字码，不影响手输路径 */ })
}

/* ★ #952 在家二维码：手机连家里 WiFi 后扫码直达 SPA（相机扫=浏览器打开；APP 扫=自动填地址） */
function renderLanQR() {
  const ip = clLanHint.value
  if (!ip || ip === '家里电脑的IP') return
  const load = () => new Promise((res, rej) => {
    if (window.QRCode) return res()
    const s = document.createElement('script')
    s.src = 'qrcode.min.js'
    s.onload = res; s.onerror = () => rej(new Error('qr-lib'))
    document.head.appendChild(s)
  })
  load().then(() => {
    if (!lanQrBox.value || lanQrBox.value.dataset.ip === ip) return   /* 同 IP 不重画 */
    lanQrBox.value.innerHTML = ''
    lanQrBox.value.dataset.ip = ip
    /* ★ #957 统一轻页：在家扫码直达 /phone 本地轻页（原 /spa/ 完整版只归电脑） */
    new window.QRCode(lanQrBox.value, { text: 'http://' + ip + ':7000/phone', width: 128, height: 128, correctLevel: window.QRCode.CorrectLevel.M })
  }).catch(() => {})
}

async function loadCl() {
  try {
    clStatus.value = await getJSON('/api/cloud_link/status')
  } catch (_) { /* 服务旧版本时静默 */ }
  try { const si = await getJSON('/api/system/info'); clLanHint.value = si.lan_ip || '家里电脑的IP' } catch (_) {}
  renderLanQR()
}

async function clToggle(enable) {
  clBusy.value = true; clMsg.value = ''
  try {
    const j = await postJSON('/api/cloud_link/toggle', { enabled: enable, password: clPwd.value })
    if (j.success) {
      clOk.value = true
      clMsg.value = enable ? '✅ 云端通道已开启，正在连接 HarvenHub…' : '⏹ 云端通道已关闭'
      clToggleNeedPwd.value = false; clPwd.value = ''
    } else if ((j.error || '').includes('密码')) {
      clOk.value = false; clMsg.value = ''
      clToggleNeedPwd.value = true; clToggleTarget.value = enable
    } else { clOk.value = false; clMsg.value = '❌ ' + (j.error || '操作失败') }
  } catch (e) {
    clOk.value = false
    const msg = e && e.message ? e.message : String(e)
    if (msg.includes('密码')) { clMsg.value = ''; clToggleNeedPwd.value = true; clToggleTarget.value = enable }
    else { clMsg.value = '❌ ' + msg }
  }
  finally {
    clBusy.value = false
    setTimeout(loadCl, 1200)
  }
}

/* ★ 2026-09-22 (#851) 实例名 */
const instNameInput = ref('')
const instNameBusy = ref(false)
const instNameMsg = ref('')
const instNameOk = ref(false)
async function saveInstName() {
  instNameBusy.value = true; instNameMsg.value = ''
  try {
    const j = await postJSON('/api/system/name', { name: instNameInput.value })
    if (j.success) { instNameOk.value = true; instNameMsg.value = '✅ 已改名为「' + j.instance_name + '」，刷新页面后左上角生效' }
    else { instNameOk.value = false; instNameMsg.value = '❌ ' + (j.error || '保存失败') }
  } catch (e) { instNameOk.value = false; instNameMsg.value = '❌ ' + e.message }
  finally { instNameBusy.value = false }
}
async function loadInstName() {
  try { const si = await getJSON('/api/system/info'); instNameInput.value = (si.instance_name || '').trim() } catch (_) {}
}

/* ★ 2026-09-22 (#853) 登录过的电脑 */
const pcDevices = ref([])
const pcEditId = ref('')
const pcEditName = ref('')
const pcMsg = ref('')
const pcOk = ref(false)
const pcRevokeDev = ref(null)
const pcPwd = ref('')
const pcBusy = ref(false)
async function loadPcDevices() {
  try { const j = await getJSON('/api/devices/computers'); pcDevices.value = j.devices || [] } catch (_) {}
}
function startPcEdit(d) { pcEditId.value = d.id; pcEditName.value = d.name; pcMsg.value = ''; pcRevokeDev.value = null }
async function savePcName(d) {
  if (!pcEditName.value) { pcOk.value = false; pcMsg.value = '❌ 名字不能为空'; return }
  try {
    const j = await postJSON('/api/devices/computer/rename', { id: d.id, name: pcEditName.value })
    if (j.success) { pcOk.value = true; pcMsg.value = '✅ 已改名'; pcEditId.value = ''; loadPcDevices() }
    else { pcOk.value = false; pcMsg.value = '❌ ' + (j.error || '改名失败') }
  } catch (e) { pcOk.value = false; pcMsg.value = '❌ ' + e.message }
}
function askRevokePc(d) { pcRevokeDev.value = d; pcPwd.value = ''; pcMsg.value = ''; pcEditId.value = '' }
async function doRevokePc() {
  if (!pcRevokeDev.value) return
  pcBusy.value = true
  try {
    const j = await postJSON('/api/devices/computer/revoke', { id: pcRevokeDev.value.id, password: pcPwd.value })
    if (j.success) { pcOk.value = true; pcMsg.value = '✅ 已移除「' + pcRevokeDev.value.name + '」'; pcRevokeDev.value = null; loadPcDevices() }
    else if ((j.error || '').includes('密码')) { pcOk.value = false; pcMsg.value = '' }
    else { pcOk.value = false; pcMsg.value = '❌ ' + (j.error || '移除失败') }
  } catch (e) {
    pcOk.value = false
    const msg = e && e.message ? e.message : String(e)
    if (msg.includes('密码')) { pcMsg.value = '' } else { pcMsg.value = '❌ ' + msg }
  }
  finally { pcBusy.value = false }
}

async function genPairCode() {
  clPairing.value = true; clMsg.value = ''; pairCode.value = ''
  try {
    const j = await postJSON('/api/cloud_link/pair/start', {})
    if (j.success) { pairCode.value = j.pair_code; clOk.value = true; nextTick(() => renderPairQR(j.pair_code)) }
    else { clOk.value = false; clMsg.value = '❌ ' + (j.error || '生成失败（先开启云端通道并等它连上）') }
  } catch (e) { clOk.value = false; clMsg.value = '❌ ' + e.message }
  finally { clPairing.value = false }
}

/* ★ #957 一键连接新手机：开通道 → 等连上 → 出码+二维码，全程不翻高级设置 */
const ocBusy = ref(false)
const ocTip = ref('')
async function oneClickPair() {
  ocBusy.value = true; ocTip.value = ''
  try {
    if (!clStatus.value.enabled) {
      ocTip.value = '第 1/3 步：正在开启云端通道…'
      await clToggle(true)
      if (clToggleNeedPwd.value) {
        ocTip.value = '开启云端通道需要管理员密码——在上方填密码点「确认」，然后再点一次「一键连接新手机」'
        return
      }
    }
    if (!clStatus.value.connected) {
      ocTip.value = '第 2/3 步：等云端通道连上（最多 15 秒）…'
      const ok = await waitConnected(15)
      if (!ok) {
        clOk.value = false
        clMsg.value = '❌ 云端通道 15 秒内没连上：检查电脑能不能上网；连上后再点一次「一键连接新手机」'
        ocTip.value = ''
        return
      }
    }
    ocTip.value = '第 3/3 步：生成配对码…'
    await genPairCode()
    ocTip.value = pairCode.value
      ? '✅ 配对码已生成！手机相机「扫一扫」扫下面的二维码 → 点一下绑定即可'
      : ''
  } finally { ocBusy.value = false }
}
async function waitConnected(sec) {
  for (let i = 0; i < sec * 2; i++) {
    await new Promise(r => setTimeout(r, 500))
    try { clStatus.value = await getJSON('/api/cloud_link/status') } catch (_) {}
    if (clStatus.value.connected) return true
  }
  return false
}

async function unbindDevice(d) {
  if (!confirm(`确定解绑「${d.name}」吗？解绑后这台手机要重新配对才能连上。`)) return
  try {
    await postJSON('/api/cloud_link/devices/unbind', { device_id: d.id })
    await loadCl()
  } catch (_) {}
}

/* ── ★ 2026-09-17 关于页重做 ── */
/* ★ 方案B：版本号唯一户口在后端 src/version.py，这里启动时从 /health 拉真值（拉不到才用兜底） */
const APP_VERSION = ref('0.3.1')
const CHANGELOG = [
  { ver: 'v0.3.1', cur: true, date: '2026-09', items: [
    '全新界面（你现在看到的就是）：本地优先 + 外脑双模式一键切换',
    '手机也能用：浏览器安装到桌面/主屏幕（PWA）',
    '家庭面板套件：记账、孩子成长档案、家庭提醒等开箱即用',
    '垂直大脑直答：音乐/图片/医疗等专项模型独立面板',
    '对话时间轴：按日按月翻全部聊天记录',
  ]},
  { ver: 'v0.3.0', date: '2026-08', items: [
    'HarvenHub 云端市场上线：技能 / 模型 / 工具一键安装',
    '模型中心重构：零推荐搜索，按你的电脑配置匹配',
    '技能库升级：支持 AI 自动创建技能',
  ]},
  { ver: 'v0.2.1', date: '2026-07', items: [
    '首个稳定版本：本地对话、知识库、联网搜索、定时任务',
  ]},
]
const update = ref(null)
const checking = ref(false)
const sysInfo = ref({})
const backing = ref(false)
const backupMsg = ref(null)
const backupOk = ref(false)

async function checkUpdate() {
  checking.value = true
  try { update.value = await getJSON('/api/version/check') }
  catch (e) { update.value = { error: e.message, has_update: false } }
  finally { checking.value = false }
}

// ★ #1361 一键更新：下载→校验→备份→覆盖 全自动，完成后一键重启
const oneUpdating = ref(false)
const oneUpdPct = ref(0)
const oneUpdMsg = ref('')
const oneUpdOk = ref(false)
const oneUpdDone = ref(false)
let oneUpdTimer = null

async function oneClickUpdate() {
  if (oneUpdating.value) return
  // ★ #1398（2026-11-02 定版拍板 A+B）增量通道优先：清单带 update_channel 就走增量包（KB~MB 级秒下），否则完整包兜底
  const uc = update.value.update_channel
  const useDelta = !!(uc && uc.url && uc.md5)
  const target = useDelta
    ? { url: uc.url, md5: uc.md5 }
    : { url: update.value.download_url || update.value.url, md5: update.value.package_md5 || '' }
  const sizeHint = useDelta
    ? (uc.size ? `约 ${(uc.size / 1024 / 1024).toFixed(1)} MB` : '增量小包')
    : (update.value.package_size ? `约 ${(update.value.package_size / 1024 / 1024).toFixed(0)} MB` : '完整包')
  const modeLabel = useDelta ? '增量更新（只下改动部分）' : '完整更新（全包下载）'
  if (!confirm(`${modeLabel}，包体${sizeHint}。\n会自动备份你的数据，然后覆盖新文件，完成后需重启哈文。\n继续吗？`)) return
  oneUpdating.value = true; oneUpdMsg.value = useDelta ? '⏳ 正在下载增量更新包…' : '⏳ 正在下载并应用更新…'
  oneUpdOk.value = false; oneUpdDone.value = false; oneUpdPct.value = 0
  try {
    await postJSON('/api/version/update', target)
    clearInterval(oneUpdTimer)
    oneUpdTimer = setInterval(async () => {
      try {
        const p = await getJSON('/api/version/update-progress')
        oneUpdPct.value = p.progress || 0
        if (p.status === 'done') {
          clearInterval(oneUpdTimer)
          oneUpdating.value = false; oneUpdDone.value = true; oneUpdOk.value = true
          oneUpdMsg.value = `✅ 更新完成（应用 ${p.applied || 0} 个文件），数据已备份到 ${p.backup || 'backup 目录'}，点「重启哈文生效」完成`
        } else if (p.status === 'error') {
          clearInterval(oneUpdTimer)
          oneUpdating.value = false; oneUpdOk.value = false
          oneUpdMsg.value = '❌ ' + (p.error || '更新失败')
        }
      } catch (e) { /* 瞬时网络抖动忽略，下轮再试 */ }
    }, 1000)
  } catch (e) {
    oneUpdating.value = false; oneUpdOk.value = false
    oneUpdMsg.value = '❌ ' + e.message
  }
}

async function restartHarven() {
  if (!confirm('哈文将立即重启，页面约 10 秒后自动恢复（没恢复就刷新一下）。确定吗？')) return
  oneUpdMsg.value = '🔄 正在重启…页面稍后自动恢复'
  try { await postJSON('/api/version/restart', {}) } catch (e) { /* 重启中连接断开属正常 */ }
}

async function doBackup() {
  backing.value = true; backupMsg.value = null
  try {
    const j = await postJSON('/api/system/backup')
    if (j.success) { backupOk.value = true; backupMsg.value = j }
    else { backupOk.value = false; backupMsg.value = j.error || '备份失败' }
  } catch (e) { backupOk.value = false; backupMsg.value = '备份失败：' + e.message }
  finally { backing.value = false }
}

async function doRestore(ev) {
  const file = ev.target.files[0]
  ev.target.value = ''
  if (!file) return
  if (!confirm('恢复将覆盖当前同名数据，确定继续吗？')) return
  backupOk.value = false; backupMsg.value = '⏳ 正在恢复…'
  try {
    const fd = new FormData(); fd.append('backup_file', file)
    const res = await fetch('/api/system/restore', { method: 'POST', body: fd })
    const j = await res.json()
    if (j.success) backupMsg.value = '✅ ' + (j.message || '恢复完成，请重启哈文')
    else backupMsg.value = '❌ ' + (j.error || '恢复失败')
  } catch (e) { backupMsg.value = '❌ 恢复失败：' + e.message }
}

function flash(text, ok = true) {
  msg.value = text; msgOk.value = ok
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 5000)
}

async function loadAll(showErr = false) {
  try {
    loadPerm()  // ★ 权限中心随刷新加载
    const [m, cf] = await Promise.all([
      getJSON('/api/security/mode'),
      getJSON('/api/models/current-foreign').catch(() => ({ model: '' })),
    ])
    mode.value = m.mode || 'safe'
    secInfo.value = m
    currentForeign.value = (cf.model || '').trim()
    if (showErr) msg.value = ''
  } catch (e) { if (showErr) flash('加载失败：' + e.message, false) }
  try { const s = await getJSON('/api/harvenhub-market/stats'); hubOk.value = (s.total_items || 0) >= 0 } catch (_) { hubOk.value = false }
  try { sysInfo.value = await getJSON('/api/system/info') } catch (_) {}
}

async function setFull() {
  if (!pwd.value) { flash('先输入管理员密码', false); return }
  busy.value = true
  try {
    const j = await postJSON('/api/security/full-access', { password: pwd.value })
    if (j.success) { flash('已进入全权模式（重启后自动回到安全模式）'); pwd.value = '' }
    else flash('开启失败：' + (j.error || '密码不对？'), false)
    await loadAll()
  } catch (e) { flash('开启失败：' + e.message, false) }
  finally { busy.value = false }
}

async function setSafe() {
  busy.value = true
  try {
    await postJSON('/api/security/disable')
    flash('已回到安全模式')
    await loadAll()
  } catch (e) { flash('切换失败：' + e.message, false) }
  finally { busy.value = false }
}

/* ── ★ 2026-09-17 管理员改密 ── */
const oldPwd = ref('')
const newPwd = ref('')
const newPwd2 = ref('')
const pwdBusy = ref(false)
const pwdMsg = ref('')
const pwdOk = ref(false)

async function changePwd() {
  if (!oldPwd.value || !newPwd.value) { pwdOk.value = false; pwdMsg.value = t('set.pwdNeedBoth'); return }
  if (newPwd.value !== newPwd2.value) { pwdOk.value = false; pwdMsg.value = t('set.pwdMismatch'); return }
  pwdBusy.value = true; pwdMsg.value = ''
  try {
    const j = await postJSON('/api/security/change-password', { old_password: oldPwd.value, new_password: newPwd.value })
    if (j.success) {
      pwdOk.value = true
      pwdMsg.value = t('set.pwdOk')
      oldPwd.value = ''; newPwd.value = ''; newPwd2.value = ''
    } else {
      pwdOk.value = false
      pwdMsg.value = t('set.pwdFail') + (j.error || '')
    }
  } catch (e) { pwdOk.value = false; pwdMsg.value = t('set.pwdFail') + e.message }
  finally { pwdBusy.value = false }
}

/* ── ★ #1362（2026-11-02）密码恢复：安全问题 + 恢复盘 ── */
const recBank = [
  '你的第一所学校叫什么名字？', '你小时候最好的朋友叫什么名字？', '你出生的城市是哪里？',
  '你第一辆车的品牌是什么？', '你最爱吃的一道菜是什么？', '你小学班主任的名字是？',
  '你养过的第一只宠物的名字是？', '你的生日是几月几日？（不含年份）',
]
const recStatus = ref({ set: false, questions: [], code: '' })
const recEditing = ref(false)
const recQ1 = ref(''); const recQ1c = ref(''); const recA1 = ref('')
const recQ2 = ref(''); const recQ2c = ref(''); const recA2 = ref('')
const recPwd = ref('')
const recCode = ref('')
const recMsg = ref(''); const recOk = ref(false); const recBusy = ref(false)

async function loadRecStatus() {
  try {
    const j = await getJSON('/api/security/recovery/hints')
    if (j.set) recStatus.value = { set: true, questions: [j.q1, j.q2], code: '' }
  } catch (_) {}
}

function startRecEdit() {
  recEditing.value = true
  recCode.value = recStatus.value.code || ''
  if (recStatus.value.set) {
    // 已设置：问题回显（在题库内则选中，否则进自定义）
    const [q1, q2] = recStatus.value.questions
    recQ1.value = recBank.includes(q1) ? q1 : '__custom'; if (!recBank.includes(q1)) recQ1c.value = q1
    recQ2.value = recBank.includes(q2) ? q2 : '__custom'; if (!recBank.includes(q2)) recQ2c.value = q2
  }
}

async function saveRecovery() {
  const q1 = recQ1.value === '__custom' ? recQ1c.value : recQ1.value
  const q2 = recQ2.value === '__custom' ? recQ2c.value : recQ2.value
  if (!q1 || !recA1.value || !q2 || !recA2.value) { recOk.value = false; recMsg.value = '问题和答案都要填'; return }
  if (q1 === q2) { recOk.value = false; recMsg.value = '两个问题不能一样'; return }
  if (!recPwd.value) { recOk.value = false; recMsg.value = '先输当前管理密码验证'; return }
  recBusy.value = true; recMsg.value = ''
  try {
    const j = await postJSON('/api/security/recovery/setup', { password: recPwd.value, q1, a1: recA1.value, q2, a2: recA2.value })
    if (j.success) {
      recOk.value = true; recMsg.value = '已保存。恢复码在下面，建议下载恢复盘存到手机或U盘'
      recCode.value = j.code
      recEditing.value = false
      recPwd.value = ''; recA1.value = ''; recA2.value = ''
      loadRecStatus()
    } else { recOk.value = false; recMsg.value = j.error || '没保存上' }
  } catch (e) { recOk.value = false; recMsg.value = '没保存上：' + e.message }
  finally { recBusy.value = false }
}

function downloadRecDisk() {
  const txt = [
    '════════ 哈文密码恢复盘 ════════',
    '',
    '恢复码：' + recCode.value,
    '',
    '用途：如果忘记了哈文的管理密码，打开哈文登录页，',
    '点「忘记密码？」→ 选「用恢复码」→ 输入上面的恢复码',
    '和新密码，就能重置密码。',
    '',
    '保管建议：存到手机备忘录或U盘，别和电脑放在一起。',
    '生成时间：' + new Date().toLocaleString(),
  ].join('\n')
  const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '哈文密码恢复盘.txt'
  a.click()
  URL.revokeObjectURL(a.href)
}

/* ── ★ 2026-09-18 #751 学习词典 + 对话即录入省token开关 ── */
const lexicon = ref([])
const domainMap = ref({})   // {panel_id: {name, domains:{key:label}}}
const assist = ref({ extract_allow_cloud: 'true', answer_allow_cloud: 'false', vision_routing: 'auto',
                    summary_dream_weekly: 'false', summary_accounting_monthly: 'false', summary_kid_monthly: 'false', summary_fridge_expiry: 'false', summary_meal_weekly: 'false' })
const assistMsg = ref('')
const assistOk = ref(false)

function lexLabel(it) {
  const p = domainMap.value[it.panel_id]
  let s = p?.name || it.panel_id
  if (it.domain && p?.domains?.[it.domain]) s += ' · ' + p.domains[it.domain]
  else if (it.domain) s += ' · ' + it.domain
  return s
}
/* ★ 2026-10-23 词典分组：按面板归堆（芯片流用） */
const lexGroups = computed(() => {
  const m = {}
  for (const it of lexicon.value) {
    const pid = it.panel_id || '_other'
    if (!m[pid]) m[pid] = { panel: pid, name: domainMap.value[pid]?.name || pid, items: [] }
    m[pid].items.push(it)
  }
  return Object.values(m).sort((a, b) => b.items.length - a.items.length)
})
async function loadLexicon() {
  try {
    const [lx, dm, st] = await Promise.all([
      getJSON('/api/assist/lexicon'),
      getJSON('/api/assist/domains'),
      getJSON('/api/assist/settings'),
    ])
    lexicon.value = lx.items || []
    const m = {}
    for (const d of (dm.domains || [])) {
      m[d.panel_id] = { name: d.name, domains: {} }
      for (const dd of (d.domains || [])) m[d.panel_id].domains[dd.key] = dd.label
    }
    domainMap.value = m
    if (st.settings) assist.value = { ...assist.value, ...st.settings }
  } catch (e) { flash('词典加载失败：' + e.message, false) }
}
async function delLexicon(it) {
  if (!confirm(`删除叫法「${it.pattern}」？哈文以后不再按这个叫法直达（删错可以重新教）`)) return
  try {
    await delJSON(`/api/assist/lexicon/${it.id}`)
    lexicon.value = lexicon.value.filter(x => x.id !== it.id)
  } catch (e) { flash('删除失败：' + e.message, false) }
}
// ★ #1379 误识别黑名单：加载 + 移除
const blocklist = ref([])
/* ★ #1509 自定义口令 */
const ckwItems = ref([])
const ckwNew = ref({ trigger: '', type: 'reply', value: '' })
const ckwBusy = ref(false)
const ckwMsg = ref('')
const ckwOk = ref(false)
async function loadCkw() {
  try {
    const r = await getJSON('/api/custom_keywords')
    ckwItems.value = r.items || []
  } catch (e) { /* 静默：老版本后端无此端点 */ }
}
async function saveCkw(items, okText) {
  ckwBusy.value = true
  try {
    const r = await postJSON('/api/custom_keywords', { items })
    ckwItems.value = (r && r.items) || items
    ckwOk.value = true; ckwMsg.value = okText || '已保存，立即生效'
    setTimeout(() => { ckwMsg.value = '' }, 2500)
  } catch (e) { ckwOk.value = false; ckwMsg.value = '保存失败：' + (e.message || e) }
  finally { ckwBusy.value = false }
}
async function addCkw() {
  const n = ckwNew.value
  if (!n.trigger) { ckwOk.value = false; ckwMsg.value = '先填触发词'; return }
  if (ckwItems.value.some(k => k.trigger === n.trigger)) { ckwOk.value = false; ckwMsg.value = '这个触发词已经有了'; return }
  const entry = { trigger: n.trigger }
  if (n.type === 'open') entry.open = n.value
  else if (n.type === 'remind') entry.remind = true
  else entry.reply = n.value || '好的～'
  if (n.type !== 'remind' && n.type !== 'open' && !n.value) { /* 回复留空给默认语 */ }
  if (n.type === 'open' && !n.value) { ckwOk.value = false; ckwMsg.value = '打开路径需要填一个文件夹或文件路径'; return }
  await saveCkw([...ckwItems.value, entry], '已添加「' + n.trigger + '」，立即生效')
  ckwNew.value = { trigger: '', type: n.type, value: '' }
}
async function delCkw(i) {
  if (!confirm('删掉口令「' + ckwItems.value[i].trigger + '」？')) return
  await saveCkw(ckwItems.value.filter((_, x) => x !== i), '已删除，立即生效')
}
async function loadBlocklist() {
  try {
    const r = await getJSON('/api/assist/blocklist')
    blocklist.value = r.words || []
  } catch (e) { /* 静默：老版本后端无此端点 */ }
}
async function delBlockWord(w) {
  if (!confirm(`把「${w}」移出黑名单？以后拍照遇到这个词哈文会恢复自动识别记账`)) return
  try {
    await delJSON('/api/assist/blocklist', { word: w })
    blocklist.value = blocklist.value.filter(x => x !== w)
  } catch (e) { flash('移除失败：' + e.message, false) }
}
async function saveAssist() {
  try {
    await postJSON('/api/assist/settings', { settings: {
      extract_allow_cloud: assist.value.extract_allow_cloud,
      answer_allow_cloud: assist.value.answer_allow_cloud,
      vision_routing: assist.value.vision_routing,
    } })
    assistOk.value = true
    assistMsg.value = '✓ 已保存，下一条对话即生效'
  } catch (e) { assistOk.value = false; assistMsg.value = '保存失败：' + e.message }
  setTimeout(() => { assistMsg.value = '' }, 4000)
}

/* ── ★ 2026-09-21 (#825) 语音识别模型管理 ──（★ 2026-10-23 已整体迁往 ModelsPage 底部听音区，此处清理）
   onMounted 同步去掉 loadVoiceModels / loadVoiceCap（见文件尾） */

onMounted(() => { loadAll(true); loadLexicon(); loadBlocklist(); loadCkw(); loadCl(); loadInstName(); loadPcDevices(); loadRecStatus()
  fetch('/health').then(r => r.json()).then(j => { if (j && j.version) APP_VERSION.value = String(j.version).replace(/^v/, '') }).catch(() => {}) })
</script>

<style scoped>
/* ★ 2026-10-18 #751 学习词典：分组+芯片流样式（原 lex-list 一条一行已撤） */
.lex-groups { border: 1px solid var(--line); border-radius: 12px; padding: 4px 14px 10px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 8px; }
.lex-g-head { font-size: 12.5px; font-weight: 700; color: var(--ink); margin-top: 6px; }
.lex-g-head i { font-style: normal; font-weight: 400; color: var(--sub); margin-left: 5px; font-size: 11.5px; }
.lex-chips { display: flex; flex-wrap: wrap; gap: 7px; }
.lex-chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--ink); background: var(--c-bg-soft, #F5F6F8); border: 1px solid var(--line); border-radius: 16px; padding: 3px 6px 3px 11px; max-width: 100%; }
.lex-x { border: none; background: transparent; color: var(--sub); cursor: pointer; font-size: 12px; padding: 0 5px; border-radius: 50%; line-height: 1.4; }
.lex-x:hover { color: #D9534F; background: #FDF2F2; }
/* ★ 2026-10-23 通用页内小节标题（偏好/自动化，标签不拆） */
.gs-title { font-size: 13.5px; color: var(--sub); font-weight: 700; margin: 4px 0 8px; padding-bottom: 5px; border-bottom: 1px dashed var(--line); }
.pc-ops { display: flex; gap: 7px; flex-shrink: 0; }
/* ★ 2026-09-20 (#804) 手机与云端：配对码大字卡 */
.pair-box { border: 1px dashed var(--line); background: var(--bg-soft, #FAFBFD); border-radius: 14px; padding: 14px 18px; margin: 4px 0 12px; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.pair-code { font-size: 40px; letter-spacing: 12px; font-weight: 700; font-family: 'Consolas', monospace; }
.pair-qr { background: #fff; border-radius: 10px; padding: 8px; line-height: 0; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
.pair-qr img, .pair-qr canvas { display: block; }
.pair-box span b { color: var(--text, #222); }
.pair-box span { font-size: 12px; color: var(--sub); }
/* ★ 2026-09-20 (#808) 手机连接向导：两张场景卡 */
.mob-guide { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.mg-card { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 14px 16px; }
.mg-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 9px; flex-wrap: wrap; }
.mg-head b { font-size: 13.5px; }
.mg-tag { font-size: 11px; border-radius: 8px; padding: 2px 9px; white-space: nowrap; }
.mg-tag.free { background: #EDF3EC; color: #3C7A4E; }
.mg-tag.cloud { background: var(--mode-vertical-bg); color: var(--mode-vertical); }
.mg-steps { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 7px; font-size: 12.5px; color: var(--ink); line-height: 1.55; }
.mg-steps code { background: var(--c-bg-soft); border-radius: 6px; padding: 1px 7px; font-size: 12px; }
@media (max-width: 900px) { .mob-guide { grid-template-columns: 1fr; } }
/* ★ #952 在家二维码行 + 高级折叠 */
.home-qr-row { display: flex; gap: 16px; align-items: center; }
.home-qr-row .pair-qr { flex-shrink: 0; }
.home-qr-txt { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--sub); line-height: 1.55; }
.home-qr-txt b { font-size: 13.5px; color: var(--ink); }
.home-qr-txt code { background: var(--c-bg-soft); border-radius: 6px; padding: 1px 7px; font-size: 12px; }
@media (max-width: 640px) { .home-qr-row { flex-direction: column; align-items: flex-start; } }
.adv-toggle { cursor: pointer; user-select: none; background: var(--c-bg-soft); border-radius: 10px; }
.adv-toggle:hover { background: #eef1f6; }
.adv-toggle b { font-size: 13px; }
.lex-select { border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; font-size: 13px; background: #fff; color: var(--ink); }
.tip { margin-bottom: 10px; padding: 9px 13px; border-radius: 10px; font-size: 13px; }
.tip.ok { background: var(--mode-local-bg); color: var(--mode-local); }
.tip.bad { background: #FDF2F2; color: #B4443C; }

.anchors { display: flex; gap: 7px; margin-bottom: 16px; flex-wrap: wrap; }
.anchors button { display: flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff; border-radius: 20px; padding: 7px 16px; font-size: 13px; cursor: pointer; color: var(--sub); }
.anchors button.on { border-color: var(--blue); color: var(--blue); font-weight: 700; background: #F3F7FE; }
.lang-switch { display: flex; gap: 0; border: 1px solid var(--line); border-radius: 20px; overflow: hidden; flex-shrink: 0; }
.lang-switch button { border: none; background: #fff; padding: 7px 16px; font-size: 13px; cursor: pointer; color: var(--sub); font-family: inherit; }
.lang-switch button.on { background: var(--blue); color: #fff; font-weight: 700; }

.sec-body { display: flex; flex-direction: column; gap: 10px; max-width: 860px; }
.row { display: flex; align-items: center; gap: 14px; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 13px 16px; }
.row-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.row-main b { font-size: 13.5px; }
.row-main span { font-size: 12px; color: var(--sub); word-break: break-all; }
.row-main .mono { font-family: monospace; }
.pill { font-size: 11.5px; padding: 3px 10px; border-radius: 20px; background: #FDF2F2; color: #B4443C; white-space: nowrap; }
.pill.ok { background: var(--mode-local-bg); color: var(--mode-local); }
/* ★ 2026-10-23 voice-btns 样式随语音区迁往 ModelsPage */

.mode-cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 11px; }
.mcard { border: 1px solid var(--line); border-radius: 13px; background: #fff; padding: 14px 16px; display: flex; flex-direction: column; gap: 7px; }
.mcard.on { border-color: var(--green); box-shadow: 0 0 0 1px var(--green); }
.mcard b { font-size: 14px; }
.mcard p { font-size: 12px; color: var(--sub); margin: 0; line-height: 1.6; flex: 1; }
.cur-mark { color: var(--green); font-weight: 700; font-size: 13px; }
.cur-mark.warn { color: var(--amber); }
.key-input { border: 1px solid var(--line); border-radius: 9px; padding: 8px 11px; font-size: 13px; outline: none; }
.key-input:focus { border-color: var(--blue); }
.pwd-row .pwd-fields { display: flex; flex-direction: column; gap: 7px; margin-top: 9px; max-width: 320px; }
.pwd-hint { font-size: 11.5px; color: var(--sub); line-height: 1.6; margin-top: 4px; max-width: 340px; }
.pwd-row .key-input { width: 100%; box-sizing: border-box; }

/* ── ★ 关于页重做 ── */
.about-hero { display: flex; align-items: center; gap: 16px; background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 16px 20px; }
.about-duck { width: 56px; height: 56px; border-radius: 13px; object-fit: contain; flex-shrink: 0; }
.hero-main { flex: 1; display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.hero-main b { font-size: 16px; display: flex; align-items: center; gap: 8px; }
.hero-main > span { font-size: 12.5px; color: var(--sub); }
.ver-pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 20px; background: #F3F7FE; color: var(--blue); }
.update-box { display: flex; flex-direction: column; gap: 7px; align-items: flex-start; border-radius: 12px; padding: 12px 16px; font-size: 13px; background: var(--mode-local-bg); color: var(--mode-local); }
.update-box.warn { background: #FFF8E8; color: #8A6D1C; }
.update-box.bad { background: #FDF2F2; color: #B4443C; }
.update-box b { font-size: 13.5px; }
.update-box a { text-decoration: none; }
.update-box span { font-size: 12.5px; }
.update-box span a { color: var(--blue); }
/* ★ #1361 一键更新 */
.upd-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
/* ★ #1398 增量更新徽章 */
.delta-badge { display: inline-block; margin-left: 5px; padding: 1px 6px; border-radius: 8px; font-size: 10.5px; font-weight: 600; background: #1a7f37; color: #fff; vertical-align: middle; }
.one-upd-bar { width: 100%; height: 8px; border-radius: 4px; background: rgba(0,0,0,.08); overflow: hidden; }
.one-upd-bar .bar { height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--blue), #58b6ff); transition: width .6s ease; }
.one-upd-msg { width: 100%; font-size: 12.5px; line-height: 1.6; word-break: break-all; }
.one-upd-msg.ok { color: #1a7f37; }
.one-upd-msg.err { color: #c0392b; }
.changelog { margin: 0; font-family: inherit; font-size: 12px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; background: rgba(255,255,255,.55); border-radius: 8px; padding: 8px 11px; width: 100%; box-sizing: border-box; }
.about-sec { font-size: 13.5px; margin: 12px 0 2px; color: var(--ink); }
.about-sec::before { content: ''; }
.changelog-card { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; }
.cl-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.cl-head b { font-size: 13.5px; }
.cl-date { font-size: 11.5px; color: var(--sub); margin-left: auto; }
.cur-pill { font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 20px; background: var(--mode-local-bg); color: var(--mode-local); }
.changelog-card ul { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 3px; }
.changelog-card li { font-size: 12.5px; color: var(--sub); line-height: 1.6; }
.hub-links { display: flex; gap: 8px; flex-shrink: 0; }
.hub-links a { text-decoration: none; }

@media (max-width: 760px) { .about-hero { flex-wrap: wrap; } }

@media (max-width: 760px) { .mode-cards { grid-template-columns: 1fr; } .row { flex-direction: column; align-items: stretch; } }

/* ★ 2026-10-20 权限中心 */
.perm-hint { font-size: 13px; color: var(--tx2, #888); margin-bottom: 10px; }
.perm-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px dashed var(--line, #eee); }
.perm-row .row-main b { display: block; font-size: 14px; }
.perm-row .row-main span { font-size: 12px; color: var(--tx2, #999); }
.perm-select { min-width: 110px; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--line, #ddd); background: var(--bg2, #fff); font-size: 13px; }
.perm-switch { position: relative; display: inline-block; width: 44px; height: 24px; flex: none; }
.perm-switch input { opacity: 0; width: 0; height: 0; }
.perm-switch .slider { position: absolute; inset: 0; border-radius: 24px; background: #c8c8c8; transition: .2s; cursor: pointer; }
.perm-switch .slider::before { content: ''; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px; border-radius: 50%; background: #fff; transition: .2s; }
.perm-switch input:checked + .slider { background: #22a06b; }
.perm-switch input:checked + .slider::before { transform: translateX(20px); }
.perm-switch.off { opacity: .4; }
</style>
