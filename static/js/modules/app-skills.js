/**
 * Skill Builder — 可视化创建向导
 * 功能：纯提示词 Skill + 中级脚本 Skill 的创建、预览、保存
 *
 * 注意：技能列表/安装/卸载等功能已统一迁移到 app-plugins.js
 *       本文件只保留「新建技能」的 Builder 对话框。
 */

// ═══════════════════════════════════════════════════════════════
// ★ 2026-07-14: Skill Builder 可视化向导（P1）
// ═══════════════════════════════════════════════════════════════

function showCreateSkillDialog() {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `
        <div class="modal" style="max-width:600px">
            <div class="modal-header">
                <h4>✏️ 新建技能</h4>
                <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">×</button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>技能类型</label>
                    <select id="builder-skill-type" onchange="onBuilderSkillTypeChange()">
                        <option value="prompt">📝 纯提示词 Skill</option>
                        <option value="intermediate">🚀 中级脚本 Skill（可执行+数据持久化）</option>
                    </select>
                </div>
                <div id="builder-form-content"></div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    onBuilderSkillTypeChange();
}

function onBuilderSkillTypeChange() {
    const type = document.getElementById('builder-skill-type').value;
    const contentEl = document.getElementById('builder-form-content');

    if (type === 'prompt') {
        contentEl.innerHTML = `
            <div class="skill-info-box" style="background:#f5f5f5;padding:10px;border-radius:6px;margin-bottom:12px;font-size:13px">
                纯提示词 Skill：由 Markdown 系统提示词 + 触发关键词组成，匹配后自动注入对话。
                适用于：写作风格、翻译规则、代码规范、角色扮演等。
            </div>
            <div class="form-group">
                <label>技能名称</label>
                <input type="text" id="builder-name" placeholder="例如：✍️ 写作助手">
            </div>
            <div class="form-group">
                <label>描述</label>
                <input type="text" id="builder-desc" placeholder="简短描述这个技能的用途">
            </div>
            <div class="form-group">
                <label>触发词（逗号分隔）</label>
                <input type="text" id="builder-triggers" placeholder="例如：帮我写,写一篇,写作,润色">
            </div>
            <div class="form-group">
                <label>系统提示词</label>
                <textarea id="builder-prompt" rows="6" placeholder="技能激活时注入给AI的指令..."></textarea>
            </div>
            <button class="btn btn-primary btn-sm" onclick="savePromptSkill()">💾 保存</button>
        `;
    } else {
        contentEl.innerHTML = `
            <div class="skill-info-box" style="background:#fef9e7;padding:10px;border-radius:6px;margin-bottom:12px;font-size:13px">
                <strong>🚀 中级脚本 Skill</strong>：SKILL.md + scripts/main.py，支持 SQLite 数据持久化。
                AI 会根据你的描述自动生成代码。<br>
                适用于：记账、习惯追踪、定时提醒、数据统计等需要存储数据的场景。
            </div>
            <div id="builder-intermediate-form">
                <div class="form-group">
                    <label>技能名称 <span style="color:red">*</span></label>
                    <input type="text" id="builder-name" placeholder="例如：记账助手">
                </div>
                <div class="form-group">
                    <label>触发关键词 <span style="color:red">*</span> <small style="color:#888">（逗号分隔，用户说这些话时自动激活）</small></label>
                    <input type="text" id="builder-triggers" placeholder="例如：记账,花了,消费,记一笔,账单">
                </div>
                <div class="form-group">
                    <label>功能描述（用自然语言描述你想做什么）</label>
                    <textarea id="builder-desc" rows="4" placeholder="例如：帮我记录日常收支，能添加收入/支出、查询历史记录、按类别统计汇总"></textarea>
                </div>
                <div class="form-group">
                    <label>额外信息（可选）</label>
                    <textarea id="builder-extra" rows="2" placeholder="补充说明，如需要的特殊字段、触发条件等"></textarea>
                </div>
                <button class="btn btn-success btn-sm" onclick="generateIntermediateSkill()">🤖 AI 生成代码</button>
            </div>
            <div id="builder-preview" style="margin-top:12px;display:none">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                    <strong>📋 预览生成结果</strong>
                    <button class="btn btn-xs btn-outline-secondary" onclick="cancelBuilderPreview()" title="返回修改">✏️ 返回修改</button>
                </div>
                <div style="margin-bottom:8px">
                    <div style="font-weight:600;font-size:13px;margin-bottom:4px">📄 SKILL.md</div>
                    <pre id="builder-preview-md" style="background:#2d2d2d;color:#f8f8f2;padding:10px;border-radius:4px;max-height:200px;overflow:auto;font-size:11px;white-space:pre-wrap"></pre>
                </div>
                <div style="margin-bottom:10px">
                    <div style="font-weight:600;font-size:13px;margin-bottom:4px">🐍 scripts/main.py</div>
                    <pre id="builder-preview-py" style="background:#2d2d2d;color:#f8f8f2;padding:10px;border-radius:4px;max-height:200px;overflow:auto;font-size:11px;white-space:pre-wrap"></pre>
                </div>
                <button class="btn btn-primary btn-sm" onclick="saveIntermediateSkill()">✅ 保存并启用</button>
            </div>
        `;
    }
}

/**
 * 保存纯提示词 Skill
 */
async function savePromptSkill() {
    const name = document.getElementById('builder-name').value.trim();
    const desc = document.getElementById('builder-desc').value.trim();
    const triggers = document.getElementById('builder-triggers').value.trim();
    const prompt = document.getElementById('builder-prompt').value.trim();

    if (!name || !prompt) { alert('请填写技能名称和系统提示词'); return; }

    try {
        const response = await fetch('/api/skills', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                description: desc,
                triggers: triggers,
                prompt: prompt,
            }),
        });

        const data = await response.json();
        if (data.success) {
            alert('技能创建成功');
            document.querySelector('.modal-overlay')?.remove();
            // 刷新技能列表（在 app-plugins.js 中定义）
            if (typeof refreshSkills === 'function') await refreshSkills();
        } else {
            alert('创建失败: ' + (data.error || '未知错误'));
        }
    } catch (error) {
        alert('网络错误: ' + error.message);
    }
}

/**
 * AI 生成中级脚本 Skill
 */
let _builderGeneratedData = null;

async function generateIntermediateSkill() {
    const name = document.getElementById('builder-name').value.trim();
    const triggers = document.getElementById('builder-triggers')?.value.trim() || '';
    const desc = document.getElementById('builder-desc').value.trim();
    const extra = document.getElementById('builder-extra')?.value.trim() || '';

    if (!name) { alert('请填写技能名称'); return; }
    if (!triggers) { alert('请填写触发关键词，让AI知道什么时候该激活这个技能'); return; }
    if (!desc) { alert('请填写功能描述'); return; }

    const preview = document.getElementById('builder-preview');
    const formEl = document.getElementById('builder-intermediate-form');
    const mdPre = document.getElementById('builder-preview-md');
    const pyPre = document.getElementById('builder-preview-py');

    preview.style.display = 'block';
    if (formEl) formEl.style.display = 'none';
    mdPre.textContent = '⏳ AI 正在生成代码...';
    pyPre.textContent = '⏳ 请稍候...';

    try {
        const response = await fetch('/api/skills/builder/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                description: desc,
                extra_info: extra,
                triggers: triggers,
                category: 'intermediate',
            }),
        });

        const data = await response.json();
        if (data.success && data.category === 'intermediate') {
            _builderGeneratedData = data;
            mdPre.textContent = data.skill_md || '';
            pyPre.textContent = data.main_py || '';
        } else if (data.success) {
            alert('AI 判断此技能更适合纯提示词形式，已自动切换。请在上方选择"纯提示词 Skill"重新创建。');
            preview.style.display = 'none';
            if (formEl) formEl.style.display = 'block';
        } else {
            mdPre.textContent = '生成失败: ' + (data.error || '未知错误');
            pyPre.textContent = '';
        }
    } catch (error) {
        mdPre.textContent = '网络错误: ' + error.message;
        pyPre.textContent = '';
    }
}

/**
 * 取消预览，返回修改
 */
function cancelBuilderPreview() {
    const preview = document.getElementById('builder-preview');
    const formEl = document.getElementById('builder-intermediate-form');
    if (preview) preview.style.display = 'none';
    if (formEl) formEl.style.display = 'block';
    _builderGeneratedData = null;
}

/**
 * 保存中级脚本 Skill
 */
async function saveIntermediateSkill() {
    if (!_builderGeneratedData) {
        alert('请先生成代码');
        return;
    }

    try {
        const response = await fetch('/api/skills/builder/intermediate/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                skill_md: _builderGeneratedData.skill_md,
                main_py: _builderGeneratedData.main_py,
                skill_id: _builderGeneratedData.skill_id,
                name: _builderGeneratedData.name,
            }),
        });

        const data = await response.json();
        if (data.success) {
            alert('中级技能创建成功！');
            document.querySelector('.modal-overlay')?.remove();
            _builderGeneratedData = null;
            if (typeof refreshSkills === 'function') await refreshSkills();
        } else {
            alert('保存失败: ' + (data.error || '未知错误'));
        }
    } catch (error) {
        alert('网络错误: ' + error.message);
    }
}

// ===== Window 全局导出 =====
window.showCreateSkillDialog = showCreateSkillDialog;
window.onBuilderSkillTypeChange = onBuilderSkillTypeChange;
window.savePromptSkill = savePromptSkill;
window.generateIntermediateSkill = generateIntermediateSkill;
window.cancelBuilderPreview = cancelBuilderPreview;
window.saveIntermediateSkill = saveIntermediateSkill;
