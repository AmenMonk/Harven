"""
Harven AI - Web Interface
Flask server + SSE streaming API

Copyright 2026 AmenMonk <38552540@qq.com>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import os, sys, json, time, traceback, threading, tempfile, subprocess, sqlite3, logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 每次启动生成唯一版本号，注入到所有 JS/CSS URL 参数中
# 彻底绕过浏览器 HTTP 缓存 + Service Worker 缓存
import uuid
BUILD_ID = uuid.uuid4().hex[:12]
print(f"[Harven] BUILD_ID = {BUILD_ID}")

from flask import (Flask, render_template, request, Response,
                   jsonify, send_from_directory, stream_with_context,
                   redirect, make_response)
from markupsafe import Markup
import yaml
import logging
from src.utils.config import Config

# 全局配置对象（统一使用 Config 类，替代 plain dict）
config = Config()

# 模型管理路由（Blueprint）
from model_routes import bp as models_bp

# 技能管理路由（自动注册）
from src.core.skills_routes import bp as skills_bp

# 受控进化路由（自动注册）
from src.core.evolution_routes import bp as evolution_bp

# 统计管理路由（自动注册）
from src.core.stats_routes import bp as stats_bp

# 保底机制路由（自动注册）
from src.core.fallback_routes import bp as fallback_bp

# 知识萃取路由（自动注册）
from src.core.extraction_routes import bp as extraction_bp
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            template_folder=str(PROJECT_ROOT / 'templates'),
            static_folder=str(PROJECT_ROOT / 'static'))

# ===== 全局安全配置 =====
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB 全局请求体限制

# ===== 独立性防御：出站白名单（必须在任何请求之前激活） =====
try:
    from src.core.independence import load_allowed_domains, _apply_outbound_patch
    load_allowed_domains()
    _apply_outbound_patch()
    logger.info("[Startup] 独立性防御：出站白名单已激活")
except Exception as _e:
    logger.error(f"[Startup] 独立性防御初始化失败: {_e}")

# ===== 独立性防御中间件：API 请求来源验证 =====
@app.before_request
def check_independence():
    """AI 独立性中间件：验证 API 请求来源"""
    from src.core.independence import create_independence_middleware
    _middleware = create_independence_middleware()
    return _middleware()

# ===== 速率限制中间件（P0 安全修复） =====
@app.before_request
def check_rate_limit():
    """对所有 API 请求进行速率限制"""
    if request.path.startswith('/api/'):
        from src.core.rate_limiter import rate_limit_middleware
        result = rate_limit_middleware()
        if result is not None:
            return result
    return None

@app.before_request
def check_visitor_access():
    """
    访问码校验中间件（公网分享安全门）
    - 本机/局域网直接放行
    - 已有合法 session cookie 放行
    - 访问 /visitor_login 路由放行
    - 其余情况检查访问码
    """
    from src.core.visitor_manager import (
        is_access_protected, is_valid_session, register_session
    )
    path = request.path

    # 放行：静态资源、内部接口
    skip_prefixes = ('/static/', '/favicon', '/health', '/api/visitor/', '/visitor_login')
    if any(path.startswith(p) for p in skip_prefixes):
        return None

    # 放行：本机/局域网
    ip = request.remote_addr or '127.0.0.1'
    if ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.'):
        return None

    # 未启用访问码保护，直接放行并记录
    if not is_access_protected():
        sid = request.cookies.get('hv_sid', '')
        if not sid:
            from src.core.visitor_manager import generate_session_token
            sid = generate_session_token()
        register_session(sid, ip, request.user_agent.string[:256] if request.user_agent else '')
        return None

    # 已有合法 session
    sid = request.cookies.get('hv_sid', '')
    if sid and is_valid_session(sid):
        return None

    # 需要验证访问码 — 非 HTML 请求返回 401
    if request.path.startswith('/api/'):
        return jsonify({'error': '需要访问码', 'code': 'access_required'}), 401

    # HTML 请求 → 重定向到登录页
    from flask import redirect, url_for
    return redirect(f'/visitor_login?next={request.path}')


@app.after_request
def add_cache_headers(response):
    """对所有HTML和静态文件强制禁用缓存（no-store 最强）"""
    # HTML 页面和静态资源一律禁止缓存
    if request.path == '/' or request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    else:
        # API响应也加缓存控制
        ct = response.headers.get('Content-Type', '')
        if 'text/html' in ct:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    return response

@app.route('/static/sw.js')
def service_worker():
    """Service Worker 必须从根目录提供，设置正确的 Content-Type 和 Service-Worker-Allowed"""
    resp = send_from_directory(str(PROJECT_ROOT / 'static'), 'sw.js',
                               mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


# Docker 环境变量覆盖 Ollama 地址
_ollama_host = os.environ.get('OLLAMA_HOST', '')
_ollama_port = os.environ.get('OLLAMA_PORT', '')
if _ollama_host:
    if 'ollama' not in config:
        config['ollama'] = {}
    config['ollama']['base_url'] = f"http://{_ollama_host}:{_ollama_port or '11434'}"
    logger.debug(f"[Config] Ollama 地址从环境变量覆盖: {config['ollama']['base_url']}")

def _load_config():
    """重新从磁盘读取 config.yaml（供需要最新状态的 API 调用）"""
    config._load()
    return config

def _save_config(cfg=None):
    """将配置写回 config.yaml"""
    if cfg is not None:
        for k, v in cfg.items():
            config[k] = v
    config.save()

# Initialize database (ensure all columns exist, add missing ones)
from src.models.database import init_database
init_database()

# Lazy model manager
_mm = None
_mm_lock = threading.Lock()

def _validate_model_vram(model_name: str) -> str:
    """启动时验证模型显存是否足够，不够则自动降级到安全模型"""
    try:
        from src.core.hardware_detector import detect_hardware, MODEL_DB
        spec = detect_hardware()
        if not spec.has_dedicated_gpu or spec.gpu_vram_gb <= 0:
            return model_name  # 无独显，无法判断
        
        model_entry = next((m for m in MODEL_DB if m['model_id'] == model_name), None)
        if model_entry and spec.gpu_vram_gb < model_entry['min_vram_gb']:
            logger.warning(
                f"[Startup] ⚠️ 默认模型 {model_name} 需要 {model_entry['min_vram_gb']}GB VRAM，"
                f"本机仅 {spec.gpu_vram_gb:.1f}GB → 自动降级"
            )
            # 从 MODEL_DB 中找第一个能跑的
            fallback = next(
                (m['model_id'] for m in MODEL_DB 
                 if m['min_vram_gb'] <= spec.gpu_vram_gb and m['tier'] in ('mainstream', 'high_performance')),
                None
            )
            if not fallback:
                fallback = next(
                    (m['model_id'] for m in MODEL_DB 
                     if m['min_vram_gb'] <= spec.gpu_vram_gb),
                    'qwen2.5:3b'
                )
            logger.warning(f"[Startup] → 降级模型: {fallback}")
            # 持久化修复到 config.yaml
            config.setdefault('ollama', {})['default_model'] = fallback
            try:
                with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
                    yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
                logger.info(f"[Startup] config.yaml 已更新 default_model → {fallback}")
            except Exception as e:
                logger.error(f"[Startup] 写入 config.yaml 失败: {e}")
            return fallback
    except Exception as e:
        logger.warning(f"[Startup] VRAM 验证跳过: {e}")
    return model_name

def get_mm():
    global _mm
    if _mm is None:
        with _mm_lock:
            if _mm is None:
                from src.core.model_manager import ModelManager
                base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
                model_name = config.get('ollama', {}).get('default_model', 'qwen2.5:7b')
                # 启动时验证模型是否适配本机显存
                model_name = _validate_model_vram(model_name)
                _mm = ModelManager(base_url, model_name)
    return _mm

# Register multi-model routes
try:
    from multi_model_api import register_multi_model_routes
    register_multi_model_routes(app)
    logger.info("[MultiModel] Multi-model routes registered successfully")
except Exception as e:
    logger.error(f"[MultiModel] Failed to register multi-model routes: {e}")

# Skills routes: 已通过 skills_bp Blueprint 注册（见上方）

# Register device management routes
try:
    from device_api import register_device_api_routes
    register_device_api_routes(app)
    logger.info("[DeviceAPI] Device management routes registered successfully")
    # 自动注册内置设备
    try:
        from src.core.device_starter import register_builtin_devices
        import threading
        threading.Thread(target=register_builtin_devices, daemon=True).start()
    except Exception as e:
        logger.error(f"[DeviceStarter] 内置设备注册失败: {e}")
except Exception as e:
    logger.error(f"[DeviceAPI] Failed to register device routes: {e}")

# Register plugin management routes (Blueprint)
try:
    from plugin_mgmt_api import bp as plugin_bp
    app.register_blueprint(plugin_bp)
    logger.info("[PluginMgmt] Plugin management routes registered (Blueprint)")
except Exception as e:
    logger.error(f"[PluginMgmt] Failed to register plugin routes: {e}")

# Register new features routes (browser/sandbox/scheduler/active-memory/search/workflow)
try:
    from new_features_api import register_new_feature_routes
    register_new_feature_routes(app)
except Exception as e:
    logger.error(f"[NewFeatures] Failed to register new feature routes: {e}")

# ==================== Memory Dreaming 初始化 ====================
try:
    from src.core.memory_dreaming import get_dream_engine, register_dreaming_scheduler
    dream = get_dream_engine()
    # 设置 LLM 调用函数用于梦境（仅本地 Ollama，保护隐私）
    def _dream_llm_fn(prompt: str) -> str:
        try:
            mm = get_mm()
            return mm.chat([{"role": "user", "content": prompt}], stream=False)
        except Exception as e:
            logger.debug(f"[Dreaming] 本地模型不可用，跳过本轮 Dream: {e}")
            return ""
    dream.set_llm_fn(_dream_llm_fn)
    logger.debug(f"[Dreaming] Memory Dreaming 引擎已初始化（本地 Ollama）")
except Exception as e:
    logger.error(f"[Dreaming] Memory Dreaming 初始化失败: {e}")

# ==================== 定时任务调度器初始化 ====================
try:
    from src.core.scheduler import get_scheduler
    sched = get_scheduler()

    # 注入任务执行回调：定时触发时调用 AI 引擎生成回复
    def _scheduler_execute(task_id: str, prompt: str):
        """定时任务到期时执行 AI 对话"""
        logger.info(f"[Scheduler] 执行定时任务: {task_id} -> {prompt[:80]}")
        try:
            from src.core.engine import ConversationEngine
            from src.core.model_manager import ModelManager
            mm = ModelManager()
            engine = ConversationEngine(
                model_manager=mm,
                conversation_id=f"sched_{task_id}"
            )
            response = engine.send(prompt, stream=False, query_mode="auto")
            if response:
                logger.info(f"[Scheduler] 任务 {task_id} 执行成功")
            return response
        except Exception as ex:
            logger.error(f"[Scheduler] 任务 {task_id} 执行失败: {ex}")
            return None

    sched.set_callback(_scheduler_execute)

    # 注册记忆梦境定时任务
    try:
        register_dreaming_scheduler()
        logger.info("[Scheduler] 记忆梦境任务已注册")
    except Exception as de:
        logger.warning(f"[Scheduler] 梦境注册跳过: {de}")

    logger.info("[Scheduler] 定时任务调度器已就绪")
except Exception as e:
    logger.error(f"[Scheduler] 定时任务调度器初始化失败: {e}")

# ==================== Device Binding 启动时校验 ====================
_device_binding_ok = True
_device_binding_msg = ""
try:
    from src.core.device_binding import startup_verify
    _device_binding_ok, _device_binding_msg = startup_verify()
    if not _device_binding_ok:
        logger.warning(f"[DeviceBinding] WARNING: {_device_binding_msg}")
    else:
        logger.info(f"[DeviceBinding] OK: device binding verified")
except Exception as e:
    logger.error(f"[DeviceBinding] 初始化失败: {e}")
    _device_binding_ok = True  # 出错时不阻止启动

# ==================== Health Check ====================

@app.route('/health')
def health_check():
    """Docker 健康检查端点"""
    return jsonify({
        'status': 'ok',
        'version': config.get('app', {}).get('version', '0.1.0'),
        'timestamp': datetime.now().isoformat()
    })

# ==================== Pages ====================

@app.route('/')
def index():
    # 首次启动引导：未初始化则重定向到引导页
    try:
        import os
        fr_path = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'first_run.json')
        if not os.path.exists(fr_path):
            return redirect('/first_run')
    except Exception:
        pass

    # 服务端预检 Ollama 状态，绕过浏览器缓存问题
    model_status_html = '<span id="modelStatus" class="model-status">⏳ 连接中...</span>'
    try:
        from src.core.model_manager import check_ollama
        base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        ok, msg = check_ollama(base_url)
        if ok:
            current_model = config.get('ollama', {}).get('default_model', '')
            model_status_html = f'<span id="modelStatus" class="model-status connected">🟢 已连接 · {current_model}</span>'
        else:
            model_status_html = '<span id="modelStatus" class="model-status disconnected">🔴 未连接</span>'
    except Exception:
        pass
    response = make_response(render_template('index.html', model_status_html=Markup(model_status_html), build_id=BUILD_ID))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ==================== Chat (SSE Stream) ====================

@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json or {}
    message = data.get('message', '').strip()
    conv_id = data.get('conversation_id')
    spec_id = data.get('specialization_id')
    query_mode = data.get('query_mode', 'auto')  # auto/fast/kb_only/web_only/full
    logger.info(f"[Chat] 收到原始 data: message={message[:30]!r}, query_mode={query_mode!r}, conv_id={conv_id}")

    # 记录公网访客对话次数
    sid = request.cookies.get('hv_sid', '')
    if sid:
        try:
            from src.core.visitor_manager import record_chat
            record_chat(sid)
        except Exception:
            pass

    if not message:
        return jsonify({'error': 'Empty message'}), 400

    # Create conversation if needed
    if not conv_id:
        from src.models.database import create_conversation
        title = message[:15] + ('...' if len(message) > 15 else '')
        conv_id = create_conversation(title, specialization_id=spec_id)

    # Note: user message is saved inside engine.send() to avoid duplicate

    # Get specialization prompt
    spec_prompt = None
    if spec_id:
        try:
            from src.models.database import get_specializations
            for s in get_specializations():
                if s['id'] == spec_id and s.get('system_prompt'):
                    spec_prompt = s['system_prompt']
                    break
        except (KeyError, TypeError, AttributeError):
            pass

    # If no specialization prompt, try Skills system match
    if not spec_prompt:
        try:
            from src.core.skills_manager import get_skills_manager
            skill_prompt = get_skills_manager().get_skill_prompt(message)
            if skill_prompt:
                spec_prompt = skill_prompt
        except Exception as _e:
            pass

    # ===== 对话式技能自动发现（后台并行搜索 Resource Hub，不阻塞对话）=====
    skill_discovery_result = []
    if not spec_prompt:  # 本地技能未命中时才触发全网搜索
        try:
            from src.core.skills_manager import get_skills_manager
            from src.core.skill_discovery import discover_suggestions
            existing_ids = {s.get('id', '') for s in get_skills_manager().list_skills()}
            def _run_discovery():
                try:
                    skill_discovery_result.extend(
                        discover_suggestions(message, installed_skill_ids=existing_ids, timeout=2.0, max_results=3)
                    )
                except Exception:
                    pass
            _discovery_thread = threading.Thread(target=_run_discovery, daemon=True)
            _discovery_thread.start()
        except Exception:
            pass

    mm = get_mm()  # Ollama ModelManager（始终初始化，作为 fallback）

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

            # ===== 统一消息路径 =====
            # 所有模型（Ollama/DeepSeek/Kimi 等）一律走 ConversationEngine
            # ConversationEngine 内部已处理多模型路由（engine.py:530-557）
            # 包含完整安全链路：道德审核 → 知识库RAG → 记忆注入 → 用户画像 → LLM生成

            # GitHub URL 自动下载（预处理，先于 ConversationEngine）
            github_note = None
            try:
                from src.core.github_downloader import detect_github_urls, download_and_import_github
                urls = detect_github_urls(message)
                if urls:
                    yield f"data: {json.dumps({'type': 'thinking', 'content': '\U0001F4E6 检测到 GitHub 链接，正在下载...'}, ensure_ascii=False)}\n\n"
                    for url_info in urls[:2]:
                        try:
                            import threading
                            result = [None]
                            def _dl():
                                result[0] = download_and_import_github(url_info['url'])
                            t = threading.Thread(target=_dl, daemon=True)
                            t.start()
                            t.join(timeout=45)
                            if result[0] and result[0].get('success'):
                                r = result[0]
                                src_msg = f"\U0001F4E6 {r['repo']}: 已下载，{r['imported']}个文件导入知识库"
                                yield f"data: {json.dumps({'type': 'source', 'content': src_msg}, ensure_ascii=False)}\n\n"
                                github_note = f"\n\n[系统通知] 主人刚下载了 GitHub 仓库 {r['repo']}，共 {r['imported']} 个文件已导入本地知识库。如果主人问到相关内容，请优先引用。"
                        except Exception as gh_e:
                            logger.error(f"[GitHub] 下载失败: {gh_e}")
            except ImportError:
                pass
            except Exception as gh_e:
                logger.error(f"[GitHub] 检测失败: {gh_e}")

            # 如果有 GitHub 通知，追加到 spec_prompt
            final_spec = spec_prompt
            if github_note:
                final_spec = (spec_prompt or "") + github_note

            from src.core.engine import ConversationEngine
            engine = ConversationEngine(mm, conv_id, specialization_prompt=final_spec, query_mode=query_mode)
            for msg_type, content in engine.send(message, stream=True, query_mode=query_mode):
                yield f"data: {json.dumps({'type': msg_type, 'content': content}, ensure_ascii=False)}\n\n"

            # ===== 注入技能建议（对话式自动发现）=====
            try:
                _discovery_thread.join(timeout=1.0)  # 等待发现线程完成（最多1秒）
            except Exception:
                pass
            if skill_discovery_result:
                yield f"data: {json.dumps({'type': 'suggestion', 'content': skill_discovery_result}, ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp

# ==================== Image Chat ====================

@app.route('/api/chat/image', methods=['POST'])
def chat_image():
    message = request.form.get('message', '描述这张图片')
    conv_id = request.form.get('conversation_id')
    image_file = request.files.get('image')

    if not image_file:
        return jsonify({'error': 'No image'}), 400

    tmp_path = Path(tempfile.mkdtemp()) / image_file.filename
    image_file.save(str(tmp_path))

    # 文件安全扫描（fail-closed: 扫描失败=拒绝上传）
    try:
        from src.core.file_scanner import quick_check
        if not quick_check(str(tmp_path), image_file.filename):
            tmp_path.unlink(missing_ok=True)
            return jsonify({'error': '文件安全检测未通过，已拒绝上传'}), 400
    except Exception as scan_e:
        logger.debug(f"[Security] 图片扫描异常，拒绝上传: {scan_e}")
        tmp_path.unlink(missing_ok=True)
        return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

    try:
        if not conv_id:
            from src.models.database import create_conversation
            conv_id = create_conversation(f'[图片] {message[:10]}')

        from src.models.database import save_message
        save_message(conv_id, 'user', f'[图片] {message}')

        from src.core.image_processor import analyze_image
        mm = get_mm()
        full_text = ""
        for msg_type, content in analyze_image(mm, message, [str(tmp_path)], stream=False):
            if msg_type == "done":
                full_text = content
                break
        save_message(conv_id, 'assistant', full_text)

        return jsonify({'conversation_id': conv_id, 'response': full_text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)

# ==================== Video Chat ====================

@app.route('/api/chat/video', methods=['POST'])
def chat_video():
    """视频上传 + AI 分析（SSE 流式返回）"""
    message = request.form.get('message', '请分析这个视频')
    conv_id = request.form.get('conversation_id')
    video_file = request.files.get('video')

    if not video_file:
        return jsonify({'error': 'No video'}), 400

    # 保存临时文件
    tmp_dir = Path(tempfile.mkdtemp(prefix='harven_video_'))
    tmp_path = tmp_dir / video_file.filename
    video_file.save(str(tmp_path))

    # 文件安全扫描（fail-closed: 扫描失败=拒绝上传）
    try:
        from src.core.file_scanner import quick_check
        if not quick_check(str(tmp_path), video_file.filename):
            import shutil
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
            return jsonify({'error': '文件安全检测未通过，已拒绝上传'}), 400
    except Exception as scan_e:
        logger.debug(f"[Security] 视频扫描异常，拒绝上传: {scan_e}")
        import shutil
        shutil.rmtree(str(tmp_dir), ignore_errors=True)
        return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

    if not conv_id:
        from src.models.database import create_conversation
        conv_id = create_conversation(f'[视频] {message[:10]}')

    from src.models.database import save_message
    save_message(conv_id, 'user', f'[视频] {message}')

    def generate():
        try:
            from src.core.video_processor import analyze_video, get_video_info
            mm = get_mm()
            info = get_video_info(str(tmp_path))
            if info:
                w = info["width"]
                h = info["height"]
                dur = info["duration_sec"]
                size = info["size_mb"]
                thinking_msg = f'视频 {w}x{h}，时长 {dur:.1f}秒，{size}MB'
                yield f"data: {json.dumps({'type': 'thinking', 'content': thinking_msg}, ensure_ascii=False)}\n\n"

            full_text = ''
            for msg_type, content in analyze_video(mm, message, str(tmp_path), stream=True):
                if msg_type == 'text':
                    full_text += content
                    yield f"data: {json.dumps({'type': 'text', 'content': content}, ensure_ascii=False)}\n\n"
                elif msg_type == 'thinking':
                    yield f"data: {json.dumps({'type': 'thinking', 'content': content}, ensure_ascii=False)}\n\n"
                elif msg_type == 'error':
                    yield f"data: {json.dumps({'type': 'error', 'content': content}, ensure_ascii=False)}\n\n"
                    return

            save_message(conv_id, 'assistant', full_text)
            yield "data: [DONE]\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # 清理临时目录
            import shutil
            try:
                shutil.rmtree(str(tmp_dir), ignore_errors=True)
            except OSError:
                pass

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp

# ==================== Audio Chat ====================

@app.route('/api/chat/audio', methods=['POST'])
def chat_audio():
    """音频上传 → 语音转文字 → AI 回答"""
    message = request.form.get('message', '')
    conv_id = request.form.get('conversation_id')
    audio_file = request.files.get('audio')

    if not audio_file:
        return jsonify({'error': 'No audio'}), 400

    # 保存临时文件（前端已发WAV格式，无需转换）
    tmp_path = Path(tempfile.mktemp(suffix='.wav'))
    audio_file.save(str(tmp_path))

    # 文件安全扫描
    try:
        from src.core.file_scanner import quick_check
        if not quick_check(str(tmp_path), audio_file.filename):
            tmp_path.unlink(missing_ok=True)
            return jsonify({'error': '文件安全检测未通过，上传被拒绝'}), 403
    except Exception as scan_e:
        logger.debug(f"[Security] 音频扫描异常，拒绝上传: {scan_e}")
        tmp_path.unlink(missing_ok=True)
        return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

    if not conv_id:
        from src.models.database import create_conversation
        conv_id = create_conversation('[语音消息]')

    try:
        # 1. 语音转文字
        from src.core.voice_input import get_voice_input
        vi = get_voice_input()
        transcript = vi.transcribe(str(tmp_path), language='zh')

        if not transcript:
            return jsonify({'error': '无法识别语音内容，请重试'}), 400

        # 组合消息：用户文字 + 转写文字
        combined = message.strip()
        if combined:
            combined += '\n\n[语音转写]: ' + transcript
        else:
            combined = '[语音转写]: ' + transcript

        from src.models.database import save_message
        save_message(conv_id, 'user', f'[语音] {transcript}')

        # 2. 走正常 AI 聊天流程（SSE 流式）
        spec_prompt = ''
        try:
            from src.models.database import get_conversation, get_specializations
            conv = get_conversation(conv_id)
            if conv and conv.get('specialization_id'):
                for s in get_specializations():
                    if s['id'] == conv['specialization_id'] and s.get('system_prompt'):
                        spec_prompt = s['system_prompt']
                        break
        except (KeyError, TypeError, AttributeError, ImportError):
            pass

        def generate():
            try:
                yield f"data: {json.dumps({'type': 'transcript', 'content': transcript}, ensure_ascii=False)}\n\n"
                from src.core.engine import ConversationEngine
                mm = get_mm()
                engine = ConversationEngine(mm, conv_id, specialization_prompt=spec_prompt)
                for msg_type, content in engine.send(combined, stream=True):
                    yield f"data: {json.dumps({'type': msg_type, 'content': content}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                traceback.print_exc()
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

        resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)

# ==================== File Chat ====================

@app.route('/api/chat/file', methods=['POST'])
def chat_file():
    """通用文件上传 → AI 回答"""
    message = request.form.get('message', '')
    conv_id = request.form.get('conversation_id')
    upload_file = request.files.get('file')

    if not upload_file:
        return jsonify({'error': 'No file'}), 400

    # 保存临时文件
    ext = Path(upload_file.filename).suffix or '.bin'
    tmp_path = Path(tempfile.mktemp(suffix=ext))
    upload_file.save(str(tmp_path))
    filename = upload_file.filename or 'unknown'

    # 文件安全扫描（P0 安全，fail-closed：扫描失败=拒绝上传）
    try:
        from src.core.file_scanner import quick_check
        if not quick_check(str(tmp_path), filename):
            tmp_path.unlink(missing_ok=True)
            return jsonify({'error': '文件安全检测未通过，上传被拒绝'}), 403
    except Exception as scan_e:
        logger.debug(f"[Security] 文件扫描异常，拒绝上传: {scan_e}")
        tmp_path.unlink(missing_ok=True)
        return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

    if not conv_id:
        from src.models.database import create_conversation
        conv_id = create_conversation('[文件] ' + filename)

    try:
        from src.models.database import save_message
        save_message(conv_id, 'user', f'[文件] {filename}')

        # 组合消息
        combined = message.strip()
        file_info = f'[用户上传了文件: {filename}]'
        if combined:
            combined = combined + '\n\n' + file_info
        else:
            combined = file_info

        # 获取专项提示词
        spec_prompt = ''
        try:
            from src.models.database import get_conversation, get_specializations
            conv = get_conversation(conv_id)
            if conv and conv.get('specialization_id'):
                for s in get_specializations():
                    if s['id'] == conv['specialization_id'] and s.get('system_prompt'):
                        spec_prompt = s['system_prompt']
                        break
        except (KeyError, TypeError, AttributeError, ImportError):
            pass

        def generate():
            try:
                from src.core.engine import ConversationEngine
                mm = get_mm()
                engine = ConversationEngine(mm, conv_id, specialization_prompt=spec_prompt)
                for msg_type, content in engine.send(combined, stream=True):
                    yield f"data: {json.dumps({'type': msg_type, 'content': content}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                traceback.print_exc()
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

        resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.headers['X-Accel-Buffering'] = 'no'
        return resp

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)

# ==================== Audio Transcribe (standalone) ====================

@app.route('/api/audio/transcribe', methods=['POST'])
def transcribe_audio():
    """纯语音转文字（不走AI）"""
    audio_file = request.files.get('audio')
    if not audio_file:
        return jsonify({'error': 'No audio'}), 400

    tmp_path = Path(tempfile.mktemp(suffix='.wav'))
    audio_file.save(str(tmp_path))

    # 文件安全扫描
    try:
        from src.core.file_scanner import quick_check
        if not quick_check(str(tmp_path), audio_file.filename):
            tmp_path.unlink(missing_ok=True)
            return jsonify({'error': '文件安全检测未通过，上传被拒绝'}), 403
    except Exception as scan_e:
        logger.debug(f"[Security] 转录音频扫描异常，拒绝上传: {scan_e}")
        tmp_path.unlink(missing_ok=True)
        return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

    try:
        from src.core.voice_input import get_voice_input
        vi = get_voice_input()
        text = vi.transcribe(str(tmp_path), language='zh')
        return jsonify({'text': text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)

# ==================== Conversations ====================

@app.route('/api/conversations', methods=['GET'])
def list_conversations():
    from src.models.database import get_conversations
    spec_id = request.args.get('spec_id', '')
    sid = spec_id if spec_id else None
    return jsonify(get_conversations(specialization_id=sid))

@app.route('/api/conversations', methods=['POST'])
def new_conversation():
    from src.models.database import create_conversation
    title = (request.json or {}).get('title', 'New Chat')
    conv_id = create_conversation(title)
    return jsonify({'id': conv_id, 'title': title})

@app.route('/api/conversations/<cid>', methods=['DELETE'])
def del_conversation(cid):
    from src.models.database import delete_conversation
    delete_conversation(cid)
    return jsonify({'ok': True})

@app.route('/api/conversations/batch-delete', methods=['POST'])
def batch_delete_conversations():
    from src.models.database import delete_conversations_batch
    data = request.json or {}
    ids = data.get('ids', [])
    if ids:
        delete_conversations_batch(ids)
    return jsonify({'ok': True, 'deleted': len(ids)})

@app.route('/api/conversations/auto-classify', methods=['POST'])
def auto_classify():
    from src.models.database import get_conversations, get_messages, update_conversation_category
    
    try:
        convs = get_conversations()
        unclassified = [c for c in convs if not c.get('category')]
        if not unclassified:
            return jsonify({'classified': 0, 'message': '所有对话已分类', 'total': len(convs)})
        
        classified_count = 0
        results = []
        
        # 每次处理最多 20 个未分类对话
        for conv in unclassified[:20]:
            msgs = get_messages(conv['id'], limit=20)
            if not msgs:
                continue
            
            chat_text = '\n'.join(
                f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content'][:200]}"
                for m in msgs
            )
            
            category = _quick_classify(chat_text)
            if category:
                update_conversation_category(conv['id'], category)
                classified_count += 1
                results.append({'id': conv['id'], 'title': conv.get('title', ''), 'category': category})
                logger.debug(f"[AutoClassify] 对话 {conv.get('title', conv['id'])[:20]} → {category}")
        
        logger.debug(f"[AutoClassify] 本次分类: {classified_count}/{len(unclassified)} 个")
        return jsonify({'classified': classified_count, 'total_unclassified': len(unclassified), 'results': results})
    except Exception as e:
        logger.error(f"[AutoClassify] 错误: {e}")
        return jsonify({'classified': 0, 'error': str(e)}), 500

def _quick_classify(text):
    """智能分类对话（v2: 高精度关键词 + 加权评分 + 阈值过滤）"""
    # 使用精确短语而不是单字词，避免误命中
    keywords = {
        '🤖 AI人工智能': {
            'strong': ['人工智能', '机器学习', '深度学习', '神经网络', '大模型', '提示词工程',
                       '自然语言处理', '计算机视觉', '强化学习', 'transformer', '注意力机制',
                       'fine-tune', '预训练模型', '知识图谱', 'ocr识别', '语音识别', '文本生成',
                       'ollama', 'harven ai', 'ollama本地模型', '云端模型'],
            'normal': ['ai助手', 'ai模型', 'ai生成', 'gpt-', 'chatgpt', 'llm模型', '推理速度',
                       '模型部署', 'token消耗', '量化模型', 'ai芯片', '训练数据', '微调模型',
                       'ai绘画', 'ai写作', 'ai编程', '智能体', 'agent'],
            'weak': ['ai', 'gpt', 'llm'],
        },
        '💻 编程开发': {
            'strong': ['写代码', '代码报错', '编程语言', '源代码', '代码仓库', '代码审查',
                       'git提交', 'pull request', '单元测试', '持续集成', '部署上线',
                       '前端框架', '后端服务', '数据库设计', '接口调试', 'restful api',
                       '代码重构', '性能优化', '内存泄漏', '并发编程', '多线程'],
            'normal': ['python脚本', 'java程序', 'javascript框架', 'typescript', 'react组件',
                       'vue项目', 'docker容器', 'kubernetes', 'linux命令', 'shell脚本',
                       'sql查询', 'html页面', 'css样式', '函数定义', '类继承', '模块导入',
                       '依赖安装', 'npm包', 'pip安装', '编译错误', '运行时错误'],
            'weak': ['代码', '编程', 'python', 'java', 'bug', '函数', '接口'],
        },
        '🔧 工具软件': {
            'strong': ['软件安装', '软件卸载', '插件开发', '浏览器插件', 'vscode插件',
                       '应用程序', '桌面软件', '命令行工具', '开发环境', '环境配置',
                       '端口冲突', '防火墙', '代理设置', '系统设置', '软件配置'],
            'normal': ['下载安装', '版本更新', '压缩文件', '文件管理', '截图工具',
                       '文本编辑器', 'ide', '编辑器', '终端', '补丁', '序列号'],
            'weak': ['软件', '工具', 'app', '插件', '扩展'],
        },
        '💰 财务投资': {
            'strong': ['股票行情', '基金净值', '投资组合', '理财产品', '银行利率',
                       '贷款利率', '税务申报', '财务报表', '年度预算', '资产配置',
                       '涨停板', '跌停板', '牛市熊市', '市盈率', '市净率', 'k线图',
                       '技术分析', '基本面分析', '龙头股', '仓位管理', '止损止盈'],
            'normal': ['股票', '基金', '理财', '投资收益', '存款', '贷款', '信用卡',
                       '汇率', '通货膨胀', '财经新闻', '股价', '港股', '美股', 'a股',
                       '买入卖出', '加仓减仓', '分红', '债券', '期货'],
            'weak': ['钱', '工资', '收入'],
        },
        '🚗 汽车': {
            'strong': ['汽车保养', '驾照考试', '购车攻略', '车辆年检', '汽车保险',
                       '违章查询', '加油优惠', '充电桩', '新能源汽车', '自动驾驶',
                       '发动机故障', '刹车片', '轮胎更换', '汽车维修'],
            'normal': ['驾照', '加油', '保养', '维修店', '4s店', '车型对比',
                       '堵车', '高速费', '停车位', '导航路线', '洗车'],
            'weak': ['汽车', '车'],
        },
        '📚 教育学习': {
            'strong': ['考试复习', '学习方法', '在线课程', '学位论文', '研究课题',
                       '毕业论文', '学术研究', '教育培训', '考研', '高考', '中考',
                       '课外辅导', '知识付费', '公开课', '慕课'],
            'normal': ['学校', '老师', '学生', '作业', '考试', '成绩', '教材',
                       '课堂', '课程表', '选修课', '专业选择', '奖学金'],
            'weak': ['学习', '读书'],
        },
        '💪 健康运动': {
            'strong': ['运动健身', '健康体检', '体重管理', '饮食营养', '睡眠质量',
                       '医疗保险', '挂号预约', '处方药', '健身计划', '跑步训练',
                       '瑜伽课程', '游泳馆', '体检报告', '血糖血压'],
            'normal': ['锻炼', '健身房', '蛋白粉', '卡路里', '有氧运动', '无氧运动',
                       '感冒发烧', '挂号', '看病', '住院', '手术', '康复'],
            'weak': ['健康', '运动', '跑步'],
        },
        '🎮 娱乐游戏': {
            'strong': ['电影推荐', '游戏攻略', '音乐播放', '动漫新番', '综艺节目',
                       '电视剧追剧', '游戏直播', '电竞赛事', '手游攻略', '端游推荐',
                       '影评', '票房', '游戏评测', 'steam游戏', '主机游戏'],
            'normal': ['看电影', '玩游戏', '听音乐', '追剧', '动漫', '综艺',
                       '视频', '直播', '歌曲', '小说', '漫画', '番剧'],
            'weak': ['游戏', '电影', '音乐', '小说', '动漫'],
        },
        '🍜 美食烹饪': {
            'strong': ['菜谱做法', '烹饪技巧', '美食推荐', '餐厅推荐', '外卖优惠',
                       '营养搭配', '家常菜', '烘焙教程', '食材采购', '调料配方'],
            'normal': ['做饭', '炒菜', '煲汤', '烤箱', '菜刀', '食材', '食谱',
                       '美味', '好吃', '甜品', '蛋糕', '面包'],
            'weak': ['吃', '菜'],
        },
        '🏠 生活家居': {
            'strong': ['天气预报', '房屋装修', '家具选购', '家电维修', '搬家攻略',
                       '收纳整理', '物业费', '租房合同', '买房攻略', '水电费',
                       '家政服务', '家居清洁', '装修设计'],
            'normal': ['天气', '搬家', '装修', '购物', '快递', '衣服', '家务',
                       '洗衣机', '空调', '冰箱', '热水器', '网购'],
            'weak': ['天气', '购物'],
        },
        '💼 工作职场': {
            'strong': ['工作汇报', '项目管理', '团队协作', '面试技巧', '简历优化',
                       '加班调休', '劳动合同', '跳槽经验', '职场规划', '绩效考核',
                       '年终总结', '商务谈判', '客户沟通', '产品需求'],
            'normal': ['上班', '下班', '会议', '项目', '同事', '老板', '加班',
                       '辞职', '面试', '简历', '工资条', '社保', '公积金',
                       '远程办公', '出差', '部门'],
            'weak': ['工作', '上班'],
        },
        '❤️ 情感社交': {
            'strong': ['恋爱关系', '婚姻生活', '家庭关系', '朋友聚会', '社交技巧',
                       '心理疏导', '情绪管理', '约会攻略', '分手挽回', '婆媳关系'],
            'normal': ['喜欢', '恋爱', '结婚', '离婚', '朋友', '约会', '表白',
                       '吵架', '和好', '感情', '心情', '焦虑', '压力'],
            'weak': ['喜欢', '感情', '心情'],
        },
        '📰 新闻资讯': {
            'strong': ['新闻联播', '时事热点', '国际新闻', '社会事件', '新闻报道',
                       '头条新闻', '热点事件', '突发新闻', '新闻发布会'],
            'normal': ['新闻', '热点', '事件', '报道', '头条', '舆论', '热搜',
                       '快讯', '评论文章'],
            'weak': ['消息'],
        },
        '🔮 风水命理': {
            'strong': ['风水学', '寻龙点穴', '罗盘', '阴阳宅', '龙脉', '穴位',
                       '八字命理', '紫微斗数', '六爻', '易经', '奇门遁甲',
                       '面相手相', '风水布局', '五行相克', '天干地支', '梅花易数'],
            'normal': ['风水', '命理', '算命', '卦象', '占卜', '八字', '生肖',
                       '星座运势', '黄历', '吉日', '旺运', '化煞', '招财'],
            'weak': [],
        },
    }

    scores = {}
    text_lower = text.lower()

    for cat, tiers in keywords.items():
        total_score = 0
        # 强匹配：每个 +3 分
        for kw in tiers.get('strong', []):
            if kw.lower() in text_lower:
                total_score += 3
        # 普通匹配：每个 +2 分
        for kw in tiers.get('normal', []):
            if kw.lower() in text_lower:
                total_score += 2
        # 弱匹配：每个 +1 分
        for kw in tiers.get('weak', []):
            if kw.lower() in text_lower:
                total_score += 1
        if total_score > 0:
            scores[cat] = total_score

    if scores:
        # 要求最高分至少达到 3 分（至少1个强匹配 或 组合匹配）
        best_cat = max(scores, key=scores.get)
        best_score = scores[best_cat]
        if best_score >= 3:
            return best_cat
        # 2分也可以接受（两个普通匹配 或 一个弱+一个普通）
        elif best_score >= 2:
            return best_cat
        # 只有1个弱匹配 = 不可靠，不分类

    return ''  # 不足以分类

@app.route('/api/conversations/<cid>/messages', methods=['GET'])
def get_msgs(cid):
    from src.models.database import get_messages
    return jsonify(get_messages(cid, limit=100))

@app.route('/api/conversations/<cid>/title', methods=['PUT'])
def rename_conversation(cid):
    try:
        from src.models.database import rename_conversation as db_rename
        title = (request.json or {}).get('title', '')
        db_rename(cid, title)
        return jsonify({'ok': True})
    except ImportError as e:
        logger.error(f"[API] 重命名对话 {cid} 失败：数据库模块未加载 ({e})")
        return jsonify({'ok': False, 'error': '数据库模块不可用'}), 500
    except Exception as e:
        logger.error(f"[API] 重命名对话 {cid} 失败: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

# ==================== Specializations ====================

@app.route('/api/specializations', methods=['GET'])
def list_specs():
    try:
        from src.models.database import get_specializations
        return jsonify(get_specializations())
    except (ImportError, sqlite3.Error, KeyError) as e:
        logger.error(f"[API] 获取专项培养失败: {e}")
        return jsonify([])

@app.route('/api/specializations', methods=['POST'])
def save_spec():
    try:
        from src.models.database import create_specialization, update_specialization
        d = request.json or {}
        sid = create_specialization(d['name'], description=d.get('description', ''))
        if d.get('system_prompt'):
            update_specialization(sid, system_prompt=d['system_prompt'])
        return jsonify({'id': sid})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/specializations/<sid>', methods=['DELETE'])
def del_spec(sid):
    try:
        from src.models.database import delete_specialization
        delete_specialization(sid)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Topic Monitor ====================

@app.route('/api/monitor/topics', methods=['GET'])
def list_monitor_topics():
    """获取所有监控主题"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        topics = monitor.get_topics()
        return jsonify({'topics': topics})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/topics', methods=['POST'])
def add_monitor_topic():
    """添加监控主题"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        
        data = request.get_json(force=True)
        topic_name = data.get('topic_name', '').strip()
        keywords = data.get('keywords', [])
        sources = data.get('sources', ['rss', 'web'])
        
        if not topic_name:
            return jsonify({'error': '主题名称不能为空'}), 400
        if not keywords:
            return jsonify({'error': '关键词不能为空'}), 400
        
        topic_id = monitor.add_topic(topic_name, keywords, sources)
        return jsonify({'success': True, 'id': topic_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/topics/<int:topic_id>', methods=['DELETE'])
def delete_monitor_topic(topic_id):
    """删除监控主题"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        monitor.delete_topic(topic_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/check/<int:topic_id>', methods=['POST'])
def check_monitor_topic(topic_id):
    """手动检查某个主题"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        items = monitor.check_topic(topic_id)
        return jsonify({'success': True, 'count': len(items)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/items', methods=['GET'])
def list_monitor_items():
    """获取监控结果"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        
        topic_id = request.args.get('topic_id', None)
        unread_only = request.args.get('unread', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 50))
        
        if topic_id:
            topic_id = int(topic_id)
        
        items = monitor.get_items(topic_id, unread_only, limit)
        return jsonify({'items': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/read/<int:item_id>', methods=['POST'])
def mark_item_read(item_id):
    """标记已读"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        monitor.mark_as_read(item_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/monitor/unread_count', methods=['GET'])
def get_unread_count():
    """获取未读数量"""
    try:
        from src.core.monitor import get_monitor
        monitor = get_monitor()
        
        topic_id = request.args.get('topic_id', None)
        if topic_id:
            topic_id = int(topic_id)
        
        count = monitor.get_unread_count(topic_id)
        return jsonify({'count': count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Knowledge Base ====================

@app.route('/api/knowledge/upload', methods=['POST'])
def upload_kb():
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        files = request.files.getlist('files')
        results = []
        for f in files:
            # 文件安全扫描（P0 安全，fail-closed：扫描失败=拒绝该文件）
            import tempfile as _tmp
            _tmp_path = Path(_tmp.mktemp(suffix=Path(f.filename).suffix or '.bin'))
            f.save(str(_tmp_path))
            from src.core.file_scanner import quick_check
            _scan_ok = False
            try:
                _scan_ok = quick_check(str(_tmp_path), f.filename)
            except Exception as _scan_e:
                logger.debug(f"[Security] 知识库文件扫描异常，拒绝上传: {_scan_e}")
                _tmp_path.unlink(missing_ok=True)
                results.append({
                    'name': f.filename, 'success': False, 'chunks': 0,
                    'format': '', 'message': '安全扫描服务异常，已拒绝上传',
                    'error': f'文件安全扫描异常: {_scan_e}', 'warning': None,
                })
                continue
            if not _scan_ok:
                _tmp_path.unlink(missing_ok=True)
                results.append({
                    'name': f.filename, 'success': False, 'chunks': 0,
                    'format': '', 'message': '安全检测未通过',
                    'error': '文件安全检测未通过，包含危险内容', 'warning': None,
                })
                continue

            kb_dir = PROJECT_ROOT / 'data' / 'knowledge'
            kb_dir.mkdir(parents=True, exist_ok=True)
            path = kb_dir / f.filename
            # 移动临时文件到目标位置
            import shutil
            shutil.move(str(_tmp_path), str(path))
            # P1: 知识库注入防御 — 导入前 LLM 复核
            _injection_safe = True
            _injection_reason = ""
            try:
                # 读取文件前 8000 字符进行审查
                _content_sample = Path(path).read_text(encoding="utf-8", errors="replace")[:8000]
                from src.core.independence import review_knowledge_content
                _injection_safe, _injection_reason = review_knowledge_content(
                    _content_sample, source=f.filename
                )
            except Exception as _inj_e:
                logger.debug(f"[Security] 知识注入审查异常: {_inj_e}")

            if not _injection_safe:
                # 删除已保存的文件
                Path(path).unlink(missing_ok=True)
                results.append({
                    'name': f.filename, 'success': False, 'chunks': 0,
                    'format': '', 'message': '知识注入防御已拦截',
                    'error': _injection_reason, 'warning': None,
                })
                continue

            # 导入到知识库（向量化索引）— 返回结构化结果
            result = kb.import_file(str(path))
            results.append({
                'name': f.filename,
                'success': result['success'],
                'chunks': result['chunks'],
                'format': result.get('format', ''),
                'message': result.get('message', ''),
                'error': result.get('error'),
                'warning': result.get('warning'),
            })
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/add_text', methods=['POST'])
def add_text_kb():
    """粘贴文本到知识库"""
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        
        data = request.get_json(force=True)
        title = data.get('title', '').strip()
        content = data.get('content', '').strip()
        
        if not title:
            return jsonify({'error': '标题不能为空'}), 400
        if not content:
            return jsonify({'error': '内容不能为空'}), 400

        # P1: 知识库注入防御 — 粘贴文本时 LLM 复核
        try:
            from src.core.independence import review_knowledge_content
            _safe, _reason = review_knowledge_content(content, source=f"粘贴文本: {title}")
            if not _safe:
                return jsonify({
                    'error': '知识注入防御已拦截',
                    'detail': _reason,
                    'code': 'injection_blocked'
                }), 403
        except Exception as _inj_e:
            logger.debug(f"[Security] 知识注入审查异常: {_inj_e}")

        doc_id = kb.add_text(title, content)
        if doc_id:
            return jsonify({'success': True, 'id': doc_id, 'message': '已添加到知识库'})
        else:
            return jsonify({'error': '添加失败，内容为空'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/list', methods=['GET'])
def list_kb():
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        stats = kb.get_stats()
        # 列出 data/knowledge 目录下的文件
        kb_dir = PROJECT_ROOT / 'data' / 'knowledge'
        files = []
        if kb_dir.exists():
            files = [{'name': f.name} for f in kb_dir.iterdir() if f.is_file()]
        return jsonify({'files': files, 'stats': stats})
    except (OSError, ImportError, KeyError) as e:
        logger.error(f"[API] 知识库文件列表失败: {e}")
        return jsonify({'files': [], 'stats': {}})

# ==================== Tasks ====================

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    try:
        from src.core.task_manager import get_task_manager
        tm = get_task_manager()
        tasks = tm.get_tasks(limit=50)
        # 转换前端需要的字段
        result = []
        for t in tasks:
            status = t.get('status', 'pending')
            result.append({
                'id': t['id'],
                'title': t.get('title', ''),
                'content': t.get('description', ''),
                'status': 'done' if status == 'completed' else status,
                'priority': {1: 'low', 2: 'medium', 3: 'high', 4: 'urgent'}.get(t.get('priority', 2), 'medium'),
                'progress': t.get('progress', 0),
                'created_at': t.get('created_at', ''),
            })
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify([])

@app.route('/api/tasks', methods=['POST'])
def create_task():
    try:
        from src.core.task_manager import get_task_manager
        d = request.json or {}
        tm = get_task_manager()
        priority_map = {'low': 1, 'medium': 2, 'high': 3, 'urgent': 4}
        priority = priority_map.get(d.get('priority', 'medium'), 2)
        task_id = tm.create_task(
            conversation_id=d.get('conversation_id', ''),
            title=d.get('title', '新任务'),
            description=d.get('content', ''),
            priority=priority
        )
        return jsonify({'id': task_id, 'title': d.get('title', '新任务')})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<tid>/complete', methods=['POST'])
def complete_task(tid):
    try:
        from src.core.task_manager import get_task_manager
        tm = get_task_manager()
        if hasattr(tm, 'update_task'):
            tm.update_task(tid, status='done', progress=100)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<tid>/progress', methods=['POST'])
def update_task_progress(tid):
    try:
        from src.core.task_manager import get_task_manager
        d = request.json or {}
        tm = get_task_manager()
        if hasattr(tm, 'update_task'):
            tm.update_task(tid, progress=d.get('progress', 0))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tasks/<tid>', methods=['DELETE'])
def delete_task(tid):
    try:
        from src.core.task_manager import get_task_manager
        tm = get_task_manager()
        if hasattr(tm, 'delete_task'):
            tm.delete_task(tid)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Memory Consolidation API ====================

@app.route('/api/memory/consolidate', methods=['POST'])
def consolidate_memory():
    """
    AI 智能提炼记忆
    从对话历史中用 Ollama 提取结构化记忆
    """
    try:
        from src.core.memory import ai_consolidate_memories
        d = request.json or {}
        conversation_id = d.get('conversation_id')  # None = 所有对话
        result = ai_consolidate_memories(conversation_id=conversation_id)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==================== Settings APIs (Complete) ====================

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get all settings"""
    return jsonify(config)

@app.route('/api/settings', methods=['POST'])
def save_settings():
    """Save settings (generic)"""
    data = request.json
    for section, values in data.items():
        if section not in config:
            config[section] = {}
        if isinstance(values, dict):
            for key, val in values.items():
                config[section][key] = val
        else:
            config[section] = values
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/user', methods=['POST'])
def save_user_settings():
    data = request.json
    if 'user' not in config:
        config['user'] = {}
    config['user'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/model', methods=['POST'])
def save_model_settings():
    data = request.json
    if 'ollama' not in config:
        config['ollama'] = {}
    config['ollama'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/morality', methods=['POST'])
def save_morality_settings():
    data = request.json
    if 'morality' not in config:
        config['morality'] = {}
    config['morality'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/security', methods=['POST'])
def save_security_settings():
    data = request.json
    if 'security' not in config:
        config['security'] = {}
    config['security'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/ui', methods=['POST'])
def save_ui_settings():
    data = request.json
    if 'ui' not in config:
        config['ui'] = {}
    config['ui'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/language', methods=['POST'])
def save_language_settings():
    data = request.json
    if 'language' not in config:
        config['language'] = {}
    config['language'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/web_search', methods=['POST'])
def save_web_search_settings():
    data = request.json
    if 'web_search' not in config:
        config['web_search'] = {}
    config['web_search'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Ollama ====================

@app.route('/api/settings/ollama', methods=['POST'])
def save_ollama_settings():
    data = request.json
    if 'ollama' not in config:
        config['ollama'] = {}
    config['ollama'].update(data)
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/server/info')
def server_info():
    """返回服务器访问信息：局域网 IP、端口、公网隧道 URL"""
    import socket
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    local_ip = get_local_ip()
    tunnel_url = None
    try:
        from cloudflare_tunnel import get_tunnel
        t = get_tunnel()
        tunnel_url = t.public_url if t.is_running else None
    except Exception:
        pass

    return jsonify({
        'local_ip': local_ip,
        'port': 5000,
        'local_url': f'http://{local_ip}:5000',
        'tunnel_url': tunnel_url,
    })


@app.route('/api/tunnel/start', methods=['POST'])
def tunnel_start():
    """手动启动 Cloudflare Tunnel"""
    try:
        from cloudflare_tunnel import start_tunnel
        url = start_tunnel()
        if url:
            return jsonify({'success': True, 'url': url})
        else:
            return jsonify({'success': False, 'error': '隧道启动失败，请检查网络'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/tunnel/stop', methods=['POST'])
def tunnel_stop():
    """停止 Cloudflare Tunnel"""
    try:
        from cloudflare_tunnel import stop_tunnel
        stop_tunnel()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ollama/status', methods=['GET'])
def ollama_status():
    try:
        from src.core.model_manager import check_ollama
        base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        ok, msg = check_ollama(base_url)
        current_model = config.get('ollama', {}).get('default_model', '')
        # 如果 config 里没有，尝试从运行中的 ModelManager 取
        if not current_model:
            try:
                mm = get_mm()
                current_model = mm.model
            except Exception:
                pass
        return jsonify({'connected': ok, 'message': msg, 'model': current_model})
    except (requests.RequestException, ConnectionError, OSError) as e:
        logger.error(f"[API] Ollama 连接检测失败: {e}")
        return jsonify({'connected': False, 'message': 'Cannot connect', 'model': ''})

@app.route('/api/ollama/models', methods=['GET'])
def ollama_models():
    try:
        import requests
        base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        data = r.json() if r.status_code == 200 else {}
        models = data.get('models', []) if isinstance(data, dict) else []
        return jsonify([m['name'] for m in models])
    except Exception as e:
        logger.error(f"[API] Ollama 模型列表获取失败: {e}")
        return jsonify([])

@app.route('/api/ollama/switch', methods=['POST'])
def ollama_switch():
    d = request.json or {}
    model = d.get('model', '')
    if not model:
        return jsonify({'error': 'No model specified'}), 400
    try:
        # VRAM 检查：拒绝切换至显存不足的模型
        try:
            from src.core.hardware_detector import detect_hardware, MODEL_DB
            spec = detect_hardware()
            model_entry = next((m for m in MODEL_DB if m['model_id'] == model), None)
            if model_entry and spec.has_dedicated_gpu:
                if spec.gpu_vram_gb < model_entry['min_vram_gb']:
                    logger.warning(
                        f"[API] 拒绝切换至 {model}：需要 {model_entry['min_vram_gb']}GB VRAM，"
                        f"本机仅 {spec.gpu_vram_gb:.1f}GB"
                    )
                    return jsonify({
                        'error': f'显存不足：{model} 需要至少 {model_entry["min_vram_gb"]}GB VRAM，'
                                 f'本机仅 {spec.gpu_vram_gb:.1f}GB。请选择更小的模型。'
                    }), 400
        except Exception as e:
            logger.warning(f"[API] VRAM 检查跳过: {e}")
        
        mm = get_mm()
        mm.set_model(model)
        config.setdefault('ollama', {})['default_model'] = model
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Ollama Model Management (L1) ====================

@app.route('/api/ollama/pull', methods=['POST'])
def ollama_pull():
    """拉取新模型（后台线程 + 进度轮询）"""
    data = request.json or {}
    model = data.get('model', '').strip()
    if not model:
        return jsonify({'error': '模型名称不能为空'}), 400

    try:
        import requests as pull_requests
        base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')

        r = pull_requests.get(f"{base_url}/api/tags", timeout=3)
        installed = [m['name'] for m in r.json().get('models', [])]
        if model in installed:
            return jsonify({'error': f'模型 {model} 已安装'}), 409
    except Exception:
        pass

    if not hasattr(app, '_pull_tasks'):
        app._pull_tasks = {}
    app._pull_tasks[model] = {'status': 'pulling', 'progress': 0, 'error': None}

    def _do_pull():
        try:
            import requests as pull_requests
            base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
            r = pull_requests.post(
                f"{base_url}/api/pull",
                json={"name": model, "stream": True},
                stream=True, timeout=1800
            )
            for line in r.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if 'completed' in chunk:
                            pct = chunk.get('completed', 0) / max(chunk.get('total', 1), 1)
                            app._pull_tasks[model]['progress'] = min(int(pct * 100), 99)
                        if chunk.get('status') == 'success':
                            app._pull_tasks[model]['status'] = 'done'
                            app._pull_tasks[model]['progress'] = 100
                            return
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
            app._pull_tasks[model]['status'] = 'done'
            app._pull_tasks[model]['progress'] = 100
        except Exception as e:
            app._pull_tasks[model]['status'] = 'error'
            app._pull_tasks[model]['error'] = str(e)
            logger.error(f"[Ollama] Pull model {model} failed: {e}")

    threading.Thread(target=_do_pull, daemon=True).start()
    return jsonify({'ok': True, 'model': model, 'message': f'开始拉取 {model}...'})


@app.route('/api/ollama/pull-status', methods=['GET'])
def ollama_pull_status():
    """查询模型拉取进度"""
    model = request.args.get('model', '')
    if not hasattr(app, '_pull_tasks'):
        return jsonify({'status': 'idle', 'progress': 0})
    task = app._pull_tasks.get(model, {})
    return jsonify({
        'status': task.get('status', 'idle'),
        'progress': task.get('progress', 0),
        'error': task.get('error')
    })


@app.route('/api/ollama/model/<model_name>', methods=['DELETE'])
def ollama_delete_model(model_name):
    """删除已安装的 Ollama 模型"""
    try:
        import requests as del_requests
        base_url = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        r = del_requests.delete(f"{base_url}/api/delete", json={"name": model_name}, timeout=30)
        if r.status_code == 200:
            if config.get('ollama', {}).get('default_model') == model_name:
                config['ollama']['default_model'] = ''
                try:
                    with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
                        yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
                except Exception as e:
                    logger.error(f"[Ollama] 删除模型 {model_name} 后保存配置失败: {e}，默认模型未清除")
            logger.info(f"[Ollama] Deleted model: {model_name}")
            return jsonify({'ok': True, 'message': f'模型 {model_name} 已删除'})
        else:
            return jsonify({'error': r.text}), r.status_code
    except Exception as e:
        logger.error(f"[Ollama] Delete model {model_name} failed: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== 模型推荐 & 模型市场 (L1) ====================

@app.route('/api/models/recommend', methods=['GET'])
def models_recommend():
    """根据硬件配置推荐模型"""
    try:
        from src.core.model_recommend import detect_gpu_vram, detect_system_ram, recommend_models
        vram_gb, gpu_name = detect_gpu_vram()
        ram_gb = detect_system_ram()
        result = recommend_models(vram_gb, ram_gb)
        return jsonify({
            'ok': True,
            'recommended': result.get('recommended', []),
            'installable': result.get('installable', []),
            'too_large': result.get('too_large', []),
            'hardware': result.get('hardware', {}),
        })
    except Exception as e:
        logger.error(f"[Models] 推荐失败: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


# ==================== App Update Detection (L2) ====================

UPDATE_CHECK_URL = config.get('update', {}).get('check_url', 'https://harvenhub.com/version.json')
_update_download_progress = {'status': 'idle', 'progress': 0, 'error': None, 'file': None}


def _version_greater(a, b):
    """比较语义化版本号 a > b"""
    def _parse(v):
        parts = v.replace('v', '').split('.')
        return tuple(int(p) for p in parts if p.isdigit())
    try:
        return _parse(a) > _parse(b)
    except (ValueError, IndexError):
        return a != b


@app.route('/api/version/check', methods=['GET'])
def version_check():
    """检测新版本"""
    try:
        import requests as ver_requests
        current = config.get('app', {}).get('version', '0.1.0')
        try:
            r = ver_requests.get(UPDATE_CHECK_URL, timeout=8)
            if r.status_code == 200:
                latest = r.json()
                latest_ver = latest.get('version', current)
                update_url = latest.get('url', '')
                changelog = latest.get('changelog', '')
                has_update = _version_greater(latest_ver, current)
                return jsonify({
                    'current': current,
                    'latest': latest_ver,
                    'has_update': has_update,
                    'url': update_url,
                    'changelog': changelog,
                    'checked_at': datetime.now().isoformat()
                })
            return jsonify({'current': current, 'error': f'服务器返回 {r.status_code}', 'has_update': False})
        except Exception as e:
            # 捕获所有异常（DNS 失败、json 解析错误、socket 错误等）
            return jsonify({
                'current': current,
                'error': '无法连接更新服务器',
                'has_update': False,
                'detail': str(e)[:200]
            })
    except Exception as e:
        return jsonify({'current': config.get('app', {}).get('version', '0.1.0'), 'error': str(e)[:200], 'has_update': False})


@app.route('/api/version/update', methods=['POST'])
def version_download():
    """下载并安装更新包"""
    global _update_download_progress
    data = request.json or {}
    download_url = data.get('url', '')
    if not download_url:
        return jsonify({'error': '下载地址为空'}), 400

    _update_download_progress = {'status': 'downloading', 'progress': 0, 'error': None, 'file': None}

    def _do_update():
        global _update_download_progress
        try:
            import requests as dl_requests
            r = dl_requests.get(download_url, stream=True, timeout=600)
            total = int(r.headers.get('content-length', 0))
            zip_path = os.path.join(tempfile.gettempdir(), 'harven_update.zip')
            downloaded = 0

            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            _update_download_progress['progress'] = min(int(downloaded * 100 / total), 95)

            _update_download_progress['progress'] = 96
            _update_download_progress['status'] = 'extracting'

            import zipfile as zf_lib
            extract_dir = os.path.join(tempfile.gettempdir(), 'harven_update_extract')
            with zf_lib.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            _update_download_progress['progress'] = 98

            import shutil
            src = extract_dir
            if os.path.isdir(os.path.join(extract_dir, 'HarvenAI')):
                src = os.path.join(extract_dir, 'HarvenAI')

            for root2, dirs2, files2 in os.walk(src):
                rel = os.path.relpath(root2, src)
                dest_dir = os.path.join(str(PROJECT_ROOT), rel)
                os.makedirs(dest_dir, exist_ok=True)
                for f in files2:
                    shutil.copy2(os.path.join(root2, f), os.path.join(dest_dir, f))

            try:
                shutil.rmtree(extract_dir, ignore_errors=True)
                os.remove(zip_path)
            except Exception:
                pass

            _update_download_progress['status'] = 'done'
            _update_download_progress['progress'] = 100
            _update_download_progress['file'] = str(PROJECT_ROOT)
            logger.info("[Update] Update complete, restart required")

        except Exception as e:
            _update_download_progress['status'] = 'error'
            _update_download_progress['error'] = str(e)
            logger.error(f"[Update] Update failed: {e}")

    threading.Thread(target=_do_update, daemon=True).start()
    return jsonify({'ok': True, 'message': '开始下载更新...'})


@app.route('/api/version/update-progress', methods=['GET'])
def version_update_progress():
    """查询更新进度"""
    return jsonify(_update_download_progress)


# ==================== Privacy Password ====================

@app.route('/api/privacy/set-password', methods=['POST'])
def set_privacy_password():
    data = request.json
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    current = config.get('security', {}).get('privacy_password', '')
    
    if current and current != old_password:
        return jsonify({'error': '旧密码不正确'}), 400
    
    if 'security' not in config:
        config['security'] = {}
    config['security']['privacy_password'] = new_password
    
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== Antivirus (兼容别名) ====================

@app.route('/api/security/test-antivirus', methods=['POST'])
def security_test_antivirus():
    """安全面板杀毒测试（别名 → /api/antivirus/test）"""
    return test_antivirus()

@app.route('/api/antivirus/test', methods=['POST'])
def test_antivirus():
    data = request.json
    path = data.get('path', '')
    if not path:
        return jsonify({'error': '路径不能为空'}), 400

    # ★ 安全：白名单限制，仅允许运行已知杀毒软件路径
    ALLOWED_AV_PATTERNS = [
        r'C:\\Program Files( \(x86\))?\\Windows Defender\\MpCmdRun\.exe',
        r'C:\\Program Files( \(x86\))?\\ClamWin\\bin\\clamscan\.exe',
        r'C:\\Program Files\\ClamAV\\clamscan\.exe',
        r'/usr/bin/clamscan',
        r'/usr/local/bin/clamscan',
    ]
    import re
    if not any(re.match(p, path, re.IGNORECASE) for p in ALLOWED_AV_PATTERNS):
        return jsonify({'error': '不允许运行该路径，仅支持已知杀毒软件'}), 403

    if not os.path.exists(path):
        return jsonify({'error': '路径不存在'}), 400

    try:
        # 对 Windows Defender 使用 /? 而非 --version
        args = ['/?'] if 'MpCmdRun' in path else ['--version']
        result = subprocess.run([path] + args,
                              capture_output=True, text=True, timeout=5, shell=False)
        return jsonify({
            'success': True,
            'version': result.stdout[:100] if result.stdout else 'Unknown'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ==================== System Info ====================

@app.route('/api/system/info', methods=['GET'])
def get_system_info():
    """系统信息 — 各模块独立 try/except，确保不会整体 500"""
    import platform
    info = {'os': platform.system(), 'arch': platform.machine()}
    
    # CPU
    try:
        import psutil
        info['cpu_percent'] = psutil.cpu_percent(interval=0.5)
    except (ImportError, AttributeError) as e:
        logger.warning(f"[API] psutil 不可用: {e}")
        info['cpu_percent'] = -1
    
    # 内存
    try:
        import psutil
        info['memory'] = {
            'total': psutil.virtual_memory().total // (1024**3),
            'used': psutil.virtual_memory().used // (1024**3),
            'percent': psutil.virtual_memory().percent
        }
    except (ImportError, AttributeError) as e:
        logger.warning(f"[API] psutil 内存读取失败: {e}")
        info['memory'] = {'total': 0, 'used': 0, 'percent': 0}
    
    # 磁盘
    try:
        import psutil, os
        if platform.system() == 'Windows':
            disk_path = os.environ.get('SYSTEMDRIVE', 'C:') + '\\'
        else:
            disk_path = '/'
        info['disk'] = {
            'total': psutil.disk_usage(disk_path).total // (1024**3),
            'free': psutil.disk_usage(disk_path).free // (1024**3)
        }
    except (ImportError, AttributeError, OSError, PermissionError) as e:
        logger.warning(f"[API] psutil 磁盘读取失败: {e}")
        info['disk'] = {'total': 0, 'free': 0}
    
    # GPU
    try:
        import torch
        if torch.cuda.is_available():
            info['gpu'] = {
                'name': torch.cuda.get_device_name(0),
                'memory_total': torch.cuda.get_device_properties(0).total_memory // (1024**3),
                'memory_allocated': torch.cuda.memory_allocated(0) // (1024**3)
            }
        else:
            info['gpu'] = {'name': 'No CUDA GPU', 'memory_total': 0, 'memory_allocated': 0}
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.warning(f"[API] GPU 信息获取失败: {e}")
        info['gpu'] = {'name': 'Unknown', 'memory_total': 0, 'memory_allocated': 0}
    
    return jsonify(info)

# ==================== GitHub Download ====================

@app.route('/api/github/download', methods=['POST'])
def github_download():
    """手动触发 GitHub 仓库下载（不通过对话）"""
    data = request.json or {}
    repo_url = data.get('url', '').strip()
    branch = data.get('branch', 'main')

    if not repo_url:
        return jsonify({'error': '缺少 GitHub URL'}), 400

    try:
        from src.core.github_downloader import download_and_import_github, detect_github_urls
        urls = detect_github_urls(repo_url)
        if not urls:
            return jsonify({'success': False, 'message': '未检测到有效的 GitHub URL'}), 400

        result = download_and_import_github(repo_url, branch)
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except ImportError:
        return jsonify({'error': 'GitHub 下载模块未加载'}), 500
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/github/detect', methods=['POST'])
def github_detect():
    """检测文本中的 GitHub URL（预览用）"""
    data = request.json or {}
    text = data.get('text', '')
    try:
        from src.core.github_downloader import detect_github_urls
        urls = detect_github_urls(text)
        return jsonify({'urls': urls, 'count': len(urls)})
    except ImportError:
        return jsonify({'urls': [], 'count': 0, 'error': '模块未加载'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Plugin Installer ====================

PLUGIN_DIR = str(PROJECT_ROOT / 'plugins')

@app.route('/api/plugins/search', methods=['GET'])
def search_plugins():
    """搜索 GitHub 上的插件"""
    query = request.args.get('q', 'harven-ai plugin')
    try:
        from src.core.plugin_installer import search_github_plugins
        results = search_github_plugins(query, limit=10)
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ⚠️ /api/plugins/install 已迁移至 plugin_mgmt_api.py Blueprint（统一处理 GitHub/URL/代码三种安装方式）

@app.route('/api/plugins/scan', methods=['POST'])
def scan_plugin():
    """扫描已下载的插件代码（预览用）"""
    data = request.json or {}
    plugin_name = data.get('plugin_name', '')
    if not plugin_name:
        return jsonify({'error': '缺少 plugin_name'}), 400

    # ★ 安全：禁止路径遍历，plugin_name 必须是纯目录名
    import re
    if not re.match(r'^[a-zA-Z0-9_\-.]+$', plugin_name):
        return jsonify({'error': '插件名包含非法字符'}), 400
    if '..' in plugin_name or '/' in plugin_name or '\\' in plugin_name:
        return jsonify({'error': '插件名包含路径分隔符'}), 400

    plugin_path = os.path.join(PLUGIN_DIR, plugin_name)
    # ★ 双重检查：确保解析后的路径仍在 PLUGIN_DIR 内
    real_plugin_path = os.path.realpath(plugin_path)
    real_plugin_dir = os.path.realpath(PLUGIN_DIR)
    if not real_plugin_path.startswith(real_plugin_dir + os.sep) and real_plugin_path != real_plugin_dir:
        return jsonify({'error': '插件路径非法'}), 403

    if not os.path.exists(plugin_path):
        return jsonify({'error': '插件不存在'}), 404

    try:
        from src.core.plugin_installer import scan_plugin_package, compute_hash
        result = scan_plugin_package(plugin_path)
        # 计算哈希
        hashes = {}
        for root, dirs, files in os.walk(plugin_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                with open(fpath, 'rb') as f:
                    hashes[os.path.relpath(fpath, plugin_path)] = compute_hash(f.read())
        result['hashes'] = hashes
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/plugins/list-installed', methods=['GET'])
def list_installed_plugins():
    """列出已安装的插件"""
    try:
        from src.core.plugin_installer import list_available_plugins
        plugins = list_available_plugins(PLUGIN_DIR)
        return jsonify({'plugins': plugins})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Resource Hub — 全网资源发现 ====================

@app.route('/api/resource-hub/channels', methods=['GET'])
def rh_channels():
    """获取可用搜索渠道列表"""
    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()
        return jsonify({'channels': hub.get_channels()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/resource-hub/browse', methods=['GET'])
def rh_browse():
    """浏览模式：获取指定渠道的热门技能列表（无需搜索词）。

    用法：
        GET /api/resource-hub/browse?channels=skillhub,clawhub
        GET /api/resource-hub/browse?channels=skillhub&category=ai-intelligence

    适用场景：
        - 用户点击渠道 Tab 时自动触发
        - 像逛商店一样浏览各渠道的技能
    """
    channels_str = request.args.get('channels', '')
    category = request.args.get('category', '')
    limit = int(request.args.get('limit', 30))

    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()

        channels = [c.strip() for c in channels_str.split(',') if c.strip()] if channels_str else None
        categories = None
        if category and channels:
            categories = {channels[0]: category}

        result = hub.browse_channels(channels=channels, categories=categories, limit=limit)
        # 增强：返回是否支持浏览
        for ch_info in result.get('channels', []):
            ch_name = ch_info.get('id')
            if ch_name and ch_name in hub._channels:
                adapter = hub._channels[ch_name]
                ch_info['supports_browse'] = adapter.supports_browse
                ch_info['categories'] = adapter.browse_categories()

        return jsonify({'success': True, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/browse/<channel>', methods=['GET'])
def rh_browse_channel(channel):
    """浏览单个渠道：获取该渠道的热门技能列表。

    用法：
        GET /api/resource-hub/browse/skillhub?limit=20
        GET /api/resource-hub/browse/clawhub?limit=20&category=developer-tools
    """
    category = request.args.get('category', '')
    limit = int(request.args.get('limit', 20))

    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()

        ch = hub._channels.get(channel)
        if not ch:
            return jsonify({'success': False, 'error': f'未知渠道: {channel}'}), 404
        if not ch.supports_browse:
            return jsonify({'success': False, 'error': f'渠道 {channel} 不支持浏览模式'}), 400

        results = ch.browse(category=category if category else None, limit=limit)
        categories = ch.browse_categories()

        return jsonify({
            'success': True,
            'channel': channel,
            'channel_label': ch.label,
            'channel_icon': ch.icon,
            'count': len(results),
            'categories': categories,
            'results': results,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/search-official-first', methods=['GET'])
def rh_search_official_first():
    """HarvenHub 优先搜索：先搜官方市场，无结果自动降级到全网渠道

    策略：
    1. 优先搜索 HarvenHub 官方技能市场
    2. 如果 HarvenHub 有结果 → 直接返回（标注官方精选）
    3. 如果 HarvenHub 无结果/未上线 → 自动搜索全网渠道
    4. 结果中明确标注来源渠道，HarvenHub 结果排在最前
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': '请输入搜索关键词'}), 400

    resource_type = request.args.get('type', 'all')
    limit = int(request.args.get('limit', 20))

    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()

        # 第一步：搜索 HarvenHub
        harvenhub_ch = hub._channels.get('harvenhub')
        harvenhub_results = []
        if harvenhub_ch and harvenhub_ch.enabled:
            harvenhub_results = harvenhub_ch.search(query, resource_type, limit=limit)

        # 第二步：无论 HarvenHub 有无结果，同时搜索全网（给用户更多选择）
        # 但标记 HarvenHub 结果优先展示
        all_results = hub.search(query, resource_type=resource_type, channels=None, limit=limit)

        # 增强返回信息
        return jsonify({
            'success': True,
            'harvenhub_count': len(harvenhub_results),
            'search_strategy': 'official_first',
            'harvenhub_status': {
                'available': len(harvenhub_results) > 0,
                'message': (f'🏠 HarvenHub 官方精选 {len(harvenhub_results)} 个结果'
                           if harvenhub_results else
                           '🏠 HarvenHub 正在建设中，已自动为您搜索全网资源'),
                'hint': ('官方认证资源，安全可靠' if harvenhub_results else
                        'HarvenHub 上线后将优先展示官方精选内容'),
            },
            **all_results,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/search', methods=['GET'])
def rh_search():
    """全网聚合搜索：技能/插件/知识/工具"""
    query = request.args.get('q', '').strip()
    if not query or len(query) < 1:
        return jsonify({'error': '请输入搜索关键词'}), 400

    resource_type = request.args.get('type', 'all')  # all/plugin/skill/knowledge/tool
    channels_raw = request.args.get('channels', '')  # 逗号分隔，空=全部
    limit = int(request.args.get('limit', 20))

    channels = [c.strip() for c in channels_raw.split(',') if c.strip()] if channels_raw else None

    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()
        results = hub.search(query, resource_type=resource_type, channels=channels, limit=limit)
        return jsonify({'success': True, **results})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/install', methods=['POST'])
def rh_install():
    """从任意渠道安装资源（含许可证检查和归属记录）

    安全流程：
    1. 许可证检测 → 合规性判断
    2. GPL/AGPL/无LICENSE → 需前端确认后才能安装
    3. 安装完成后 → 自动记录版权归属（attributions.json）
    4. 来源标注 → 知识库/技能中标注原始出处
    """
    data = request.json or {}
    source = data.get('source', 'github')
    resource_id = data.get('resource_id', '')
    url = data.get('url', '')
    repo_url = data.get('repo_url', url or '')  # GitHub/Gitee 使用 repo_url
    rtype = data.get('resource_type', 'plugin')
    name = data.get('name', '')
    license_text = data.get('license_text', '')  # 前端传来的许可证文本
    skip_license_check = data.get('skip_license_check', False)  # 用户已确认风险

    if not resource_id and not repo_url:
        return jsonify({'error': '缺少 resource_id 或 repo_url'}), 400

    try:
        from src.core.resource_hub import get_resource_hub, detect_license
        hub = get_resource_hub()

        # === 许可证合规检查 ===
        license_info = None
        if license_text:
            license_info = detect_license(license_text)

        if license_info and not skip_license_check:
            policy = hub.enforce_license_policy(license_info, source)
            if policy.get('require_confirm') and not policy.get('allowed'):
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'block_reason': policy.get('block_reason', 'license_policy'),
                    'reason': policy.get('reason', '许可证不合规'),
                    'license': policy.get('license', {}),
                    'hint': '请确认了解风险后重试安装（将 skip_license_check 设为 true）',
                }), 403

        # === 执行安装 ===
        # GitHub/Gitee 安装（插件或技能）
        if source in ('github', 'gitee'):
            install_url = repo_url or f"https://{source}.com/{resource_id}"
            result = hub.install_from_github(install_url)

            # 安装成功后记录归属
            if result.get('success'):
                hub.record_attribution(
                    name=result.get('name', name or resource_id),
                    source=source,
                    author=data.get('author', ''),
                    license_info=license_info or {'license': 'unknown', 'category': 'danger'},
                    resource_type=result.get('type', rtype),
                )

            # 如果是知识类型，标记来源
            if result.get('type') == 'knowledge':
                result['source_attribution'] = f'来源: {source}/{resource_id}'
            return jsonify(result)

        # PyPI 安装
        elif source == 'pypi':
            import subprocess
            pkg_name = resource_id or name
            if not pkg_name:
                return jsonify({'error': '缺少包名'}), 400

            # PyPI 安全提示
            if not skip_license_check:
                pypi_warning = (
                    f'⚠️ 即将安装 PyPI 包: {pkg_name}\n\n'
                    'PyPI 包的许可证信息需安装后才能确认。\n'
                    '建议：\n'
                    '• 优先选择有明确 LICENSE 声明的包\n'
                    '• 安装后可在 PyPI 页面查看许可证\n'
                    '• GPL 包可能影响你的项目许可证选择'
                )
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'block_reason': 'pypi_confirm',
                    'reason': pypi_warning,
                    'hint': '确认后设置 skip_license_check=true 重新安装',
                }), 403

            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', pkg_name],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                hub.record_attribution(
                    name=pkg_name, source='pypi',
                    author='', license_info={'license': 'unknown', 'category': 'danger'},
                    resource_type='tool',
                )
                return jsonify({'success': True, 'type': 'pypi',
                               'name': pkg_name, 'message': f'已安装 {pkg_name}',
                               'hint': '建议在 PyPI 页面确认许可证信息: https://pypi.org/project/' + pkg_name})
            else:
                return jsonify({'success': False, 'error': result.stderr[:500]})

        # Winget 安装
        elif source == 'winget':
            import subprocess
            pkg_id = resource_id or name
            if not pkg_id:
                return jsonify({'error': '缺少软件包 ID'}), 400

            if not skip_license_check:
                winget_warning = (
                    f'⚠️ 即将通过 Winget 安装: {pkg_id}\n\n'
                    'Winget 软件来自 Microsoft 社区，Harven 无法验证其安全性。\n'
                    '建议：\n'
                    '• 确认软件来自可信发布者\n'
                    '• 查看软件官网了解许可证条款\n'
                    '• 注意第三方软件可能包含捆绑安装'
                )
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'block_reason': 'winget_confirm',
                    'reason': winget_warning,
                    'hint': '确认后设置 skip_license_check=true 重新安装',
                }), 403

            result = subprocess.run(
                ['winget', 'install', pkg_id, '--accept-source-agreements', '--accept-package-agreements'],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                hub.record_attribution(
                    name=pkg_id, source='winget',
                    author='', license_info={'license': 'unknown', 'category': 'danger'},
                    resource_type='tool',
                )
                return jsonify({'success': True, 'type': 'winget',
                               'name': pkg_id, 'message': f'已安装 {pkg_id}'})
            else:
                return jsonify({'success': False, 'error': result.stderr[:500]})

        # HuggingFace
        elif source == 'huggingface':
            return jsonify({
                'success': False,
                'error': 'HuggingFace 资源请通过模型管理页面下载。可直接在对话中@模型名。'
            })

        else:
            return jsonify({'error': f'不支持的来源: {source}'}), 400

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/generate-skill', methods=['POST'])
def rh_generate_skill():
    """AI 根据描述自动生成技能"""
    data = request.json or {}
    description = data.get('description', '').strip()
    if not description:
        return jsonify({'error': '请描述你需要的技能'}), 400

    try:
        from src.core.resource_hub import get_resource_hub
        hub = get_resource_hub()
        result = hub.ai_generate_skill(description)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/resource-hub/license-check', methods=['POST'])
def rh_license_check():
    """检测资源的 LICENSE"""
    data = request.json or {}
    text = data.get('text', '')

    if not text:
        return jsonify({'error': '缺少文本内容'}), 400

    from src.core.resource_hub import detect_license
    result = detect_license(text)
    return jsonify({'success': True, 'license': result})


# ==================== Main ====================

@app.route('/api/test')
def test_route():
    return jsonify({'success': True, 'message': 'Test route works!'})

# ==================== Knowledge Auto-Import ====================

@app.route('/api/kb/auto-import/status', methods=['GET'])
def kb_auto_import_status():
    """查询知识库自动导入状态"""
    from src.core.knowledge_base import get_auto_import_status
    return jsonify(get_auto_import_status())

@app.route('/api/kb/auto-import/list', methods=['GET'])
def kb_imported_list():
    """查询已导入文件列表"""
    from src.core.knowledge_base import get_imported_files_list
    return jsonify({'files': get_imported_files_list()})

@app.route('/api/kb/auto-import/trigger', methods=['POST'])
def kb_trigger_import():
    """手动触发一次自动导入"""
    from src.core.knowledge_base import get_knowledge_base, auto_import_new_files
    kb = get_knowledge_base()
    t = auto_import_new_files(kb)
    if t is None:
        return jsonify({'success': False, 'message': '导入正在进行中'})
    return jsonify({'success': True, 'message': '导入已启动'})


@app.route('/api/kb/formats', methods=['GET'])
def kb_formats():
    """获取知识库支持的文件格式列表"""
    from src.core.knowledge_base import get_supported_formats_info
    info = get_supported_formats_info()
    return jsonify({
        'supported': info['supported'],
        'unsupported': info['unsupported'],
        'total_supported': info['total_supported'],
        'total_unsupported': info['total_unsupported'],
    })

# ==================== Phase 5: 手机联动 ====================

@app.route('/api/mobile/token', methods=['POST'])
def mobile_create_token():
    """创建手机连接 Token + 二维码"""
    data = request.json or {}
    name = data.get('name', '我的手机')
    hours = data.get('hours', 72)
    try:
        from src.core.mobile_connector import create_mobile_token, generate_qr_svg
        result = create_mobile_token(name=name, lifetime_hours=hours)
        result['qr_svg'] = generate_qr_svg(result['qr_url'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile/qrcode', methods=['GET'])
def mobile_qrcode():
    """获取二维码 SVG"""
    name = request.args.get('name', '移动设备')
    hours = int(request.args.get('hours', 72))
    try:
        from src.core.mobile_connector import create_mobile_token, generate_qr_svg
        result = create_mobile_token(name=name, lifetime_hours=hours)
        svg = generate_qr_svg(result['qr_url'])
        return svg, 200, {'Content-Type': 'image/svg+xml'}
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile/devices', methods=['GET'])
def mobile_list_devices():
    """列出已授权设备"""
    try:
        from src.core.mobile_connector import list_devices
        return jsonify({'devices': list_devices()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile/status', methods=['GET'])
def mobile_status():
    """移动端连接状态"""
    try:
        from src.core.mobile_connector import get_device_status
        return jsonify(get_device_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile/revoke', methods=['POST'])
def mobile_revoke_token():
    """吊销设备 Token"""
    data = request.json or {}
    token = data.get('token', '')
    if not token:
        return jsonify({'error': '缺少 token'}), 400
    try:
        from src.core.mobile_connector import revoke_token
        ok = revoke_token(token)
        return jsonify({'success': ok, 'message': '已吊销' if ok else 'Token 不存在或已失效'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/mobile/chat', methods=['POST'])
def mobile_chat():
    """移动端代理对话"""
    data = request.json or {}
    token = data.get('token', '')
    message = data.get('message', '')
    conv_id = data.get('conversation_id')
    spec_id = data.get('specialization_id')

    if not token or not message:
        return jsonify({'error': '缺少 token 或 message'}), 400

    try:
        from src.core.mobile_connector import proxy_mobile_chat
        result = proxy_mobile_chat(token, message, conv_id, spec_id)
        if 'error' in result:
            return jsonify(result), 403 if result.get('code') == 'invalid_token' else 500
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/mobile', methods=['GET'])
def mobile_landing():
    """移动端落地页"""
    token = request.args.get('token', '')
    return render_template('mobile.html', token=token)


@app.route('/test/mobile-chat', methods=['GET'])
def test_mobile_chat():
    """测试路由"""
    return "ROUTE_TEST_12345", 200


@app.route('/mobile/chat', methods=['GET'])
def mobile_chat_page():
    """移动端聊天界面（平板优化）"""
    token = request.args.get('token', '')
    return render_template('mobile-chat.html', token=token)


# ==================== 移动端通知 API ====================

@app.route('/api/mobile/notifications', methods=['GET'])
def mobile_get_notifications():
    """获取移动设备的未读通知"""
    token = request.args.get('token', '')
    if not token:
        return jsonify({'success': False, 'error': '缺少 token'}), 400
    
    try:
        from src.core.mobile_connector import validate_token, get_notifications
        
        device = validate_token(token)
        if not device:
            return jsonify({'success': False, 'error': '无效或过期的 token'}), 401
        
        notifications = get_notifications(token)
        return jsonify({'success': True, 'notifications': notifications})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mobile/notifications/read', methods=['POST'])
def mobile_mark_notifications_read():
    """标记移动设备的通知为已读"""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    
    if not token:
        return jsonify({'success': False, 'error': '缺少 token'}), 400
    
    try:
        from src.core.mobile_connector import validate_token, mark_notifications_read
        
        device = validate_token(token)
        if not device:
            return jsonify({'success': False, 'error': '无效或过期的 token'}), 401
        
        count = mark_notifications_read(token)
        return jsonify({'success': True, 'marked': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mobile/notify', methods=['POST'])
def mobile_push_notification():
    """向移动设备推送通知（Harven AI 调用）"""
    data = request.get_json(silent=True) or {}
    token = data.get('token', '')
    message = data.get('message', '')
    title = data.get('title', 'Harven AI')
    extra = data.get('data', {})
    
    if not token or not message:
        return jsonify({'success': False, 'error': '缺少 token 或 message'}), 400
    
    try:
        from src.core.mobile_connector import push_notification
        
        success = push_notification(token, message, title, extra)
        if success:
            return jsonify({'success': True, 'message': '通知已推送'})
        else:
            return jsonify({'success': False, 'error': '设备不存在或已禁用'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/visitor_login', methods=['GET', 'POST'])
def visitor_login():
    """访问码校验页（公网访客入口）"""
    from src.core.visitor_manager import verify_access_code, generate_session_token, register_session
    from flask import make_response, redirect, request as req

    next_url = request.args.get('next', '/')

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if verify_access_code(code):
            sid = generate_session_token()
            ip = req.remote_addr or '127.0.0.1'
            ua = req.user_agent.string[:256] if req.user_agent else ''
            register_session(sid, ip, ua)
            resp = make_response(redirect(next_url))
            resp.set_cookie('hv_sid', sid, max_age=86400 * 7, httponly=True, samesite='Lax')
            return resp
        # 错误
        return _visitor_login_html(error=True), 200

    return _visitor_login_html(error=False), 200


# ========================
# 首次启动引导页
# ========================
@app.route('/first_run')
def first_run():
    """首次启动引导页"""
    config_path = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'first_run.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    is_first = not os.path.exists(config_path)
    return render_template('first_run.html', is_first_run=is_first)


@app.route('/api/settings/first-run/check')
def api_first_run_check():
    """检查是否首次启动"""
    config_path = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'first_run.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    is_first = not os.path.exists(config_path)
    return jsonify({'is_first_run': is_first})


@app.route('/api/settings/first-run/complete', methods=['POST'])
def api_first_run_complete():
    """标记首次启动已完成，并写入用户选择的默认模型到 config.yaml"""
    config_path = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'first_run.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    import json
    from datetime import datetime

    # 写入 first_run.json
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump({'completed': True, 'timestamp': str(datetime.now())}, f, ensure_ascii=False)

    # 写入用户选择的默认模型到 config.yaml
    try:
        data = request.get_json(silent=True) or {}
        default_model = data.get('default_model', '').strip()
        if default_model:
            from src.core import config as _cfg
            cfg = _cfg.load_config()
            if 'ollama' not in cfg:
                cfg['ollama'] = {}
            cfg['ollama']['default_model'] = default_model
            _cfg.save_config(cfg)
    except Exception as e:
        logger.error(f"[FirstRun] 写入默认模型到 config.yaml 失败: {e}")

    return jsonify({'status': 'ok'})


@app.route('/api/settings/db-status')
def api_db_status():
    """检查数据库初始化状态"""
    try:
        import os
        from src.core import config as _cfg
        db_path = _cfg.get_sqlite_path()
        if not os.path.exists(db_path):
            return jsonify({'initialized': False, 'error': '数据库文件不存在'})
        # 检查关键表是否存在
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('conversations','messages','knowledge','users')")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        required = ['conversations', 'messages', 'knowledge', 'users']
        missing = [t for t in required if t not in tables]
        return jsonify({'initialized': len(missing) == 0, 'tables': tables, 'missing': missing})
    except Exception as e:
        return jsonify({'initialized': False, 'error': str(e)}), 500


@app.route('/api/settings/db-init', methods=['POST'])
def api_db_init():
    """初始化数据库表结构"""
    try:
        import os
        from src.core import config as _cfg
        db_path = _cfg.get_sqlite_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # 创建关键表
        cursor.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            created_at TEXT,
            updated_at TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            source TEXT,
            created_at TEXT
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT DEFAULT 'user'
        )''')
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



def _visitor_login_html(error: bool = False) -> str:
    err_msg = '<p style="color:#f87171;font-size:13px;margin-top:8px;">访问码错误，请重试</p>' if error else ''
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harven AI — 访问验证</title>
<style>
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0f0f1a;font-family:'Microsoft YaHei',sans-serif;color:#e2e8f0;}}
  .card{{background:#1a1a2e;border:1px solid #2d2d4e;border-radius:16px;padding:40px 36px;
         width:320px;box-shadow:0 8px 32px rgba(0,0,0,.5);text-align:center;}}
  h2{{margin:0 0 6px;font-size:22px;}}
  p{{color:#888;font-size:13px;margin:0 0 24px;}}
  input{{width:100%;box-sizing:border-box;padding:10px 14px;border:1px solid #2d2d4e;
         border-radius:8px;background:#12122a;color:#e2e8f0;font-size:15px;outline:none;
         text-align:center;letter-spacing:4px;}}
  input:focus{{border-color:#4a9eff;}}
  button{{margin-top:14px;width:100%;padding:11px;border:none;border-radius:8px;
          background:#4a9eff;color:#fff;font-size:15px;cursor:pointer;font-weight:600;}}
  button:hover{{background:#3b8de0;}}
</style>
</head>
<body>
<div class="card">
  <h2>🔐 Harven AI</h2>
  <p>请输入访问码以继续</p>
  <form method="post">
    <input type="password" name="code" placeholder="访问码" autofocus autocomplete="off">
    {err_msg}
    <button type="submit">进入</button>
  </form>
</div>
</body>
</html>"""


@app.route('/api/visitor/stats', methods=['GET'])
def visitor_stats():
    """访客统计汇总（仅本机/局域网可访问）"""
    ip = request.remote_addr or '127.0.0.1'
    if not (ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')):
        return jsonify({'error': '仅限本地访问'}), 403
    from src.core.visitor_manager import get_stats
    return jsonify(get_stats())


@app.route('/api/visitor/list', methods=['GET'])
def visitor_list():
    """访客列表（仅本机/局域网）"""
    ip = request.remote_addr or '127.0.0.1'
    if not (ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')):
        return jsonify({'error': '仅限本地访问'}), 403
    from src.core.visitor_manager import get_visitor_list
    limit = int(request.args.get('limit', 50))
    return jsonify(get_visitor_list(limit))


@app.route('/api/visitor/set-access-code', methods=['POST'])
def visitor_set_access_code():
    """设置/清除访问码（仅本机/局域网）"""
    ip = request.remote_addr or '127.0.0.1'
    if not (ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')):
        return jsonify({'error': '仅限本地访问'}), 403
    data = request.json or {}
    code = data.get('code', '').strip()
    from src.core.visitor_manager import set_access_code
    set_access_code(code)
    return jsonify({'success': True, 'protected': bool(code)})


@app.route('/api/admin/change-password', methods=['POST'])
def admin_change_password():
    """修改管理员密码（需已登录管理后台，验证当前密码后更新 config.yaml）"""
    # 🔒 必须通过管理后台认证
    if not _check_admin_session():
        return jsonify({'error': '请先登录管理后台'}), 401

    data = request.json or {}
    current_pwd = data.get('current_password', '').strip()
    new_pwd = data.get('value', '').strip()

    if not current_pwd or not new_pwd:
        return jsonify({'error': '当前密码和新密码不能为空'}), 400
    if len(new_pwd) < 6:
        return jsonify({'error': '新密码不能少于 6 位'}), 400

    # 验证当前密码（直接从 config dict 读取，由 startup 加载）
    import hashlib as _hl
    stored_pwd = config.get('security', {}).get('admin_password', '')

    if current_pwd != stored_pwd:
        return jsonify({'error': '当前密码错误'}), 403

    # 写入新密码（明文存储，_admin_password_hash 读取时 SHA256 处理）
    if 'security' not in config:
        config['security'] = {}
    config['security']['admin_password'] = new_pwd
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True, 'message': '密码已修改，下次登录管理后台时生效'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/visitor/status', methods=['GET'])
def visitor_status():
    """获取访客保护状态（供前端登录管理 Tab 刷新用）"""
    try:
        from src.core.visitor_manager import is_access_protected
        protected = is_access_protected()
    except Exception:
        protected = False
    ip = request.remote_addr or '127.0.0.1'
    is_local = ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')
    return jsonify({
        'protected': protected,
        'is_local': is_local,
        'source': '本地访问' if is_local else '公网访问',
        'authenticated': True  # 能访问到这里就是已认证
    })


@app.route('/api/visitor/block', methods=['POST'])
def visitor_block():
    """封禁访客（仅本机/局域网）"""
    ip = request.remote_addr or '127.0.0.1'
    if not (ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')):
        return jsonify({'error': '仅限本地访问'}), 403
    data = request.json or {}
    sid = data.get('session_id', '')
    from src.core.visitor_manager import block_visitor
    block_visitor(sid)
    return jsonify({'success': True})


@app.route('/api/visitor/unblock', methods=['POST'])
def visitor_unblock():
    """解封访客（仅本机/局域网）"""
    ip = request.remote_addr or '127.0.0.1'
    if not (ip in ('127.0.0.1', '::1') or ip.startswith('192.168.') or ip.startswith('10.')):
        return jsonify({'error': '仅限本地访问'}), 403
    data = request.json or {}
    sid = data.get('session_id', '')
    from src.core.visitor_manager import unblock_visitor
    unblock_visitor(sid)
    return jsonify({'success': True})


# ==================== Phase 6+: 硬层面安全 ====================




@app.route('/api/security/device-status', methods=['GET'])
def security_device_status():
    """设备安全状态"""
    try:
        from src.core.hw_security import get_device_security_status
        status = get_device_security_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/security/bind', methods=['POST'])
def security_bind_device():
    """设备绑定"""
    data = request.json or {}
    bind_code = data.get('bind_code', '')
    try:
        from src.core.hw_security import bind_current_device
        result = bind_current_device(bind_code)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/security/unbind', methods=['POST'])
def security_unbind_device():
    """解除设备绑定"""
    data = request.json or {}
    device_id = data.get('device_id', '')
    try:
        from src.core.hw_security import unbind_device
        result = unbind_device(device_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== 安全设置 API =====

_SECURITY_SETTING_KEYS = {
    'ssrf_protection', 'plugin_network_whitelist', 'plugin_allowed_domains',
    'rate_limit_enabled', 'rate_limit_chat', 'rate_limit_global',
    'sandbox_enabled', 'sandbox_network_isolate', 'sandbox_timeout',
    'device_bind_policy', 'injection_detection', 'file_scan_enabled',
    'antivirus_path', 'password_protected_actions',
}


@app.route('/api/security/settings', methods=['GET'])
def security_get_settings():
    """获取所有安全设置"""
    try:
        sec = config.get('security', {})
        settings = {k: sec.get(k) for k in _SECURITY_SETTING_KEYS if k in sec}
        # 默认值
        defaults = {
            'ssrf_protection': True,
            'rate_limit_enabled': True,
            'rate_limit_chat': 30,
            'rate_limit_global': 120,
            'sandbox_network_isolate': True,
            'sandbox_timeout': 30,
            'injection_detection': True,
            'device_bind_policy': 'log_only',
            'password_protected_actions': [],
        }
        for k, v in defaults.items():
            if k not in settings:
                settings[k] = v
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/security/setting', methods=['POST'])
def security_save_setting():
    """保存单个安全配置项"""
    data = request.get_json(silent=True) or {}
    key = data.get('key', '').strip()
    value = data.get('value')
    if not key or key not in _SECURITY_SETTING_KEYS:
        return jsonify({'success': False, 'error': f'无效的配置键: {key}'}), 400
    try:
        cfg = _load_config()
        if 'security' not in cfg:
            cfg['security'] = {}
        cfg['security'][key] = value
        _save_config(cfg)
        config.setdefault('security', {})[key] = value
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/security/device-fingerprint', methods=['GET'])
def security_device_fingerprint():
    """获取当前设备指纹信息"""
    try:
        from src.core.hw_security import get_device_fingerprint
        fp = get_device_fingerprint()
        return jsonify({'success': True, 'fingerprint': fp})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/security/events', methods=['GET'])
def security_events():
    """获取安全事件记录"""
    try:
        from src.core.hw_security import get_security_events
        events = get_security_events(limit=50)
        return jsonify({'success': True, 'events': events})
    except Exception as e:
        return jsonify({'success': True, 'events': []})


# ════════════════════════════════════
# 密码验证
# ════════════════════════════════════

@app.route('/api/security/verify-password', methods=['POST'])
def security_verify_password():
    """
    验证管理员密码
    用于需要密码确认的操作前验证。
    请求体：{"password": "用户输入的密码"}
    返回：{"success": true/false, "message": "..."}
    """
    try:
        data = request.get_json(silent=True) or {}
        pwd = (data.get('password') or '').strip()
        if not pwd:
            return jsonify({'success': False, 'error': '密码不能为空'}), 400

        cfg = _load_config()
        sec = cfg.get('security', {})
        stored = (sec.get('admin_password') or '').strip()

        # 未设置密码 → 拒绝验证
        if not stored:
            logger.warning('[Security] 未设置管理员密码，拒绝验证')
            return jsonify({'success': False, 'error': '未设置管理员密码，请在「基本设置」中设置'}), 403

        # 恒定时间比较（防时序攻击）
        import secrets
        match = secrets.compare_digest(pwd, stored)

        if match:
            logger.info('[Security] 密码验证通过')
            return jsonify({'success': True, 'message': '验证通过'})
        else:
            logger.warning('[Security] 密码验证失败')
            return jsonify({'success': False, 'error': '密码错误'}), 401

    except Exception as e:
        logger.error(f'[Security] 密码验证异常: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== 系统配置摘要 API =====

@app.route('/api/system/config-summary', methods=['GET'])
def system_config_summary():
    """返回配置摘要（敏感字段已脱敏）"""
    try:
        cfg = _load_config()
        sensitive = {'admin_password', 'access_code', 'deepseek_api_key', 'kimi_api_key',
                     'openai_api_key', 'password', 'secret', 'token', 'antivirus_path'}
        summary = {}
        def flatten(d, prefix=''):
            for k, v in d.items():
                full_key = f'{prefix}.{k}' if prefix else k
                if k in sensitive or any(s in k.lower() for s in ('password', 'key', 'token', 'secret')):
                    summary[full_key] = '***（已脱敏）'
                elif isinstance(v, dict):
                    flatten(v, full_key)
                else:
                    summary[full_key] = v
        flatten(cfg)
        return jsonify({'success': True, 'config': summary})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/harvenhub/sources', methods=['GET'])
def harvenhub_sources():
    """获取所有可打包的数据源"""
    try:
        from src.core.harvenhub_api import get_data_sources
        sources = get_data_sources()
        return jsonify(sources)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub/analyze', methods=['POST'])
def harvenhub_analyze():
    """AI 隐私分析"""
    try:
        from src.core.harvenhub_api import batch_analyze
        data = request.json or {}
        items = data.get('items', [])
        if not items:
            return jsonify({'error': '请选择要分析的数据项'}), 400
        if len(items) > 50:
            return jsonify({'error': '一次最多分析 50 项'}), 400
        results = batch_analyze(items)
        return jsonify(results)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub/export', methods=['POST'])
def harvenhub_export():
    """导出知识包"""
    try:
        from src.core.harvenhub_api import export_package
        data = request.json or {}
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        items = data.get('items', [])
        exclude_privacy = data.get('exclude_privacy', True)

        if not name:
            return jsonify({'error': '请提供知识包名称'}), 400
        if not items:
            return jsonify({'error': '请选择要打包的数据项'}), 400
        if len(items) > 100:
            return jsonify({'error': '一次最多打包 100 项'}), 400

        result = export_package(name, description, items, exclude_privacy)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub/history', methods=['GET'])
def harvenhub_history():
    """获取导出历史"""
    try:
        from src.core.harvenhub_api import get_export_history
        history = get_export_history()
        return jsonify(history)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== B站视频上传（biliup） ====================

@app.route('/api/bilibili/auth', methods=['POST'])
def bilibili_save_auth():
    """保存B站认证凭证"""
    try:
        from src.core.biliup_manager import BilibiliCredentials, save_credentials, validate_credentials
        data = request.get_json(silent=True) or {}
        creds = BilibiliCredentials(
            sessdata=data.get('sessdata', ''),
            bili_jct=data.get('bili_jct', ''),
            dedeuserid=data.get('dedeuserid', ''),
            buvid3=data.get('buvid3', ''),
            access_token=data.get('access_token', ''),
            refresh_token=data.get('refresh_token', ''),
        )
        save_credentials(creds)
        # 验证凭证
        result = validate_credentials(creds)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bilibili/auth', methods=['GET'])
def bilibili_get_auth():
    """获取当前B站认证状态（不暴露敏感字段）"""
    try:
        from src.core.biliup_manager import load_credentials
        creds = load_credentials()
        return jsonify({
            'configured': creds.is_valid(),
            'uid': creds.uid,
            'uname': creds.uname,
            'expires_at': creds.expires_at,
            'updated_at': creds.updated_at,
        })
    except Exception as e:
        return jsonify({'configured': False, 'error': str(e)})


@app.route('/api/bilibili/categories', methods=['GET'])
def bilibili_categories():
    """获取B站视频分类"""
    try:
        from src.core.biliup_manager import get_categories
        return jsonify({'categories': get_categories()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bilibili/validate', methods=['POST'])
def bilibili_validate_video():
    """验证视频文件"""
    try:
        from src.core.biliup_manager import validate_video
        data = request.get_json(silent=True) or {}
        file_path = data.get('file_path', '')
        if not file_path:
            return jsonify({'valid': False, 'error': '请提供文件路径'})
        result = validate_video(file_path)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'valid': False, 'error': str(e)}), 500


@app.route('/api/bilibili/upload', methods=['POST'])
def bilibili_create_upload():
    """创建上传任务"""
    try:
        from src.core.biliup_manager import create_upload_task
        data = request.get_json(silent=True) or {}
        file_path = data.get('file_path', '')
        if not file_path:
            return jsonify({'success': False, 'error': '请提供视频文件路径'})
        result = create_upload_task(
            file_path=file_path,
            title=data.get('title', ''),
            description=data.get('description', ''),
            tags=data.get('tags', []),
            tid=data.get('tid', 188),
            cover_path=data.get('cover_path', ''),
            copyright=data.get('copyright', 2),
            source=data.get('source', ''),
        )
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bilibili/upload/<task_id>/start', methods=['POST'])
def bilibili_start_upload(task_id):
    """开始上传"""
    try:
        from src.core.biliup_manager import start_upload
        result = start_upload(task_id)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bilibili/upload/<task_id>', methods=['GET'])
def bilibili_task_status(task_id):
    """查询任务状态"""
    try:
        from src.core.biliup_manager import get_task_status
        status = get_task_status(task_id)
        if not status:
            return jsonify({'error': '任务不存在'}), 404
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bilibili/upload/<task_id>', methods=['DELETE'])
def bilibili_cancel_upload(task_id):
    """取消上传"""
    try:
        from src.core.biliup_manager import cancel_upload
        result = cancel_upload(task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bilibili/tasks', methods=['GET'])
def bilibili_list_tasks():
    """列出所有上传任务"""
    try:
        from src.core.biliup_manager import list_tasks
        tasks = list_tasks()
        return jsonify({'tasks': tasks})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bilibili/history', methods=['GET'])
def bilibili_upload_history():
    """上传历史"""
    try:
        from src.core.biliup_manager import get_upload_history
        history = get_upload_history()
        return jsonify({'history': history})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bilibili/biliup-check', methods=['GET'])
def bilibili_check_cli():
    """检查 biliup CLI 是否可用"""
    try:
        from src.core.biliup_manager import install_biliup_check
        result = install_biliup_check()
        return jsonify(result)
    except Exception as e:
        return jsonify({'installed': False, 'error': str(e)}), 500


# ==================== 用户画像自动更新 ====================

@app.route('/api/profile/auto/analyze', methods=['POST'])
def profile_auto_analyze():
    """触发一次画像自动分析"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        limit = int(data.get('limit', 50))
        since_hours = int(data.get('since_hours', 72))
        updater = get_profile_updater()
        result = updater.analyze_recent_conversations(limit=limit, since_hours=since_hours)
        return jsonify({
            'success': result.success,
            'stats': result.stats,
            'new_traits': {
                dim: [{'label': t.label, 'confidence': t.confidence} for t in traits]
                for dim, traits in result.new_traits.items() if traits
            },
            'suggestions_count': len(result.suggestions),
            'error': result.error,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/auto/force', methods=['POST'])
def profile_auto_force():
    """强制执行完整画像分析（30天范围）"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        updater = get_profile_updater()
        result = updater.force_analyze()
        return jsonify({
            'success': result.success,
            'stats': result.stats,
            'new_traits': {
                dim: [{'label': t.label, 'confidence': t.confidence} for t in traits]
                for dim, traits in result.new_traits.items() if traits
            },
            'suggestions_count': len(result.suggestions),
            'error': result.error,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/auto/state', methods=['GET'])
def profile_auto_state():
    """获取画像自动分析状态"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        updater = get_profile_updater()
        profile = updater.get_profile()
        suggestions = updater.get_suggestions()
        return jsonify({
            'profile': profile,
            'suggestions': suggestions,
            'suggestions_count': len(suggestions),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/profile/auto/summary', methods=['GET'])
def profile_auto_summary():
    """获取画像文本摘要（供 system_prompt 注入）"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        summary = get_profile_updater().get_profile_summary()
        return jsonify({'summary': summary, 'has_content': bool(summary.strip())})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/profile/auto/suggestions/approve', methods=['POST'])
def profile_approve_suggestion():
    """批准画像建议"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        dim = data.get('dimension', '')
        label = data.get('label', '')
        if not dim or not label:
            return jsonify({'success': False, 'error': 'dimension和label必填'})
        result = get_profile_updater().approve_suggestion(dim, label)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/auto/suggestions/dismiss', methods=['POST'])
def profile_dismiss_suggestion():
    """忽略画像建议"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        dim = data.get('dimension', '')
        label = data.get('label', '')
        if not dim or not label:
            return jsonify({'success': False, 'error': 'dimension和label必填'})
        result = get_profile_updater().dismiss_suggestion(dim, label)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/auto/suggestions/clear', methods=['POST'])
def profile_clear_suggestions():
    """清空所有建议"""
    try:
        result = get_profile_updater().dismiss_all_suggestions()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/auto/reset', methods=['POST'])
def profile_auto_reset():
    """重置自动画像"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        result = get_profile_updater().reset_profile()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== HarvenHub Community Market — 本地社区市场 ====================

@app.route('/api/harvenhub-market/init', methods=['POST'])
def harvenhub_market_init():
    """初始化市场数据库"""
    try:
        from src.core.harvenhub_market import init_marketplace_db
        init_marketplace_db()
        return jsonify({'success': True, 'message': '市场数据库已初始化'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/categories', methods=['GET'])
def harvenhub_market_categories():
    """获取所有分类"""
    try:
        from src.core.harvenhub_market import get_categories, get_category_stats
        cats = get_categories()
        return jsonify({'categories': cats})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/stats', methods=['GET'])
def harvenhub_market_stats():
    """获取市场统计"""
    try:
        from src.core.harvenhub_market import get_market_stats, get_featured_items
        stats = get_market_stats()
        featured = get_featured_items(6)
        stats['featured'] = featured
        return jsonify(stats)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/browse', methods=['GET'])
def harvenhub_market_browse():
    """浏览市场
    Query params: category, type, sort(newest/popular/rating/downloads), limit, offset
    """
    try:
        from src.core.harvenhub_market import browse_items
        category = request.args.get('category', '')
        item_type = request.args.get('type', '')
        sort = request.args.get('sort', 'newest')
        limit = min(int(request.args.get('limit', 20)), 100)
        offset = int(request.args.get('offset', 0))
        return jsonify(browse_items(
            category=category or None,
            item_type=item_type or None,
            sort=sort, limit=limit, offset=offset
        ))
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/search', methods=['GET'])
def harvenhub_market_search():
    """搜索市场"""
    try:
        from src.core.harvenhub_market import search_market
        query = request.args.get('q', '')
        category = request.args.get('category', '')
        item_type = request.args.get('type', '')
        limit = min(int(request.args.get('limit', 20)), 100)
        results = search_market(
            query, category=category or None,
            item_type=item_type or None, limit=limit
        )
        return jsonify({'results': results, 'query': query, 'total': len(results)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/item/<item_id>', methods=['GET'])
def harvenhub_market_item(item_id):
    """获取单个项目详情"""
    try:
        from src.core.harvenhub_market import get_item
        item = get_item(item_id)
        if not item:
            return jsonify({'error': '项目不存在'}), 404
        return jsonify(item)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/publish', methods=['POST'])
def harvenhub_market_publish():
    """发布项目到市场"""
    try:
        from src.core.harvenhub_market import publish_item
        data = request.get_json(silent=True) or {}
        result = publish_item(
            name=data.get('name', ''),
            description=data.get('description', ''),
            author=data.get('author', 'Anonymous'),
            category_id=data.get('category_id', 'other'),
            item_type=data.get('item_type', 'skill'),
            tags=data.get('tags', []),
            icon=data.get('icon', ''),
            version=data.get('version', '1.0.0'),
            content=data.get('content', ''),
            content_path=data.get('content_path', ''),
        )
        if result.get('success'):
            return jsonify(result)
        return jsonify(result), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/item/<item_id>', methods=['PUT'])
def harvenhub_market_update(item_id):
    """更新项目"""
    try:
        from src.core.harvenhub_market import update_item
        data = request.get_json(silent=True) or {}
        result = update_item(item_id, **data)
        if result.get('success'):
            return jsonify(result)
        return jsonify(result), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/item/<item_id>', methods=['DELETE'])
def harvenhub_market_remove(item_id):
    """下架项目（软删除，标记为 hidden）"""
    try:
        from src.core.harvenhub_market import update_item
        result = update_item(item_id, status='hidden')
        if result.get('success'):
            return jsonify({'success': True, 'message': '已下架'})
        return jsonify(result), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/rate', methods=['POST'])
def harvenhub_market_rate():
    """评分"""
    try:
        from src.core.harvenhub_market import rate_item
        data = request.get_json(silent=True) or {}
        item_id = data.get('item_id', '')
        user_name = data.get('user_name', 'Anonymous')
        score = int(data.get('score', 0))
        comment = data.get('comment', '')
        result = rate_item(item_id, user_name, score, comment)
        if result.get('success'):
            return jsonify(result)
        return jsonify(result), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/download/<item_id>', methods=['POST'])
def harvenhub_market_download(item_id):
    """记录下载并返回下载信息"""
    try:
        from src.core.harvenhub_market import get_item, increment_download
        item = get_item(item_id)
        if not item:
            return jsonify({'error': '项目不存在'}), 404
        increment_download(item_id)
        return jsonify({
            'success': True,
            'item': item,
            'action': 'install' if item.get('content_path') else 'view',
            'message': f'正在获取 {item["name"]}...'
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/my-published', methods=['GET'])
def harvenhub_market_my_published():
    """获取我发布的项目"""
    try:
        from src.core.harvenhub_market import get_my_published
        author = request.args.get('author', 'Anonymous')
        items = get_my_published(author)
        return jsonify({'items': items, 'author': author})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== 管理后台 /admin ====================

import hashlib as _hashlib

def _admin_password_hash() -> str:
    """从 config dict 读取管理员密码并返回 SHA256 哈希（不存储明文）"""
    pwd = config.get('security', {}).get('admin_password', '')
    if not pwd:
        # 未设置密码时，使用初始密码 harven2026
        pwd = 'harven2026'
    return _hashlib.sha256(pwd.encode('utf-8')).hexdigest()


def _check_admin_session() -> bool:
    """检查当前请求是否已通过管理员认证"""
    token = request.cookies.get('hv_admin')
    if not token:
        return False
    return token == _admin_password_hash()


@app.route('/admin')
def admin_dashboard():
    """管理后台 — 需要管理员密码，任何网络环境均可，但必须先登录"""
    if not _check_admin_session():
        return redirect('/admin/login')
    # 强制改密检查：密码为初始密码 harven2026 时强制跳转
    _current_hash = _admin_password_hash()
    _initial_hash = _hashlib.sha256('harven2026'.encode('utf-8')).hexdigest()
    if _current_hash == _initial_hash:
        return redirect('/admin/force-change-password')
    return render_template('admin.html')


@app.route('/admin/force-change-password', methods=['GET', 'POST'])
def admin_force_change_password():
    """首次登录强制改密页（GET 显示页面，POST 返回 JSON）"""
    if not _check_admin_session():
        return redirect('/admin/login')

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        current_pwd = data.get('current_password', '').strip()
        new_pwd = data.get('value', '').strip()
        confirm_pwd = data.get('confirm_password', '').strip()

        # 验证当前密码（必须是初始密码 harven2026）
        if _hashlib.sha256(current_pwd.encode('utf-8')).hexdigest() != _admin_password_hash():
            return jsonify({'error': '当前密码错误'}), 400
        if not new_pwd or len(new_pwd) < 6:
            return jsonify({'error': '新密码长度至少6位'}), 400
        if new_pwd != confirm_pwd:
            return jsonify({'error': '两次输入的新密码不一致'}), 400

        # 更新 config.yaml
        try:
            cfg = _load_config()
            if 'security' not in cfg:
                cfg['security'] = {}
            cfg['security']['admin_password'] = new_pwd
            _save_config(cfg)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': f'保存失败：{str(e)}'}), 500

    # GET：显示改密页面
    return '''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<title>强制修改密码 — Harven AI</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #0f0f1a; font-family: "PingFang SC", sans-serif; color: #e2e8f0; }
  .card { background: #1a1a2e; border: 1px solid #f59e0b55; border-radius: 16px;
           padding: 40px 48px; width: 400px; text-align: center; }
  h2 { color: #f59e0b; font-size: 20px; margin-bottom: 8px; }
  p.sub { color: #94a3b8; font-size: 13px; margin-bottom: 28px; }
  input { width: 100%; padding: 12px 16px; background: #0f0f1a; border: 1px solid #3a3a5a;
           border-radius: 8px; color: #e2e8f0; font-size: 15px; outline: none;
           transition: border-color .2s; margin-bottom: 12px; }
  input:focus { border-color: #f59e0b; }
  button { width: 100%; margin-top: 16px; padding: 12px; background: #f59e0b;
            border: none; border-radius: 8px; color: #1a1a2e; font-size: 15px; font-weight: 700;
            cursor: pointer; transition: background .2s; }
  button:hover { background: #d97706; }
  .err { color: #f87171; font-size: 13px; margin-top: 12px; }
  .ok { color: #34d399; font-size: 13px; margin-top: 12px; }
  .hint { color: #f59e0b99; font-size: 12px; margin-top: 20px; }
</style>
</head><body>
<div class="card">
  <h2>&#9888;&#65039; 请修改管理员密码</h2>
  <p class="sub">检测到您正在使用初始密码，请立即修改以确保安全</p>
  <form id="pwForm">
    <input type="password" name="current_password" placeholder="当前密码（初始密码）" required autofocus>
    <input type="password" name="value" placeholder="新密码（至少6位）" required>
    <input type="password" name="confirm_password" placeholder="确认新密码" required>
    <button type="submit">修改密码</button>
  </form>
  <p class="hint">修改完成后将自动跳转至后台</p>
</div>
<script>
document.getElementById('pwForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const data = {
    current_password: form.current_password.value,
    value: form.value.value,
    confirm_password: form.confirm_password.value,
  };
  const resp = await fetch('/admin/force-change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  const result = await resp.json();
  const oldErr = document.querySelector('.err');
  const oldOk = document.querySelector('.ok');
  if (oldErr) oldErr.remove();
  if (oldOk) oldOk.remove();

  if (result.success) {
    const ok = document.createElement('p');
    ok.className = 'ok';
    ok.textContent = '\u2705 密码修改成功！正在跳转...';
    form.after(ok);
    setTimeout(() => window.location.href = '/admin', 1500);
  } else {
    const err = document.createElement('p');
    err.className = 'err';
    err.textContent = '\u26a0\ufe0f ' + (result.error || '修改失败');
    form.after(err);
  }
});
</script>
</body></html>''', 200


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """管理员登录页"""
    error = ''
    # 判断是否已设置密码，以及是否是初始密码
    stored_pwd = config.get('security', {}).get('admin_password', '')
    has_password = bool(stored_pwd)
    is_initial = (has_password and 
                  _hashlib.sha256(stored_pwd.encode('utf-8')).hexdigest() == 
                  _hashlib.sha256('harven2026'.encode('utf-8')).hexdigest())
    
    if request.method == 'POST':
        pwd = request.form.get('password', '').strip()
        expected = _admin_password_hash()
        if _hashlib.sha256(pwd.encode('utf-8')).hexdigest() == expected:
            resp = make_response(redirect('/admin'))
            # httponly + samesite=Strict，防止 XSS/CSRF
            resp.set_cookie(
                'hv_admin', expected,
                max_age=86400 * 1,   # 1天有效，每天重新登录
                httponly=True,
                samesite='Strict'
            )
            return resp
        error = '密码错误，请重试'

    # 仅当密码是初始密码时才显示提示
    if is_initial:
        setup_hint = '<p class="setup-hint">🔑 初始密码：<code>harven2026</code>（首次登录后请立即修改）</p>'
    elif not has_password:
        setup_hint = '<p class="setup-hint">⚠️ 未设置管理员密码，请在 config.yaml 中设置 security.admin_password</p>'
    else:
        setup_hint = ''

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><title>管理员登录 — Harven AI</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #0f0f1a; font-family: "PingFang SC", sans-serif; }}
  .card {{ background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 16px;
           padding: 40px 48px; width: 360px; text-align: center; }}
  h2 {{ color: #e0e0f0; font-size: 20px; margin-bottom: 8px; }}
  p.sub {{ color: #6b6b8a; font-size: 13px; margin-bottom: 28px; }}
  input {{ width: 100%; padding: 12px 16px; background: #0f0f1a; border: 1px solid #3a3a5a;
           border-radius: 8px; color: #e0e0f0; font-size: 15px; outline: none;
           transition: border-color .2s; }}
  input:focus {{ border-color: #7c7cff; }}
  button {{ width: 100%; margin-top: 16px; padding: 12px; background: #5a5aff;
            border: none; border-radius: 8px; color: #fff; font-size: 15px;
            cursor: pointer; transition: background .2s; }}
  button:hover {{ background: #4444dd; }}
  .err {{ color: #e74c3c; font-size: 13px; margin-top: 12px; }}
  .hint {{ color: #44445a; font-size: 12px; margin-top: 20px; }}
  .setup-hint {{ color: #f59e0b; font-size: 13px; margin-top: 16px; padding: 10px;
                 background: rgba(245,158,11,0.08); border-radius: 8px; }}
</style>
</head><body>
<div class="card">
  <h2>🔐 管理员登录</h2>
  <p class="sub">Harven AI 后台管理</p>
  <form method="post">
    <input type="password" name="password" placeholder="输入管理员密码" autofocus>
    <button type="submit">进入后台</button>
  </form>
  {"<p class='err'>⚠ " + error + "</p>" if error else ""}
  {setup_hint}
  <p class="hint">密码在 config.yaml → security.admin_password</p>
</div>
</body></html>'''


@app.route('/admin/logout')
def admin_logout():
    """管理员退出"""
    resp = make_response(redirect('/admin/login'))
    resp.delete_cookie('hv_admin')
    return resp


# ===== 图片生成 API（调用 Stable Diffusion） =====
@app.route('/api/image/generate', methods=['POST'])
def api_generate_image():
    """
    调用 Stable Diffusion 生成图片
    请求体：{"prompt": "..."}
    返回：{"success": true, "url": "/static/generated/xxx.png"}
    """
    data = request.get_json(force=True, silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify({'success': False, 'error': '请提供图片描述（prompt）'}), 400

    cfg = config.get('image_gen', {})
    provider = cfg.get('provider', 'auto')
    sd_url = cfg.get('stable_diffusion_url', 'http://127.0.0.1:7860')

    import base64, time as _time
    from datetime import datetime as _dt

    result_path = None
    error_msg = ''

    # 尝试调用 Stable Diffusion WebUI API
    if provider in ('auto', 'stable_diffusion'):
        try:
            import requests as _requests
            payload = {
                "prompt": prompt,
                "negative_prompt": "",
                "steps": 20,
                "width": 512,
                "height": 512,
                "cfg_scale": 7.5,
                "sampler_name": "Euler a",
                "n_iter": 1,
                "batch_size": 1,
            }
            _resp = _requests.post(
                f"{sd_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=120
            )
            _resp.raise_for_status()
            _data = _resp.json()
            if _data.get('images'):
                _img_b64 = _data['images'][0]
                _img_bytes = base64.b64decode(_img_b64)
                _fname = f"sd_{_dt.now().strftime('%Y%m%d_%H%M%S')}_{_time.time_ns() % 10**9}.png"
                _out_dir = PROJECT_ROOT / 'static' / 'generated'
                _out_dir.mkdir(parents=True, exist_ok=True)
                _out_path = _out_dir / _fname
                _out_path.write_bytes(_img_bytes)
                result_path = f"/static/generated/{_fname}"
                logger.info(f"[ImageGen] SD 生成成功: {result_path}")
            else:
                error_msg = 'SD 返回空图片'
        except Exception as _e:
            error_msg = f'Stable Diffusion 调用失败: {_e}'
            logger.warning(f"[ImageGen] SD 调用失败: {_e}")

    if not result_path:
        return jsonify({
            'success': False,
            'error': error_msg or '图片生成失败，请确保 Stable Diffusion WebUI 已启动（运行 webui-user.bat）',
            'help': '请先启动 H:\\StableDiffusion\\webui-user.bat，等待 WebUI 加载完成后重试。'
        }), 503

    return jsonify({'success': True, 'url': result_path, 'prompt': prompt})


if __name__ == '__main__':
    import webbrowser
    import socket
    port = 5000

    # 获取局域网 IP
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    local_ip = get_local_ip()
    logger.debug(f"\n{'='*50}")
    logger.debug(f"  Harven AI 已启动")
    logger.debug(f"  本机访问:   http://localhost:{port}")
    logger.debug(f"  局域网访问: http://{local_ip}:{port}")
    logger.debug(f"{'='*50}\n")

    # 启动时自动迁移 knowledge_meta.json 分片（如果文件过大）
    try:
        from src.core.meta_sharder import auto_shard_meta_on_startup
        auto_shard_meta_on_startup()
    except Exception as e:
        logger.error(f"[Startup] 元数据分片检查失败: {e}")

    # 启动 Ollama 进程守护（默认自动激活，无需手动设置环境变量）
    if os.environ.get('HARVEN_AUTO_OLLAMA', '1').lower() in ('1', 'true', 'yes'):
        try:
            from src.core.ollama_daemon import start_ollama_daemon
            start_ollama_daemon()
        except Exception as e:
            logger.error(f"[Startup] Ollama 守护启动失败: {e}")

    # ===== 独立性防御：启动时全套检查 =====
    try:
        from src.core.independence import startup_independence_check
        _indep_ok, _indep_msgs = startup_independence_check()
        logger.debug(f"\n{'='*50}")
        logger.debug(f"  🏛️ AI 独立性防御检查")
        logger.debug(f"{'='*50}")
        for _m in _indep_msgs:
            logger.debug(f"  {_m}")
        logger.debug(f"{'='*50}\n")
        if not _indep_ok:
            logger.warning("\n⚠️ 独立性检查未全部通过，但继续启动（已改为警告模式）\n")
    except Exception as e:
        logger.debug(f"[Startup] 独立性检查异常: {e}")
        traceback.print_exc()

    # 初始化技能管理器（启动热更新监控）
    try:
        from src.core.skills_manager import get_skills_manager
        get_skills_manager()
        logger.debug("[Startup] 技能管理器初始化完成")
    except Exception as e:
        logger.error(f"[Startup] 技能管理器初始化失败: {e}")

    # 启动时自动清理过渡库过期数据
    try:
        from src.core.db_router import auto_cleanup_transient
        deleted = auto_cleanup_transient()
        if deleted > 0:
            logger.debug(f"[Startup] 过渡库清理: {deleted} 条过期记录")
    except Exception as e:
        logger.error(f"[Startup] 过渡库清理失败: {e}")

    # 启动时自动驱逐过期记忆
    try:
        from src.core.memory_eviction import auto_evict_memories
        evicted = auto_evict_memories(max_memories=1000)
        if evicted > 0:
            logger.debug(f"[Startup] 记忆驱逐: {evicted} 条过期记忆已清理")
    except Exception as e:
        logger.error(f"[Startup] 记忆驱逐失败: {e}")

    # 启动时自动归档 30 天未活跃的旧对话
    try:
        from src.core.conversation_archiver import auto_archive_old_conversations
        archived = auto_archive_old_conversations(days=30)
        if archived > 0:
            logger.debug(f"[Startup] 对话归档: {archived} 个旧对话已归档")
    except Exception as e:
        logger.error(f"[Startup] 对话归档失败: {e}")

    # 启动时自动检测并导入新知识库文件（后台线程，不阻塞启动）
    def _on_import_done(file_count, results):
        if file_count > 0:
            logger.debug(f"[Startup] 知识库自动导入完成: 新增/更新 {file_count} 个文件")

    try:
        from src.core.knowledge_base import get_knowledge_base, auto_import_new_files
        kb = get_knowledge_base()
        auto_import_new_files(kb, callback=_on_import_done)
        logger.debug("[Startup] 知识库自动导入已启动（后台运行）")
    except Exception as e:
        logger.error(f"[Startup] 知识库自动导入启动失败: {e}")

    # 可选：启动 Cloudflare Tunnel 公网穿透
    enable_tunnel = os.environ.get('HARVEN_TUNNEL', '').lower() in ('1', 'true', 'yes')
    if enable_tunnel:
        logger.debug("[Tunnel] 正在启动 Cloudflare 公网穿透...")
        def _start_tunnel():
            try:
                from cloudflare_tunnel import start_tunnel
                url = start_tunnel()
                if url:
                    logger.debug(f"[Tunnel] 公网地址: {url}")
                else:
                    logger.error("[Tunnel] 公网穿透启动失败，不影响本地使用")
            except Exception as e:
                logger.debug(f"[Tunnel] 启动异常: {e}")
        threading.Thread(target=_start_tunnel, daemon=True).start()

    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()

    # 注册模型管理路由（Blueprint）
    try:
        app.register_blueprint(models_bp)
        logger.info("[Startup] 模型管理路由已注册（Blueprint）")
    except Exception as _e:
        logger.error(f"[Startup] 模型管理路由注册失败: {_e}")

    # 注册技能管理路由
    try:
        app.register_blueprint(skills_bp)
        logger.info("[Startup] 技能管理路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 技能管理路由注册失败: {_e}")

    # 注册受控进化路由
    try:
        app.register_blueprint(evolution_bp)
        logger.info("[Startup] 受控进化路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 受控进化路由注册失败: {_e}")

    # 注册统计管理路由
    try:
        app.register_blueprint(stats_bp)
        logger.info("[Startup] 统计管理路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 统计管理路由注册失败: {_e}")

    # 注册保底机制路由
    try:
        app.register_blueprint(fallback_bp)
        logger.info("[Startup] 保底机制路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 保底机制路由注册失败: {_e}")

    # 注册知识萃取路由
    try:
        app.register_blueprint(extraction_bp)
        logger.info("[Startup] 知识萃取路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 知识萃取路由注册失败: {_e}")

    # 注册 IM 连接路由
    try:
        from src.core.im_routes import bp as im_bp
        app.register_blueprint(im_bp)
        from src.core.im_connector import init_adapters
        init_adapters()
        logger.info("[Startup] IM 连接路由已注册 + 4 个平台适配器已加载")
    except Exception as _e:
        logger.error(f"[Startup] IM 连接路由注册失败: {_e}")

    # ===== 注册内容读取路由（B站/抖音） =====
    try:
        from src.core.content_reader import get_content_reader

        bili_cookie = config.get("bilibili", {}).get("cookie", "") if config else ""
        douyin_cookie = config.get("douyin", {}).get("cookie", "") if config else ""
        _content_reader = get_content_reader(bili_cookie=bili_cookie,
                                              douyin_cookie=douyin_cookie)

        @app.route("/api/content/read")
        def api_content_read():
            url = request.args.get("url", "")
            if not url:
                return jsonify({"success": False, "error": "缺少 url 参数"}), 400
            return jsonify(_content_reader.read(url))

        @app.route("/api/content/bilibili/video/<bvid>")
        def api_bili_video(bvid: str):
            return jsonify(_content_reader.read_bili(bvid))

        @app.route("/api/content/bilibili/comments/<bvid>")
        def api_bili_comments(bvid: str):
            pn = request.args.get("pn", 1, type=int)
            ps = request.args.get("ps", 20, type=int)
            return jsonify(_content_reader.get_bili_comments(bvid, pn=pn, ps=ps))

        @app.route("/api/content/bilibili/user/<int:mid>")
        def api_bili_user(mid: int):
            return jsonify(_content_reader.bili.get_user_info(mid))

        @app.route("/api/content/bilibili/search")
        def api_bili_search():
            keyword = request.args.get("keyword", "")
            page = request.args.get("page", 1, type=int)
            if not keyword:
                return jsonify({"success": False, "error": "缺少 keyword 参数"}), 400
            return jsonify(_content_reader.search_bili(keyword, page=page))

        @app.route("/api/content/douyin/video", methods=["POST"])
        def api_douyin_video():
            data = request.get_json(force=True, silent=True) or {}
            url = data.get("url", "")
            if not url:
                return jsonify({"success": False, "error": "缺少 url 参数"}), 400
            return jsonify(_content_reader.read_douyin(url))

        logger.info("[Startup] 内容读取路由已注册（B站 + 抖音）")
    except Exception as _e:
        logger.error(f"[Startup] 内容读取路由注册失败: {_e}")

    # ===== 注册 Planner 任务规划器路由 =====
    try:
        from src.core.planner import get_planner

        @app.route("/api/plans", methods=["GET"])
        def api_list_plans():
            status = request.args.get("status", "").strip() or None
            plans = get_planner().list_plans(status=status)
            return jsonify({"success": True, "plans": [p.to_dict() for p in plans]})

        @app.route("/api/plans", methods=["POST"])
        def api_create_plan():
            data = request.get_json(force=True, silent=True) or {}
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"success": False, "error": "计划名称不能为空"}), 400
            plan = get_planner().create_plan(
                name=name,
                description=data.get("description", ""),
                steps=data.get("steps"),
                mode=data.get("mode", "sequential"),
                metadata=data.get("metadata"),
            )
            return jsonify({"success": True, "plan": plan.to_dict()})

        @app.route("/api/plans/<plan_id>", methods=["GET"])
        def api_get_plan(plan_id):
            plan = get_planner().get_plan(plan_id)
            if not plan:
                return jsonify({"success": False, "error": "计划不存在"}), 404
            return jsonify({"success": True, "plan": plan.to_dict()})

        @app.route("/api/plans/<plan_id>", methods=["DELETE"])
        def api_delete_plan(plan_id):
            success = get_planner().delete_plan(plan_id)
            return jsonify({"success": success})

        @app.route("/api/plans/<plan_id>/execute", methods=["POST"])
        def api_execute_plan(plan_id):
            data = request.get_json(force=True, silent=True) or {}
            auto = data.get("auto", True)
            planner = get_planner()
            plan = planner.get_plan(plan_id)
            if not plan:
                return jsonify({"success": False, "error": "计划不存在"}), 404

            if auto:
                results = planner.execute_auto(plan_id)
                return jsonify({
                    "success": True,
                    "results": [{"ok": r[0], "message": r[1]} for r in results],
                    "plan": planner.get_plan(plan_id).to_dict(),
                })
            else:
                step_id = data.get("step_id", "")
                if not step_id:
                    return jsonify({"success": False, "error": "单步执行需要 step_id"}), 400
                ok, msg = planner.execute_step(plan_id, step_id)
                return jsonify({"success": ok, "message": msg})

        @app.route("/api/plans/generate", methods=["POST"])
        def api_generate_plan():
            """AI 根据描述生成任务计划"""
            data = request.get_json(force=True, silent=True) or {}
            description = data.get("description", "").strip()
            if not description:
                return jsonify({"success": False, "error": "请提供任务描述"}), 400
            plan = get_planner().generate_plan_from_description(
                description=description,
                plan_name=data.get("name", ""),
            )
            return jsonify({"success": True, "plan": plan.to_dict()})

        logger.info("[Startup] Planner 任务规划器路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] Planner 路由注册失败: {_e}")

    # ===== 注册硬件自检 & 模型推荐路由 =====
    try:
        from src.core.gen_scheduler import get_gen_scheduler

        @app.route("/api/gen/submit", methods=["POST"])
        def api_gen_submit():
            data = request.get_json(force=True, silent=True) or {}
            task_type = data.get("type", "music")
            if task_type not in ("music", "video", "image", "tts"):
                return jsonify({"success": False, "error": f"不支持的类型: {task_type}"}), 400
            task = get_gen_scheduler().submit(
                task_type=task_type,
                name=data.get("name", f"生成任务"),
                prompt=data.get("prompt", ""),
                params=data.get("params"),
            )
            return jsonify({"success": True, "task": task.to_dict()})

        @app.route("/api/gen/tasks", methods=["GET"])
        def api_gen_list():
            status = request.args.get("status", "").strip() or None
            tasks = get_gen_scheduler().list_tasks(status=status)
            return jsonify({"success": True, "tasks": [t.to_dict() for t in tasks]})

        @app.route("/api/gen/tasks/<task_id>", methods=["GET"])
        def api_gen_get(task_id):
            task = get_gen_scheduler().get_task(task_id)
            if not task:
                return jsonify({"success": False, "error": "任务不存在"}), 404
            return jsonify({"success": True, "task": task.to_dict()})

        @app.route("/api/gen/tasks/<task_id>/cancel", methods=["POST"])
        def api_gen_cancel(task_id):
            ok = get_gen_scheduler().cancel_task(task_id)
            return jsonify({"success": ok})

        @app.route("/api/gen/process", methods=["POST"])
        def api_gen_process():
            result = get_gen_scheduler().process_next()
            if result is None:
                return jsonify({"success": True, "message": "队列为空"})
            return jsonify({"success": result[0], "message": result[1]})

        @app.route("/api/gen/gpu", methods=["GET"])
        def api_gpu_status():
            info = get_gen_scheduler()._gpu.get_memory_info()
            return jsonify({"success": True, "gpu": info})

        logger.info("[Startup] GenScheduler 生成调度器路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] GenScheduler 路由注册失败: {_e}")

    # ===== 注册硬件自检 & 模型推荐路由 =====
    try:
        from src.core.hardware_detector import (
            detect_hardware, recommend_models,
            format_recommendation, pull_model, MODEL_DB
        )
        logger.info("[Startup] 硬件自检模块已加载")

        @app.route("/api/hardware/detect", methods=["GET"])
        def api_hardware_detect():
            """硬件自检（返回本机配置）"""
            try:
                spec = detect_hardware()
                return jsonify({
                    "cpu_name": spec.cpu_name,
                    "cpu_cores": spec.cpu_cores,
                    "cpu_threads": spec.cpu_threads,
                    "ram_total_gb": round(spec.ram_total_gb, 1),
                    "ram_available_gb": round(spec.ram_available_gb, 1),
                    "gpu_name": spec.gpu_name,
                    "gpu_vram_gb": round(spec.gpu_vram_gb, 1),
                    "has_dedicated_gpu": spec.has_dedicated_gpu,
                    "os_name": spec.os_name,
                    "os_arch": spec.os_arch,
                    "ollama_installed": spec.ollama_installed,
                    "ollama_models": spec.ollama_models,
                })
            except Exception as e:
                logger.error(f"[Hardware] 检测失败: {e}")
                return jsonify({"error": str(e)}), 500

        @app.route("/api/hardware/recommend", methods=["GET"])
        def api_hardware_recommend():
            """根据硬件配置推荐模型"""
            try:
                spec = detect_hardware()
                recommended, best_match, compatible = recommend_models(spec)
                return jsonify({
                    "recommended": recommended,
                    "best_match": best_match,
                    "compatible": compatible,
                    "hardware": {
                        "gpu_vram_gb": round(spec.gpu_vram_gb, 1),
                        "ram_total_gb": round(spec.ram_total_gb, 1),
                        "has_dedicated_gpu": spec.has_dedicated_gpu,
                    }
                })
            except Exception as e:
                logger.error(f"[Hardware] 推荐失败: {e}")
                return jsonify({"error": str(e)}), 500

        @app.route("/api/hardware/model-db", methods=["GET"])
        def api_hardware_model_db():
            """返回所有可用模型列表"""
            return jsonify({"models": MODEL_DB})

        @app.route("/api/hardware/pull-model", methods=["POST"])
        def api_hardware_pull_model():
            """下载指定模型（SSE 进度推送）"""
            data = request.get_json(force=True, silent=True) or {}
            model_id = data.get("model_id", "").strip()
            if not model_id:
                return jsonify({"error": "model_id 不能为空"}), 400

            def generate():
                import subprocess, select, json
                try:
                    proc = subprocess.Popen(
                        ["ollama", "pull", model_id],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                    )
                    # 非阻塞读取
                    while True:
                        rlist, _, _ = select.select([proc.stdout], [], [], 0.5)
                        if rlist:
                            line = proc.stdout.readline()
                            if not line:
                                break
                            l = line.decode("utf-8", errors="replace").strip()
                            if l:
                                yield f"data: {json.dumps({'progress': l}, ensure_ascii=False)}\n\n"
                        if proc.poll() is not None:
                            break
                    proc.wait()
                    if proc.returncode == 0:
                        yield f"data: {json.dumps({'done': True, 'success': True}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps({'done': True, 'success': False, 'code': proc.returncode}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

            return Response(stream_with_context(generate()), mimetype="text/event-stream")

        # 启动时设备完整性检查（方案A：只记录变更，不阻止运行）
        try:
            from src.core.hw_security import verify_device_integrity
            ok, msg = verify_device_integrity()
            logger.info(f"[Startup] 设备完整性检查: {msg}")
        except Exception as _e:
            logger.error(f"[Startup] 设备完整性检查失败: {_e}")

        logger.info("[Startup] 硬件自检路由已注册")
    except Exception as _hw_e:
        logger.warning(f"[Startup] 硬件自检模块加载失败: {_hw_e}")

    # ===== 启动时初始化 HarvenHub 社区市场数据库 =====
    try:
        from src.core.harvenhub_market import ensure_market_ready
        ensure_market_ready()
        logger.info("[Startup] HarvenHub 社区市场已就绪")
    except Exception as _e:
        logger.error(f"[Startup] HarvenHub 社区市场初始化失败: {_e}")

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
