"""
新功能 API 路由 — Harven AI
整合：浏览器自动化 / 代码沙箱 / 定时任务 / Active Memory / 多搜索引擎 / 工作流

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import json
import uuid
import traceback
from datetime import datetime
from pathlib import Path
from flask import request, jsonify
import logging
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent


def register_new_feature_routes(app):
    """注册所有新功能 API"""

    # ─────────────── 浏览器自动化 ───────────────

    @app.route('/api/browser/execute', methods=['POST'])
    def browser_execute():
        """执行浏览器任务"""
        data = request.get_json() or {}
        instruction = data.get('instruction', '').strip()
        if not instruction:
            return jsonify({"success": False, "error": "指令不能为空"})

        try:
            from src.core.browser_agent import get_browser_agent, parse_browser_intent
            intent = parse_browser_intent(instruction)
            if intent is None:
                return jsonify({"success": False, "error": "未识别到浏览器操作意图"})

            agent = get_browser_agent()
            if not agent.is_available():
                return jsonify({
                    "success": False,
                    "error": "Playwright 未安装",
                    "install_cmd": "pip install playwright && playwright install chromium"
                })

            action = intent.get("action")
            if action == "navigate":
                result = agent.navigate(intent["url"])
            elif action == "search":
                result = agent.search(intent["query"])
            elif action == "screenshot":
                result = agent.screenshot()
            elif action == "fetch_content":
                result = agent.fetch_content(intent.get("url", ""))
            else:
                return jsonify({"success": False, "error": f"不支持的操作：{action}"})

            return jsonify({
                "success": result.success,
                "data": result.data,
                "error": result.error
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/current', methods=['GET'])
    def browser_current():
        """获取当前页面信息"""
        try:
            from src.core.browser_agent import get_browser_agent
            agent = get_browser_agent()
            return jsonify({"success": True, "page": agent.get_current_info()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/screenshot', methods=['POST'])
    def browser_screenshot():
        """截图当前页面"""
        try:
            from src.core.browser_agent import get_browser_agent
            agent = get_browser_agent()
            result = agent.screenshot()
            return jsonify({"success": result.success, "data": result.data, "error": result.error})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/close', methods=['POST'])
    def browser_close():
        """关闭浏览器"""
        try:
            from src.core.browser_agent import get_browser_agent
            get_browser_agent().close()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/status', methods=['GET'])
    def browser_status():
        """检测Playwright和Chromium安装状态"""
        import importlib.util
        playwright_installed = importlib.util.find_spec("playwright") is not None
        chromium_available = False
        if playwright_installed:
            try:
                import subprocess
                result = subprocess.run(
                    ["python", "-c", "from playwright.sync_api import sync_playwright; p=sync_playwright().__enter__(); p.chromium.launch(headless=True).close(); print('ok')"],
                    capture_output=True, text=True, timeout=15
                )
                chromium_available = 'ok' in result.stdout
            except Exception:
                # 尝试检测本地chromium路径
                import pathlib
                pw_path = pathlib.Path.home() / '.cache' / 'ms-playwright'
                chromium_available = any(pw_path.glob('chromium-*')) if pw_path.exists() else False
        return jsonify({
            "success": True,
            "playwright_installed": playwright_installed,
            "chromium_available": chromium_available
        })

    @app.route('/api/browser/install', methods=['POST'])
    def browser_install():
        """安装Playwright Chromium"""
        import subprocess
        import sys
        try:
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                return jsonify({"success": True, "output": result.stdout})
            else:
                return jsonify({"success": False, "error": result.stderr or result.stdout})
        except subprocess.TimeoutExpired:
            return jsonify({"success": False, "error": "安装超时（5分钟），请手动运行安装命令"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/navigate', methods=['POST'])
    def browser_navigate():
        """导航到指定URL"""
        data = request.get_json() or {}
        url = data.get('url', '').strip()
        if not url:
            return jsonify({"success": False, "error": "URL不能为空"})
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        try:
            from src.core.browser_agent import get_browser_agent
            agent = get_browser_agent()
            if not agent.is_available():
                return jsonify({"success": False, "error": "Playwright未安装，请先安装"})
            result = agent.navigate(url)
            return jsonify({"success": result.success, "title": result.data.get("title","") if result.data else "", "error": result.error})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/browser/content', methods=['POST'])
    def browser_content():
        """获取当前页面文本内容（无需URL参数）"""
        try:
            from src.core.browser_agent import get_browser_agent
            agent = get_browser_agent()
            # 直接获取当前页面文本
            if hasattr(agent, 'get_text'):
                result = agent.get_text()
            else:
                result = agent.fetch_content("")
            content = ''
            if result.data:
                content = result.data.get("text") or result.data.get("content") or str(result.data)
            return jsonify({"success": result.success, "content": content, "error": result.error})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ─────────────── 代码沙箱 ───────────────

    @app.route('/api/sandbox/execute', methods=['POST'])
    def sandbox_execute():
        """在沙箱中执行代码"""
        data = request.get_json() or {}
        code = data.get('code', '').strip()
        language = data.get('language', 'python')
        if not code:
            return jsonify({"success": False, "error": "代码不能为空"})
        try:
            from src.core.code_sandbox import get_sandbox
            sandbox = get_sandbox()
            result = sandbox.execute(code, language)
            output_combined = result.stdout or ''
            if result.stderr:
                output_combined += ('\n[STDERR]\n' + result.stderr)
            return jsonify({
                "success": result.success,
                "output": output_combined.strip(),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": result.error,
                "execution_time": (result.duration_ms or 0) / 1000.0,
                "duration_ms": result.duration_ms,
                "display": result.format_for_display(),
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/sandbox/scan', methods=['POST'])
    def sandbox_scan():
        """代码安全扫描"""
        data = request.get_json() or {}
        code = data.get('code', '').strip()
        try:
            from src.core.code_sandbox import scan_code_safety
            is_safe, warnings = scan_code_safety(code)
            return jsonify({"is_safe": is_safe, "warnings": warnings})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ─────────────── 定时任务 ───────────────

    @app.route('/api/scheduler/tasks', methods=['GET'])
    def scheduler_list():
        """获取所有定时任务"""
        try:
            from src.core.scheduler import get_scheduler
            tasks = [t.to_dict() for t in get_scheduler().get_all_tasks()]
            next_runs = {r["task_id"]: r["next_run"] for r in get_scheduler().get_next_runs()}
            for t in tasks:
                t["next_run"] = next_runs.get(t["task_id"])
            return jsonify({"success": True, "tasks": tasks})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/tasks', methods=['POST'])
    def scheduler_add():
        """添加定时任务（兼容自然语言时间格式）"""
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        # 兼容两种字段格式
        prompt = data.get('prompt') or data.get('content', '')
        prompt = prompt.strip()
        schedule_type = data.get('schedule_type') or data.get('task_type', 'cron')
        schedule_config = data.get('schedule_config', {})
        schedule_str = data.get('schedule_str', '').strip()

        if not name or not prompt:
            return jsonify({"success": False, "error": "name 和 content 不能为空"})

        # 如果提供了自然语言时间字符串，解析成 schedule_config
        if schedule_str and not schedule_config:
            try:
                from src.core.scheduler import parse_schedule_from_text
                parsed = parse_schedule_from_text(schedule_str)
                if parsed:
                    schedule_type = parsed.get('schedule_type', 'cron')
                    schedule_config = parsed.get('schedule_config', {})
            except Exception as _e: logger.debug(f"[new_features_api] 忽略: {_e}")

        if not schedule_config:
            return jsonify({"success": False, "error": "无法解析时间配置，请尝试更清晰的描述（如：每天早上8点）"})

        try:
            from src.core.scheduler import get_scheduler, ScheduledTask
            task = ScheduledTask(
                task_id=f"task_{uuid.uuid4().hex[:8]}",
                name=name,
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_config=schedule_config,
            )
            result = get_scheduler().add_task(task)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/tasks/<task_id>', methods=['DELETE'])
    def scheduler_delete(task_id):
        """删除定时任务"""
        try:
            from src.core.scheduler import get_scheduler
            return jsonify(get_scheduler().delete_task(task_id))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/tasks/<task_id>/toggle', methods=['POST'])
    def scheduler_toggle(task_id):
        """启用/禁用定时任务"""
        data = request.get_json() or {}
        enabled = data.get('enabled', True)
        try:
            from src.core.scheduler import get_scheduler
            return jsonify(get_scheduler().toggle_task(task_id, enabled))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/tasks/<task_id>/pause', methods=['POST'])
    def scheduler_pause(task_id):
        """暂停定时任务（toggle disabled 的别名）"""
        try:
            from src.core.scheduler import get_scheduler
            return jsonify(get_scheduler().toggle_task(task_id, False))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/parse', methods=['POST'])
    def scheduler_parse():
        """解析自然语言定时配置"""
        data = request.get_json() or {}
        text = data.get('text', '')
        try:
            from src.core.scheduler import parse_schedule_from_text
            result = parse_schedule_from_text(text)
            return jsonify({"success": True, "schedule": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/scheduler/runs', methods=['GET'])
    def scheduler_run_history():
        """获取定时任务执行历史"""
        try:
            from src.core.scheduler import get_scheduler
            task_id = request.args.get('task_id', '')
            limit = min(int(request.args.get('limit', 30)), 100)
            history = get_scheduler().get_run_history(
                task_id=task_id if task_id else None,
                limit=limit
            )
            return jsonify({"success": True, "runs": history, "count": len(history)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # P1: Multi-Agent Collaboration 已归档（2026-07-02 瘦身，与 agent_registry 重叠）
    # P1: 工作流已归档（2026-07-02 瘦身，空架子无实际工作流在跑）
    # P1: 多渠道接入已归档（2026-07-02 瘦身，与 resource_hub 重叠）

    # ─────────────── Active Memory ───────────────

    @app.route('/api/memory/active', methods=['GET'])
    def active_memory_list():
        """获取所有 Active Memory"""
        try:
            from src.core.active_memory import get_active_memory
            memories = get_active_memory().get_all(limit=100)
            return jsonify({"success": True, "memories": memories, "count": len(memories)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/memory/active', methods=['POST'])
    def active_memory_add():
        """手动添加记忆"""
        data = request.get_json() or {}
        content = data.get('content', '').strip()
        category = data.get('category', 'general')
        if not content:
            return jsonify({"success": False, "error": "content 不能为空"})
        try:
            from src.core.active_memory import get_active_memory
            mem_id = get_active_memory().store(content, category=category, source="manual")
            return jsonify({"success": True, "id": mem_id})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/memory/active/<int:memory_id>', methods=['DELETE'])
    def active_memory_delete(memory_id):
        """删除指定记忆"""
        try:
            from src.core.active_memory import get_active_memory
            get_active_memory().delete(memory_id)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/memory/active/search', methods=['POST'])
    def active_memory_search():
        """搜索相关记忆"""
        data = request.get_json() or {}
        query = data.get('query', '').strip()
        if not query:
            return jsonify({"success": False, "error": "query 不能为空"})
        try:
            from src.core.active_memory import get_active_memory
            results = get_active_memory().retrieve(query, top_k=10)
            return jsonify({
                "success": True,
                "results": [
                    {"content": r.content, "category": r.category,
                     "relevance": round(r.relevance, 3), "created_at": r.created_at}
                    for r in results
                ]
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ─────────────── 多搜索引擎 ───────────────

    @app.route('/api/search', methods=['POST'])
    def multi_search():
        """多引擎搜索"""
        data = request.get_json() or {}
        query = data.get('query', '').strip()
        engine = data.get('engine', 'auto')
        max_results = data.get('max_results', 5)
        if not query:
            return jsonify({"success": False, "error": "query 不能为空"})
        try:
            raise ImportError("web_search模块未安装")  # get_web_searcher
            results = get_web_searcher().search(query, max_results=max_results, engine=engine)
            return jsonify({"success": True, "results": results, "engine": engine})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/search/engines', methods=['GET'])
    def search_engines_list():
        """获取可用搜索引擎列表"""
        raise ImportError("web_search模块未安装")  # AVAILABLE_ENGINES
        return jsonify({"success": True, "engines": AVAILABLE_ENGINES})

    # ─────────────── 工作流 ───────────────

    @app.route('/api/workflows', methods=['GET'])
    def workflow_list():
        """获取所有工作流"""
        try:
            raise ImportError("workflow模块未安装")  # get_workflow_engine
            return jsonify({"success": True, "workflows": get_workflow_engine().get_all_workflows()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/workflows', methods=['POST'])
    def workflow_create():
        """创建工作流"""
        data = request.get_json() or {}
        name = data.get('name', '').strip()
        description = data.get('description', '')
        steps = data.get('steps', [])
        if not name or not steps:
            return jsonify({"success": False, "error": "name 和 steps 不能为空"})
        try:
            raise ImportError("workflow模块未安装")  # get_workflow_engine
            wf = get_workflow_engine().create_workflow(name, description, steps)
            return jsonify({"success": True, "workflow_id": wf.workflow_id, "workflow": wf.to_dict()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/workflows/<workflow_id>/run', methods=['POST'])
    def workflow_run(workflow_id):
        """运行工作流"""
        try:
            raise ImportError("workflow模块未安装")  # get_workflow_engine
            from src.core.model_manager import ModelManager

            engine = get_workflow_engine()

            # 注入 LLM 调用函数
            def llm_fn(prompt: str) -> str:
                try:
                    from src.core.model_manager import ModelManager
                    import yaml
                    from pathlib import Path
                    cfg_path = Path(__file__).parent / 'config.yaml'
                    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
                    base_url = cfg.get('ollama', {}).get('base_url', 'http://localhost:11434')
                    model = cfg.get('ollama', {}).get('default_model', 'qwen2.5:14b')
                    mm = ModelManager(base_url, model)
                    return mm.chat([{"role": "user", "content": prompt}], stream=False)
                except Exception as e:
                    return f"LLM 调用失败：{e}"

            result = engine.run_workflow(workflow_id, llm_chat_fn=llm_fn)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/workflows/<workflow_id>', methods=['GET'])
    def workflow_get(workflow_id):
        """获取工作流详情"""
        try:
            raise ImportError("workflow模块未安装")  # get_workflow_engine
            wf = get_workflow_engine().get_workflow(workflow_id)
            if not wf:
                return jsonify({"success": False, "error": "工作流不存在"})
            return jsonify({"success": True, "workflow": wf})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/workflows/<workflow_id>', methods=['DELETE'])
    def workflow_delete(workflow_id):
        """删除工作流"""
        try:
            raise ImportError("workflow模块未安装")  # get_workflow_engine
            get_workflow_engine().delete_workflow(workflow_id)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/workflows/templates', methods=['GET'])
    def workflow_templates():
        """获取内置工作流模板"""
        raise ImportError("workflow模块未安装")  # WORKFLOW_TEMPLATES
        return jsonify({"success": True, "templates": WORKFLOW_TEMPLATES})

    # P1: 工作流已归档（2026-07-02 瘦身，空架子无实际工作流在跑）
    # P1: 多渠道接入（2026-08-09 重建 SQLite 简化版）
    @app.route('/api/channels', methods=['GET'])
    def channel_list():
        """获取所有渠道"""
        try:
            from src.core.channel_manager import get_channel_manager
            return jsonify({"success": True, "channels": get_channel_manager().get_all_channels()})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/channels', methods=['POST'])
    def channel_add():
        """添加渠道"""
        data = request.get_json() or {}
        try:
            from src.core.channel_manager import get_channel_manager
            return jsonify(get_channel_manager().add_channel(data))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/channels/<channel_id>', methods=['DELETE'])
    def channel_delete(channel_id):
        """删除渠道"""
        try:
            from src.core.channel_manager import get_channel_manager
            return jsonify(get_channel_manager().remove_channel(channel_id))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/webhook/<channel_id>', methods=['POST'])
    def webhook_receive(channel_id):
        """接收外部渠道 Webhook — 统一入口"""
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        headers = dict(request.headers)
        try:
            from src.core.channel_manager import get_channel_manager
            manager = get_channel_manager()
            channel = manager.get_channel(channel_id)
            if not channel:
                return jsonify({"error": "channel not found"}), 404
            # 飞书验证挑战直接返回
            if 'challenge' in data:
                return jsonify({"challenge": data['challenge']})
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/channels/test', methods=['POST'])
    def channel_test():
        """测试向某渠道发送消息"""
        data = request.get_json() or {}
        channel_id = data.get('channel_id')
        message = data.get('message', '这是一条测试消息来自 Harven AI 🚀')
        try:
            raise ImportError("channel_manager模块未安装")  # get_channel_manager, ChannelMessage, ChannelReply
            manager = get_channel_manager()
            ch = manager._channels.get(channel_id)
            if not ch:
                return jsonify({"success": False, "error": "渠道不存在"})
            test_msg = ChannelMessage(
                channel_type=ch.channel_type,
                channel_id=channel_id,
                sender_id='test',
                sender_name='系统测试',
                content='test',
                chat_id=data.get('chat_id', '')
            )
            ok = ch.send_reply(test_msg, ChannelReply(content=message))
            return jsonify({"success": ok, "message": "测试消息已发送" if ok else "发送失败"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    logger.info("[NewFeatures] All new feature routes registered successfully")

    # ─────────────── 图片配置（生成功能在 web_app.py 主线） ───────────────

    @app.route('/api/image/config', methods=['GET'])
    def image_config():
        """获取图片生成配置（不泄露密钥）"""
        try:
            from src.core.image_gen import get_image_generator
            gen = get_image_generator()
            return jsonify({
                "success": True,
                "config": {
                    "default_provider": gen.default_provider,
                    "hunyuan_configured": bool(gen.hunyuan_id and gen.hunyuan_key),
                    "dalle_configured": bool(gen.dalle_key),
                    "sd_configured": bool(gen.sd_url),
                }
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/media/generated/<filename>')
    def serve_generated_image(filename):
        """提供生成的图片文件"""
        from flask import send_from_directory
        from pathlib import Path
        gen_dir = Path(__file__).parent / 'data' / 'generated'
        gen_dir.mkdir(parents=True, exist_ok=True)
        return send_from_directory(str(gen_dir), filename, mimetype='image/png')

    # ─────────────── Docker 沙箱状态 ───────────────

    @app.route('/api/sandbox/docker-status', methods=['GET'])
    def sandbox_docker_status():
        """获取 Docker 沙箱状态"""
        try:
            from src.core.code_sandbox import is_docker_available
            docker_ok = is_docker_available()
            return jsonify({
                "success": True,
                "docker_available": docker_ok,
                "mode": "docker" if docker_ok else "subprocess",
                "message": "Docker 容器隔离模式" if docker_ok else "子进程隔离模式（建议安装 Docker 以获得真正的沙箱隔离）"
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ─────────────── 操作审批 requireApproval ───────────────

    @app.route('/api/approval/check', methods=['POST'])
    def approval_check():
        """检查操作是否需要审批"""
        data = request.get_json() or {}
        operation = data.get('operation', '')
        action_type = data.get('action_type', '')
        params = data.get('params', {})

        DANGER_LEVELS = {
            "file_delete": "high",
            "file_modify_system": "high",
            "email_send": "medium",
            "system_cmd": "high",
            "code_exec": "medium",
            "webhook_send": "medium",
            "browser_navigate": "low",
            "browser_form_fill": "medium",
            "file_read": "low",
            "knowledge_modify": "low",
        }
        level = DANGER_LEVELS.get(action_type, "low")

        return jsonify({
            "success": True,
            "requires_approval": level in ("medium", "high"),
            "danger_level": level,
            "message": f"操作类型「{action_type}」级别：{level}",
        })

    @app.route('/api/approval/confirm', methods=['POST'])
    def approval_confirm():
        """用户确认操作"""
        data = request.get_json() or {}
        action_id = data.get('action_id', '')
        approved = data.get('approved', False)
        return jsonify({
            "success": True,
            "action_id": action_id,
            "approved": approved,
            "message": "操作已确认" if approved else "操作已取消"
        })

    # ─────────────── Memory Dreaming ───────────────

    @app.route('/api/memory/dream', methods=['POST'])
    def memory_dream():
        """触发记忆梦境（手动）"""
        data = request.get_json() or {}
        dream_type = data.get('type', 'light')
        try:
            from src.core.memory_dreaming import get_dream_engine
            engine = get_dream_engine()
            results = engine.force_dream(dream_type)
            return jsonify({
                "success": True,
                "dreams": [
                    {
                        "type": d.dream_type,
                        "input_count": d.input_count,
                        "output_count": d.output_count,
                        "merged_pairs": d.merged_pairs,
                        "removed_count": d.removed_count,
                        "insights": d.insights,
                        "duration_ms": d.duration_ms,
                    }
                    for d in results
                ]
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/memory/dream/report', methods=['GET'])
    def memory_dream_report():
        """获取梦境报告"""
        try:
            from src.core.memory_dreaming import get_dream_engine
            return jsonify({
                "success": True,
                "report": get_dream_engine().get_dream_report()
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/memory/dream/status', methods=['GET'])
    def memory_dream_status():
        """获取梦境状态（别名）"""
        return memory_dream_report()

    # ─────────────── 设备绑定 ───────────────

    @app.route('/api/device/info', methods=['GET'])
    def device_info():
        """获取设备信息"""
        try:
            from src.core.device_binding import get_device_binding
            return jsonify({
                "success": True,
                "device": get_device_binding().get_device_info()
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/device/bind', methods=['POST'])
    def device_bind():
        """执行设备绑定"""
        data = request.get_json() or {}
        force = data.get('force', False)
        try:
            from src.core.device_binding import get_device_binding
            ok, msg = get_device_binding().bind(force=force)
            return jsonify({"success": ok, "message": msg})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/device/verify', methods=['POST'])
    def device_verify():
        """验证设备绑定"""
        try:
            from src.core.device_binding import get_device_binding
            ok, msg = get_device_binding().verify()
            return jsonify({"success": ok, "message": msg})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/device/unbind', methods=['POST'])
    def device_unbind():
        """解除设备绑定"""
        try:
            from src.core.device_binding import get_device_binding
            ok, msg = get_device_binding().unbind()
            return jsonify({"success": ok, "message": msg})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ─────────────── 渠道 OAuth ───────────────

    @app.route('/api/channels/oauth/<channel_id>/callback', methods=['GET', 'POST'])
    def channel_oauth_callback(channel_id):
        """OAuth 回调（飞书/钉钉）"""
        code = request.args.get('code', '')
        state = request.args.get('state', '')
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            code = data.get('code', code)
            state = data.get('state', state)
        if not code:
            return jsonify({"success": False, "error": "缺少授权码 code"})
        try:
            raise ImportError("channel_manager模块未安装")  # get_channel_manager
            manager = get_channel_manager()
            result = manager.complete_oauth(channel_id, code, state)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/api/channels/<channel_id>/oauth-url', methods=['GET'])
    def channel_oauth_url(channel_id):
        """获取 OAuth 授权 URL"""
        try:
            raise ImportError("channel_manager模块未安装")  # get_channel_manager
            manager = get_channel_manager()
            ch = manager._channels.get(channel_id)
            if not ch:
                return jsonify({"success": False, "error": "渠道不存在"})
            auth_url = ch.get_oauth_url()
            if not auth_url:
                return jsonify({"success": False, "error": "该渠道不支持 OAuth"})
            return jsonify({"success": True, "oauth_url": auth_url})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})


