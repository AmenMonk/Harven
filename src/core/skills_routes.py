# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - Skills 管理路由

提供技能管理的 RESTful API：
- GET  /api/skills              列出所有技能
- POST /api/skills              创建技能
- PUT  /api/skills/<skill_id>   更新技能
- DEL  /api/skills/<skill_id>   删除技能
- POST /api/skills/<skill_id>/toggle  启用/禁用技能
- POST /api/skills/reload             重新加载所有技能文件
- GET  /api/skills/hot-reload/status  查看热更新状态
- POST /api/skills/hot-reload/toggle  启用/禁用热更新

★ 中级Skill脚本路由：
- GET  /api/skills/scripts              列出所有有脚本的技能
- POST /api/skills/scripts/<name>/run    执行技能脚本
- GET  /api/skills/scripts/<name>/info   获取技能脚本信息
"""

import logging
from flask import Blueprint, request, jsonify

from src.core.skills_manager import get_skills_manager

logger = logging.getLogger(__name__)

bp = Blueprint('skills', __name__, url_prefix='/api')


# ═══════════════════════════════════════════════════════════════════════
# 技能 CRUD
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills', methods=['GET'])
def list_skills():
    """列出所有技能"""
    try:
        mgr = get_skills_manager()
        skills = mgr.list_skills()
        return jsonify({'success': True, 'skills': skills})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 列出技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills', methods=['POST'])
def create_skill():
    """创建技能"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ['name', 'description', 'triggers', 'prompt']
        for field in required:
            if field not in data:
                return jsonify({'success': False, 'error': f'缺少必需字段: {field}'}), 400

        mgr = get_skills_manager()
        result = mgr.create_skill(
            name=data['name'],
            description=data['description'],
            triggers=data['triggers'],
            prompt=data['prompt'],
            author=data.get('author', 'user'),
            version=data.get('version', '1.0'),
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] 创建技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/<skill_id>', methods=['PUT'])
def update_skill(skill_id):
    """更新技能"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        mgr = get_skills_manager()
        result = mgr.update_skill(skill_id, **data)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] 更新技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/<skill_id>', methods=['DELETE'])
def delete_skill(skill_id):
    """删除技能"""
    try:
        mgr = get_skills_manager()
        result = mgr.delete_skill(skill_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] 删除技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/<skill_id>/toggle', methods=['POST'])
def toggle_skill(skill_id):
    """启用/禁用技能"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        enabled = data.get('enabled', True)
        mgr = get_skills_manager()
        result = mgr.toggle_skill(skill_id, enabled)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] 切换技能状态失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# ★ 2026-07-13: Skill Builder — 模板+预览+确认
# ═══════════════════════════════════════════════════════════════════════

# Skill模板库
SKILL_TEMPLATES = {
    'faq': {
        'name': 'FAQ问答Skill',
        'icon': '❓',
        'description': '常见问题自动回答',
        'template': {
            'name': '',
            'description': '常见问题自动回答',
            'triggers': [],
            'prompt': (
                '## 角色与任务\n'
                '你是「{skill_name}」的问答助手。\n'
                '用户提问时，基于以下知识回答：\n\n'
                '## 知识库\n'
                '{knowledge}\n\n'
                '## 规则\n'
                '1. 基于知识库回答，不编造\n'
                '2. 如果知识库没有相关内容，诚实说"我不确定"\n'
                '3. 回答简洁明了\n'
            ),
        },
    },
    'workflow': {
        'name': '流程指引Skill',
        'icon': '📋',
        'description': '指导用户完成多步骤流程',
        'template': {
            'name': '',
            'description': '流程指引',
            'triggers': [],
            'prompt': (
                '## 角色与任务\n'
                '你是「{skill_name}」的流程指引助手。\n'
                '用户需要完成以下流程：\n\n'
                '## 流程步骤\n'
                '{steps}\n\n'
                '## 规则\n'
                '1. 按步骤指导用户\n'
                '2. 每完成一步 → 确认后再进入下一步\n'
                '3. 如果用户遇到问题 → 提供解决方案\n'
            ),
        },
    },
    'rule': {
        'name': '规则检查Skill',
        'icon': '📐',
        'description': '根据规则检查/验证内容',
        'template': {
            'name': '',
            'description': '规则检查',
            'triggers': [],
            'prompt': (
                '## 角色与任务\n'
                '你是「{skill_name}」的规则检查助手。\n'
                '根据以下规则检查用户输入：\n\n'
                '## 规则\n'
                '{rules}\n\n'
                '## 输出格式\n'
                '✅ 符合规则的部分\n'
                '❌ 不符合规则的部分 + 修改建议\n'
            ),
        },
    },
    'custom': {
        'name': '自定义Skill',
        'icon': '✨',
        'description': '完全自定义的Skill',
        'template': {
            'name': '',
            'description': '自定义技能',
            'triggers': [],
            'prompt': (
                '## 角色与任务\n'
                '你是「{skill_name}」助手。\n'
                '{custom_content}\n'
            ),
        },
    },
}


@bp.route('/skills/templates', methods=['GET'])
def get_skill_templates():
    """获取Skill模板列表"""
    templates = []
    for key, tpl in SKILL_TEMPLATES.items():
        templates.append({
            'id': key,
            'name': tpl['name'],
            'icon': tpl['icon'],
            'description': tpl['description'],
        })
    return jsonify({'success': True, 'templates': templates})


@bp.route('/skills/draft', methods=['POST'])
def create_skill_draft():
    """生成Skill草案（不保存）→ 用户预览后确认"""
    data = request.get_json(force=True, silent=True) or {}
    template_id = data.get('template', 'custom')
    skill_name = data.get('name', '').strip()
    triggers = data.get('triggers', [])
    content = data.get('content', '')

    if not skill_name:
        return jsonify({'success': False, 'error': '请输入Skill名称'}), 400

    # 获取模板
    tpl = SKILL_TEMPLATES.get(template_id, SKILL_TEMPLATES['custom'])
    tpl_data = tpl['template'].copy()

    # 填充模板
    prompt = tpl_data['prompt']
    prompt = prompt.replace('{skill_name}', skill_name)
    prompt = prompt.replace('{knowledge}', content or '（请在这里填写知识内容）')
    prompt = prompt.replace('{steps}', content or '（请在这里填写流程步骤）')
    prompt = prompt.replace('{rules}', content or '（请在这里填写规则）')
    prompt = prompt.replace('{custom_content}', content or '（请在这里填写自定义内容）')

    # 触发词
    if not triggers:
        triggers = [skill_name.lower()]

    # 生成草案（不保存）
    draft = {
        'name': skill_name,
        'description': tpl_data['description'],
        'triggers': triggers,
        'prompt': prompt,
        'template': template_id,
        'preview': f"---\nname: {skill_name}\ndescription: {tpl_data['description']}\ntriggers: {triggers}\n---\n{prompt}",
    }

    return jsonify({'success': True, 'draft': draft})


@bp.route('/skills/confirm', methods=['POST'])
def confirm_skill():
    """用户确认后保存Skill"""
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name', '').strip()
    description = data.get('description', '')
    triggers = data.get('triggers', [])
    prompt = data.get('prompt', '')

    if not name or not prompt:
        return jsonify({'success': False, 'error': '名称和内容不能为空'}), 400

    mgr = get_skills_manager()
    result = mgr.create_skill(
        name=name,
        description=description,
        triggers=triggers,
        prompt=prompt,
        author='user',
        version='1.0',
    )
    return jsonify(result)


@bp.route('/skills/list', methods=['GET'])
def list_skills_alias():
    """前端兼容：/api/skills/list → /api/skills"""
    return list_skills()


@bp.route('/skills/<skill_id>/uninstall', methods=['DELETE'])
def uninstall_skill(skill_id):
    """卸载技能（前端兼容：/api/skills/<id>/uninstall → /api/skills/<id> DELETE）"""
    return delete_skill(skill_id)


@bp.route('/skills/install', methods=['POST'])
def install_skill():
    """安装技能（从 HarvenHub / GitHub / 本地）"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        source = data.get('source', '')
        mgr = get_skills_manager()

        if source == 'harvenhub':
            skill_name = data.get('skill_name', '').strip()
            if not skill_name:
                return jsonify({'success': False, 'error': '请输入技能名称'}), 400
            result = mgr.install_from_harvenhub(skill_name)
            return jsonify(result)

        elif source == 'github':
            url = data.get('url', '').strip()
            if not url:
                return jsonify({'success': False, 'error': '请输入 GitHub URL'}), 400
            result = mgr.install_from_github(url)
            return jsonify(result)

        elif source == 'local':
            path = data.get('path', '').strip()
            if not path:
                return jsonify({'success': False, 'error': '请输入本地路径'}), 400
            result = mgr.install_from_local(path)
            return jsonify(result)

        else:
            return jsonify({'success': False, 'error': f'不支持的安装来源: {source}'}), 400

    except Exception as e:
        logger.error(f'[SkillsRoutes] 安装技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/install-url', methods=['POST'])
def install_skill_from_url():
    """从 URL 安装技能（统一入口，支持 .md / .zip 直链）

    Request body:
        url: 技能文件直链（.md 或 .zip）
        name: 可选，技能名称
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        url = data.get('url', '').strip()
        if not url:
            return jsonify({'success': False, 'error': '请输入 URL'}), 400

        skill_name = data.get('name', '').strip() or None
        mgr = get_skills_manager()
        result = mgr.install_from_url(url, skill_name)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] URL 安装失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/harvenhub/search', methods=['GET'])
def search_skills_harvenhub():
    """在 HarvenHub 搜索技能"""
    try:
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'success': True, 'skills': []})
        mgr = get_skills_manager()
        skills = mgr.search_harvenhub(query)
        return jsonify({'success': True, 'skills': skills})
    except Exception as e:
        logger.error(f'[SkillsRoutes] HarvenHub 搜索失败: {e}')
        return jsonify({'success': False, 'error': str(e), 'skills': []}), 500


@bp.route('/skills/check-updates', methods=['POST'])
def check_skill_updates():
    """检查技能更新"""
    try:
        mgr = get_skills_manager()
        updates = mgr.check_updates()
        return jsonify({'success': True, 'updates': updates})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 检查技能更新失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# 技能匹配测试
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills/match', methods=['POST'])
def match_skill():
    """测试消息匹配技能"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = data.get('message', '')
        if not message:
            return jsonify({'success': False, 'error': '消息内容不能为空'}), 400
        mgr = get_skills_manager()
        skill = mgr.match_skill(message)
        if skill:
            return jsonify({'success': True, 'matched': True, 'skill': skill.to_dict()})
        return jsonify({'success': True, 'matched': False})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 技能匹配失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/reload', methods=['POST'])
def reload_skills():
    """重新加载所有技能文件"""
    try:
        mgr = get_skills_manager()
        mgr.reload()
        skills = mgr.list_skills()
        return jsonify({'success': True, 'count': len(skills), 'message': '技能已重新加载'})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 重载技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# 热更新状态
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills/hot-reload/status', methods=['GET'])
def hot_reload_status():
    """查看热更新状态"""
    try:
        from src.core.skills_manager import _WATCHDOG_AVAILABLE
        mgr = get_skills_manager()
        reloader = mgr._hot_reloader
        status = {
            'enabled': reloader is not None and reloader._running,
            'watchdog_installed': _WATCHDOG_AVAILABLE,
            'monitoring_dir': getattr(reloader, '_skills_dir', None),
        }
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 查看热更新状态失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/hot-reload/toggle', methods=['POST'])
def hot_reload_toggle():
    """启用/禁用热更新"""
    try:
        from src.core.skills_manager import _WATCHDOG_AVAILABLE
        data = request.get_json(force=True, silent=True) or {}
        enabled = data.get('enabled', True)
        if enabled and not _WATCHDOG_AVAILABLE:
            return jsonify({
                'success': False,
                'error': 'watchdog 未安装，无法启用热更新（pip install watchdog）'
            }), 400
        mgr = get_skills_manager()
        if enabled:
            mgr._start_hot_reload()
        else:
            mgr.stop_hot_reload()
        return jsonify({'success': True, 'enabled': enabled})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 切换热更新状态失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# 内置技能安装（出厂预装 Skill 文件 → skills/ 目录）
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills/install-builtin', methods=['POST'])
def install_builtin_skill():
    """从 data/builtin_skills/ 复制 Skill 文件到 skills/ 目录"""
    try:
        import shutil
        from pathlib import Path
        data = request.get_json(silent=True) or {}
        skill_id = data.get('skill_id', '')
        if not skill_id:
            return jsonify({'success': False, 'error': '缺少 skill_id'}), 400

        builtin_dir = Path(__file__).parent.parent.parent / 'data' / 'builtin_skills'
        skills_dir = Path(__file__).parent.parent.parent / 'data' / 'skills'
        skills_dir.mkdir(parents=True, exist_ok=True)

        source = builtin_dir / f'{skill_id}.md'
        if not source.exists():
            return jsonify({'success': False, 'error': f'内置技能 {skill_id} 不存在'}), 404

        dest = skills_dir / f'{skill_id}.md'
        shutil.copy2(str(source), str(dest))

        # 重新加载技能
        mgr = get_skills_manager()
        if hasattr(mgr, 'reload'):
            mgr.reload()
        else:
            from src.core.skills_manager import reset_skills_manager
            reset_skills_manager()

        logger.info(f'[SkillsRoutes] 内置技能 {skill_id} 安装成功')
        return jsonify({'success': True, 'message': f'技能 {skill_id} 安装成功'})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 安装内置技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# Skill Builder — AI 制作向导
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills/builder/categories', methods=['GET'])
def skill_builder_categories():
    """获取 Skill 类别列表"""
    try:
        from src.core.skill_builder import get_skill_builder
        builder = get_skill_builder()
        return jsonify({'success': True, 'categories': builder.list_categories()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/builder/generate', methods=['POST'])
def skill_builder_generate():
    """
    AI 生成 Skill 代码

    Request body:
        name: Skill 名称
        description: 功能描述（自然语言）
        category: 类别（可选，不传则自动检测）
        extra_info: 额外信息（可选）
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()

        if not name:
            return jsonify({'success': False, 'error': '请输入 Skill 名称'}), 400
        if not description:
            return jsonify({'success': False, 'error': '请描述 Skill 的功能'}), 400

        from src.core.skill_builder import get_skill_builder
        builder = get_skill_builder()

        category = data.get('category', '').strip() or None
        extra_info = data.get('extra_info', '')
        triggers = data.get('triggers', '')  # 用户自定义触发词

        if not category:
            category = builder.detect_skill_category(description)

        # ★ 2026-07-14: 中级Skill生成独立通道
        if category == "intermediate":
            result = builder.generate_intermediate_skill(
                name=name,
                description=description,
                extra_info=extra_info,
                triggers=triggers,
            )
            return jsonify({
                'success': True,
                'category': 'intermediate',
                'skill_md': result['skill_md'],
                'main_py': result['main_py'],
                'skill_id': result['skill_id'],
                'name': name,
                'detected_category': 'intermediate',
            })

        code = builder.generate_skill_code(
            name=name,
            description=description,
            category=category,
            extra_info=extra_info,
        )

        # 验证代码
        valid, msg = builder.validate_skill_code(code)
        detected_category = category

        return jsonify({
            'success': True,
            'code': code,
            'valid': valid,
            'validation_msg': msg,
            'detected_category': detected_category,
            'name': name,
        })
    except Exception as e:
        logger.error(f'[SkillsRoutes] Skill 生成失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/builder/save', methods=['POST'])
def skill_builder_save():
    """保存生成的 Skill 到 skills/ 目录"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        code = data.get('code', '').strip()
        name = data.get('name', '').strip()
        skill_id = data.get('skill_id', '').strip()

        if not code:
            return jsonify({'success': False, 'error': '代码不能为空'}), 400
        if not name:
            return jsonify({'success': False, 'error': '名称不能为空'}), 400

        from src.core.skill_builder import get_skill_builder
        builder = get_skill_builder()

        # 验证
        valid, msg = builder.validate_skill_code(code)
        if not valid:
            return jsonify({'success': False, 'error': f'代码验证失败: {msg}'}), 400

        # 保存
        if not skill_id:
            import uuid
            skill_id = f"skill_{uuid.uuid4().hex[:8]}"

        success, msg = builder.save_skill(skill_id, code, name)

        # 重新加载 skills 注册表
        try:
            from src.core.skills_manager import get_skills_manager
            mgr = get_skills_manager()
            mgr.reload()
        except Exception as _e: logger.debug(f"[skills_routes] 忽略: {_e}")

        return jsonify({
            'success': success,
            'message': msg,
            'skill_id': skill_id,
        })
    except Exception as e:
        logger.error(f'[SkillsRoutes] Skill 保存失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/builder/validate', methods=['POST'])
def skill_builder_validate():
    """验证 Skill 代码合法性"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        code = data.get('code', '').strip()
        if not code:
            return jsonify({'success': False, 'error': '代码不能为空'}), 400

        from src.core.skill_builder import get_skill_builder
        builder = get_skill_builder()
        valid, msg = builder.validate_skill_code(code)

        return jsonify({'success': True, 'valid': valid, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/builder/intermediate/save', methods=['POST'])
def skill_builder_intermediate_save():
    """
    保存中级 Skill（SKILL.md + scripts/main.py 到 data/skills/ 目录）

    Request body:
        skill_id: Skill 标识
        name: Skill 名称
        skill_md: SKILL.md 内容
        main_py: scripts/main.py 内容
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        skill_id = data.get('skill_id', '').strip()
        name = data.get('name', '').strip()
        skill_md = data.get('skill_md', '').strip()
        main_py = data.get('main_py', '').strip()

        if not skill_id:
            return jsonify({'success': False, 'error': 'skill_id 不能为空'}), 400
        if not name:
            return jsonify({'success': False, 'error': '名称不能为空'}), 400
        if not skill_md:
            return jsonify({'success': False, 'error': 'SKILL.md 内容不能为空'}), 400
        if not main_py:
            return jsonify({'success': False, 'error': 'main.py 内容不能为空'}), 400

        from src.core.skill_builder import get_skill_builder
        builder = get_skill_builder()

        success, msg = builder.save_intermediate_skill(
            skill_id=skill_id,
            name=name,
            skill_md=skill_md,
            main_py=main_py,
        )

        # 重新加载 skills 注册表
        if success:
            try:
                from src.core.skills_manager import reset_skills_manager
                reset_skills_manager()
            except Exception as _e: logger.debug(f"[skills_routes] 忽略: {_e}")

        return jsonify({
            'success': success,
            'message': msg,
            'skill_id': skill_id,
        })
    except Exception as e:
        logger.error(f'[SkillsRoutes] 中级Skill 保存失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# ★ 2026-07-14: 中级Skill脚本执行路由
# ═══════════════════════════════════════════════════════════════════════

@bp.route('/skills/scripts', methods=['GET'])
def list_scripted_skills():
    """列出所有拥有可执行脚本的技能"""
    try:
        from src.core.scripts_runner import list_skill_scripts
        scripts = list_skill_scripts()
        return jsonify({'success': True, 'scripts': scripts, 'count': len(scripts)})
    except Exception as e:
        logger.error(f'[SkillsRoutes] 列出脚本技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/scripts/<skill_name>/run', methods=['POST'])
def run_skill_script_route(skill_name):
    """执行技能脚本"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        action = data.get('action', 'run')
        params = data.get('params', {})

        from src.core.scripts_runner import run_skill_script
        result = run_skill_script(skill_name, action=action, params=params)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[SkillsRoutes] 执行技能脚本失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/scripts/<skill_name>/info', methods=['GET'])
def get_skill_script_info_route(skill_name):
    """获取技能脚本信息"""
    try:
        from src.core.scripts_runner import get_skill_script_info
        info = get_skill_script_info(skill_name)
        if info:
            return jsonify({'success': True, 'info': info})
        return jsonify({'success': False, 'error': f'技能 {skill_name} 不存在或无脚本'}), 404
    except Exception as e:
        logger.error(f'[SkillsRoutes] 获取脚本信息失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/scripts/upload', methods=['POST'])
def upload_skill_with_scripts():
    """上传带脚本的中级Skill（ZIP包：SKILL.md + scripts/目录）"""
    try:
        import zipfile, shutil
        from pathlib import Path
        from werkzeug.utils import secure_filename

        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请上传ZIP文件'}), 400

        file = request.files['file']
        if not file.filename or not file.filename.endswith('.zip'):
            return jsonify({'success': False, 'error': '只支持.zip格式'}), 400

        # 安全文件名
        skill_name = request.form.get('name', '').strip() or secure_filename(file.filename.replace('.zip', ''))

        # 解压到临时目录然后再移动
        skills_dir = Path(__file__).parent.parent.parent / 'data' / 'skills'
        skill_dir = skills_dir / skill_name

        if skill_dir.exists():
            return jsonify({'success': False, 'error': f'技能 {skill_name} 已存在'}), 409

        temp_dir = Path(__file__).parent.parent.parent / 'data' / 'temp_skill_upload'
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 解压
            with zipfile.ZipFile(file, 'r') as zf:
                zf.extractall(temp_dir)

            # 查找 SKILL.md
            skill_md = None
            for f in temp_dir.rglob('SKILL.md'):
                skill_md = f
                break

            if not skill_md:
                return jsonify({'success': False, 'error': 'ZIP中未找到 SKILL.md 文件'}), 400

            # 移动整个目录
            parent_dir = skill_md.parent
            if parent_dir == temp_dir:
                # 文件在根目录
                shutil.copytree(temp_dir, skill_dir)
            else:
                # 文件在子目录
                shutil.copytree(parent_dir, skill_dir)

            # 重新加载技能
            mgr = get_skills_manager()
            mgr.reload()

            return jsonify({
                'success': True,
                'message': f'技能 {skill_name} 上传成功',
                'skill_name': skill_name,
            })

        finally:
            # 清理临时目录
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f'[SkillsRoutes] 上传技能失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
