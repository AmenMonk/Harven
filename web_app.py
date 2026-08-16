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

# ===== Ollama 模型下载国内镜像加速 =====
if not os.environ.get("OLLAMA_HF_MIRROR"):
    os.environ["OLLAMA_HF_MIRROR"] = "https://modelscope.cn"
    print("[Harven] 已自动设置 OLLAMA_HF_MIRROR=https://modelscope.cn (模型下载加速5-10倍)")

# ===== ★ 2026-07-04: HuggingFace 离线模式（修复 httpx client closed 错误）=====
# MusicGen 模型已缓存在本地，不需要联网检查
# 在所有 import 之前设置，确保 audiocraft/transformers/huggingface_hub 都能看到
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 每次启动生成唯一版本号，注入到所有 JS/CSS URL 参数中
# 彻底绕过浏览器 HTTP 缓存 + Service Worker 缓存
import uuid
BUILD_ID = uuid.uuid4().hex[:12]
print(f"[Harven] BUILD_ID = {BUILD_ID}")

from flask import (Flask, render_template, request, Response,
                   jsonify, send_from_directory, stream_with_context,
                   redirect, make_response, session)
from markupsafe import Markup
import yaml
import logging
from src.utils.config import Config

# 全局配置对象（统一使用 Config 类，替代 plain dict）
config = Config()

# ★ 2026-08-16: i18n — 启动时从 config.yaml 读取界面语言（language.ui_lang）
try:
    from src.utils.i18n import init_from_config
    init_from_config()
except Exception as _i18n_e:
    print(f"[Harven] i18n 初始化跳过: {_i18n_e}")

# 模型管理路由（Blueprint）
from model_routes import bp as models_bp

# 技能管理路由（自动注册）
from src.core.skills_routes import bp as skills_bp

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# 屏蔽 Flask 开发服务器警告（Harven 是本地桌面应用，不需要生产级 WSGI）
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            template_folder=str(PROJECT_ROOT / 'templates'),
            static_folder=str(PROJECT_ROOT / 'static'))
app.config['TEMPLATES_AUTO_RELOAD'] = True  # 🔑 开发阶段始终重新加载模板，避免缓存旧版

# ★ 2026-08-08: 禁用浏览器缓存（pywebview缓存顽固）
@app.after_request
def add_no_cache_headers(response):
    if 'text/html' in response.headers.get('Content-Type', ''):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# ===== Flask Session Secret Key =====
# 从 config.yaml 读取，不存在则自动生成并保存
_secret_key = config.get('flask_secret_key', '')
if not _secret_key:
    import secrets
    _secret_key = secrets.token_hex(32)
    config['flask_secret_key'] = _secret_key
    config.save()
    logger.info("[Startup] 已生成并保存 flask_secret_key 到 config.yaml")
app.secret_key = _secret_key

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

# ===== 远程访问认证中间件 =====
# ★ 2026-08-09 安全修复：Cookie加HMAC签名，防止伪造
REMOTE_AUTH_SESSION_KEY = 'harven_remote_auth'

def _get_auth_secret():
    """获取认证密钥（从config读取，不存在则用机器指纹生成）"""
    import hashlib, platform
    cfg_path = Path(__file__).parent / 'config.yaml'
    secret = None
    if cfg_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
            secret = cfg.get('security', {}).get('auth_secret')
        except:
            pass
    if not secret:
        # 用机器指纹生成（node+cpu+用户名）
        machine_id = str(platform.node()) + str(platform.processor()) + str(os.getlogin())
        secret = hashlib.sha256(machine_id.encode()).hexdigest()[:32]
    return secret

def _sign_cookie(expire_ts):
    """对过期时间戳生成HMAC签名"""
    import hmac, hashlib
    secret = _get_auth_secret()
    sig = hmac.new(secret.encode(), str(expire_ts).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{expire_ts}.{sig}"

def _verify_cookie(cookie_val):
    """验证Cookie签名，返回是否有效"""
    import hmac, time, hashlib
    if not cookie_val or '.' not in cookie_val:
        return False
    parts = cookie_val.split('.')
    if len(parts) != 2:
        return False
    try:
        expire_ts = int(parts[0])
        sig = parts[1]
    except (ValueError, IndexError):
        return False
    # 检查是否过期
    if time.time() >= expire_ts:
        return False
    # 验证签名
    secret = _get_auth_secret()
    expected_sig = hmac.new(secret.encode(), str(expire_ts).encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected_sig)

@app.before_request
def check_remote_auth():
    """远程访问密码校验：本地免登录，未设密码时局域网也免登录"""
    ip = request.remote_addr or '127.0.0.1'
    
    # 本机直接放行
    if ip in ('127.0.0.1', '::1', 'localhost'):
        return None
    
    # ★ 2026-08-12: 未设置安全密码时，局域网也放行（首次安装体验）
    cfg = _load_config()
    pwd = cfg.get('security', {}).get('admin_password', '')
    if not pwd or pwd == 'harven2026':
        if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
            logger.info(f"[Auth] 未设自定义密码，局域网 {ip} 免登录")
            return None
    # 已有有效 session（★ 修复：验证签名）
    session_ok = request.cookies.get(REMOTE_AUTH_SESSION_KEY)
    if session_ok:
        if _verify_cookie(session_ok):
            return None
        else:
            logger.warning(f"[Auth] 无效/伪造的远程session，已拦截")
    # 静态资源和 API（login 自身除外）
    if request.path.startswith('/static/'):
        return None
    if request.path == '/api/auth/remote-login':
        return None
    if request.path == '/login':
        return None
    # API 请求返回 401
    if request.path.startswith('/api/'):
        return jsonify({'error': '需要远程密码', 'code': 'remote_auth_required'}), 401
    # HTML 页面 → 重定向到登录
    return redirect('/login?next=' + request.path)


@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html', next_url=request.args.get('next', '/'))


@app.route('/api/auth/remote-login', methods=['POST'])
def remote_login():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '')
    import yaml, time
    cfg_path = Path(__file__).parent / 'config.yaml'
    if not cfg_path.exists():
        return jsonify({'success': False, 'error': '配置不存在'}), 500
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    expected = cfg.get('security', {}).get('remote_password', 'harven2026')
    if pwd == expected:
        resp = jsonify({'success': True})
        # ★ 修复：Cookie值带HMAC签名，不能伪造
        expire_ts = int(time.time() + 86400 * 30)
        cookie_val = _sign_cookie(expire_ts)
        resp.set_cookie(REMOTE_AUTH_SESSION_KEY, cookie_val, max_age=86400 * 30, httponly=True, samesite='Lax')
        return resp
    return jsonify({'success': False, 'error': '密码错误'}), 401


@app.route('/api/auth/remote-password', methods=['GET'])
def get_remote_password_hint():
    """返回远程密码的掩码版本（用于设置页显示）"""
    import yaml
    cfg_path = Path(__file__).parent / 'config.yaml'
    if not cfg_path.exists():
        return jsonify({'hidden': '****'})
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    pwd = cfg.get('security', {}).get('remote_password', 'harven2026')
    # ★ 2026-08-09: 恢复 ?real=1 的明文显示（用户已在设置页登录，能看自己的密码）
    from flask import request
    if request.args.get('real') == '1':
        return jsonify({'real': pwd, 'hidden': '*' * len(pwd)})
    return jsonify({'hidden': '*' * len(pwd)})


@app.route('/api/auth/remote-password', methods=['POST'])
def update_remote_password():
    """修改远程访问密码"""
    import yaml
    data = request.get_json(silent=True) or {}
    # ★ 修复：必须验证旧密码
    old_pwd = data.get('old_password', '').strip()
    new_pwd = data.get('password', '').strip()
    if not new_pwd or len(new_pwd) < 8:  # ★ 修复：最少4位→8位
        return jsonify({'success': False, 'error': '新密码至少8位'}), 400
    cfg_path = Path(__file__).parent / 'config.yaml'
    if not cfg_path.exists():
        return jsonify({'success': False, 'error': '配置不存在'}), 500
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    current_pwd = cfg.get('security', {}).get('remote_password', 'harven2026')
    # ★ 验证旧密码
    if old_pwd != current_pwd:
        return jsonify({'success': False, 'error': '旧密码错误'}), 401
    cfg.setdefault('security', {})['remote_password'] = new_pwd
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
    return jsonify({'success': True})

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


@app.route('/data/media/<path:filename>')
def serve_media(filename):
    """提供 data/media/ 目录下的媒体文件"""
    return send_from_directory(os.path.join(PROJECT_ROOT, 'data', 'media'), filename)


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
                model_name = config.get('ollama', {}).get('default_model', '')
                # empty → 用户未选模型，不强制回退（让调用方自行判断可用引擎）
                # 启动时验证模型是否适配本机显存（有模型名时才做）
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
    # P2: device_starter 已归档（2026-07-02 瘦身，与 device_manager 重叠）
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

# Register vault routes (data vault / privacy mode / lock)
try:
    from src.core.vault_routes import BP as vault_bp
    app.register_blueprint(vault_bp)
    logger.info("[Vault] Data vault routes registered (Blueprint)")
except Exception as e:
    logger.error(f"[Vault] Failed to register vault routes: {e}")

# P1: orchestrator routes removed (overlaps with collab_routes, archived 2026-07-02)

# Register collab routes (Ollama + 1 foreign model collaboration)
try:
    from src.core.collab_routes import BP as collab_bp
    app.register_blueprint(collab_bp)
    logger.info("[Collab] Collaboration routes registered (Blueprint)")
except Exception as e:
    logger.error(f"[Collab] Failed to register collab routes: {e}")

# ==================== 垂直模型统一转换接口 ====================
try:
    from src.core.vertical_api import bp as vertical_bp
    app.register_blueprint(vertical_bp)
    # 初始化动态适配器和画板模板
    from src.core.vertical_api import init_vertical_system
    init_vertical_system()
    logger.info("[Vertical] 垂直模型 API 路由已注册，动态适配器系统已初始化")
except Exception as e:
    logger.error(f"[Vertical] 垂直模型路由注册失败: {e}")

# ==================== 安全防护路由（2026-07-12）====================
try:
    from src.core.security_routes import bp as security_bp
    app.register_blueprint(security_bp)
    logger.info("[Security] 安全防护路由已注册")
except Exception as e:
    logger.error(f"[Security] 安全防护路由注册失败: {e}")

# ★ 2026-07-21: 受控进化路由
try:
    from src.core.evolution_routes import bp as evolution_bp
    app.register_blueprint(evolution_bp)
    logger.info("[Evolution] 受控进化路由已注册")
except Exception as e:
    logger.error(f"[Evolution] 受控进化路由注册失败: {e}")

# ★ 2026-07-25: 知识萃取路由（存根）
try:
    from src.core.extraction_routes import bp as extraction_bp
    app.register_blueprint(extraction_bp)
    logger.info("[Extraction] 知识萃取路由已注册（存根）")
except Exception as e:
    logger.error(f"[Extraction] 知识萃取路由注册失败: {e}")

# ==================== Fallback 保底机制 Stub（2026-07-04）====================
# fallback_routes.py 已归档，前端 app-fallback.js 仍会检查这两个端点
# 返回简单 stub 避免 404 噪音
@app.route('/api/fallback/health')
def fallback_health_stub():
    return jsonify({"success": True, "status": "healthy", "message": "stub"})

@app.route('/api/fallback/status')
def fallback_status_stub():
    return jsonify({"success": True, "active": False, "message": "stub"})

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
            # ★ send() 是生成器函数，stream=False 返回的是生成器对象而非文本
            # 改用 stream=True 收集 text 块
            response_parts = []
            for msg_type, content in engine.send(prompt, stream=True, query_mode="auto"):
                if msg_type == "text":
                    response_parts.append(content)
            response = "".join(response_parts)
            if response:
                logger.info(f"[Scheduler] 任务 {task_id} 执行成功")
            return response
        except Exception as ex:
            logger.error(f"[Scheduler] 任务 {task_id} 执行失败: {ex}")
            return None

    sched.set_callback(_scheduler_execute)

    # 注册记忆梦境定时任务
    try:
        register_dreaming_scheduler(sched)
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

# ==================== Workflow Editor ====================

@app.route('/api/workflows/save', methods=['POST'])
def save_workflow():
    """保存工作流"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get('name', '未命名')
        nodes = data.get('nodes', [])
        workflow_dir = PROJECT_ROOT / 'data' / 'workflows'
        workflow_dir.mkdir(parents=True, exist_ok=True)
        fname = name.replace(' ', '_').replace('/', '_') + '.json'
        with open(workflow_dir / fname, 'w', encoding='utf-8') as f:
            json.dump({'name': name, 'nodes': nodes, 'saved_at': str(datetime.now())}, f, ensure_ascii=False)
        return jsonify({'success': True, 'filename': fname})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/workflows/list', methods=['GET'])
def list_workflows():
    """列出已保存的工作流"""
    try:
        workflow_dir = PROJECT_ROOT / 'data' / 'workflows'
        files = []
        if workflow_dir.exists():
            for f in workflow_dir.glob('*.json'):
                try:
                    d = json.loads(f.read_text(encoding='utf-8'))
                    files.append({'name': d.get('name', f.stem), 'nodes': len(d.get('nodes', [])), 'file': f.name})
                except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
        return jsonify({'workflows': files})
    except Exception as e:
        return jsonify({'workflows': []})

# ==================== Pages ====================

@app.route('/docs/guide.html')
@app.route('/docs/guide')
@app.route('/docs/使用指南')
def docs_guide():
    """★ 2026-08-10: 完整使用指南"""
    return send_from_directory('docs', 'guide.html')

@app.route('/')
def index():
    # 首次启动引导：已移除密码要求（2026-06-17），直接进入主页面
    pass  # 保留空白块保持缩进一致

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
    except Exception as _e:
        logger.debug(f"[WebApp] 忽略错误: {_e}")
    response = make_response(render_template('index.html', model_status_html=Markup(model_status_html), build_id=BUILD_ID))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ==================== Chat (SSE Stream) ====================

@app.route('/api/chat/internal', methods=['POST'])
def chat_internal():
    """内部调用接口 — 供Skill脚本调通用模型（非流式）
    支持model参数指定不同外脑（如deepseek/zhipu/qwen）
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        prompt = data.get('prompt', '')
        system_prompt = data.get('system_prompt', '你是一个有帮助的AI助手。简洁回答。')
        specified_model = data.get('model')  # ★ 2026-08-06: 支持指定模型
        if not prompt:
            return jsonify({'error': 'prompt不能为空'}), 400

        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()

        # ★ 如果指定了模型且与当前不同，临时切换
        original_model = None
        if specified_model:
            try:
                original_model = mm.get_current_foreign_model() if hasattr(mm, 'get_current_foreign_model') else None
                mm.switch_foreign_model(specified_model)
                logger.info(f"[ChatInternal] 临时切换外脑: {specified_model}")
            except Exception as e:
                logger.warning(f"[ChatInternal] 切换模型失败({specified_model})，用默认: {e}")

        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt}
        ]
        response_text = ''
        try:
            for chunk in mm.generate_response(messages, stream=False):
                if isinstance(chunk, dict):
                    if chunk.get('type') == 'text' or chunk.get('type') == 'content':
                        response_text += chunk.get('content', '')
                elif isinstance(chunk, str):
                    response_text += chunk
        except Exception:
            response_text = mm.chat(messages) if hasattr(mm, 'chat') else ''

        # ★ 恢复原模型
        if original_model:
            try:
                mm.switch_foreign_model(original_model)
            except Exception:
                pass

        return jsonify({'response': response_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    # 检查保险箱锁定状态
    if session.get('vault_locked', False):
        logger.warning(f"[Chat] 保险箱已锁定，拒绝请求来自 {request.remote_addr}")
        return jsonify({'error': '保险箱已锁定，请输入密码解锁', 'code': 'vault_locked'}), 403
    
    # 用 get_data() 获取原始请求体，然后用 json.loads 解析（绕过 Werkzeug 的 get_json 问题）
    import json as _json
    try:
        raw_bytes = request.get_data()
        raw_str = raw_bytes.decode('utf-8')
        data = _json.loads(raw_str) if raw_str else {}
    except Exception as e:
        logger.warning(f"[Chat] JSON 解析失败: {e}")
        return jsonify({'error': 'Invalid JSON', 'details': str(e)}), 400
    
    message = data.get('message', '').strip()
    conv_id = data.get('conversation_id')
    spec_id = data.get('specialization_id')
    query_mode = data.get('query_mode', 'direct')  # kb_only/direct（auto已取消）
    collab_mode = data.get('collab_mode', None)  # None=读config.yaml, "off"/"cloud_local"/"local_dual"=强制覆盖
    web_search = data.get('web_search', False)  # 联网搜索（Bing内置+SearXNG可选）
    skip_vertical = data.get('skip_vertical', False)  # ★ 转通用模型跳过垂直路由
    logger.info(f"[Chat] 收到原始 data: message={message[:30]!r}, query_mode={query_mode!r}, conv_id={conv_id}, collab_mode={collab_mode}, web_search={web_search}")

    # 记录公网访客对话次数
    sid = request.cookies.get('hv_sid', '')
    if sid:
        try:
            from src.core.visitor_manager import record_chat
            record_chat(sid)
        except Exception as _e:
            logger.debug(f"[WebApp] 忽略错误: {_e}")

    if not message:
        return jsonify({'error': 'Empty message'}), 400

    # Create conversation if needed (验证ID是否存在，不存在则创建)
    if not conv_id:
        from src.models.database import create_conversation
        title = message[:15] + ('...' if len(message) > 15 else '')
        conv_id = create_conversation(title, specialization_id=spec_id)
    else:
        # 验证 conversation_id 是否存在于数据库中
        try:
            from src.models.database import get_conversation, create_conversation
            existing = get_conversation(conv_id)
            if not existing:
                title = message[:15] + ('...' if len(message) > 15 else '')
                conv_id = create_conversation(title, specialization_id=spec_id)
                logger.info(f"[Chat] conversation_id 不存在，已创建新会话: {conv_id}")
        except Exception as _conv_err:
            logger.warning(f"[Chat] 检查会话存在性失败: {_conv_err}")
            # 不阻断流程，让 engine.send 处理可能的错误

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
        except (KeyError, TypeError, AttributeError) as _e:
            logger.debug(f"[Chat] 专项培养匹配跳过: {_e}")

    # If no specialization prompt, try Skills system match
    if not spec_prompt:
        try:
            from src.core.skills_manager import get_skills_manager
            skill_prompt = get_skills_manager().get_skill_prompt(message)
            if skill_prompt:
                spec_prompt = skill_prompt
        except Exception as _e:
            logger.debug(f"[web_app] 忽略: {_e}")

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
                except Exception as _e:
                    logger.debug(f"[WebApp] 忽略错误: {_e}")
            _discovery_thread = threading.Thread(target=_run_discovery, daemon=True)
            _discovery_thread.start()
        except Exception as _e:
            logger.debug(f"[WebApp] 忽略错误: {_e}")

    mm = get_mm()  # Ollama ModelManager（始终初始化，作为 fallback）

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': conv_id}, ensure_ascii=False)}\n\n"

            # ===== 统一消息路径 =====
            _stream_full_text = ""

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
            except ImportError as _e:
                logger.debug(f"[web_app] 忽略: {_e}")
            except Exception as gh_e:
                logger.error(f"[GitHub] 检测失败: {gh_e}")

            # 如果有 GitHub 通知，追加到 spec_prompt
            final_spec = spec_prompt
            if github_note:
                final_spec = (spec_prompt or "") + github_note

            from src.core.engine import ConversationEngine
            # 从 session 获取隐私模式状态
            _privacy_mode = session.get('privacy_mode', False)
            engine = ConversationEngine(mm, conv_id, specialization_prompt=final_spec, query_mode=query_mode, privacy_mode=_privacy_mode, collab_mode=collab_mode)
            engine._skip_vertical_route = skip_vertical  # ★ 转通用模型
            for msg_type, content in engine.send(message, stream=True, query_mode=query_mode, web_search=web_search):
                if msg_type == 'text':
                    _stream_full_text += content
                yield f"data: {json.dumps({'type': msg_type, 'content': content}, ensure_ascii=False)}\n\n"

            # ===== 自动检测并创建 Skill（外脑输出 Skill 时自动安装）=====
            try:
                if query_mode == 'direct' and _stream_full_text and '---' in _stream_full_text:
                    _auto_create_skill(_stream_full_text)
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")

            # ===== 注入技能建议（对话式自动发现）=====
            try:
                _discovery_thread.join(timeout=1.0)  # 等待发现线程完成（最多1秒）
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")
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


# ═══════════════════════════════════════════════════════════════
# 垂直模型路由已迁移到 src/core/vertical_api.py Blueprint（/api/vertical/*）
# 旧路由已删除：vertical_execute（调用已删的 engine._execute_vertical_model）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 长任务分片 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/task/pending', methods=['GET'])
def list_pending_tasks():
    """获取所有未完成的长任务（供断点续跑）"""
    try:
        from src.core.task_sharder import TaskSharder
        db = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'lingzhi.db')
        tasks = TaskSharder.get_pending_tasks(db)
        return jsonify({"success": True, "data": tasks})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/task/cancel', methods=['POST'])
def cancel_task():
    """取消长任务"""
    data = request.get_json(force=True, silent=True)
    if not data or not data.get('task_id'):
        return jsonify({"success": False, "error": "缺少 task_id"}), 400

    try:
        from src.core.task_sharder import TaskSharder
        db = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'lingzhi.db')
        sharder = TaskSharder(None, "", db)
        sharder.cancel(data['task_id'])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/task/resume', methods=['POST'])
def resume_task():
    """断点续跑长任务（SSE 流式）"""
    data = request.get_json(force=True, silent=True)
    if not data or not data.get('task_id'):
        return jsonify({"success": False, "error": "缺少 task_id"}), 400

    task_id = data['task_id']

    def generate():
        try:
            db = os.path.join(os.path.dirname(__file__), 'data', 'sqlite', 'lingzhi.db')
            sharder = TaskSharder(None, "", db)
            for progress in sharder.resume(task_id):
                yield f"data: {json.dumps({'type': 'task_progress', 'content': progress}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})



# ==================== Image Chat ====================

@app.route('/api/chat/image', methods=['POST'])
def chat_image():
    message = request.form.get('message', '描述这张图片')
    conv_id = request.form.get('conversation_id')
    image_files = request.files.getlist('images')
    if not image_files:
        # 兼容旧版单图字段
        old_img = request.files.get('image')
        if old_img:
            image_files = [old_img]
        else:
            return jsonify({'error': 'No image'}), 400

    # 保存所有图片
    tmp_paths = []
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        from src.core.file_scanner import quick_check
        for i, img in enumerate(image_files):
            p = tmp_dir / f"img_{i}_{img.filename}"
            img.save(str(p))
            if not quick_check(str(p), img.filename):
                return jsonify({'error': f'文件安全检测未通过: {img.filename}'}), 400
            tmp_paths.append(p)
    except Exception as scan_e:
        logger.debug(f"[Security] 图片扫描异常: {scan_e}")
        return jsonify({'error': '文件安全扫描服务异常'}), 503

    try:
        if not conv_id:
            from src.models.database import create_conversation
            conv_id = create_conversation(f'[图片×{len(tmp_paths)}] {message[:10]}')

        from src.models.database import save_message
        save_message(conv_id, 'user', f'[图片×{len(tmp_paths)}] {message}')

        from src.core.image_processor import analyze_image
        mm = get_mm()
        full_text = ""
        for msg_type, content in analyze_image(mm, message, [str(p) for p in tmp_paths], stream=False):
            if msg_type == "done":
                full_text = content
                break
        save_message(conv_id, 'assistant', full_text)

        return jsonify({'conversation_id': conv_id, 'response': full_text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)
        try: tmp_dir.rmdir()
        except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')

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
            except OSError as _e:
                logger.debug(f"[web_app] 忽略: {_e}")

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

def _read_file_content(file_path, filename):
    """★ 2026-07-05: 读取上传文件内容，返回文本
    
    支持: txt/md/py/js/json/csv/html/xml 等文本 + pdf/docx/xlsx
    Returns: str (文件内容文本)，失败返回空字符串
    """
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    max_chars = 8000  # 最多读取8000字符，避免超长文件
    
    try:
        # ── 文本类：直接读 ──
        if ext in ('.txt', '.md', '.py', '.js', '.json', '.csv', '.html', '.htm', '.xml', '.yaml', '.yml', '.ini', '.conf', '.log', '.sql', '.java', '.c', '.cpp', '.h', '.css', '.sh', '.bat', '.ts', '.jsx', '.tsx', '.vue', '.go', '.rs', '.rb', '.php'):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read(max_chars * 2)
                return content[:max_chars]
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='gbk', errors='ignore') as f:
                    content = f.read(max_chars * 2)
                return content[:max_chars]
        
        # ── PDF ──
        if ext == '.pdf':
            import pdfplumber
            texts = []
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i >= 20:  # 最多20页
                        texts.append(f'... (仅读取前20页，共{len(pdf.pages)}页)')
                        break
                    page_text = page.extract_text() or ''
                    texts.append(page_text)
                    if sum(len(t) for t in texts) > max_chars * 2:
                        break
            content = '\n---页码分隔---\n'.join(texts) if texts else ''
            return content[:max_chars]
        
        # ── Word docx ──
        if ext == '.docx':
            import docx
            doc = docx.Document(file_path)
            texts = []
            for para in doc.paragraphs:
                if para.text.strip():
                    texts.append(para.text)
                if sum(len(t) for t in texts) > max_chars * 2:
                    break
            # 也读表格
            for table in doc.tables:
                for row in table.rows:
                    row_text = ' | '.join(cell.text.strip() for cell in row.cells)
                    if row_text.strip():
                        texts.append(row_text)
                    if sum(len(t) for t in texts) > max_chars * 2:
                        break
            return '\n'.join(texts)[:max_chars]
        
        # ── Excel xlsx ──
        if ext in ('.xlsx', '.xlsm'):
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            texts = []
            for ws in wb.worksheets:
                texts.append(f'[工作表: {ws.title}]')
                for row in ws.iter_rows(max_row=50, values_only=True):
                    row_text = ' | '.join(str(c) if c is not None else '' for c in row)
                    if row_text.strip():
                        texts.append(row_text)
                    if sum(len(t) for t in texts) > max_chars * 2:
                        break
                if sum(len(t) for t in texts) > max_chars * 2:
                    break
            wb.close()
            return '\n'.join(texts)[:max_chars]

        # ── PowerPoint pptx ──
        if ext == '.pptx':
            from pptx import Presentation
            prs = Presentation(file_path)
            texts = []
            for i, slide in enumerate(prs.slides):
                if i >= 30:  # 最多30页
                    texts.append(f'... (仅读取前30页，共{len(prs.slides)}页)')
                    break
                texts.append(f'[幻灯片 {i+1}]')
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            t = para.text.strip()
                            if t:
                                texts.append(t)
                    if sum(len(t) for t in texts) > max_chars * 2:
                        break
                # 也读取表格
                for shape in slide.shapes:
                    if shape.has_table:
                        for row in shape.table.rows:
                            row_text = ' | '.join(cell.text.strip() for cell in row.cells)
                            if row_text.strip():
                                texts.append(row_text)
                if sum(len(t) for t in texts) > max_chars * 2:
                    break
            return '\n'.join(texts)[:max_chars]

        # ── 旧版PowerPoint ppt（暂不支持）──
        if ext == '.ppt':
            return '（旧版 .ppt 格式暂不支持预览，请转换为 .pptx 后上传）'

        # ── 旧版Excel xls（需xlrd，暂不支持）──
        if ext == '.xls':
            return '（旧版 .xls 格式暂不支持预览，请转换为 .xlsx 后上传）'
        
        # ── 其他格式 ──
        return f'（暂不支持预览 {ext} 格式文件内容）'
    
    except Exception as e:
        logger.warning(f"[FileChat] 读取文件内容失败 {filename}: {e}")
        return f'（文件内容读取失败: {e}）'


@app.route('/api/chat/file', methods=['POST'])
def chat_file():
    """通用文件上传 → AI 回答"""

    message = request.form.get('message', '')
    conv_id = request.form.get('conversation_id')
    # ★ 2026-07-05: 支持多文件（前端发的是 'files'）和单文件（兼容 'file'）
    upload_files = request.files.getlist('files')
    if not upload_files or len(upload_files) == 0:
        upload_files = [request.files.get('file')] if request.files.get('file') else []
    
    if not upload_files or not upload_files[0]:
        return jsonify({'error': 'No file'}), 400

    # 处理多文件：逐个保存+扫描+读取内容
    all_file_infos = []
    saved_tmp_paths = []
    filenames = []
    
    for upload_file in upload_files:
        ext = Path(upload_file.filename).suffix or '.bin'
        tmp_path = Path(tempfile.mktemp(suffix=ext))
        upload_file.save(str(tmp_path))
        filename = upload_file.filename or 'unknown'
        filenames.append(filename)
        saved_tmp_paths.append(tmp_path)

        # 文件安全扫描
        try:
            from src.core.file_scanner import quick_check
            if not quick_check(str(tmp_path), filename):
                for p in saved_tmp_paths:
                    p.unlink(missing_ok=True)
                return jsonify({'error': f'文件 {filename} 安全检测未通过，上传被拒绝'}), 403
        except Exception as scan_e:
            logger.debug(f"[Security] 文件扫描异常，拒绝上传: {scan_e}")
            for p in saved_tmp_paths:
                p.unlink(missing_ok=True)
            return jsonify({'error': '文件安全扫描服务异常，请稍后重试'}), 503

        # 读取文件内容
        file_content = _read_file_content(str(tmp_path), filename)
        if file_content:
            all_file_infos.append(f'[文件: {filename}]\n--- 内容 ---\n{file_content}\n--- 结束 ---')
        else:
            all_file_infos.append(f'[文件: {filename}]')
    
    filename = ', '.join(filenames) if filenames else 'unknown'
    # 清理临时文件
    for p in saved_tmp_paths:
        try: p.unlink(missing_ok=True)
        except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')

    if not conv_id:
        from src.models.database import create_conversation
        conv_id = create_conversation('[文件] ' + filename)

    try:
        from src.models.database import save_message
        save_message(conv_id, 'user', f'[文件] {filename}')

        # 组合消息
        combined = message.strip()
        # ★ 2026-07-05: 文件内容已在上面读取，直接组装
        file_info = '\n\n'.join(all_file_infos) if all_file_infos else f'[用户上传了文件: {filename}]'
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
    """添加监控主题（立即返回，首次检查后台异步执行）"""
    import threading
    
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
        
        # 后台异步执行首次检查（不阻塞前端响应）
        def _bg_first_check(tid):
            try:
                items = monitor.check_topic(tid)
                logger.info(f"[Monitor] 主题{tid}首次检查完成: {len(items)}条")
            except Exception as e:
                logger.warning(f"[Monitor] 主题{tid}首次检查失败: {e}")
        
        t = threading.Thread(target=_bg_first_check, args=(topic_id,), daemon=True)
        t.start()
        
        return jsonify({
            'success': True, 'id': topic_id,
            'background_check': True,
            'message': f'已添加「{topic_name}」，后台检查中...'
        })
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

@app.route('/api/knowledge/view/<path:filename>', methods=['GET'])
def view_kb_file(filename):
    """查看知识库文件内容"""
    try:
        kb_dir = PROJECT_ROOT / 'data' / 'knowledge'
        safe_path = (kb_dir / filename).resolve()
        if not str(safe_path).startswith(str(kb_dir.resolve())):
            return jsonify({'error': '路径越权'}), 403
        if not safe_path.exists():
            return jsonify({'error': '文件不存在'}), 404
        content = safe_path.read_text(encoding='utf-8', errors='replace')
        return jsonify({'filename': filename, 'content': content[:10000], 'size': len(content)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/delete/<path:filename>', methods=['DELETE'])
def delete_kb_file(filename):
    """删除知识库文件"""
    try:
        kb_dir = PROJECT_ROOT / 'data' / 'knowledge'
        safe_path = (kb_dir / filename).resolve()
        if not str(safe_path).startswith(str(kb_dir.resolve())):
            return jsonify({'error': '路径越权'}), 403
        if safe_path.exists():
            safe_path.unlink()
            return jsonify({'success': True, 'filename': filename})
        return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== System Agent ====================

@app.route('/api/agent/execute', methods=['POST'])
def agent_execute():
    """执行 Agent 操作（需用户授权）"""
    try:
        from src.core.agent_service import get_agent
        data = request.get_json(force=True, silent=True) or {}
        action = data.get('action', '')
        params = data.get('params', {})
        agent = get_agent()
        result = agent.execute(action, params)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[Agent] 执行失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/agent/pending', methods=['GET'])
def agent_pending():
    """获取待授权的操作"""
    try:
        from src.core.agent_service import get_agent
        agent = get_agent()
        return jsonify({'approvals': agent.get_pending_approvals()})
    except Exception as e:
        return jsonify({'approvals': []})

@app.route('/api/agent/approve', methods=['POST'])
def agent_approve():
    """批准操作"""
    try:
        from src.core.agent_service import get_agent
        data = request.get_json(force=True, silent=True) or {}
        req_id = data.get('id', '')
        agent = get_agent()
        ok = agent.approve(req_id)
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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

# ★ 2026-08-16: i18n — 前端语言包端点（供 app-i18n.js 拉取 locales/*.json）
@app.route('/api/locales/<lang>', methods=['GET'])
def get_locale_file(lang):
    import re as _re
    if not _re.match(r'^[A-Za-z_\-]+$', lang):
        return jsonify({'error': 'invalid lang'}), 400
    fp = PROJECT_ROOT / 'locales' / f'{lang}.json'
    if not fp.exists():
        return jsonify({'error': 'not found'}), 404
    from flask import send_file
    resp = send_file(str(fp), mimetype='application/json')
    resp.headers['Cache-Control'] = 'no-store'
    return resp

@app.route('/api/locales', methods=['GET'])
def list_locales():
    """列出可用界面语言"""
    try:
        from src.utils.i18n import get_available_languages
        return jsonify({'languages': get_available_languages()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        # 同步到 SQLite 偏好（engine.py 从 SQLite 读取 temperature/context_rounds 等）
        _sync_engine_prefs_to_sqlite(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _sync_engine_prefs_to_sqlite(data):
    """将前端设置同步到 SQLite user_preferences（给 engine.py 使用）"""
    try:
        from src.models.database import set_preference
        # ai.temperature
        ai = data.get('ai', {})
        if 'temperature' in ai:
            set_preference('temperature', str(ai['temperature']))
        # memory.working_context_turns → context_rounds
        mem = data.get('memory', {})
        if 'working_context_turns' in mem:
            set_preference('context_rounds', str(mem['working_context_turns']))
        # web_search 同步
        ws = data.get('web_search', {})
        if 'enabled' in ws:
            set_preference('web_search_enabled', 'true' if ws['enabled'] else 'false')
        if 'default_query_mode' in ws:
            set_preference('query_mode', ws['default_query_mode'])
    except Exception as e:
        logger.warning(f"[Settings] 同步引擎偏好到 SQLite 失败: {e}")

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

# ★ 2026-07-14: /api/settings/morality 路由已删除 — morality 配置块已移除

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
    # ★ 2026-08-16: i18n — 界面语言变化时实时切换后端输出语言（B方案标签等）
    try:
        from src.utils.i18n import set_language, detect_system_language
        _ui = (data or {}).get('ui_lang')
        if _ui == 'follow':
            _ui = detect_system_language()
        if _ui:
            set_language(_ui)
    except Exception:
        pass
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# P1: /api/settings/web_search 已移除（2026-07-02 搜索 Skill 化）

# ==================== 模型目录 ====================

@app.route('/api/models/catalog', methods=['GET'])
def get_model_catalog():
    category = request.args.get('category', None)
    vram = request.args.get('vram', type=float, default=0)
    ram = request.args.get('ram', type=float, default=0)
    try:
        from src.core.model_catalog import get_all_models, get_by_category, get_by_hardware
        if category:
            if vram > 0:
                models = get_by_hardware(vram, ram, category)
            else:
                models = get_by_category(category)
        else:
            models = get_all_models()
        return jsonify({'models': models, 'total': len(models)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/catalog/recommend', methods=['POST'])
def recommend_models():
    data = request.json or {}
    hardware = data.get('hardware', {})
    user_text = data.get('user_text', '')
    try:
        from src.core.model_catalog import recommend_by_user_need
        result = recommend_by_user_need(hardware, user_text)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== GGUF 导入 ====================

@app.route('/api/models/import-gguf', methods=['POST'])
def import_gguf_model():
    """导入本地 GGUF 文件到 data/models/"""
    try:
        import os
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        file = request.files['file']
        if not file.filename or file.filename == '':
            return jsonify({'success': False, 'error': '文件名为空'}), 400
        if not file.filename.lower().endswith('.gguf'):
            return jsonify({'success': False, 'error': '仅支持 .gguf 格式'}), 400
        models_dir = os.path.join(os.path.dirname(__file__), 'data', 'models')
        os.makedirs(models_dir, exist_ok=True)
        dest = os.path.join(models_dir, os.path.basename(file.filename))
        file.save(dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        return jsonify({'success': True, 'filename': os.path.basename(file.filename), 'size_mb': round(size_mb, 1)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== 数据保险箱 ====================

@app.route('/api/vault/backup', methods=['POST'])
def vault_backup():
    """打包 data/ 目录为 zip（排除 model 大文件）"""
    try:
        import shutil, os, time, zipfile
        data_dir = Path(__file__).parent / 'data'
        backup_name = f'harven_backup_{time.strftime("%Y%m%d_%H%M%S")}'
        backup_dir = Path(__file__).parent / 'data' / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        zip_path = str(backup_dir / (backup_name + '.zip'))

        # 用 zipfile 手动打包，排除 models/ 目录（GGUF 太大）
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(data_dir)):
                # 跳过 backups 和 models 目录
                rel = os.path.relpath(root, str(data_dir))
                if rel.startswith('backups') or rel.startswith('models'):
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    zf.write(fp, os.path.relpath(fp, str(data_dir)))

        size_mb = round(os.path.getsize(zip_path) / (1024 * 1024), 1)
        return jsonify({'success': True, 'url': f'/api/vault/download/{backup_name}.zip', 'size_mb': size_mb})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vault/download/<filename>', methods=['GET'])
def vault_download(filename):
    """下载备份文件"""
    from flask import send_file
    filepath = Path(__file__).parent / 'data' / 'backups' / filename
    if not filepath.exists():
        return jsonify({'error': '文件不存在'}), 404
    return send_file(filepath, as_attachment=True, download_name=filename)

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

# ==================== 硬件信息 ====================

@app.route('/api/hardware/info', methods=['GET'])
def get_hardware_info():
    """返回上次检测的硬件信息"""
    try:
        from src.core.hardware_detector import detect_hardware
        spec = detect_hardware()
        return jsonify({
            "cpu_name": spec.cpu_name,
            "cpu_cores": spec.cpu_cores,
            "cpu_threads": spec.cpu_threads,
            "ram_total_gb": spec.ram_total_gb,
            "ram_available_gb": spec.ram_available_gb,
            "gpu_name": spec.gpu_name,
            "gpu_vram_gb": spec.gpu_vram_gb,
            "has_dedicated_gpu": spec.has_dedicated_gpu,
            "gpu_is_nvidia": spec.gpu_is_nvidia,
            "disk_type": spec.disk_type,
            "has_ssd": spec.has_ssd,
            "os_name": spec.os_name,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== 模型同步 ====================

@app.route('/api/models/sync', methods=['POST'])
def sync_models():
    """手动触发模型目录同步"""
    try:
        from src.core.model_syncer import sync_catalog
        result = sync_catalog(dry_run=False)
        return jsonify(result)
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
    # 暂不支持 Cloudflare Tunnel 公网穿透（模块待添加）

    return jsonify({
        'local_ip': local_ip,
        'port': 7000,
        'local_url': f'http://{local_ip}:7000',
        'tunnel_url': None,
    })


# Cloudflare Tunnel 路由暂未实现（计划后续添加）
# @app.route('/api/tunnel/start', methods=['POST'])
# def tunnel_start(): ...
# @app.route('/api/tunnel/stop', methods=['POST'])
# def tunnel_stop(): ...


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
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")
        return jsonify({'connected': ok, 'message': msg, 'model': current_model})
    except (ConnectionError, OSError) as e:
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
    except Exception as _e:
        logger.debug(f"[WebApp] 忽略错误: {_e}")

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
    """根据硬件配置推荐模型（默认只推荐对话模型）"""
    try:
        import psutil, torch
        from src.core.model_recommend import recommend_by_hardware
        # 内联硬件检测（避免依赖不存在的 import）
        vram_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1) if torch.cuda.is_available() else 0
        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
        category = request.args.get('category', 'llm')
        raw = recommend_by_hardware(vram_gb, ram_gb, category=category if category != 'all' else None)
        # 标准化字段匹配前端 buildModelCard
        def _norm(m):
            return {
                'name': m.get('name', ''),
                'description': m.get('description', ''),
                'size': f"{m.get('size_gb', 0):.1f}GB" if m.get('size_gb') else '',
                'vram_gb': m.get('vram_gb', 0),
                'tags': m.get('tags', []),
                'status': 'normal',
                'installed': False,
            }
        models = [_norm(m) for m in raw]
        return jsonify({
            'ok': True,
            'recommended': models[:3],
            'installable': models[3:],
            'too_large': [],
            'hardware': {'vram_gb': round(vram_gb, 1), 'ram_gb': ram_gb, 'gpu': gpu_name},
        })
    except Exception as e:
        logger.error(f"[Models] 推荐失败: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/models/check', methods=['GET'])
def models_check():
    """检查模型目录是否存在"""
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'exists': False, 'error': 'missing path'})
    full = os.path.join(os.path.dirname(__file__), path)
    if os.path.isdir(full):
        size = 0
        for root, dirs, files in os.walk(full):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.isfile(fp):
                    size += os.path.getsize(fp)
        return jsonify({'exists': True, 'size_mb': round(size / 1048576, 1), 'files': sum(1 for _ in os.listdir(full))})
    return jsonify({'exists': False})


@app.route('/api/models/local-all', methods=['GET'])
def models_local_all():
    """统一本地模型列表：Ollama + data/models/ + HuggingFace缓存"""
    models = []
    # 1. Ollama
    try:
        from requests import get as rget
        r = rget('http://localhost:11434/api/tags', timeout=5)
        if r.status_code == 200:
            for m in r.json().get('models', []):
                models.append({'name': m['name'], 'source': 'Ollama', 'size_gb': round(m['size']/1e9,1), 'type': 'llm', 'icon': '🦙', 'id': 'ollama:'+m['name']})
    except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
    # 2. data/models/
    mdir = os.path.join(os.path.dirname(__file__), 'data', 'models')
    if os.path.isdir(mdir):
        for d in os.listdir(mdir):
            full = os.path.join(mdir, d)
            if not os.path.isdir(full): continue
            flist = os.listdir(full)[:50]
            gguf = any(f.endswith('.gguf') for f in flist)
            mtype = 'llm' if gguf else ('music' if any(k in d.lower() for k in ['musicgen','acestep']) else 'other')
            icon = '📦' if gguf else ('🎵' if mtype=='music' else '📁')
            src = '魔搭/GGUF' if gguf else '魔搭/HF'
            size = sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(full) for f in fs if os.path.isfile(os.path.join(r,f)))
            models.append({'name': d, 'source': src, 'size_gb': round(size/1e9,1), 'type': mtype, 'icon': icon, 'id': 'local:'+d})
    # 3. ★ 2026-07-12: 也扫描HuggingFace缓存
    try:
        from src.core.system_knowledge import scan_huggingface_cache
        hf_models = scan_huggingface_cache()
        for m in hf_models:
            mid = m.get('id', '')
            mname = m.get('name', mid.split('/')[-1] if '/' in mid else mid)
            mpath = m.get('path', '')
            has_gguf = m.get('has_gguf', False)
            # 计算大小
            msize = 0
            if mpath and os.path.isdir(mpath):
                msize = sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(mpath) for f in fs if os.path.isfile(os.path.join(r,f)))
            # 跳过已经在data/models里的
            if not any(m['id'] == 'local:'+mname.split(' ')[0] for m in models):
                models.append({
                    'name': mname,
                    'source': '魔搭/HF' if not has_gguf else 'HF/GGUF',
                    'size_gb': round(msize/1e9, 1),
                    'type': 'llm' if has_gguf else 'other',
                    'icon': '📦' if has_gguf else '📁',
                    'id': 'local:'+mname.split(' ')[0].replace('/','_')
                })
    except Exception as e:
        logger.warning(f"[Models] HuggingFace缓存扫描失败: {e}")
    # 确定当前模型
    cur = config.get('current_model', '')
    # 也检查 Ollama 默认模型
    olla_default = config.get('ollama', {}).get('default_model', '')
    return jsonify({'models': sorted(models, key=lambda m: m['source']), 'current': cur, 'ollama_current': olla_default})


@app.route('/api/quickpick', methods=['GET'])
def models_quickpick():
    """@ 快速切换：返回所有可用功能分类"""
    import requests
    items = []
    cur = config.get('current_model', '')
    try:
        r = requests.get('http://localhost:11434/api/tags', timeout=3)
        if r.status_code == 200:
            for m in r.json().get('models', []):
                if 'embed' in m['name'].lower(): continue
                items.append({'id':'ollama:'+m['name'],'name':m['name'],'icon':'🦙','category':'llm','label':'对话 · '+m['name'],'desc':str(round(m["size"]/1e9,1))+'GB','action':'switch_model','is_current':('ollama:'+m['name'])==cur})
    except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
    try:
        from src.core.agent_registry import AGENT_DEFINITIONS
        for aid, ag in AGENT_DEFINITIONS.items():
            tr = ag.get('triggers',[])
            if not tr: continue
            items.append({'id':aid,'name':ag.get('name',aid),'icon':ag.get('icon','⚡'),'category':ag.get('kind','skill'),'label':ag.get('name',aid),'desc':'、'.join(tr[:3]),'action':'insert_keyword','keyword':tr[0]})
    except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
    for did,icon,label,desc in [('law','⚖️','法律文书','合同审查、诉讼'),('medical','🏥','医疗咨询','健康建议'),('finance','💰','金融分析','股票基金'),('programming','💻','编程调试','代码修复'),('architecture','🏗️','建筑工程','图纸计算'),('education','📚','教育培训','论文润色'),('business','📊','商业分析','市场调研')]:
        items.append({'id':'domain:'+did,'name':label,'icon':icon,'category':'domain','label':label,'desc':desc,'action':'info','keyword':label})
    return jsonify({'items':items,'current':cur})


@app.route('/api/models/set-current', methods=['POST'])
def models_set_current():
    """设为当前对话模型"""
    data = request.get_json(silent=True) or {}
    model_id = data.get('model_id', '').strip()
    if not model_id:
        return jsonify({'success': False, 'error': '缺少 model_id'}), 400

    # ★ 2026-07-12: 禁止把垂直模型设为当前通用模型
    _VERTICAL_KW = ['musicgen', 'bark', 'riffusion', 'audiocraft', 'whisper',
                     'stable-diffusion', 'sdxl', 'sd3', 'flux', 'kolors',
                     'cogvideo', 'animatediff', 'embed', 'bge-', 'e5-', 'nomic',
                     'huatuo', 'self-play']
    _id_lower = model_id.lower()
    for kw in _VERTICAL_KW:
        if kw in _id_lower:
            return jsonify({'success': False, 'error': f'「{model_id}」是垂直模型，不能设为当前通用模型。请在模型配置里选择。'}), 400

    config['current_model'] = model_id
    try:
        # 持久化到 config.yaml
        config.save()
        # ★ 立即加载模型（warmup）
        actual_name = model_id
        for prefix in ('ollama:', 'local:'):
            if actual_name.startswith(prefix):
                actual_name = actual_name[len(prefix):]
        try:
            import requests as _rq
            _rq.post(f'http://localhost:11434/api/generate',
                     json={'model': actual_name, 'prompt': '', 'stream': False, 'keep_alive': -1},
                     timeout=10)
            logger.info(f"[Models] 模型已切换并加载: {actual_name}")
        except Exception:
            logger.debug(f"[Models] 模型加载尝失败（将在首次对话时加载）: {actual_name}")
    except Exception as e:
        logger.warning(f"[Models] 持久化 current_model 失败: {e}")
    
    return jsonify({'success': True, 'current': model_id})


# ==================== 模型搜索 & 安装 ====================

@app.route('/api/models/search', methods=['GET'])
def models_search():
    """搜索模型（HuggingFace 镜像 + ModelScope 回退）"""
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    results = []
    try:
        import requests as ms_requests
        # 主搜索：hf-mirror（HuggingFace 国内镜像，速度快）
        r = ms_requests.get(
            'https://hf-mirror.com/api/models',
            params={'search': q, 'limit': 8, 'sort': 'downloads', 'full': 'false'},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            for m in data[:8]:
                mid = m.get('id', m.get('modelId', ''))
                sname = mid.split('/')[-1] if '/' in mid else mid
                # 生成 Windows 下载命令
                safe_dir = mid.replace('/', '_')
                dl_cmd = (
                    f'pip install huggingface_hub\n'
                    f'set HF_ENDPOINT=https://hf-mirror.com\n'
                    f'huggingface-cli download {mid} --local-dir data\\models\\{safe_dir} --resume-download'
                )
                results.append({
                    'model_id': mid,
                    'name': sname,
                    'desc': (m.get('pipeline_tag', '') + ' · ' + ', '.join(m.get('tags', [])[:3]))[:120],
                    'source': 'hf-mirror',
                    'dl_cmd': dl_cmd
                })
    except Exception as e:
        logger.warning(f"[Models] HF 搜索失败: {e}")

    if not results:
        # 回退：直接给 ModelScope 搜索链接
        results.append({
            'model_id': q,
            'name': f'"{q}" 的搜索结果',
            'desc': '点击打开魔搭搜索页面',
            'source': 'modelscope',
            'url': f'https://modelscope.cn/models?name={q}'
        })
    return jsonify({'results': results})


@app.route('/api/models/install', methods=['POST'])
def models_install():
    """安装模型（HF镜像下载 + ModelScope回退）"""
    data = request.get_json(silent=True) or {}
    model_id = data.get('model_id', '').strip()
    if not model_id:
        return jsonify({'success': False, 'error': '缺少 model_id'}), 400
    try:
        # 1. 先尝试内置目录
        from src.core.model_installer import install_model, MODEL_CATALOG
        if model_id in MODEL_CATALOG:
            result = install_model(model_id)
            return jsonify(result)
        # 2. HF镜像下载（huggingface_hub）
        import os, sys
        models_dir = os.path.join(os.path.dirname(__file__), 'data', 'models')
        os.makedirs(models_dir, exist_ok=True)
        target = os.path.join(models_dir, model_id.replace('/', '_'))
        if os.path.exists(target) and os.listdir(target):
            return jsonify({'success': True, 'message': f'已存在: {target}'})
        # 使用 HF_ENDPOINT 国内镜像
        env = os.environ.copy()
        env['HF_ENDPOINT'] = 'https://hf-mirror.com'
        import subprocess
        cp = subprocess.run(
            [sys.executable, '-m', 'huggingface_hub', 'snapshot_download', model_id,
             '--local-dir', target, '--resume-download'],
            capture_output=True, text=True, timeout=900, env=env
        )
        if cp.returncode == 0:
            return jsonify({'success': True, 'message': f'已安装到 {target}'})
        # 失败：给手动链接
        return jsonify({
            'success': False,
            'error': f'下载失败。手动下载:\nhttps://hf-mirror.com/{model_id}\nhttps://modelscope.cn/models/{model_id}',
            'url': f'https://hf-mirror.com/{model_id}'
        }), 200
    except Exception as e:
        logger.error(f"[Models] 安装失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== Skill API: 音乐生成 ====================

@app.route('/api/skill/music-gen', methods=['POST'])
def skill_music_gen():
    """Skill API: 根据文本描述用 ACE-Step 生成音乐"""
    try:
        data = request.get_json(silent=True) or {}
        prompt = data.get('prompt', '').strip()
        duration = float(data.get('duration', 30))
        output_name = data.get('output', None)

        if not prompt:
            return jsonify({'success': False, 'error': 'prompt 不能为空'}), 400

        # 导入 generate 模块并调用
        model_dir = os.path.join(os.path.dirname(__file__), 'data', 'models', 'acestep-v15-xl-turbo')
        if not os.path.isdir(model_dir):
            return jsonify({'success': False, 'error': '模型未安装，路径: ' + model_dir}), 404

        import sys, importlib
        sys.path.insert(0, model_dir)
        from modeling_acestep_v15_xl_turbo import AceStepConditionGenerationModel
        from configuration_acestep_v15 import AceStepConfig
        import torch, soundfile as sf

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"[MusicGen] 设备: {device}, prompt: {prompt[:60]}")

        config = AceStepConfig.from_pretrained(model_dir)
        model = AceStepConditionGenerationModel.from_pretrained(
            model_dir, config=config,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32
        ).to(device).eval()

        with torch.no_grad():
            audio = model.generate(prompt=prompt, max_length=int(duration * 50), device=device)

        import time
        media_dir = os.path.join(os.path.dirname(__file__), 'data', 'media')
        os.makedirs(media_dir, exist_ok=True)
        out_name = output_name or f"music_{int(time.time())}.wav" if output_name and '.' in output_name else f"{output_name or 'music'}_{int(time.time())}.wav"
        out_path = os.path.join(media_dir, out_name)

        audio_np = audio.cpu().numpy().squeeze()
        audio_np = audio_np / max(abs(audio_np.max()), abs(audio_np.min()), 1.0)
        sf.write(out_path, audio_np, 44100)
        logger.info(f"[MusicGen] 已保存: {out_path}")

        return jsonify({'success': True, 'output': out_path, 'filesize_mb': round(os.path.getsize(out_path) / 1048576, 1)})
    except Exception as e:
        logger.error(f"[MusicGen] 生成失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


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
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")

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


# ==================== 推荐工具 ====================

@app.route('/api/plugins/recommended', methods=['GET'])
def get_recommended_tools():
    """获取推荐工具列表，含安装状态检测"""
    tools = [
        # === 电脑控制 ===
        {
            'id': 'cli-anything-hub',
            'name': 'CLI-Anything Hub',
            'icon': '🎨',
            'category': '电脑控制',
            'desc': '79个创意软件CLI封装，Apache 2.0协议',
            'size': '基础包 ~5MB',
            'tip': '安装后可通过对话直接控制 Blender / GIMP / Inkscape 等软件。目标软件需自行安装。',
            'pkg': 'cli-anything-hub',
            'license': 'Apache-2.0',
            'installed': False,
        },
        {
            'id': 'windows-use',
            'name': 'Windows-Use',
            'icon': '🖥️',
            'category': '电脑控制',
            'desc': 'AI操作Windows界面——点击/输入/切换窗口/运行命令',
            'size': '~5MB',
            'tip': '⚠️ AI可控制鼠标键盘，建议全权模式使用。需要Windows 10/11 + Python 3.10+。',
            'pkg': 'windows-use',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'browser-use',
            'name': 'Browser Use',
            'icon': '🌐',
            'category': '电脑控制',
            'desc': 'AI控制浏览器——自动填表单/抓数据/网页自动化',
            'size': '~10MB',
            'tip': '⚠️ 可自动填写网页表单。需要Playwright浏览器。',
            'pkg': 'browser-use',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'pyautogui',
            'name': 'pyautogui',
            'icon': '🖱️',
            'category': '电脑控制',
            'desc': '鼠标键盘自动化——模拟点击/移动/拖拽/键盘输入',
            'size': '~3MB',
            'tip': '⚠️ AI可控制鼠标键盘，建议全权模式使用。轻量级GUI自动化。',
            'pkg': 'pyautogui',
            'license': 'BSD',
            'installed': False,
        },
        # === 办公文档 ===
        {
            'id': 'python-docx',
            'name': 'python-docx',
            'icon': '📝',
            'category': '办公文档',
            'desc': '读写Word文档——创建/编辑.docx文件',
            'size': '~3MB',
            'tip': '让哈文帮你自动生成Word报告。',
            'pkg': 'python-docx',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'openpyxl',
            'name': 'openpyxl',
            'icon': '📊',
            'category': '办公文档',
            'desc': '读写Excel表格——创建/编辑.xlsx文件，公式/图表',
            'size': '~5MB',
            'tip': '让哈文帮你处理Excel数据。',
            'pkg': 'openpyxl',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'python-pptx',
            'name': 'python-pptx',
            'icon': '📑',
            'category': '办公文档',
            'desc': '生成PowerPoint——创建/编辑.pptx文件',
            'size': '~4MB',
            'tip': '让哈文帮你自动做PPT。',
            'pkg': 'python-pptx',
            'license': 'MIT',
            'installed': False,
        },
        # === 媒体处理 ===
        {
            'id': 'moviepy',
            'name': 'moviepy',
            'icon': '🎬',
            'category': '媒体处理',
            'desc': '视频剪辑——剪切/合并/加水印/加字幕',
            'size': '~15MB',
            'tip': '需要FFmpeg（需单独安装）。',
            'pkg': 'moviepy',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'pydub',
            'name': 'pydub',
            'icon': '🎵',
            'category': '媒体处理',
            'desc': '音频处理——剪切/合并/转格式/调音量',
            'size': '~3MB',
            'tip': '需要FFmpeg（需单独安装）。',
            'pkg': 'pydub',
            'license': 'MIT',
            'installed': False,
        },
        {
            'id': 'pytesseract',
            'name': 'pytesseract',
            'icon': '🔍',
            'category': '媒体处理',
            'desc': 'OCR文字识别——从图片中提取文字',
            'size': '~20MB',
            'tip': '需要Tesseract-OCR引擎（需单独安装）。',
            'pkg': 'pytesseract',
            'license': 'Apache-2.0',
            'installed': False,
        },
        # === 数据分析 ===
        {
            'id': 'pandas',
            'name': 'pandas',
            'icon': '📈',
            'category': '数据分析',
            'desc': '数据分析——处理CSV/Excel/SQL数据',
            'size': '~30MB',
            'tip': '数据科学的标配库。',
            'pkg': 'pandas',
            'license': 'BSD',
            'installed': False,
        },
        {
            'id': 'matplotlib',
            'name': 'matplotlib',
            'icon': '📉',
            'category': '数据分析',
            'desc': '图表生成——折线图/柱状图/饼图/散点图',
            'size': '~10MB',
            'tip': '让哈文帮你把数据变成可视化图表。',
            'pkg': 'matplotlib',
            'license': 'MIT',
            'installed': False,
        },
        # === 语音 ===
        {
            'id': 'pyttsx3',
            'name': 'pyttsx3',
            'icon': '🔊',
            'category': '语音',
            'desc': '文字转语音——离线TTS引擎',
            'size': '~2MB',
            'tip': '让哈文开口说话。支持中英文。',
            'pkg': 'pyttsx3',
            'license': 'MIT',
            'installed': False,
        },
    ]

    # 检测已安装状态 — 使用子进程避免import崩溃影响主进程
    PKG_IMPORT_MAP = {
        'python-docx': 'docx',
        'python-pptx': 'pptx',
        'cli-anything-hub': 'cli_anything_hub',
        'windows-use': 'windows_use',
        'browser-use': 'browser_use',
    }
    for tool in tools:
        try:
            import_name = PKG_IMPORT_MAP.get(tool['pkg'], tool['pkg'].replace('-', '_'))
            # 用子进程检测，避免import崩溃影响Flask主进程
            result = subprocess.run(
                [sys.executable, '-c', f'import {import_name}; print("OK")'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and 'OK' in result.stdout:
                tool['installed'] = True
        except Exception:
            pass

    return jsonify({'tools': tools})


@app.route('/api/plugins/recommended/install', methods=['POST'])
def install_recommended_tool():
    """安装推荐工具（pip install）+ 自动重新加载FC封装"""
    data = request.get_json(silent=True) or {}
    tool_id = data.get('tool_id', '')
    pkg_name = data.get('pkg', '')

    if not pkg_name:
        return jsonify({'success': False, 'error': '缺少包名'}), 400

    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', pkg_name],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0:
            # ★ 2026-07-30: 安装成功后自动重新加载FC封装
            fc_loaded = []
            try:
                from src.core.fc_wrapper_loader import load_fc_wrappers
                import src.core.builtin_tools as _bt
                
                # 重新加载所有封装
                wrapper_tools, wrapper_handlers = load_fc_wrappers()
                
                # 只注册新增的（避免重复）
                for tname, thandler in wrapper_handlers.items():
                    if tname not in _bt.TOOL_REGISTRY:
                        _bt.TOOL_REGISTRY[tname] = {
                            'description': tname,
                            'params': {},
                            'handler': thandler,
                        }
                        fc_loaded.append(tname)
                
                logger.info(f"[Install] {pkg_name} 安装成功，新增 {len(fc_loaded)} 个FC工具: {fc_loaded}")
            except Exception as e:
                logger.error(f"[Install] FC重新加载失败: {e}")

            return jsonify({
                'success': True,
                'name': tool_id,
                'message': f'{pkg_name} 安装成功',
                'fc_tools': fc_loaded,
                'need_restart': False,
            })
        else:
            error_lines = result.stderr.strip().split('\n')[-5:]
            return jsonify({
                'success': False,
                'error': '\n'.join(error_lines) if error_lines else result.stderr.strip(),
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': '安装超时（超过120秒），请检查网络后重试'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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

# ★ 2026-07-13: 知识库列表+删除
@app.route('/api/kb/list', methods=['GET'])
def kb_list():
    """列出知识库内容"""
    from src.core.builtin_tools import list_knowledge
    return jsonify(list_knowledge())

@app.route('/api/kb/delete', methods=['POST'])
def kb_delete():
    """删除知识库条目"""
    data = request.get_json(silent=True) or {}
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'success': False, 'error': '请指定文件名'})
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        # 删除向量库里该来源的所有条目
        removed = 0
        new_docs = []
        for doc in kb.documents:
            if doc.get('source', '') != filename:
                new_docs.append(doc)
            else:
                removed += 1
        if removed > 0:
            kb.documents = new_docs
            kb._rebuild_index()
            kb._save_index()
            kb._save_meta()
            return jsonify({'success': True, 'removed': removed})
        else:
            return jsonify({'success': False, 'error': '未找到该文件的内容'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]})

# ★ 2026-07-14: 查看知识库内容（从向量库取，非磁盘文件）
@app.route('/api/kb/content', methods=['GET'])
def kb_content():
    """查看知识库某来源的全部文本内容"""
    source = request.args.get('source', '')
    if not source:
        return jsonify({'success': False, 'error': '请指定来源'})
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        texts = []
        for doc in kb.documents:
            if (doc.get('source', '') if isinstance(doc, dict) else '') == source:
                t = doc.get('text', '') if isinstance(doc, dict) else str(doc)
                if t:
                    texts.append(t)
        if not texts:
            return jsonify({'success': False, 'error': '未找到该来源的内容'})
        content = '\n\n---\n\n'.join(texts)
        return jsonify({'success': True, 'content': content[:20000], 'chunks': len(texts), 'total_len': len(content)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]})

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


# ==================== 知识库打包导出/导入 ====================

@app.route('/api/kb/export', methods=['POST'])
def kb_export():
    """导出知识库为 zip 包（FAISS 索引 + 元数据 + 源文件列表）"""
    import zipfile
    import tempfile
    from datetime import datetime

    try:
        faiss_dir = PROJECT_ROOT / 'data' / 'faiss'
        knowledge_dir = PROJECT_ROOT / 'data' / 'knowledge'
        imported_files_path = PROJECT_ROOT / 'data' / 'imported_files.json'

        # 生成 zip 到临时目录
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_name = f'harven_kb_export_{timestamp}.zip'
        zip_path = PROJECT_ROOT / 'data' / 'exports' / zip_name
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
            # 1. FAISS 索引文件
            if faiss_dir.exists():
                for f in faiss_dir.iterdir():
                    if f.is_file():
                        zf.write(str(f), f'faiss/{f.name}')

            # 2. 知识库源文件（文档）
            if knowledge_dir.exists():
                for f in knowledge_dir.rglob('*'):
                    if f.is_file():
                        rel = f.relative_to(knowledge_dir)
                        zf.write(str(f), f'knowledge/{rel}')

            # 3. 导入文件记录
            if imported_files_path.exists():
                zf.write(str(imported_files_path), 'imported_files.json')

            # 4. 导出元信息
            export_meta = {
                'exported_at': datetime.now().isoformat(),
                'harven_version': '1.0.0',
                'faiss_files': len(list(faiss_dir.iterdir())) if faiss_dir.exists() else 0,
                'knowledge_files': len(list(knowledge_dir.rglob('*'))) if knowledge_dir.exists() else 0,
            }
            zf.writestr('_export_meta.json', json.dumps(export_meta, ensure_ascii=False, indent=2))

        # 返回下载 URL
        download_url = f'/data/exports/{zip_name}'
        file_size = zip_path.stat().st_size

        logger.info(f'[KB] 知识库导出完成: {zip_name} ({file_size/1024:.1f}KB)')

        return jsonify({
            'success': True,
            'download_url': download_url,
            'filename': zip_name,
            'size': file_size,
            'size_text': f'{file_size/1024:.1f} KB' if file_size < 1024*1024 else f'{file_size/1024/1024:.1f} MB',
        })
    except Exception as e:
        logger.error(f'[KB] 知识库导出失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/kb/import', methods=['POST'])
def kb_import():
    """从 zip 包导入知识库"""
    import zipfile
    import shutil

    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请上传 zip 文件'}), 400

        file = request.files['file']
        if not file.filename.endswith('.zip'):
            return jsonify({'success': False, 'error': '只支持 .zip 文件'}), 400

        # 保存到临时文件
        tmp_zip = PROJECT_ROOT / 'data' / 'imports' / file.filename
        tmp_zip.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(tmp_zip))

        faiss_dir = PROJECT_ROOT / 'data' / 'faiss'
        knowledge_dir = PROJECT_ROOT / 'data' / 'knowledge'
        imported_files_path = PROJECT_ROOT / 'data' / 'imported_files.json'

        imported_count = 0

        with zipfile.ZipFile(str(tmp_zip), 'r') as zf:
            for info in zf.infolist():
                # 跳过目录和元信息
                if info.is_dir() or info.filename.startswith('_'):
                    continue

                # 解压到对应目录
                if info.filename.startswith('faiss/'):
                    target = faiss_dir / info.filename.replace('faiss/', '', 1)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(str(target), 'wb') as dst:
                        dst.write(src.read())
                    imported_count += 1

                elif info.filename.startswith('knowledge/'):
                    target = knowledge_dir / info.filename.replace('knowledge/', '', 1)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(str(target), 'wb') as dst:
                        dst.write(src.read())
                    imported_count += 1

                elif info.filename == 'imported_files.json':
                    with zf.open(info) as src:
                        imported_files_path.write_bytes(src.read())

        # 清理临时文件
        tmp_zip.unlink()

        # 重新加载知识库
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            if hasattr(kb, 'reload'):
                kb.reload()
        except Exception as _e:
            logger.debug(f"[WebApp] 忽略错误: {_e}")

        logger.info(f'[KB] 知识库导入完成: {imported_count} 个文件')

        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'message': f'成功导入 {imported_count} 个文件',
        })
    except Exception as e:
        logger.error(f'[KB] 知识库导入失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


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
    """获取二维码（base64 PNG）"""
    name = request.args.get('name', '移动设备')
    hours = float(request.args.get('hours', 72))
    try:
        from src.core.mobile_connector import create_mobile_token, generate_qr_base64
        result = create_mobile_token(name=name, lifetime_hours=hours)
        qr_b64 = generate_qr_base64(result['qr_url'])
        return jsonify({
            'qrcode': qr_b64,
            'qrcode_format': 'base64_png',
            'token': result.get('token',''),
            'qr_url': result.get('qr_url',''),
            'local_ip': result.get('local_ip',''),
            'port': result.get('port',7000),
        })
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

    # Phase 4: 写入用户选择的默认模型到 config.yaml（同时写入 backends.local 和 ollama 兼容字段）
    try:
        data = request.get_json(silent=True) or {}
        default_model = data.get('default_model', '').strip()
        if default_model:
            from src.utils.config import Config
            cfg = Config()
            # Phase 2: 写入 backends.local
            if 'backends' not in cfg:
                cfg['backends'] = {}
            if 'local' not in cfg['backends']:
                cfg['backends']['local'] = {}
            cfg['backends']['local']['default_model'] = default_model
            # 兼容旧代码
            if 'ollama' not in cfg:
                cfg['ollama'] = {}
            cfg['ollama']['default_model'] = default_model
            cfg.save()
    except Exception as e:
        logger.error(f"[FirstRun] 写入默认模型到 config.yaml 失败: {e}")

    return jsonify({'status': 'ok'})


@app.route('/api/settings/db-status')
def api_db_status():
    """检查数据库初始化状态"""
    try:
        import os
        from src.models.database import DEFAULT_DB_PATH
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        if not os.path.exists(db_path):
            return jsonify({'initialized': False, 'error': '数据库文件不存在'})
        # 检查关键表是否存在
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')
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
        from src.models.database import DEFAULT_DB_PATH
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')
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


@app.route('/api/models/api-keys', methods=['GET'])
def get_api_keys():
    try:
        from src.utils.config import Config
        import json
        config = Config()
        mkeys = config.get('model_keys', {}) or {}
        
        # ★ 合并 multi_models.json 中的 Key（旧存储路径）
        mm_path = PROJECT_ROOT / 'data' / 'multi_models.json'
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                for pid, entry in mm_config.get('models', {}).items():
                    if pid == 'ollama': continue
                    if entry.get('api_key'):
                        mkeys[pid] = entry['api_key']
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")
        
        result = {}
        for k in ['deepseek', 'doubao', 'qwen', 'kimi', 'zhipu', 'yi', 'baichuan', 'stepfun']:
            v = mkeys.get(k, '')
            result[k] = {
                'configured': bool(v),
                'masked': (v[:4] + '****' + v[-4:]) if len(v) > 8 else (v or '')
            }
        # ★ 也包含自定义provider
        for k in mkeys:
            if k not in result and k != 'ollama':
                v = mkeys.get(k, '')
                result[k] = {
                    'configured': bool(v),
                    'masked': (v[:4] + '****' + v[-4:]) if len(v) > 8 else (v or ''),
                    'name': k
                }
        return jsonify({'success': True, 'keys': result})
    except Exception as e:
        return jsonify({'success': False, 'keys': {}, 'error': str(e)}), 500

@app.route('/api/models/api-keys', methods=['POST'])
def save_api_keys():
    try:
        from src.utils.config import Config
        data = request.get_json(force=True, silent=True) or {}
        # ★ 兼容新格式 {keys:{...}, models:{...}} 和旧格式 {...}
        if 'keys' in data:
            key_data = data['keys']
            model_data = data.get('models', {})
        else:
            key_data = data
            model_data = {}
        config = Config()
        # ★ 2026-08-03: 保存所有传入的key（不再硬编码4个）
        all_keys = {}
        for k, v in key_data.items():
            if v:  # 只保存非空的
                all_keys[k] = v
        # 保留旧的key（用户没填的provider不清空）
        old_keys = config.get('model_keys', {}) or {}
        for k, v in old_keys.items():
            if k not in all_keys and v:
                all_keys[k] = v
        config.set('model_keys', all_keys)
        config.save()
        # ★ 同时同步到 multi_models.json（兼容旧存储）
        import json
        mm_path = PROJECT_ROOT / 'data' / 'multi_models.json'
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                mm_config.setdefault('models', {})
                # ★ 2026-08-03: 同步所有provider（不再硬编码4个）
                for pid, key_val in key_data.items():
                    if pid == 'ollama': continue
                    mm_config['models'].setdefault(pid, {})
                    # ★ 只在非空时更新key，不清空已有key
                    if key_val:
                        mm_config['models'][pid]['api_key'] = key_val
                        mm_config['models'][pid]['enabled'] = True
                    try:
                        from src.core.multi_model_manager import PROVIDERS
                        if pid in PROVIDERS:
                            mm_config['models'][pid]['base_url'] = PROVIDERS[pid]['base_url']
                    except Exception:
                        pass
                # ★ 同步模型选择（所有provider）
                for pid, mdl in model_data.items():
                    mm_config['models'].setdefault(pid, {})
                    mm_config['models'][pid]['model_name'] = mdl
                mm_path.write_text(json.dumps(mm_config, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as _e: logger.debug(f"[WebApp] 忽略错误: {_e}")
        # ★ 刷新内存缓存（确保引擎立即看到新 Key）
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            mm.config = mm._load_config()
            mm.current_model = mm.config.get('current_model', 'ollama')
        except Exception as _e: logger.debug(f"[WebApp] 忽略错误: {_e}")
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/models/switch-foreign', methods=['POST'])
def switch_foreign_model():
    """切换当前外脑模型（持久化到磁盘）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        data = request.get_json(force=True, silent=True) or {}
        model = data.get('model', '')
        mm = get_multi_model_manager()
        # ★ 使用 set_current_model 持久化
        result = mm.set_current_model(model or 'ollama')
        return jsonify({'success': True, 'current': model or 'ollama'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/models/current-foreign', methods=['GET'])
def current_foreign_model():
    """获取当前外脑模型"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        model = mm.current_model
        return jsonify({'model': model if model != 'ollama' else ''})
    except Exception:
        return jsonify({'model': ''})

@app.route('/api/models/test-connect', methods=['POST'])
def test_model_connect():
    """测试已配置的 API Key 连接（逐个测试，返回第一个成功的结果）"""
    try:
        from src.utils.config import Config
        import json
        keys = Config().get('model_keys', {})
        
        # ★ 合并 multi_models.json 中的 Key
        mm_path = PROJECT_ROOT / 'data' / 'multi_models.json'
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                for pid, entry in mm_config.get('models', {}).items():
                    if pid == 'ollama': continue
                    if entry.get('api_key'):
                        keys[pid] = entry['api_key']
            except Exception as _e: logger.debug(f"[WebApp] 忽略错误: {_e}")
        
        # ★ 读取当前选定模型名（用户可在设置中选择）
        mm_models = {}
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                mm_models = mm_config.get('models', {})
            except Exception as _e: logger.debug(f"[WebApp] 忽略错误: {_e}")
        
        # 各厂商的测试端点（模型名优先从用户选择读取）
        test_configs = [
            ('deepseek', keys.get('deepseek', ''),
             'https://api.deepseek.com/v1/chat/completions', mm_models.get('deepseek', {}).get('model_name', 'deepseek-chat')),
            ('doubao', keys.get('doubao', ''),
             'https://ark.cn-beijing.volces.com/api/v3/chat/completions', mm_models.get('doubao', {}).get('model_name', 'doubao-seed-2-0-pro-260215')),
            ('qwen', keys.get('qwen', ''),
             'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', 'qwen-turbo'),
            ('kimi', keys.get('kimi', ''),
             'https://api.moonshot.cn/v1/chat/completions', 'moonshot-v1-8k'),
            ('zhipu', keys.get('zhipu', ''),
             'https://open.bigmodel.cn/api/paas/v4/chat/completions', mm_models.get('zhipu', {}).get('model_name', 'glm-4-flash')),
            ('yi', keys.get('yi', ''),
             'https://api.lingyiwanwu.com/v1/chat/completions', mm_models.get('yi', {}).get('model_name', 'yi-lightning')),
            ('baichuan', keys.get('baichuan', ''),
             'https://api.baichuan-ai.com/v1/chat/completions', 'Baichuan4-Turbo'),
            ('stepfun', keys.get('stepfun', ''),
             'https://api.stepfun.com/v1/chat/completions', 'step-1-flash'),
        ]
        
        tested = []
        for name, key, url, model in test_configs:
            if not key:
                continue
            try:
                import requests as _r
                resp = _r.post(url,
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role':'user','content':'hi'}], 'max_tokens': 1},
                    timeout=10)
                if resp.status_code == 200:
                    return jsonify({'success': True, 'model': f'{name} ✅', 'tested': tested + [name]})
                else:
                    err_info = f'{name}: HTTP {resp.status_code}'
                    try:
                        err_body = resp.json()
                        err_info += f' ({err_body.get("error",{}).get("message","")})'
                    except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
                    tested.append(err_info)
            except Exception as e:
                tested.append(f'{name}: {str(e)[:50]}')
        
        if tested:
            return jsonify({'success': False, 'error': '; '.join(tested), 'tested': tested})
        return jsonify({'success': False, 'error': '请先配置至少一个 API Key'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/settings/search', methods=['GET', 'POST'])
def handle_search_settings():
    """联网搜索设置 — GET读取 / POST保存（SearXNG / Bing）"""
    try:
        from src.utils.config import Config
        config = Config()

        if request.method == 'GET':
            # ★ 2026-07-06: 新增GET方法，供前端读取SearXNG配置
            return jsonify({
                'search_provider': config.get('search_provider', 'bing'),
                'searxng_url': config.get('searxng_url', ''),
                'bing_api_key': config.get('bing_api_key', ''),
                'web_search_enabled': config.get('web_search_enabled', True)
            })

        # POST: 保存设置
        data = request.get_json(force=True, silent=True) or {}
        config.set('search_provider', data.get('search_provider', 'off'))
        config.set('searxng_url', data.get('searxng_url', ''))
        config.set('bing_api_key', data.get('bing_api_key', ''))
        config.set('web_search_enabled', data.get('web_search_enabled', False))
        config.save()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/settings/searxng-test', methods=['POST'])
def test_searxng_connection():
    """★ 2026-07-20: SearXNG 连通性检测 — 请求一次搜索API验证服务是否在运行"""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip().rstrip('/')
    if not url:
        return jsonify({'reachable': False, 'error': 'URL为空'})

    try:
        from urllib.parse import quote_plus
        import requests
        test_url = f"{url}/search?q={quote_plus('test')}&format=json&categories=general&language=zh-CN"
        resp = requests.get(test_url, headers={"User-Agent": "HarvenAI/1.0"}, timeout=8)
        if resp.status_code == 200:
            try:
                j = resp.json()
                n = len(j.get('results', []))
                return jsonify({'reachable': True, 'results_count': n, 'error': ''})
            except Exception:
                return jsonify({'reachable': False, 'error': f'服务返回非JSON（可能不是SearXNG，或未开启JSON格式）'})
        else:
            return jsonify({'reachable': False, 'error': f'HTTP {resp.status_code}'})
    except requests.exceptions.Timeout:
        return jsonify({'reachable': False, 'error': '连接超时（8秒），服务可能未启动'})
    except requests.exceptions.ConnectionError:
        return jsonify({'reachable': False, 'error': '无法连接，服务可能未启动'})
    except Exception as e:
        return jsonify({'reachable': False, 'error': str(e)[:200]})

# ★ 2026-07-07: /api/settings/web-search 已合并到 /api/settings/search（GET+POST）
# ★ 2026-07-07: /api/system/open-install-searxng 已删除（SearXNG现在可选配置，不需要安装脚本）

@app.route('/api/models/installer/catalog', methods=['GET'])
def model_catalog():
    """获取可安装的非 Ollama 模型列表（不再用于自动推荐，仅供搜索API内部调用）"""
    from src.core.model_installer import get_catalog, check_model_installed
    catalog = get_catalog()
    result = {}
    for mid, info in catalog.items():
        result[mid] = dict(info)
        result[mid]['installed'] = check_model_installed(mid)
    return jsonify(result)

@app.route('/api/models/installer/search', methods=['GET'])
def search_installable_models():
    """
    搜索可安装模型（零推荐：用户主动搜索才返回结果）
    合并 model_installer.py 的 6 个硬编码模型 + model_catalog.json 的 44 个目录模型
    """
    q = (request.args.get('q', '') or '').strip().lower()
    category = (request.args.get('category', 'all') or 'all').strip().lower()
    
    from src.core.model_installer import get_catalog, check_model_installed, MODEL_CATALOG
    from src.core.model_catalog import search_models, get_all_models
    
    # 分类标准化映射（中→英，统一比较）
    _CAT_NORM = {'音乐': 'music', '语音': 'speech', '图像': 'image', '视频': 'video',
                 '多模态': 'multimodal', '嵌入': 'embedding', '大语言': 'llm',
                 '文字': 'llm', '对话': 'llm'}
    
    def _norm_cat(c):
        """将中文分类名标准化为英文"""
        cl = (c or '').strip().lower()
        return _CAT_NORM.get(cl, cl)
    
    results = []
    seen_names = set()
    
    # 1) 搜索硬编码安装器模型（6个）
    for mid, info in MODEL_CATALOG.items():
        name_lower = info['name'].lower()
        cat_lower = _norm_cat(info.get('category', ''))
        match = False
        if not q:
            match = True
        elif q in name_lower or q in cat_lower:
            match = True
        elif info.get('install', {}).get('desc', '').lower().find(q) >= 0:
            match = True
        
        if match and (category == 'all' or cat_lower == category):
            installed = check_model_installed(mid)
            entry = dict(info)
            entry['id'] = mid
            entry['installed'] = installed
            entry['source'] = 'installer'
            entry['license'] = info.get('extra_info', '').find('MIT') >= 0 and 'MIT' or info.get('extra_info', '').find('Apache') >= 0 and 'Apache 2.0' or '开源（详见官网）'
            results.append(entry)
            seen_names.add(info['name'])
    
    # 2) 搜索目录模型（44个，去重）
    if not q:
        catalog_models = get_all_models()
    else:
        catalog_models = search_models(q)
    
    for m in catalog_models:
        name_lower = m.get('name', '').lower()
        cat_lower = _norm_cat(m.get('category', ''))
        
        # 跳过已存在的（来自安装器的同名模型）
        if m.get('name') in seen_names:
            continue
        
        if category != 'all' and cat_lower != category:
            continue
        
        entry = {
            'id': m.get('id', name_lower.replace(' ', '_').replace('-', '_')),
            'name': m.get('name', ''),
            'category': m.get('category', ''),
            'install': {
                'type': 'ollama',
                'package': m.get('ollama_model', m.get('model_id', '')),
                'desc': m.get('description', ''),
            },
            'model_size': m.get('size', m.get('size_gb', '?')),
            'requirements': {
                'ram': f"{m.get('ram_gb', '?')}GB",
                'gpu': m.get('vram_gb', 0) > 0 and '需要' or '可选',
                'disk': '未知',
            },
            'installed': False,
            'source': 'catalog',
            'license': m.get('license', '开源'),
        }
        results.append(entry)
    
    # 硬件信息
    hw = _get_hardware_info()
    
    return jsonify({
        'models': results,
        'total': len(results),
        'hardware': hw,
        'query': q or '',
        'category': category,
    })

@app.route('/api/models/installer/hardware', methods=['GET'])
def hardware_info():
    """返回当前设备的硬件信息（VRAM/RAM），供前端显示"""
    return jsonify(_get_hardware_info())


def _get_hardware_info() -> dict:
    """检测硬件配置"""
    info = {'ram_gb': 8, 'vram_gb': 0, 'gpu_name': '未知'}
    try:
        import psutil
        info['ram_gb'] = round(psutil.virtual_memory().total / (1024**3))
    except ImportError:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            info['vram_gb'] = round(torch.cuda.get_device_properties(0).total_memory / (1024**3))
            info['gpu_name'] = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    # 尝试 nvidia-smi 兜底
    if info['vram_gb'] == 0:
        try:
            import subprocess
            r = subprocess.run(['nvidia-smi', '--query-gpu=memory.total,name', '--format=csv,noheader,nounits'],
                              capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(',')
                info['vram_gb'] = round(float(parts[0].strip()) / 1024)
                if len(parts) > 1:
                    info['gpu_name'] = parts[1].strip()
        except Exception as _e:
            logger.debug(f"[System] GPU信息检测跳过: {_e}")
    return info

@app.route('/api/models/installer/install/<model_id>', methods=['POST'])
def install_external_model(model_id):
    """安装一个非 Ollama 模型"""
    from src.core.model_installer import install_model
    result = install_model(model_id)
    return jsonify(result)

# ═══════════════ GGUF 本地模型下载（无需 Ollama）═══════════════

@app.route('/api/models/local-list', methods=['GET'])
def local_model_list():
    """获取可下载的本地 GGUF 模型列表（ModelScope）"""
    try:
        vram = float(request.args.get('vram_gb', 0))
    except (ValueError, TypeError):
        vram = None
    from src.core.model_downloader import list_models
    return jsonify({"models": list_models(vram)})

@app.route('/api/models/local-download/<model_id>', methods=['POST'])
def download_local_model(model_id):
    """从 ModelScope 下载 GGUF 模型"""
    from src.core.model_downloader import start_download_async
    start_download_async(model_id)
    return jsonify({"status": "started", "model_id": model_id})

@app.route('/api/models/local-download/<model_id>/status', methods=['GET'])
def download_status(model_id):
    """查询下载进度"""
    from src.core.model_downloader import get_download_status
    status = get_download_status(model_id)
    if not status:
        return jsonify({"status": "not_found"})
    return jsonify(status)

@app.route('/api/models/local-installed', methods=['GET'])
def local_models_installed():
    """获取已安装的 GGUF 本地模型"""
    from src.core.model_downloader import list_models, get_installed_model_path
    models = [m for m in list_models() if m.get("installed")]
    path = get_installed_model_path()
    return jsonify({
        "models": models,
        "active_model_path": str(path) if path else None
    })

@app.route('/api/models/custom-provider', methods=['POST'])
def add_custom_provider():
    """添加自定义外部模型"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        data = request.get_json(force=True, silent=True) or {}
        mm = get_multi_model_manager()
        result = mm.add_custom_provider(
            provider_id=data.get('provider_id',''),
            name=data.get('name',''),
            base_url=data.get('base_url',''),
            api_key=data.get('api_key',''),
            model_name=data.get('model_name',''),
            models=data.get('models', [])
        )
        if result.get('success'):
            mm.enable_model(data['provider_id'], api_key=data.get('api_key',''), skip_test=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/evolution/code-proposal', methods=['POST'])
def create_code_proposal():
    try:
        from src.core.evolution_manager import get_evolution_manager
        data = request.get_json(force=True, silent=True) or {}
        em = get_evolution_manager()
        result = em.create_code_proposal(
            title=data.get('title', ''),
            description=data.get('description', ''),
            changes=data.get('changes', []),
            proposed_by=data.get('proposed_by', 'unknown'))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/evolution/code-proposal/<int:proposal_id>/apply', methods=['POST'])
def apply_code_proposal(proposal_id):
    try:
        from src.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        result = em.apply_code_proposal(proposal_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/local-service/start', methods=['POST'])
def start_local_service():
    """启动本地 AI 服务（ACE-Step 等）"""
    try:
        import subprocess, platform
        data = request.get_json(force=True, silent=True) or {}
        service = data.get('service', '')
        dir_path = data.get('dir', '')
        
        if not dir_path:
            return jsonify({'success': False, 'message': '未指定服务目录'})
        
        # 查找启动脚本
        p = Path(dir_path)
        if not p.exists():
            return jsonify({'success': False, 'message': f'目录不存在: {dir_path}'})
        
        # 优先 bat（Windows），其次 sh
        bat_files = list(p.glob('start_api_server.*'))
        if not bat_files:
            bat_files = list(p.glob('start_*.bat')) + list(p.glob('start_*.sh'))
        
        if not bat_files:
            return jsonify({'success': False, 'message': f'未找到启动脚本\n请在该目录手动执行 bat/sh 文件'})
        
        script = bat_files[0]
        if platform.system() == 'Windows' and script.suffix == '.bat':
            subprocess.Popen(['cmd', '/c', str(script)], cwd=str(p), 
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen(['bash', str(script)], cwd=str(p))
        
        return jsonify({'success': True, 'message': f'已启动 {script.name}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/local-service/stop', methods=['POST'])
def stop_local_service():
    """停止本地 AI 服务"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        service = data.get('service', '')
        # 查找端口 8001 进程
        if service == 'ace_step':
            import subprocess
            result = subprocess.run(
                ['netstat', '-ano'], capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                if ':8001' in line and 'LISTENING' in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                    return jsonify({'success': True, 'message': f'已停止 PID {pid}'})
        return jsonify({'success': True, 'message': '未找到运行中的服务'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/file/check', methods=['GET'])
def check_file_path():
    """检查文件/目录是否存在"""
    try:
        import os
        p = request.args.get('path', '')
        home = str(Path.home()).replace('\\', '/')
        full = home + p
        exists = os.path.exists(full)
        return jsonify({'exists': exists, 'path': full})
    except Exception:
        return jsonify({'exists': False})

@app.route('/api/agents', methods=['GET'])
def agent_list():
    """列出所有已注册 Agent"""
    try:
        from src.core.agent_registry import get_registry
        return jsonify({'agents': get_registry().list_agents()})
    except Exception as e:
        return jsonify({'agents': [], 'error': str(e)})

@app.route('/api/agents/register', methods=['POST'])
def agent_register():
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ['id', 'name', 'role', 'system_prompt']
        for key in required:
            if not data.get(key):
                return jsonify({'success': False, 'error': f'缺少 {key}'}), 400
        from src.core.agent_registry import get_registry
        registry = get_registry()
        ok = registry.register(
            agent_id=data['id'], name=data['name'], role=data['role'],
            system_prompt=data['system_prompt'],
            model_pref=data.get('model_pref', 'local'),
            skills=data.get('skills', []),
            triggers=data.get('triggers', []))
        return jsonify({'success': ok, 'error': '' if ok else 'ID 已被占用'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/agents/<agent_id>', methods=['DELETE'])
def agent_delete(agent_id):
    try:
        from src.core.agent_registry import get_registry
        registry = get_registry()
        a = registry._agents.get(agent_id)
        if not a: return jsonify({'success': False, 'error': '不存在'})
        if not a.get('custom'): return jsonify({'success': False, 'error': '不能删内置 Agent'})
        del registry._agents[agent_id]; registry._save()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ★ 2026-07-14: /api/learner/* 路由已删除 — learner.py 模块不存在


@app.route('/api/system/backup', methods=['POST'])
def system_backup():
    """一键备份所有数据为 ZIP（用于迁移到新电脑）"""
    try:
        import zipfile
        import shutil
        backup_dir = PROJECT_ROOT / 'data' / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_path = backup_dir / f'harven_backup_{timestamp}.zip'

        with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
            # 数据库
            db_path = PROJECT_ROOT / 'data' / 'sqlite' / 'lingzhi.db'
            if db_path.exists():
                zf.write(str(db_path), 'data/sqlite/lingzhi.db')
            # 知识库
            kb_dir = PROJECT_ROOT / 'data' / 'knowledge'
            if kb_dir.exists():
                for f in kb_dir.rglob('*'):
                    if f.is_file():
                        zf.write(str(f), str(f.relative_to(PROJECT_ROOT)))
            # 配置
            config_path = PROJECT_ROOT / 'config.yaml'
            if config_path.exists():
                zf.write(str(config_path), 'config.yaml')
            # 习惯数据
            habits_path = PROJECT_ROOT / 'data' / 'habits.json'
            if habits_path.exists():
                zf.write(str(habits_path), 'data/habits.json')
            # 工作流
            wf_dir = PROJECT_ROOT / 'data' / 'workflows'
            if wf_dir.exists():
                for f in wf_dir.rglob('*'):
                    if f.is_file():
                        zf.write(str(f), str(f.relative_to(PROJECT_ROOT)))

        size_mb = round(zip_path.stat().st_size / (1024 * 1024), 1)
        return jsonify({
            'success': True,
            'path': str(zip_path),
            'filename': zip_path.name,
            'size_mb': size_mb,
            'download_url': f'/api/system/backup/download/{zip_path.name}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/system/backup/download/<filename>')
def system_backup_download(filename):
    """下载备份文件"""
    from flask import send_file
    backup_dir = PROJECT_ROOT / 'data' / 'backups'
    file_path = backup_dir / filename
    if not file_path.exists():
        return jsonify({'error': '文件不存在'}), 404
    return send_file(str(file_path), as_attachment=True, download_name=filename)

@app.route('/api/system/restore', methods=['POST'])
def system_restore():
    """从备份 ZIP 恢复数据"""
    try:
        import zipfile
        if 'backup_file' not in request.files:
            return jsonify({'success': False, 'error': '请上传备份文件'}), 400
        f = request.files['backup_file']
        with zipfile.ZipFile(f) as zf:
            zf.extractall(str(PROJECT_ROOT))
        return jsonify({'success': True, 'message': '数据已恢复，请重启哈文'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
    """浏览市场 — ★ 2026-07-24: 优先查云端，云端不可用时回退本地
    Query params: category, type, sort(newest/popular/rating/downloads), limit, offset
    """
    try:
        category = request.args.get('category', '')
        item_type = request.args.get('type', '')
        sort = request.args.get('sort', 'newest')
        limit = min(int(request.args.get('limit', 20)), 100)
        offset = int(request.args.get('offset', 0))

        # ★ 优先查云端 HarvenHub
        from src.core.harvenhub_client import hub_browse
        cloud_result = hub_browse(sort=sort, category=category or item_type or None, limit=limit, offset=offset)
        if cloud_result.get('success'):
            return jsonify({
                'items': cloud_result['items'],
                'total': cloud_result['total'],
                'source': 'cloud',
            })

        # 云端不可用 → 回退本地
        from src.core.harvenhub_market import browse_items
        items = browse_items(
            category=category or None,
            item_type=item_type or None,
            sort=sort, limit=limit, offset=offset
        )
        return jsonify({
            'items': items,
            'total': len(items),
            'source': 'local',
            'cloud_error': cloud_result.get('error', ''),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/harvenhub-market/search', methods=['GET'])
def harvenhub_market_search():
    """搜索市场 — ★ 2026-07-24: 优先搜云端"""
    try:
        query = request.args.get('q', '')
        category = request.args.get('category', '')
        item_type = request.args.get('type', '')
        limit = min(int(request.args.get('limit', 20)), 100)

        # ★ 优先搜云端
        from src.core.harvenhub_client import hub_search
        cloud_result = hub_search(query, limit=limit)
        if cloud_result.get('success'):
            return jsonify({
                'results': cloud_result['items'],
                'query': query,
                'total': len(cloud_result['items']),
                'source': 'cloud',
            })

        # 回退本地
        from src.core.harvenhub_market import search_market
        results = search_market(
            query, category=category or None,
            item_type=item_type or None, limit=limit
        )
        return jsonify({
            'results': results,
            'query': query,
            'total': len(results),
            'source': 'local',
            'cloud_error': cloud_result.get('error', ''),
        })
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


# ★ 2026-07-24: 云端 HarvenHub 路由（下载/上传/连接检查）

@app.route('/api/harvenhub-cloud/download/<item_id>', methods=['POST'])
def harvenhub_cloud_download(item_id):
    """从云端HarvenHub下载并安装"""
    try:
        from src.core.harvenhub_client import hub_download
        result = hub_download(item_id)
        if not result.get('success'):
            return jsonify(result), 500

        file_path = result.get('file_path', '')

        # ★ 2026-08-02: 如果是skill，自动安装到data/skills/
        if file_path and os.path.exists(file_path):
            filename = os.path.basename(file_path)
            name_without_ext = os.path.splitext(filename)[0]

            # 获取item信息判断类型
            import requests as ext_req
            try:
                info_resp = ext_req.get(f'https://harvenhub.com/api/hub/list?per_page=200&status=all', timeout=10)
                items = info_resp.json().get('items', [])
                item_info = next((i for i in items if i.get('id') == item_id), None)
            except:
                item_info = None

            item_type = item_info.get('type', '') if item_info else ''
            item_name = item_info.get('name', name_without_ext) if item_info else name_without_ext

            if item_type == 'skill':
                # 安装到data/skills/<name>/
                skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'skills')
                skill_dir = os.path.join(skills_dir, item_name)
                os.makedirs(skill_dir, exist_ok=True)

                if filename.endswith('.md'):
                    # MD文件直接复制为SKILL.md
                    import shutil
                    dest = os.path.join(skill_dir, 'SKILL.md')
                    shutil.copy2(file_path, dest)
                    return jsonify({'success': True, 'message': f'技能 "{item_name}" 已安装到 data/skills/{item_name}/'})
                elif filename.endswith('.zip'):
                    # ZIP文件解压
                    import zipfile
                    with zipfile.ZipFile(file_path, 'r') as zf:
                        zf.extractall(skill_dir)
                    return jsonify({'success': True, 'message': f'技能 "{item_name}" 已解压安装到 data/skills/{item_name}/'})
                else:
                    # 其他文件直接复制
                    import shutil
                    shutil.copy2(file_path, skill_dir)
                    return jsonify({'success': True, 'message': f'文件已安装到 data/skills/{item_name}/'})
            else:
                return jsonify({'success': True, 'message': f'文件已下载到 {file_path}（类型: {item_type}）'})

        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/harvenhub-cloud/upload', methods=['POST'])
def harvenhub_cloud_upload():
    """上传文件到云端HarvenHub"""
    try:
        from src.core.harvenhub_client import hub_upload

        # 支持JSON配置上传
        if request.is_json:
            data = request.get_json()
            file_path = data.get('file_path', '')
            item_type = data.get('type', 'skill')
            title = data.get('title', '')
            description = data.get('description', '')
            author = data.get('author', '')
            category = data.get('category', 'other')
        else:
            # 支持文件上传
            file = request.files.get('file')
            if not file:
                return jsonify({'success': False, 'error': '缺少文件'}), 400
            import tempfile
            tmp_path = os.path.join(tempfile.gettempdir(), file.filename)
            file.save(tmp_path)
            file_path = tmp_path
            item_type = request.form.get('type', 'skill')
            title = request.form.get('title', file.filename)
            description = request.form.get('description', '')
            author = request.form.get('author', '')
            category = request.form.get('category', 'other')

        result = hub_upload(file_path, item_type, title, description, author, category)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/harvenhub-cloud/check', methods=['GET'])
def harvenhub_cloud_check():
    """检查与云端HarvenHub的连接状态"""
    try:
        from src.core.harvenhub_client import hub_check_connection
        result = hub_check_connection()
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ 2026-08-02: 云端浏览 — 代理harvenhub.com的API
@app.route('/api/harvenhub-cloud/browse', methods=['GET'])
def harvenhub_cloud_browse():
    """浏览云端HarvenHub社区内容"""
    try:
        import requests as ext_requests
        sort = request.args.get('sort', 'newest')
        item_type = request.args.get('type', '')
        limit = int(request.args.get('limit', 50))

        url = f'https://harvenhub.com/api/hub/list?per_page={limit}&status=approved'
        if item_type:
            url += f'&type={item_type}'

        resp = ext_requests.get(url, timeout=15)
        data = resp.json()
        items = data.get('items', [])

        # 转换字段名（harvenhub用type，哈文前端用item_type）
        for item in items:
            if 'type' in item and 'item_type' not in item:
                item['item_type'] = item.pop('type')

        return jsonify({'success': True, 'items': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'items': []}), 500


@app.route('/api/harvenhub-cloud/categories', methods=['GET'])
def harvenhub_cloud_categories():
    """获取云端分类"""
    try:
        from src.core.harvenhub_client import hub_categories
        result = hub_categories()
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'categories': [], 'error': str(e)}), 500


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
            'help': '请先启动 Stable Diffusion WebUI（webui-user.bat），等待加载完成后重试。'
        }), 503

    return jsonify({'success': True, 'url': result_path, 'prompt': prompt})


def _auto_create_skill(full_text: str):
    """自动检测外脑输出中的 Skill 定义并安装"""
    import re
    # 匹配 ---frontmatter--- 格式或 --- yaml --- 格式
    fm_match = re.search(r'^---\s*[a-z]*\s*\n(.*?)\n---', full_text, re.DOTALL | re.MULTILINE)
    if not fm_match:
        fm_match = re.search(r'^---\s*\n(.*?)\n---', full_text, re.DOTALL)
    if not fm_match:
        return

    try:
        import yaml
        meta = yaml.safe_load(fm_match.group(1))
        if not meta or not meta.get('name') or not meta.get('description'):
            return
        
        name = meta['name']
        description = meta['description']
        triggers = meta.get('triggers', [name.lower()])
        prompt = meta.get('prompt', full_text)

        # 通过 API 安装
        import requests as _r
        _r.post(
            f"http://127.0.0.1:{os.environ.get('FLASK_RUN_PORT', 7000)}/api/skills",
            json={
                'name': name,
                'description': description,
                'triggers': triggers,
                'prompt': prompt[:2000],
                'author': 'external_model',
                'version': '1.0',
            },
            timeout=5
        )
        logger.info(f'[AutoSkill] 已自动创建 Skill: {name}')
    except Exception as e:
        logger.debug(f'[AutoSkill] 跳过自动创建: {e}')


# ═══════════════════════════════════════════════════════════════
# ★ 2026-07-09: HarvenHub 自动安装API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/hub/check-deps', methods=['POST'])
def hub_check_deps():
    """★ 2026-08-10: 安装前检查Skill需要的pip依赖"""
    import zipfile, tempfile, yaml, subprocess, pkg_resources
    import requests

    data = request.get_json()
    item_id = data.get('item_id', '')
    hub_url = data.get('hub_url', 'http://127.0.0.1:5001')

    if not item_id:
        return jsonify({"success": False, "error": "缺少item_id"}), 400

    try:
        # 各厂商的API端点（测试每个是否可用）
        download_url = f"{hub_url}/api/hub/download/{item_id}"
        resp = requests.get(download_url, timeout=30)
        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"下载失败: HTTP {resp.status_code}"})

        # 保存到临时文件（需要解压读skill.yml）
        tmp_dir = tempfile.mkdtemp()
        tmp_file = os.path.join(tmp_dir, f"check_{item_id}.zip")
        with open(tmp_file, 'wb') as f:
            f.write(resp.content)

        # 解压并读取skill.yml中的dependencies
        deps = []
        extract_dir = os.path.join(tmp_dir, 'extracted')
        with zipfile.ZipFile(tmp_file, 'r') as zf:
            zf.extractall(extract_dir)

        # 查找skill.yml
        for root, dirs, files in os.walk(extract_dir):
            if 'skill.yml' in files:
                with open(os.path.join(root, 'skill.yml'), 'r', encoding='utf-8') as f:
                    skill_config = yaml.safe_load(f) or {}
                    deps = skill_config.get('dependencies', [])
                    if isinstance(deps, str):
                        deps = [d.strip() for d in deps.split(',') if d.strip()]
                break
            if 'skill.yaml' in files:
                with open(os.path.join(root, 'skill.yaml'), 'r', encoding='utf-8') as f:
                    skill_config = yaml.safe_load(f) or {}
                    deps = skill_config.get('dependencies', [])
                    if isinstance(deps, str):
                        deps = [d.strip() for d in deps.split(',') if d.strip()]
                break

        # 检查每个依赖是否已安装
        results = []
        installed_set = {pkg.key for pkg in pkg_resources.working_set}
        for dep in deps:
            dep_clean = dep.strip().split('==')[0].split('>=')[0].split('<=')[0].strip()
            is_installed = dep_clean.lower().replace('-', '_') in installed_set
            results.append({
                'name': dep.strip(),
                'installed': is_installed,
                'status': '✅ 已安装' if is_installed else '⚠️ 未安装（将自动安装）'
            })

        # 清理临时文件
        shutil.rmtree(tmp_dir, ignore_errors=True)

        return jsonify({"success": True, "deps": results})
    except Exception as e:
        # 读不到skill.yml不算失败——没有声明依赖的Skill直接跳过
        return jsonify({"success": True, "deps": [], "note": "未找到依赖声明（安装无需额外pip包）"})

@app.route('/api/hub/install', methods=['POST'])
def hub_auto_install():
    """从HarvenHub下载并自动安装"""
    import zipfile, shutil, os, tempfile
    import requests

    data = request.get_json()
    item_id = data.get('item_id', '')
    item_name = data.get('name', '')
    item_type = data.get('type', '')
    hub_url = data.get('hub_url', 'http://127.0.0.1:5001')

    if not item_id:
        return jsonify({"success": False, "error": "缺少item_id"}), 400

    try:
        # 从Hub下载文件
        download_url = f"{hub_url}/api/hub/download/{item_id}"
        resp = requests.get(download_url, timeout=60)

        if resp.status_code != 200:
            return jsonify({"success": False, "error": f"下载失败: HTTP {resp.status_code}"}), 500

        # 保存到临时文件
        tmp_dir = tempfile.mkdtemp()
        tmp_file = os.path.join(tmp_dir, f"{item_name}.zip")
        with open(tmp_file, 'wb') as f:
            f.write(resp.content)

        # 解压
        extract_dir = os.path.join(tmp_dir, 'extracted')
        with zipfile.ZipFile(tmp_file, 'r') as z:
            z.extractall(extract_dir)

        installed = []

        # 根据类型安装到对应目录
        base_dir = os.path.dirname(os.path.abspath(__file__))

        if item_type == 'skill':
            # 安装Skill → skills/
            skills_dir = os.path.join(base_dir, 'skills')
            os.makedirs(skills_dir, exist_ok=True)
            if os.path.exists(os.path.join(extract_dir, 'skills')):
                for f in os.listdir(os.path.join(extract_dir, 'skills')):
                    shutil.copy(os.path.join(extract_dir, 'skills', f), skills_dir)
                    installed.append(f"skills/{f}")
            # 如果直接是md文件
            for f in os.listdir(extract_dir):
                if f.endswith('.md'):
                    shutil.copy(os.path.join(extract_dir, f), skills_dir)
                    installed.append(f"skills/{f}")

        elif item_type == 'agent':
            # 安装Agent → data/agents/
            agents_dir = os.path.join(base_dir, 'data', 'agents')
            os.makedirs(agents_dir, exist_ok=True)
            if os.path.exists(os.path.join(extract_dir, 'agents')):
                for f in os.listdir(os.path.join(extract_dir, 'agents')):
                    shutil.copy(os.path.join(extract_dir, 'agents', f), agents_dir)
                    installed.append(f"agents/{f}")

        elif item_type == 'knowledge':
            # 安装知识包 → data/knowledge/
            kb_dir = os.path.join(base_dir, 'data', 'knowledge')
            os.makedirs(kb_dir, exist_ok=True)
            if os.path.exists(os.path.join(extract_dir, 'documents')):
                for f in os.listdir(os.path.join(extract_dir, 'documents')):
                    src = os.path.join(extract_dir, 'documents', f)
                    dst = os.path.join(kb_dir, f)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy(src, dst)
                    installed.append(f"knowledge/{f}")

        elif item_type == 'bundle':
            # Bundle → 分别安装skills/agents/knowledge
            if os.path.exists(os.path.join(extract_dir, 'skills')):
                skills_dir = os.path.join(base_dir, 'skills')
                for f in os.listdir(os.path.join(extract_dir, 'skills')):
                    shutil.copy(os.path.join(extract_dir, 'skills', f), skills_dir)
                    installed.append(f"skills/{f}")
            if os.path.exists(os.path.join(extract_dir, 'agents')):
                agents_dir = os.path.join(base_dir, 'data', 'agents')
                os.makedirs(agents_dir, exist_ok=True)
                for f in os.listdir(os.path.join(extract_dir, 'agents')):
                    shutil.copy(os.path.join(extract_dir, 'agents', f), agents_dir)
                    installed.append(f"agents/{f}")
            if os.path.exists(os.path.join(extract_dir, 'knowledge')):
                kb_dir = os.path.join(base_dir, 'data', 'knowledge')
                os.makedirs(kb_dir, exist_ok=True)
                for d in os.listdir(os.path.join(extract_dir, 'knowledge')):
                    src = os.path.join(extract_dir, 'knowledge', d)
                    dst = os.path.join(kb_dir, d)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy(src, dst)
                    installed.append(f"knowledge/{d}")

        # 清理临时目录
        shutil.rmtree(tmp_dir, ignore_errors=True)

        return jsonify({
            "success": True,
            "installed": installed,
            "message": f"已安装 {len(installed)} 个文件"
        })

    except zipfile.BadZipFile:
        return jsonify({"success": False, "error": "文件不是有效zip"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/hub/execute', methods=['POST'])
def hub_execute_command():
    """执行模型/工具安装命令（ollama pull / pip install / winget / modelscope / huggingface等）"""
    import subprocess
    import sys as _sys

    data = request.get_json()
    command = data.get('command', '')
    method = data.get('method', '')

    if not command:
        return jsonify({"success": False, "error": "缺少命令"}), 400

    try:
        # 解析命令
        parts = command.split()

        # 安全检查：只允许特定命令
        allowed = ['ollama', 'pip', 'winget', 'git']
        if parts[0] not in allowed:
            # ★ 如果是Python代码（from xxx import），用python -c执行
            if command.startswith('from ') or command.startswith('import '):
                # 用.venv的python执行
                venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.venv', 'Scripts', 'python.exe')
                if not os.path.exists(venv_python):
                    venv_python = _sys.executable  # 降级用当前python

                result = subprocess.run(
                    [venv_python, '-c', command],
                    capture_output=True,
                    text=True,
                    timeout=600  # 10分钟超时（下载模型较慢）
                )
                return jsonify({
                    "success": result.returncode == 0,
                    "output": result.stdout[:3000],
                    "error": result.stderr[:3000] if result.returncode != 0 else ""
                })
            else:
                return jsonify({"success": False, "error": f"不允许的命令: {parts[0]}"}), 403

        # 执行
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )

        return jsonify({
            "success": result.returncode == 0,
            "output": result.stdout[:2000],
            "error": result.stderr[:2000] if result.returncode != 0 else ""
        })

    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "执行超时（5分钟）"}), 408
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/models/scan_local', methods=['GET'])
def scan_local_models():
    """扫描本地模型（已合并到system_knowledge，保留旧API兼容）"""
    from src.core.system_knowledge import scan_all
    knowledge = scan_all()
    models = knowledge.get('modelscope_models', []) + knowledge.get('huggingface_models', [])
    return jsonify({"success": True, "models": models})


# ★ 2026-07-09: 手动触发系统知识扫描+重新分类
@app.route('/api/system/scan', methods=['GET'])
def trigger_system_scan():
    """手动触发系统知识扫描 + 重新分类注册"""
    try:
        from src.core.system_knowledge import scan_all
        knowledge = scan_all()
        # ★ 2026-07-11: 扫描后自动重新分类注册
        try:
            from src.core.auto_classifier import auto_classify_and_register
            auto_classify_and_register()
        except Exception as e:
            logger.warning(f"[API] 重新分类失败: {e}")
        return jsonify({"success": True, "knowledge": knowledge})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    import webbrowser
    import socket
    port = 7000  # ★ 2026-07-29: 5000被Windows/macOS占用，改为7000

    # ★ 2026-07-09: 启动时后台异步扫描系统知识
    try:
        from src.core.system_knowledge import scan_async
        scan_async()
    except Exception as e:
        print(f"[警告] 系统知识扫描启动失败: {e}")

    # ★ 2026-07-10: 启动时后台异步分类+注册模型
    try:
        import time
        def _delayed_classify():
            time.sleep(5)  # 等系统扫描完成
            try:
                from src.core.auto_classifier import auto_classify_and_register
                auto_classify_and_register()
            except Exception as e:
                print(f"[警告] 模型自动分类失败: {e}")
        import threading
        threading.Thread(target=_delayed_classify, daemon=True).start()
    except Exception as e:
        print(f"[警告] 模型分类启动失败: {e}")

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

    # Cloudflare Tunnel 公网穿透功能暂未实现（计划后续添加）
    # enable_tunnel = os.environ.get('HARVEN_TUNNEL', '').lower() in ('1', 'true', 'yes')

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

    # P1: 受控进化/统计/保底/萃取 路由已归档（2026-07-02 瘦身）

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

    # P1: Planner 路由已归档（2026-07-02 瘦身）
    # P1: GenScheduler 路由已归档（2026-07-02 瘦身）

    # ===== 注册硬件自检 & 模型推荐路由 =====
    try:
        from src.core.hardware_detector import (
            detect_hardware, recommend_models,
            format_recommendation, pull_model, MODEL_DB,
            get_memory_warnings
        )
        logger.info("[Startup] 硬件自检模块已加载")

        @app.route("/api/hardware/detect", methods=["GET"])
        def api_hardware_detect():
            """硬件自检（返回本机配置）"""
            try:
                spec = detect_hardware()
                mem_warnings = get_memory_warnings(spec)
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
                    "memory_warnings": mem_warnings,
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

    # ===== 注册面板管理路由 =====
    try:
        from src.core.panel_routes import bp as panel_bp
        app.register_blueprint(panel_bp)
    except Exception as e:
        logger.warning(f"面板路由注册失败: {e}")

    # ===== ★ 2026-08-08: 记账API =====
    @app.route('/api/accounting/records', methods=['GET'])
    def accounting_records():
        """获取记账记录"""
        try:
            import sqlite3
            db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sqlite', 'lingzhi.db')
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            records = []
            # panel_accounting
            try:
                for r in conn.execute('SELECT id, type, amount, category, date, note FROM panel_accounting ORDER BY date DESC LIMIT 200'):
                    records.append(dict(r))
            except: pass
            # skill_accounting
            try:
                for r in conn.execute('SELECT id, type, amount, category, date, note FROM skill_accounting ORDER BY date DESC LIMIT 200'):
                    records.append(dict(r))
            except: pass
            conn.close()
            # 去重（按id+date+amount）
            seen = set()
            unique = []
            for r in records:
                key = f"{r.get('date','')}-{r.get('amount',0)}-{r.get('type','')}-{r.get('category','')}"
                if key not in seen:
                    seen.add(key)
                    unique.append(r)
            return jsonify({'records': unique})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/accounting/summary', methods=['GET'])
    def accounting_summary():
        """获取收支汇总"""
        try:
            import sqlite3
            db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'sqlite', 'lingzhi.db')
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            records = []
            try:
                for r in conn.execute('SELECT type, amount, category, date FROM panel_accounting'):
                    records.append(dict(r))
            except: pass
            try:
                for r in conn.execute('SELECT type, amount, category, date FROM skill_accounting'):
                    records.append(dict(r))
            except: pass
            conn.close()
            # 统计
            income = {}
            expense = {}
            monthly = {}
            for r in records:
                cat = r.get('category', '其他')
                amt = r.get('amount', 0)
                typ = r.get('type', '')
                month = (r.get('date', '') or '')[:7]
                if typ == '收入':
                    income[cat] = income.get(cat, 0) + amt
                else:
                    expense[cat] = expense.get(cat, 0) + amt
                if month:
                    if month not in monthly:
                        monthly[month] = {'income': 0, 'expense': 0}
                    if typ == '收入':
                        monthly[month]['income'] += amt
                    else:
                        monthly[month]['expense'] += amt
            return jsonify({
                'total_income': sum(income.values()),
                'total_expense': sum(expense.values()),
                'income_by_category': income,
                'expense_by_category': expense,
                'monthly': monthly,
                'record_count': len(records),
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-08: 流程模板API =====
    @app.route('/api/flows', methods=['GET'])
    def list_flows():
        """列出所有流程模板"""
        try:
            flows_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'skills')
            flows = []
            for item in sorted(os.listdir(flows_dir)):
                skill_md = os.path.join(flows_dir, item, 'SKILL.md')
                script = os.path.join(flows_dir, item, 'scripts', 'main.py')
                if os.path.isdir(os.path.join(flows_dir, item)) and os.path.exists(skill_md):
                    has_script = os.path.exists(script)
                    trigger = ''
                    name = item
                    with open(skill_md, 'r', encoding='utf-8') as f:
                        content = f.read(500)
                        if 'trigger:' in content:
                            idx = content.index('trigger:')
                            trigger = content[idx:].split('\n')[0].split(':', 1)[1].strip()[:80]
                        if 'name:' in content:
                            idx = content.index('name:')
                            name = content[idx:].split('\n')[0].split(':', 1)[1].strip()
                    flows.append({
                        'id': item, 'name': name, 'trigger': trigger,
                        'has_script': has_script,
                        'type': 'script' if has_script else 'prompt',
                    })
            return jsonify({'flows': flows})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/flows/<flow_id>/test', methods=['POST'])
    def test_flow(flow_id):
        """测试运行流程"""
        try:
            from src.core.scripts_runner import run_skill_script
            data = request.get_json(force=True, silent=True) or {}
            action = data.get('action', 'run')
            params = data.get('params', {})
            result = run_skill_script(flow_id, action, params, timeout=300)
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-08: 智能消息拆分API =====
    @app.route('/api/chat/smart-split', methods=['POST'])
    def smart_split():
        """智能拆分复杂消息为多个操作"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            message = data.get('message', '')
            if not message:
                return jsonify({'error': 'message不能为空'}), 400

            # 用正则+规则拆分（不调模型，快速）
            import re
            operations = []

            # 匹配"买XX花XX元"/"XX花费XX"/"XX XX元"等
            # 模式1：买/花 + 项目 + 金额
            patterns = [
                r'买(\w+?)\s*(?:花(?:费)?|用)?\s*(\d+(?:\.\d+)?)\s*元',
                r'(\w+?)\s*(?:花费|花了|支出|用)\s*(\d+(?:\.\d+)?)\s*元',
                r'(\w+?)\s+(\d+(?:\.\d+)?)\s*元',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, message)
                for match in matches:
                    item, amount = match[0], float(match[1])
                    # 推断分类
                    category = '其他'
                    cat_map = {
                        '菜': '买菜', '海鲜': '买菜', '肉': '买菜', '水果': '买菜',
                        '加油': '交通', '油': '交通', '车': '交通', '打车': '交通',
                        '书': '学习', '包': '学习', '笔': '学习',
                        '饭': '餐饮', '吃': '餐饮', '餐': '餐饮',
                        '咖啡': '餐饮',
                    }
                    for kw, cat in cat_map.items():
                        if kw in item:
                            category = cat
                            break
                    operations.append({
                        'type': 'expense',
                        'action': 'accounting',
                        'params': {
                            'type': '支出', 'amount': amount,
                            'category': category, 'note': item,
                        },
                        'original': f'{item} {amount}元',
                    })

            # 检测提醒类
            remind_patterns = [
                r'该(.+?)了', r'记得(.+?)', r'提醒我(.+?)', r'别忘了(.+?)',
            ]
            for pattern in remind_patterns:
                matches = re.findall(pattern, message)
                for match in matches:
                    operations.append({
                        'type': 'reminder',
                        'action': 'reminder',
                        'params': {'task': match},
                        'original': f'提醒：{match}',
                    })

            return jsonify({
                'operations': operations,
                'count': len(operations),
                'original_message': message,
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-08: 多外脑列表/切换API =====
    @app.route('/api/multi-model/list', methods=['GET'])
    def multi_model_list():
        """列出所有已配置的外脑"""
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            # ★ 2026-08-09: 直接用 get_all_models（含 configured 字段，从 config.yaml 读 Key）
            providers = mm.get_all_models()
            return jsonify({'models': providers, 'providers': providers})
        except Exception as e:
            return jsonify({'models': [], 'error': str(e)})

    @app.route('/api/multi-model/switch', methods=['POST'])
    def multi_model_switch():
        """切换当前外脑"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            provider = data.get('provider', '')
            if not provider:
                return jsonify({'success': False, 'error': 'provider不能为空'}), 400
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            if hasattr(mm, 'switch_foreign_model'):
                mm.switch_foreign_model(provider)
                return jsonify({'success': True, 'name': provider})
            elif hasattr(mm, 'set_current_foreign_model'):
                mm.set_current_foreign_model(provider)
                return jsonify({'success': True, 'name': provider})
            else:
                return jsonify({'success': False, 'error': '不支持切换'}), 500
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    # ===== ★ 2026-08-08: ask_user API =====
    @app.route('/api/ask_user/pending', methods=['GET'])
    def ask_user_pending():
        """检查是否有待回答的问题"""
        try:
            comm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'tmp', 'ask_user')
            if not os.path.exists(comm_dir):
                return jsonify({'has_question': False})
            for fname in os.listdir(comm_dir):
                if fname.endswith('_question.json'):
                    fpath = os.path.join(comm_dir, fname)
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.loads(f.read())
                    req_id = fname.replace('_question.json', '')
                    return jsonify({'has_question': True, 'question': data.get('question', ''), 'options': data.get('options', []), 'id': req_id})
            return jsonify({'has_question': False})
        except Exception as e:
            return jsonify({'has_question': False, 'error': str(e)})

    @app.route('/api/ask_user/answer', methods=['POST'])
    def ask_user_answer():
        """提交回答"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            req_id = data.get('id', '')
            answer = data.get('answer', '')
            comm_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'tmp', 'ask_user')
            resp_file = os.path.join(comm_dir, f'{req_id}_answer.txt')
            with open(resp_file, 'w', encoding='utf-8') as f:
                f.write(answer)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: 出站控制API =====
    @app.route('/api/outbound/status', methods=['GET'])
    def outbound_status():
        """出站控制状态"""
        try:
            from src.core.independence import _whitelist_enabled, _allowed_domains, get_outbound_log
            return jsonify({
                'mode': 'strict' if _whitelist_enabled else 'normal',
                'whitelist_enabled': _whitelist_enabled,
                'domain_count': len(_allowed_domains),
                'recent_requests': get_outbound_log(20),
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/outbound/toggle', methods=['POST'])
    def outbound_toggle():
        """切换出站控制模式"""
        try:
            from src.core.independence import _whitelist_enabled
            data = request.get_json(silent=True) or {}
            new_mode = data.get('mode', 'normal')
            # 更新config.yaml
            import yaml
            cfg_path = Path(__file__).parent / 'config.yaml'
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
                cfg.setdefault('independence', {})['whitelist_enabled'] = (new_mode == 'strict')
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
            return jsonify({'success': True, 'mode': new_mode})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-08: 硬件检测API =====
    @app.route('/api/hardware/gpu', methods=['GET'])
    def hardware_gpu():
        """检测GPU信息"""
        try:
            import subprocess
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.total,memory.free,name', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(',')
                if len(parts) >= 3:
                    return jsonify({
                        'vram_gb': round(int(parts[0].strip()) / 1024, 1),
                        'free_gb': round(int(parts[1].strip()) / 1024, 1),
                        'gpu_name': parts[2].strip(),
                        'available': True,
                    })
            return jsonify({'vram_gb': 0, 'available': False})
        except Exception:
            return jsonify({'vram_gb': 0, 'available': False})

    # ===== ★ 2026-08-09: 触发词管理API =====
    @app.route('/api/skills/triggers', methods=['GET'])
    def skills_triggers_list():
        """列出所有Skill的触发词"""
        try:
            import os
            from pathlib import Path
            skills_dir = Path(os.path.dirname(os.path.abspath(__file__))) / 'data' / 'skills'
            result = []
            for item in sorted(os.listdir(str(skills_dir))):
                full = skills_dir / item
                if not full.is_dir() or item.startswith('_'):
                    continue
                skill_md = full / 'SKILL.md'
                has_script = (full / 'scripts' / 'main.py').exists()
                trigger = ''
                name = item
                if skill_md.exists():
                    content = skill_md.read_text(encoding='utf-8')
                    if content.startswith('---'):
                        end = content.find('---', 3)
                        if end > 0:
                            for line in content[3:end].split('\n'):
                                line = line.strip()
                                if line.startswith('trigger:'):
                                    trigger = line.split(':', 1)[1].strip()
                                elif line.startswith('name:'):
                                    name = line.split(':', 1)[1].strip()
                result.append({
                    'id': item, 'name': name, 'trigger': trigger,
                    'has_script': has_script,
                    'type': 'script' if has_script else 'prompt',
                })
            return jsonify({'skills': result})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/skills/triggers/<skill_id>', methods=['PUT'])
    def skills_triggers_update(skill_id):
        """更新Skill触发词"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            new_trigger = data.get('trigger', '').strip()
            from pathlib import Path
            skill_md = Path(os.path.dirname(os.path.abspath(__file__))) / 'data' / 'skills' / skill_id / 'SKILL.md'
            if not skill_md.exists():
                return jsonify({'error': 'Skill不存在'}), 404
            content = skill_md.read_text(encoding='utf-8')
            if content.startswith('---'):
                end = content.find('---', 3)
                if end > 0:
                    yaml_part = content[3:end]
                    lines = yaml_part.split('\n')
                    new_lines = []
                    found = False
                    for line in lines:
                        if line.strip().startswith('trigger:'):
                            new_lines.append(f'trigger: {new_trigger}')
                            found = True
                        else:
                            new_lines.append(line)
                    if not found:
                        new_lines.insert(1, f'trigger: {new_trigger}')
                    new_content = '---' + '\n'.join(new_lines) + '---' + content[end+3:]
                    skill_md.write_text(new_content, encoding='utf-8')
                    return jsonify({'success': True, 'trigger': new_trigger})
            return jsonify({'error': 'SKILL.md格式不对'}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: 垂直面板独立历史API =====
    @app.route('/api/conversations/by-mode', methods=['GET'])
    def conversations_by_mode():
        """按模式获取对话（main/vertical）"""
        try:
            mode = request.args.get('mode', 'main')
            from src.models.database import get_conversations
            all_convs = get_conversations()
            if mode == 'vertical':
                # 垂直面板的对话（category含🎨或specialization是垂直类）
                filtered = [c for c in all_convs if c.get('category', '').startswith('🎨') or 'vertical' in (c.get('spec_name', '') or '').lower()]
            else:
                # 主对话（非垂直）
                filtered = [c for c in all_convs if not c.get('category', '').startswith('🎨') and 'vertical' not in (c.get('spec_name', '') or '').lower()]
            return jsonify({'conversations': filtered})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: Obsidian目录监控API =====
    @app.route('/api/knowledge/obsidian', methods=['POST'])
    def knowledge_obsidian_sync():
        """同步Obsidian目录到知识库"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            vault_path = data.get('path', '')
            if not vault_path or not os.path.exists(vault_path):
                return jsonify({'error': '目录不存在'}), 400
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            imported = 0
            errors = 0
            for root, dirs, files in os.walk(vault_path):
                # 跳过.obsidian目录
                if '.obsidian' in root:
                    continue
                for fname in files:
                    if fname.endswith('.md'):
                        fpath = os.path.join(root, fname)
                        try:
                            result = kb.import_file(fpath, source=f'obsidian:{fname}')
                            if result.get('success'):
                                imported += 1
                            else:
                                errors += 1
                        except:
                            errors += 1
            return jsonify({'success': True, 'imported': imported, 'errors': errors, 'path': vault_path})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: 定时触发API =====
    @app.route('/api/scheduler/list', methods=['GET'])
    def harven_scheduler_list():
        """列出定时任务"""
        try:
            import json as _json
            sched_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'scheduler.json')
            if os.path.exists(sched_file):
                with open(sched_file, 'r', encoding='utf-8') as f:
                    tasks = _json.load(f)
            else:
                tasks = []
            return jsonify({'tasks': tasks})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/scheduler/add', methods=['POST'])
    def harven_scheduler_add():
        """添加定时任务"""
        try:
            import json as _json
            data = request.get_json(force=True, silent=True) or {}
            sched_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'scheduler.json')
            tasks = []
            if os.path.exists(sched_file):
                with open(sched_file, 'r', encoding='utf-8') as f:
                    tasks = _json.load(f)
            task = {
                'id': str(int(__import__('time').time())),
                'name': data.get('name', ''),
                'skill': data.get('skill', ''),
                'time': data.get('time', ''),  # "07:00" 或 "daily:07:00"
                'enabled': True,
                'last_run': '',
            }
            tasks.append(task)
            with open(sched_file, 'w', encoding='utf-8') as f:
                _json.dump(tasks, f, ensure_ascii=False, indent=2)
            return jsonify({'success': True, 'task': task})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/scheduler/<task_id>', methods=['DELETE'])
    def harven_scheduler_delete(task_id):
        """删除定时任务"""
        try:
            import json as _json
            sched_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'scheduler.json')
            if not os.path.exists(sched_file):
                return jsonify({'error': '无任务'}), 404
            with open(sched_file, 'r', encoding='utf-8') as f:
                tasks = _json.load(f)
            tasks = [t for t in tasks if t.get('id') != task_id]
            with open(sched_file, 'w', encoding='utf-8') as f:
                _json.dump(tasks, f, ensure_ascii=False, indent=2)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: AI自动生成Skill API =====
    @app.route('/api/skills/generate', methods=['POST'])
    def skills_generate():
        """AI自动生成Skill脚本"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            description = data.get('description', '')
            skill_name = data.get('name', '').strip().lower().replace(' ', '-')
            if not description or not skill_name:
                return jsonify({'error': '需要name和description'}), 400

            # 调用通用模型生成Python脚本
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            prompt = f"""你是一个Python开发者。根据以下描述生成一个HarvenAI Skill脚本。

描述：{description}

要求：
1. 生成一个完整的Python脚本，可以用 python main.py --action run 执行
2. 脚本用print()输出结果
3. 可以使用 from data.skills._helpers import call_vertical, call_model, get_db_path, get_media_dir, get_reports_dir
4. 只输出Python代码，不要解释

```python
#!/usr/bin/env python3
"""
            messages = [{'role': 'system', 'content': '你是Python专家，只输出代码。'},
                        {'role': 'user', 'content': prompt}]
            response_text = ''
            try:
                for chunk in mm.generate_response(messages, stream=False):
                    if isinstance(chunk, dict):
                        if chunk.get('type') in ('text', 'content'):
                            response_text += chunk.get('content', '')
                    elif isinstance(chunk, str):
                        response_text += chunk
            except:
                response_text = mm.chat(messages) if hasattr(mm, 'chat') else ''

            # 提取代码
            import re
            code_match = re.search(r'```python\n(.*?)```', response_text, re.DOTALL)
            code = code_match.group(1) if code_match else response_text

            # 创建Skill
            skill_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'skills', skill_name)
            os.makedirs(os.path.join(skill_dir, 'scripts'), exist_ok=True)

            # 写SKILL.md
            skill_md = f"""---
name: {skill_name}
description: {description}
trigger: {skill_name}
author: AI生成
version: 1.0
scripts:
  entry: scripts/main.py
  actions:
    - run
---
## {skill_name}
AI自动生成的Skill。
"""
            with open(os.path.join(skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
                f.write(skill_md)

            # 写脚本
            with open(os.path.join(skill_dir, 'scripts', 'main.py'), 'w', encoding='utf-8') as f:
                f.write(code)

            return jsonify({'success': True, 'skill_name': skill_name, 'code_preview': code[:500]})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: HarvenHub流程分享API =====
    @app.route('/api/hub/flows', methods=['GET'])
    def hub_flows_list():
        """列出可分享的流程模板（本地）"""
        try:
            from pathlib import Path
            skills_dir = Path(os.path.dirname(os.path.abspath(__file__))) / 'data' / 'skills'
            flows = []
            for item in sorted(os.listdir(str(skills_dir))):
                full = skills_dir / item
                if not full.is_dir() or item.startswith('_'):
                    continue
                skill_md = full / 'SKILL.md'
                has_script = (full / 'scripts' / 'main.py').exists()
                if skill_md.exists():
                    content = skill_md.read_text(encoding='utf-8')
                    name = item
                    desc = ''
                    trigger = ''
                    if content.startswith('---'):
                        end = content.find('---', 3)
                        if end > 0:
                            for line in content[3:end].split('\n'):
                                line = line.strip()
                                if line.startswith('name:'): name = line.split(':', 1)[1].strip()
                                elif line.startswith('description:'): desc = line.split(':', 1)[1].strip()
                                elif line.startswith('trigger:'): trigger = line.split(':', 1)[1].strip()
                    flows.append({
                        'id': item, 'name': name, 'description': desc,
                        'trigger': trigger, 'has_script': has_script,
                        'shareable': has_script,
                    })
            return jsonify({'flows': flows})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ===== ★ 2026-08-09: AirLLM状态API =====
    @app.route('/api/airllm/status', methods=['GET'])
    def airllm_status():
        """获取AirLLM状态"""
        try:
            from src.core.airllm_integration import get_airllm_status
            return jsonify(get_airllm_status())
        except Exception as e:
            return jsonify({'error': str(e), 'installed': False})

    # ===== 面板静态文件服务 =====
    try:
        PANELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'panels')

        @app.route('/panel/<panel_id>/')
        @app.route('/panel/<panel_id>')
        def serve_panel_index(panel_id):
            panel_dir = os.path.join(PANELS_DIR, panel_id)
            index_path = os.path.join(panel_dir, 'index.html')
            if not os.path.isfile(index_path):
                return "面板不存在", 404
            return send_from_directory(panel_dir, 'index.html')

        @app.route('/panel/<panel_id>/<path:filename>')
        def serve_panel_assets(panel_id, filename):
            panel_dir = os.path.join(PANELS_DIR, panel_id)
            return send_from_directory(panel_dir, filename)

        logger.info("[Startup] 面板静态文件路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 面板静态文件路由注册失败: {_e}")

    # ★ 2026-08-09: 启动定时任务调度器
    try:
        from src.core.task_scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"[Startup] 定时调度器启动失败: {e}")

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
