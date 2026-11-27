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
# ★ 2026-11-10: 单实例预检 2.0（锁文件+PID 进程指纹，根治慢启动窗口双开）
# 旧版只探 7000 端口监听——哈文加载模型需 40~60 秒，窗口期内二次双击=两个实例都过检=双开
# 新版：本实例启动即抢锁（PID+端口落盘 data/runtime/harven.pid），
#       后到的实例查锁命中存活 python 进程 → 只开浏览器连旧实例，不启第二服务
# 端口探活保留为双保险（兼容无锁文件的旧实例/锁丢失等异常场景）
if __name__ == '__main__':
    import os as _os, sys as _sys, socket as _socket, webbrowser as _wb, atexit as _ae
    from pathlib import Path as _Path
    _lock_file = _Path(__file__).parent.resolve() / 'data' / 'runtime' / 'harven.pid'
    _svc_port = 7000  # 与下方 __main__ 主块的 port 保持一致

    def _pid_alive(_pid):
        """轻量判定 PID 是否为存活的 python 进程（不引 psutil，预检阶段零重依赖）"""
        try:
            import ctypes
            _k = ctypes.windll.kernel32
            _h = _k.OpenProcess(0x1000, 0, _pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not _h:
                return False
            try:
                import ctypes.wintypes as _wt
                _buf = ctypes.create_unicode_buffer(1024)
                _sz = _wt.DWORD(1024)
                if _k.QueryFullProcessImageNameW(_h, 0, _buf, ctypes.byref(_sz)):
                    return 'python' in (_buf.value or '').lower()
                return True  # 查不到映像名（权限等）时保守视为存活：宁可少开不多开
            finally:
                _k.CloseHandle(_h)
        except Exception:
            return True

    _running_port = None
    try:  # ① 锁文件优先：命中存活 python 进程即认定已在运行
        if _lock_file.exists():
            _parts = _lock_file.read_text(encoding='utf-8').strip().split()
            _old_pid, _old_port = int(_parts[0]), int(_parts[1])
            if _old_pid != _os.getpid() and _pid_alive(_old_pid):
                _running_port = _old_port
    except Exception:
        pass
    if _running_port is None:  # ② 双保险：锁没命中再探端口
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
            _s.settimeout(1)
            if _s.connect_ex(('127.0.0.1', _svc_port)) == 0:
                _running_port = _svc_port
    if _running_port is not None:
        print(f"[Harven] 检测到哈文已在运行（端口 {_running_port}），正在为您打开浏览器...")
        _wb.open(f'http://localhost:{_running_port}')
        _sys.exit(0)

    # 抢锁：立即写 PID+端口（赶在重量级 import 之前，慢启动窗口也被覆盖）
    try:
        _lock_file.parent.mkdir(parents=True, exist_ok=True)
        _lock_file.write_text(f'{_os.getpid()} {_svc_port}', encoding='utf-8')

        def _release_lock():
            """干净退出时释放锁；锁内容是自己的 PID 才删，防误删后到者的锁"""
            try:
                if _lock_file.exists() and \
                        _lock_file.read_text(encoding='utf-8').strip().split()[0] == str(_os.getpid()):
                    _lock_file.unlink()
            except Exception:
                pass
        _ae.register(_release_lock)
    except Exception:
        pass

import os, sys, json, time, traceback, threading, tempfile, subprocess, sqlite3, logging, atexit
import shutil, hashlib
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
from src.version import APP_VERSION  # ★ 版本号唯一户口（方案B 2026-10-07）

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

_handlers = [logging.StreamHandler()]
try:
    # ★ #924（2026-09-27）：日志同时落盘——此前只进控制台黑框，出问题想查「到底什么原因」
    #   时黑框一关啥都没有。落 data/logs/runtime.log，10MB 一转，留 3 份。
    from logging.handlers import RotatingFileHandler
    _logdir = PROJECT_ROOT / 'data' / 'logs'
    _logdir.mkdir(parents=True, exist_ok=True)
    _handlers.append(RotatingFileHandler(_logdir / 'runtime.log',
                                         maxBytes=10 * 1024 * 1024,
                                         backupCount=3, encoding='utf-8'))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=_handlers
)
# 屏蔽 Flask 开发服务器警告（Harven 是本地桌面应用，不需要生产级 WSGI）
logging.getLogger('werkzeug').setLevel(logging.WARNING)
# ★ #1447（2026-11-05 r50）黑框降噪：第三方库/高频模块 INFO 刷屏分流到 WARNING。
#   只改控制台级别，文件日志（runtime.log）同样受级别控制——这些本来就是心跳/常规
#   流水不是异常，出问题时 ERROR/WARNING 依然完整可见。
for _noisy in ('httpx', 'httpcore', 'jieba', 'faiss', 'faiss.loader', 'urllib3',
               'src.core.voice_input', 'voice_input',
               'faster_whisper', 'huggingface_hub', 'modelscope', 'onnxruntime'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
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
    # ★ 2026-10-10：加 no-store——防 APP WebView 缓存 302，导致登录成功后跳转又弹回登录页
    resp = redirect('/login?next=' + request.path)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/login', methods=['GET'])
def login_page():
    return render_template('login.html', next_url=request.args.get('next', '/'))


# ★ #955（2026-10-03）本地轻页：手机在家一个界面（大按钮：说句话/拍张照/问一句）
#   在家直连本地 /phone（快），出门走壳自动落云端 harvenhub.com/phone（中转）——统一轻页方案 B。
#   鉴权：自动复用上面的远程密码门（before_request：无密码免登录/有密码跳 /login，API 返回 401）
@app.route('/phone')
def phone_local_page():
    # ★ #1410（2026-11-03）手机页永远拿最新版：no-cache 强制回源验证，
    #   根治「发了新版手机 WebView 还端旧页」——页头版本随 TEMPLATES_AUTO_RELOAD 即时生效
    from flask import make_response
    resp = make_response(render_template('phone_local.html'))
    resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
    return resp


@app.route('/api/phone/send', methods=['POST'])
def phone_local_send():
    """★ #955 本地轻页指令入口：与云端 /api/relay/send 同款 {action, params}，
    同一份指令白名单（cloud_link._dispatch），能力一个不少（chat/语音/拍照/面板/图库/知识库）。
    鉴权：不走设备令牌——复用远程密码门会话 cookie（check_remote_auth 已在 before_request 拦）。
    本地即主战场：没有中转限速、没有配额、数据不出家门。"""
    from src.core.cloud_link import _dispatch
    body = request.get_json(silent=True) or {}
    action = str(body.get('action', '')).strip()
    params = body.get('params') or {}
    result = _dispatch(action, params)   # 白名单在 _dispatch 里
    ok = 'error' not in (result or {})
    payload = {'success': ok, 'data': result} if ok else \
        {'success': False, 'error': str((result or {}).get('error', '执行失败'))}
    return jsonify(payload)


@app.route('/api/relay/chat_stream', methods=['POST'])
def relay_chat_stream():
    """★ 2026-10-21 手机端流式对话（体感对齐豆包）：SSE 逐块推送。
    请求：{message, mode?('local'|'cloud')}  响应：text/event-stream
    事件：data: {"type":"status"|"text"|"done","content":...}
    鉴权复用远程密码门会话（before_request check_remote_auth 已拦），同 /api/relay/send。"""
    body = request.get_json(silent=True) or {}
    msg = str(body.get('message', '')).strip()
    mode = str(body.get('mode', '')).strip().lower()
    if not msg:
        return jsonify({'success': False, 'error': '缺少 message'}), 400
    from src.core.cloud_link import mobile_chat_stream
    from flask import Response, stream_with_context

    def gen():
        try:
            for ev in mobile_chat_stream(msg, mode):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'status', 'content': f'⚠️ {e}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(gen()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/auth/remote-login', methods=['POST'])
def remote_login():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '')
    import yaml, time
    cfg_path = Path(__file__).parent / 'config.yaml'
    if not cfg_path.exists():
        return jsonify({'success': False, 'error': '配置不存在'}), 500
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    # ★ R1 2026-09-23 密码统一：远程登录与管理员密码一把锁
    # 优先管理员密码；兼容存量用户单独改过的 remote_password（避免升级后旧密码立即失效）
    from src.core.security_guard import get_admin_password
    expected = get_admin_password()
    legacy = cfg.get('security', {}).get('remote_password', '')
    if pwd != expected and (not legacy or legacy != pwd):
        return jsonify({'success': False, 'error': '密码错误'}), 401
    resp = jsonify({'success': True})
    # ★ 修复：Cookie值带HMAC签名，不能伪造
    expire_ts = int(time.time() + 86400 * 30)
    cookie_val = _sign_cookie(expire_ts)
    resp.set_cookie(REMOTE_AUTH_SESSION_KEY, cookie_val, max_age=86400 * 30, httponly=True, samesite='Lax')
    return resp


@app.route('/api/auth/remote-password', methods=['GET'])
def get_remote_password_hint():
    """返回远程密码的掩码版本（用于设置页显示）"""
    import yaml
    cfg_path = Path(__file__).parent / 'config.yaml'
    if not cfg_path.exists():
        return jsonify({'hidden': '****'})
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
    # ★ R1 2026-09-23 密码统一：远程密码即管理员密码，显示同一把锁
    from src.core.security_guard import get_admin_password
    pwd = get_admin_password()
    from flask import request
    if request.args.get('real') == '1':
        return jsonify({'real': pwd, 'hidden': '*' * len(pwd)})
    return jsonify({'hidden': '*' * len(pwd)})


@app.route('/api/sync/version', methods=['GET'])
def api_sync_version():
    """★ #1220-A（2026-10-25）数据版本总线：前端轮询此接口，
    版本号变化即刷新面板/提醒等数据（电脑端写库 → 手机端/SPA 感知）。"""
    from src.core.sync_bus import get_version
    return jsonify(get_version())


@app.route('/api/auth/remote-password', methods=['POST'])
def update_remote_password():
    """修改远程访问密码（★ R1 2026-09-23 密码统一：改的就是管理员密码，走 security_guard 同步三处）"""
    data = request.get_json(silent=True) or {}
    # ★ 修复：必须验证旧密码
    old_pwd = data.get('old_password', '').strip()
    new_pwd = data.get('password', '').strip()
    if not new_pwd or len(new_pwd) < 8:  # ★ 修复：最少4位→8位
        return jsonify({'success': False, 'error': '新密码至少8位'}), 400
    from src.core.security_guard import change_admin_password
    result = change_admin_password(old_pwd, new_pwd)
    if not result.get('success'):
        err = result.get('error', '')
        code = 401 if '旧密码' in err else 400
        return jsonify({'success': False, 'error': ('旧密码错误' if '旧密码' in err or '不对' in err else err)}), code
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


# ★ 2026-11-02 老书签兼容（#1373）：/static/panels/xxx 是历史路径（路由已不存在），
#   301 永久重定向到正门 /panel/xxx，老书签/旧入口永不断链
@app.route('/static/panels/<path:subpath>')
def redirect_legacy_panels(subpath):
    target = '/panel/' + subpath
    if target.endswith('/index.html'):
        target = target[:-len('/index.html')]
    return redirect(target, code=301)


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
            # ★ 2026-09-24 反杀修复：旧镜像同步降级，避免新旧不一致
            config.setdefault('backends', {}).setdefault('local', {})['default_model'] = fallback
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
                # ★ #1238: 用户选了 HarvenEye（混合模型引擎）→ 走 Eye 引擎分流
                # （current_model 优先于 ollama.default_model——选 Eye 时不污染 Ollama 镜像）
                if (config.get('current_model') or '').strip() == 'harveneye':
                    model_name = 'harveneye'
                # ★ #1571 件1：对话型组合（chain:<id>）当本地大脑 → 原样传给 ModelManager 分流
                elif (config.get('current_model') or '').strip().startswith('chain:'):
                    model_name = (config.get('current_model') or '').strip()
                # empty → 用户未选模型，不强制回退（让调用方自行判断可用引擎）
                # 启动时验证模型是否适配本机显存（有模型名时才做）
                if model_name != 'harveneye' and not model_name.startswith('chain:'):
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

# ==================== Token 账本路由（A批A7 · 2026-09-13）====================
try:
    from src.core.token_ledger import bp as token_ledger_bp
    app.register_blueprint(token_ledger_bp)
    logger.info("[TokenLedger] Token账本路由已注册（GET /api/token/ledger）")
except Exception as e:
    logger.error(f"[TokenLedger] Token账本路由注册失败: {e}")

# ==================== 模型组合工坊 API（2026-11-24 骨架开工）====================
try:
    from src.core.chain_engine import bp as chain_ws_bp
    app.register_blueprint(chain_ws_bp)
    from src.core.chain_engine import ensure_chain_engine
    _ce = ensure_chain_engine()
    # ★ #1534：组合即模型——启用的组合同步进垂直模型页（动态模型，engine=chain）
    from src.core.chain_engine.binding import sync_vertical_binding
    _cb = sync_vertical_binding()
    logger.info(f"[ChainWorkshop] 组合工坊路由已注册（/api/chains），内置组合 {_ce.get('builtin_registered')} 个，"
                f"垂直页绑定 {_cb.get('registered')} 个")
except Exception as e:
    logger.error(f"[ChainWorkshop] 组合工坊路由注册失败: {e}")

# ==================== 运行状态聚合端点（B批B7 · 2026-09-13）====================
# ==================== ★ 2026-09-17 #740 定时任务增删改 API（真调度器：APScheduler + scheduled_tasks 表） ====================
@app.route('/api/cron/list', methods=['GET'])
def cron_list():
    """列出全部定时任务（含下次执行时间）"""
    try:
        from src.core.scheduler import get_scheduler
        s = get_scheduler()
        tasks = [t.to_dict() for t in s.get_all_tasks()]
        next_runs = {n['task_id']: n['next_run'] for n in s.get_next_runs()}
        for t in tasks:
            t['next_run'] = next_runs.get(t['task_id'])
        return jsonify({'success': True, 'tasks': tasks})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/add', methods=['POST'])
def cron_add():
    """添加定时任务
    schedule_type: cron（每天定时，hour/minute）| interval（每隔 minutes 分钟）| once（run_at=ISO 时间）
    """
    try:
        from src.core.scheduler import get_scheduler, ScheduledTask
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get('name') or '').strip()
        prompt = (data.get('prompt') or '').strip()
        stype = data.get('schedule_type', 'cron')
        if not name or not prompt:
            return jsonify({'success': False, 'error': '任务名称和「要干的事」都要填'}), 400
        if stype == 'cron':
            try:
                hour, minute = int(data.get('hour', 8)), int(data.get('minute', 0))
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': '时间格式不对'}), 400
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return jsonify({'success': False, 'error': '时间要在 0-23 点 / 0-59 分'}), 400
            config = {'hour': hour, 'minute': minute}
        elif stype == 'interval':
            try:
                minutes = max(1, int(data.get('minutes', 30)))
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': '间隔分钟数不对'}), 400
            config = {'minutes': minutes}
        elif stype == 'once':
            run_at = (data.get('run_at') or '').strip()
            if not run_at:
                return jsonify({'success': False, 'error': '请选择执行时间'}), 400
            try:
                datetime.fromisoformat(run_at)
            except ValueError:
                return jsonify({'success': False, 'error': '执行时间格式不对'}), 400
            config = {'run_at': run_at}
        else:
            return jsonify({'success': False, 'error': f'不支持的类型: {stype}'}), 400
        import uuid as _uuid
        task_id = f'cron_{_uuid.uuid4().hex[:10]}'
        task = ScheduledTask(task_id=task_id, name=name, prompt=prompt, schedule_type=stype, schedule_config=config)
        return jsonify(get_scheduler().add_task(task))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/<task_id>/toggle', methods=['POST'])
def cron_toggle(task_id):
    """启用/停用定时任务"""
    try:
        from src.core.scheduler import get_scheduler
        data = request.get_json(force=True, silent=True) or {}
        return jsonify(get_scheduler().toggle_task(task_id, bool(data.get('enabled', True))))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/<task_id>/update', methods=['POST'])
def cron_update(task_id):
    """修改定时任务（名称/提示词/时间均可改）"""
    try:
        from src.core.scheduler import get_scheduler, ScheduledTask
        data = request.get_json(force=True, silent=True) or {}
        s = get_scheduler()
        old = next((t for t in s.get_all_tasks() if t.task_id == task_id), None)
        if not old:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        name = (data.get('name') or old.name).strip()
        prompt = (data.get('prompt') or old.prompt).strip()
        stype = data.get('schedule_type') or old.schedule_type
        if stype == 'cron':
            config = {'hour': int(data.get('hour', old.schedule_config.get('hour', 8))),
                      'minute': int(data.get('minute', old.schedule_config.get('minute', 0)))}
        elif stype == 'interval':
            config = {'minutes': max(1, int(data.get('minutes', old.schedule_config.get('minutes', 30))))}
        elif stype == 'once':
            config = {'run_at': (data.get('run_at') or old.schedule_config.get('run_at', '')).strip()}
        else:
            config = old.schedule_config
        s.delete_task(task_id)  # 先摘除旧 job 再重挂
        result = s.add_task(ScheduledTask(task_id=task_id, name=name, prompt=prompt,
                                          schedule_type=stype, schedule_config=config,
                                          enabled=old.enabled, created_at=old.created_at))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cron/<task_id>', methods=['DELETE'])
def cron_delete(task_id):
    """删除定时任务"""
    try:
        from src.core.scheduler import get_scheduler
        return jsonify(get_scheduler().delete_task(task_id))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status/monitor', methods=['GET'])
def status_monitor():
    """B7 运行状态页六卡数据聚合：资源/模型/今日对话/安全/定时任务/健康"""
    result = {}

    # 卡1 电脑资源
    try:
        import psutil
        import shutil
        cpu = int(psutil.cpu_percent(interval=0.25))
        vm = psutil.virtual_memory()
        ram = {'percent': int(vm.percent), 'used_gb': round(vm.used / 1024**3, 1), 'total_gb': round(vm.total / 1024**3, 1)}
        vram = {'percent': 0, 'used_gb': 0, 'total_gb': 0, 'gpu': ''}
        try:
            smi = shutil.which('nvidia-smi')
            if smi:
                import subprocess
                out = subprocess.run(
                    [smi, '--query-gpu=memory.used,memory.total,name', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip().splitlines()
                if out:
                    mu, mt, name = [x.strip() for x in out[0].split(',')]
                    mu, mt = float(mu), float(mt)
                    vram = {'percent': int(mu / mt * 100) if mt else 0, 'used_gb': round(mu / 1024, 1),
                            'total_gb': round(mt / 1024, 1), 'gpu': name}
        except Exception:
            pass
        if not vram['total_gb']:
            # nvidia-smi/NVML 不可用时走 torch 兜底（RTX 4070 场景实测可用）
            try:
                import torch
                if torch.cuda.is_available():
                    free_b, total_b = torch.cuda.mem_get_info(0)
                    used_b = total_b - free_b
                    vram = {'percent': int(used_b / total_b * 100) if total_b else 0,
                            'used_gb': round(used_b / 1024**3, 1), 'total_gb': round(total_b / 1024**3, 1),
                            'gpu': torch.cuda.get_device_name(0)}
            except Exception:
                pass
        result['resources'] = {'cpu_percent': cpu, 'ram': ram, 'vram': vram}
    except Exception as e:
        result['resources'] = {'error': str(e)}

    # 卡2 模型加载（本地 Ollama + 当前外脑）
    try:
        import json as _json
        import urllib.request
        loaded, installed = [], []
        try:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=2) as r:
                for m in _json.loads(r.read().decode('utf-8')).get('models', []):
                    sz = m.get('size', 0)
                    loaded.append({'name': m.get('name', ''), 'size_gb': round(sz / 1024**3, 1) if sz else None})
            with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=2) as r:
                loaded_names = {x['name'] for x in loaded}
                for m in _json.loads(r.read().decode('utf-8')).get('models', []):
                    if m.get('name') not in loaded_names:
                        installed.append({'name': m.get('name', ''), 'size_gb': round(m.get('size', 0) / 1024**3, 1)})
        except Exception:
            pass
        foreign = ''
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            foreign = get_multi_model_manager().current_model or ''
            if foreign == 'ollama':
                foreign = ''
        except Exception:
            pass
        result['models'] = {'loaded': loaded, 'installed': installed[:6], 'ollama_reachable': bool(loaded or installed),
                            'foreign_brain': foreign, 'external_ok': bool(foreign)}
    except Exception as e:
        result['models'] = {'error': str(e)}

    # 卡3 今日对话（Token 账本 + 会话数）
    try:
        from src.core.token_ledger import summary as _ledger_summary
        led = _ledger_summary()
        from src.models.database import DEFAULT_DB_PATH
        db_path = os.path.normpath(str(DEFAULT_DB_PATH))
        today_chats = 0
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            today_chats = conn.execute("SELECT COUNT(*) FROM conversations WHERE substr(created_at,1,10)=date('now','localtime')").fetchone()[0]
            conn.close()
        except Exception:
            pass
        result['chat_today'] = {'ledger': led.get('counters', {}), 'conversations_today': today_chats,
                                'ledger_updated': led.get('updated_at', '')}
    except Exception as e:
        result['chat_today'] = {'error': str(e)}

    # 卡4 安全
    try:
        from src.core.security_guard import get_permission_mode
        mode = get_permission_mode()
        result['security'] = {'permission_mode': '全权模式' if mode == 'full' else '安全模式',
                              'outbound': '仅外脑问答', 'fc_active': True}
    except Exception as e:
        result['security'] = {'error': str(e)}

    # 卡5 定时任务
    try:
        from src.models.database import DEFAULT_DB_PATH
        _db_path = os.path.normpath(str(DEFAULT_DB_PATH))
        conn = sqlite3.connect(_db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        tasks = []
        for row in conn.execute('SELECT task_id, name, enabled, last_run FROM scheduled_tasks ORDER BY enabled DESC, task_id'):
            runs_today = conn.execute(
                "SELECT COUNT(*) FROM scheduled_task_runs WHERE task_id=? AND substr(started_at,1,10)=date('now','localtime')",
                (row['task_id'],)).fetchone()[0]
            tasks.append({'id': row['task_id'], 'name': row['name'], 'enabled': bool(row['enabled']),
                          'last_run': row['last_run'] or '未运行', 'runs_today': runs_today})
        running = conn.execute("SELECT COUNT(*) FROM scheduled_task_runs WHERE status='running'").fetchone()[0]
        conn.close()
        result['tasks'] = {'items': tasks[:8], 'running': running}
    except Exception as e:
        result['tasks'] = {'error': str(e)}

    # 卡6 健康报告
    try:
        from src.models.database import DEFAULT_DB_PATH
        _db_path = os.path.normpath(str(DEFAULT_DB_PATH))
        db_size_mb = round(os.path.getsize(_db_path) / 1024**2, 1) if os.path.exists(_db_path) else 0
        kb_count = 0
        conn = sqlite3.connect(_db_path, timeout=5)
        try:
            kb_count = conn.execute('SELECT COUNT(*) FROM knowledge').fetchone()[0]
        except Exception:
            pass
        conn.close()
        result['health'] = {'db_size_mb': db_size_mb, 'kb_entries': kb_count, 'errors': 0, 'backup': '手动备份'}
    except Exception as e:
        result['health'] = {'error': str(e)}

    return jsonify({'success': True, 'data': result})

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


@app.route('/api/net/check')
def net_check():
    """★ 2026-09-13: 轻量外网探测（首页🌐状态签真实化）——socket 连顶级域 443，2 秒超时"""
    import socket
    ok = False
    try:
        s = socket.create_connection(('223.5.5.5', 443), timeout=2)  # 阿里公共 DNS，国内可达性稳
        s.close()
        ok = True
    except Exception:
        try:  # 兜底再试一次 DNS 解析（IP 直连被防火墙挡但 DNS 通也算半通）
            socket.getaddrinfo('www.baidu.com', 443, proto=socket.IPPROTO_TCP)
            ok = True
        except Exception:
            ok = False
    return jsonify({'online': ok})

@app.route('/api/fallback/status')
def fallback_status_stub():
    return jsonify({"success": True, "active": False, "message": "stub"})

# ==================== SPA 托管（B0，灰度期与旧界面并存） ====================
# 产物构建进 static/spa/，Flask 静态托管天然可达 /static/spa/index.html；
# 此路由仅提供友好入口，302 到 SPA。旧 templates 首页不动，页全过才切换根路径。
@app.route('/spa')
@app.route('/spa/')
def spa_entry():
    return redirect('/static/spa/index.html')

# ★ #1036（2026-10-06）SPA index.html 免缓存直出：Flask 默认缓存头让浏览器抱着旧
#   index.html 引用旧 hash 的 js/css——每次发新版用户端「看起来没变化」（开发者 10-06 实锤：
#   面板中心还是改造前的旧页面）。改为显式直读文件 + no-cache；assets 带 hash 可继续吃缓存
@app.route('/static/spa/index.html')
def spa_index_nocache():
    import os
    path = os.path.join(app.static_folder or 'static', 'spa', 'index.html')
    with open(path, 'rb') as f:
        body = f.read()
    resp = app.response_class(body, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ★ 2026-09-16 旧地址兼容：老版本曾把 SPA 挂在 /spa/index.html（带 ?panel= 参数），
#   路由早已下线，但用户浏览器里的旧标签页/书签还在——统一 302 到现地址，防 404 蒙人
# ★ #1037（2026-10-06）野地址兜底：开发者浏览器停在从未存在过的 /spa/spa-index（纯缓存
#   渲染，强刷也刷不掉——服务器 404 后浏览器继续摆旧缓存）。改为：地址在 static/spa 里
#   真实存在才原样跳，不存在一律兜底回 index.html（no-cache），旧书签/坏地址全部救活
@app.route('/spa/<path:rest>')
def spa_entry_sub(rest):
    import os
    target = os.path.join(app.static_folder or 'static', 'spa', rest)
    if os.path.isfile(target):
        dest = '/static/spa/' + rest
        if request.query_string:
            dest += '?' + request.query_string.decode('utf-8')
        return redirect(dest)
    return redirect('/static/spa/index.html')

# ==================== Memory Dreaming 初始化 ====================
try:
    from src.core.memory_dreaming import get_dream_engine, register_dreaming_scheduler
    dream = get_dream_engine()
    # 设置 LLM 调用函数用于梦境（仅本地，保护隐私）
    def _dream_llm_fn(prompt: str) -> str:
        # ★ #1510（2026-11-13）：梦境是纯文本任务，改走 chat_local 轻量通道
        #   （Ollama 文本模型优先 → 进程内 GGUF 兜底）——原先走 mm.chat 会路由到
        #   HarvenEye 理解通道，每小时梦境都唤醒 8B Qwen3-VL，白占显存（日志实锤）
        try:
            from src.core.local_infer import chat_local
            text, _info, _err = chat_local(prompt, timeout=120, temperature=0.3)
            return text or ""
        except Exception:
            pass
        try:  # chat_local 不可用时退回 mm.chat（原链路，保底不中断）
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
        # ★ 2026-09-12: 梦境任务拦截 — dream_ 前缀走本地 dream_if_needed()，绝不进外脑
        #   旧实现把"执行 Memory Dreaming Light Dream"当普通消息发给 AI（走外脑计费），
        #   且记忆整理逻辑根本没执行，纯烧 token（2026-06-25~08-20 实测偷跑 1068 次）。
        #   现改为：直接调用梦境引擎，LLM 用本地 Ollama（set_llm_fn 已绑 mm.chat），零 API 费。
        if task_id.startswith("dream_"):
            try:
                from src.core.memory_dreaming import get_dream_engine
                _session = get_dream_engine().dream_if_needed()
                if _session:
                    logger.info(
                        f"[Scheduler] 梦境任务 {task_id} 本地完成: type={_session.dream_type} "
                        f"input={_session.input_count} removed={_session.removed_count}"
                    )
                    return f"本地梦境完成: {_session.dream_type}"
                logger.info(f"[Scheduler] 梦境任务 {task_id} 未到执行门槛，本轮跳过")
                return None
            except Exception as _de:
                logger.error(f"[Scheduler] 梦境任务 {task_id} 本地执行失败: {_de}")
                return None
        logger.info(f"[Scheduler] 执行定时任务: {task_id} -> {prompt[:80]}")
        try:
            from src.core.engine import ConversationEngine
            # ★ 2026-10-04 P1 修复：裸 ModelManager() 不带模型名 → 400「model is required」。
            #   改用 get_mm() 单例（读 config ollama.default_model，与主聊天同源同模型）。
            mm = get_mm()
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
        'version': APP_VERSION,
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
    # ★ 2026-09-18 定版拍板：SPA 新版 8/8 迁移完成、全面体检通过，旧版主界面下线
    #   根路径直通新版；旧版文件（templates/index.html + static/js/modules 等）暂留磁盘不删，
    #   观察一两周无问题再清理，回滚只需还原本函数。
    return redirect('/spa/')

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
    butler_mode = bool(data.get('butler'))  # ★ #1212 问管家来源：关 SkillDiscovery（「学习」等词误触发技能市场全网搜索，白耗 5-13 秒，10-25 实锤）
    images = data.get('images') or []  # ★ 2026-09-15 A方案: 随消息图片（dataURL列表，≤4张）
    if not isinstance(images, list):
        images = []
    images = [i for i in images if isinstance(i, str) and i][:4]
    logger.info(f"[Chat] 收到原始 data: message={message[:30]!r}, query_mode={query_mode!r}, conv_id={conv_id}, collab_mode={collab_mode}, web_search={web_search}, images={len(images)}")

    # 记录公网访客对话次数
    sid = request.cookies.get('hv_sid', '')
    if sid:
        try:
            from src.core.visitor_manager import record_chat
            record_chat(sid)
        except Exception as _e:
            logger.debug(f"[WebApp] 忽略错误: {_e}")

    if not message and not images:
        return jsonify({'error': 'Empty message'}), 400

    # Create conversation if needed (验证ID是否存在，不存在则创建)
    # ★ 2026-09-15: 纯图消息标题兜底（message 为空时用 [图片×N]）
    if not conv_id:
        from src.models.database import create_conversation
        title = (message or f'[图片×{len(images)}]')[:15] + ('...' if len(message) > 15 else '')
        conv_id = create_conversation(title, specialization_id=spec_id)
    else:
        # 验证 conversation_id 是否存在于数据库中
        try:
            from src.models.database import get_conversation, create_conversation
            existing = get_conversation(conv_id)
            if not existing:
                title = (message or f'[图片×{len(images)}]')[:15] + ('...' if len(message) > 15 else '')
                conv_id = create_conversation(title, specialization_id=spec_id)
                logger.info(f"[Chat] conversation_id 不存在，已创建新会话: {conv_id}")
        except Exception as _conv_err:
            logger.warning(f"[Chat] 检查会话存在性失败: {_conv_err}")
            # 不阻断流程，让 engine.send 处理可能的错误

    # ★ 2026-10-10 #1052: 新请求开始前清掉该会话可能残留的停止标记
    try:
        from src.core.engine import clear_stop as _clear_stop
        _clear_stop(conv_id)
    except Exception:
        pass

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
    _discovery_thread = None
    if not spec_prompt and not butler_mode:  # 本地技能未命中时才触发全网搜索；★ #1212 问管家请求一律关断（数据查询不需要装技能）
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
            # ★ #1212 心跳保活：engine 首字前有 RAG/记忆注入/FC 非流式轮等多个 5-10 秒零字节间隙，
            #   手机网络层/代理会把长时间静默的 SSE 连接掐掉（10-25 问管家 34 秒首字实锤零 text 事件）。
            #   立刻补一条 thinking 占位字节，之后间隙由 engine 侧每轮 FC 前的 thinking 事件覆盖。
            yield f"data: {json.dumps({'type': 'thinking', 'content': '正在思考…'}, ensure_ascii=False)}\n\n"

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

            # ★ #1220-B（2026-10-25）管家人格帽：管家入口（ButlerPage / 手机管家弹层）带
            #   butler:true → 追加管家帽系统提示。复用 specialization_prompt 通道，engine 零改动。
            #   只在 butler_mode 生效，普通聊天框不受影响（身份不冒用）。
            if butler_mode:
                _butler_hat = (
                    "\n\n【管家模式】你现在以哈文管家的身份说话：\n"
                    "- 称呼用户设置里的昵称（未设置就叫「你」），口吻像贴心的家人管家，简洁、主动、不啰嗦\n"
                    "- 回答尽量短句直达，别写长篇大论；有行动建议就直接说\n"
                    "- 适时利用你掌握的家庭数据（提醒/任务/面板记录）主动提醒和跟进\n"
                    "- 用户问「有什么要紧事」时，可汇总当前议程要点\n"
                    "- 保持哈文的基础人设不变，只是戴上管家这顶帽子"
                )
                final_spec = (final_spec or "") + _butler_hat

            from src.core.engine import ConversationEngine
            # 从 session 获取隐私模式状态
            _privacy_mode = session.get('privacy_mode', False)
            engine = ConversationEngine(mm, conv_id, specialization_prompt=final_spec, query_mode=query_mode, privacy_mode=_privacy_mode, collab_mode=collab_mode)
            engine._skip_vertical_route = skip_vertical  # ★ 转通用模型
            # ★ 2026-09-15 A方案: 带图消息（engine 内部自动路由看图模型）
            # ⚠️ 修复：不能对闭包变量 message 赋值（会把 message 编译成 generate 的局部变量，
            #    纯文字消息时未赋值 → UnboundLocalError → 所有 /api/chat 全挂 2026-09-15 开发者抓到）
            _msg_text = message
            if images and not _msg_text:
                _msg_text = '请描述这张图片的内容。'
            for msg_type, content in engine.send(_msg_text, stream=True, query_mode=query_mode, web_search=web_search, images=images):
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

            # ===== ★ 2026-09-16 对话框导航（B路线）：回复末尾 [NAVIGATE:路径] → 发独立跳转事件 =====
            # 前端收到 navigate 事件后 router.push；文本里的标记由前端在结束后剥离（老界面只剥离不跳转）
            try:
                import re as _re
                _m = _re.search(r'\[NAVIGATE:([^\]\s]+)\]\s*$', _stream_full_text or '')
                if _m:
                    yield f"data: {json.dumps({'type': 'navigate', 'content': _m.group(1)}, ensure_ascii=False)}\n\n"
            except Exception as _e:
                logger.debug(f"[WebApp] 导航标记提取失败: {_e}")

            yield "data: [DONE]\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            # ★ 2026-10-10 #1052: 生成结束兜底清理停止标记（防残留影响下一轮）
            try:
                from src.core.engine import clear_stop as _cs_finally
                _cs_finally(conv_id)
            except Exception:
                pass

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/chat/stop', methods=['POST'])
def chat_stop():
    """★ 2026-10-10 #1052: 停止当前会话的 AI 生成（前端点「⏹ 停止」时调用）

    真停止：engine.send() 流式循环每块检查停止标记，命中立即收笔，
    半截回复照常入库并追加「（已停止）」。
    """
    data = request.get_json(force=True, silent=True) or {}
    stop_conv_id = data.get('conversation_id')
    if not stop_conv_id:
        return jsonify({'ok': False, 'error': '缺少 conversation_id'}), 400
    try:
        from src.core.engine import request_stop as _req_stop
        _req_stop(stop_conv_id)
        logger.info(f"[Chat] #1052 收到停止请求: conv={stop_conv_id}")
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"[Chat] #1052 停止请求失败: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


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

        # ★ #912 旧图通道挂默录（2026-09-27）：此前 /api/chat/image 分析完图就用完即删，
        #   「发图+说记账」只聊天不入档。现在把首图喂给家庭默录链（后台线程，不拖慢响应），
        #   抽取成功自动入档 + 图存附件库挂记录——与主聊天 /api/chat 同等待遇。
        try:
            import base64 as _b64m
            import threading as _thm
            from src.core.panel_assist import maybe_home_record
            _imgs = []
            for p in tmp_paths:
                try:
                    _imgs.append(_b64m.b64encode(p.read_bytes()).decode('utf-8'))
                except Exception:
                    pass
            if _imgs:
                _thm.Thread(target=maybe_home_record,
                            args=(conv_id, message, full_text, _imgs), daemon=True).start()
        except Exception as _mh:
            logger.debug(f"[ChatImage] 默录挂接跳过: {_mh}")

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

    # 保存临时文件（★ 2026-09-23: 后缀跟随前端原始文件名——手机录音可能是 webm/mp4，不再硬编码 .wav）
    _orig_name = (audio_file.filename or '').strip()
    _suffix = os.path.splitext(_orig_name)[1].lower() if _orig_name else '.wav'
    if _suffix not in ('.wav', '.webm', '.mp3', '.m4a', '.mp4', '.ogg', '.flac', '.aac', '.amr'):
        _suffix = '.wav'
    tmp_path = Path(tempfile.mktemp(suffix=_suffix))
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
        # ★ #954: AMR 录音引擎解不开时给小白能看懂的提示
        if _suffix == '.amr':
            return jsonify({'error': '这段录音的格式（AMR）没能转成文字。可以在手机「录音机」里换一种保存格式再试，或直接打字告诉哈文。'}), 422
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
            return '[系统注：旧版 .ppt 格式本地解析不了。请告诉用户转成 .pptx 后重新发送即可，请勿在电脑上搜索此文件。]'

        # ── 旧版Excel xls（需xlrd，暂不支持）──
        if ext == '.xls':
            return '[系统注：旧版 .xls 格式本地解析不了。请告诉用户转成 .xlsx 后重新发送即可，请勿在电脑上搜索此文件。]'

        # ── 其他格式 ──
        return f'[系统注：「{{filename}}」的 {ext} 格式本地解析不了内容。请直接告诉用户暂不支持该格式，可转成 pdf/txt/docx 再发，请勿在电脑上搜索此文件。]'.replace('{filename}', filename)
    
    except Exception as e:
        logger.warning(f"[FileChat] 读取文件内容失败 {filename}: {e}")
        # ★ 2026-09-26 引导话术（定版拍板）：不再报「读取失败」结论诱发模型全盘搜文件，
        #   改为系统注事实+出路，模型照着转述即可
        return (f'[系统注：文件「{filename}」在本地解析不出文字内容，'
                f'很可能是扫描版/图片型文档。请勿在电脑上搜索这个文件（它不在硬盘其他位置）。'
                f'请直接告诉用户：这类文档建议切到外脑模式重新发送（GLM-5.3-Flash / Gemini 等'
                f'支持直接读文件），或把内容拍照/截图发给我走看图识别。]')


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
                import base64 as _b64mod
                from src.core.engine import ConversationEngine
                mm = get_mm()
                engine = ConversationEngine(mm, conv_id, specialization_prompt=spec_prompt)

                # ★ 2026-09-26 文件直发优先（定版拍板）：外脑模式 + 单文件 pdf/docx/pptx/xlsx
                #   → 文件 base64 直发外脑原生读取（GLM-5.3-Flash / Gemini 能吃文件，扫描版 PDF 也能读），
                #   失败自动降级走本地解析文本管线，不堵死路
                _ext_hit = Path(filenames[0]).suffix.lower() if len(filenames) == 1 else ''
                if engine._query_mode == 'direct' and _ext_hit in ('.pdf', '.docx', '.pptx', '.xlsx'):
                    try:
                        _ext_id = (getattr(mm, 'current_model', '') or '')
                        if _ext_id:
                            _mname = ''
                            try:
                                _mname = (mm.config.get('models', {}).get(_ext_id, {}) or {}).get('model_name', '') or ''
                            except Exception:
                                pass
                            _mime_map = {
                                '.pdf': 'application/pdf',
                                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            }
                            with open(saved_tmp_paths[0], 'rb') as _ff:
                                _fb64 = _b64mod.b64encode(_ff.read()).decode()
                            _q = message.strip() or f'请阅读这个文件（{filenames[0]}），总结它的主要内容。'
                            _file_msg = [
                                {'role': 'system', 'content': '你是哈文，本地AI助手。用户发来一个文件，请用中文、口语化地回答关于文件内容的问题。'},
                                {'role': 'user', 'content': [
                                    {'type': 'text', 'text': _q},
                                    {'type': 'file', 'file': {'filename': filenames[0],
                                                              'file_data': f'data:{_mime_map[_ext_hit]};base64,{_fb64}'}},
                                ]},
                            ]
                            # ★ #1265（2026-10-29）：模型显式传参，不再临时换血 mm.current_model
                            #   （stream=False 全程在换血窗口内消费，并发批改读到临时值实锤）
                            _full = ''
                            for evt in mm.generate_response(_file_msg, stream=False, model=_ext_id):
                                _t = evt.get('type', '') if isinstance(evt, dict) else ''
                                _c = evt.get('content', '') if isinstance(evt, dict) else ''
                                if _t == 'text':
                                    _full += _c
                                elif _t == 'error':
                                    logger.warning(f"[FileChat] 外脑直读文件报错，降级本地解析: {str(_c)[:150]}")
                                    _full = ''
                                    break
                            if _full.strip():
                                _label = f'🤖 外脑读文件 · {_mname or _ext_id}'
                                yield f"data: {json.dumps({'type': 'model_source', 'content': {'model': _ext_id, 'label': _label, 'is_external': True, 'mode': 'file'}}, ensure_ascii=False)}\n\n"
                                yield f"data: {json.dumps({'type': 'text', 'content': _full}, ensure_ascii=False)}\n\n"
                                save_message(conv_id, 'assistant', _full, metadata={'source': _label})
                                yield "data: [DONE]\n\n"
                                return
                            # _full 为空 → 外脑不收文件，降级到下方本地解析管线
                    except Exception as _de:
                        logger.warning(f"[FileChat] 文件直发外脑异常，降级本地解析: {_de}")

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

    # ★ 2026-09-23 下午补漏: 此接口才是手机录音真正走的路——后缀跟随原始文件名（webm/mp4），不再硬编码 .wav
    _orig_name = (audio_file.filename or '').strip()
    _suffix = os.path.splitext(_orig_name)[1].lower() if _orig_name else '.wav'
    if _suffix not in ('.wav', '.webm', '.mp3', '.m4a', '.mp4', '.ogg', '.flac', '.aac', '.amr'):
        _suffix = '.wav'
    tmp_path = Path(tempfile.mktemp(suffix=_suffix))
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
        # ★ #929-A4：暂存最近一次转写文字，供「💾 存档 / 这段话存下来」直接入库
        try:
            from src.core.voice_archive import stash_last_transcription
            stash_last_transcription(text)
        except Exception:
            pass
        return jsonify({'text': text})
    except Exception as e:
        err = str(e)
        if 'faster-whisper not installed' in err or 'No module named' in err:
            return jsonify({'error': '语音组件未安装。请在哈文对话中说"帮我安装语音输入组件"，或手动运行: python\\python.exe -m pip install faster-whisper', 'need_install': True}), 503
        # ★ #954: AMR 等老式录音格式引擎解不开时，给小白能看懂的提示（不再裸抛堆栈英文）
        if _suffix == '.amr':
            return jsonify({'error': '这段录音的格式（AMR）没能转成文字。可以在手机「录音机」里换一种保存格式再试，或直接打字告诉哈文。'}), 422
        return jsonify({'error': str(e)}), 500
    finally:
        tmp_path.unlink(missing_ok=True)

# ★ 2026-08-20: 语音依赖状态检测（前端录音前预检）
@app.route('/api/voice/status', methods=['GET'])
def voice_status():
    try:
        import faster_whisper  # noqa: F401
        return jsonify({'available': True})
    except ImportError:
        return jsonify({'available': False, 'error': '语音组件未安装，录音功能暂不可用。可对哈文说"帮我安装语音输入组件"来安装。'})

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

# ── ★ 2026-09-24 定版拍板：「+」菜单=发文件给哈文（不落知识库）──
# 对话框选文件 → 后端解析成文本回给前端 → 拼进消息发给当前模型（本地/外脑都行）。
# 与知识库上传完全无关：不入库、不建索引、不落 data/knowledge。
_CHAT_FILE_MAX_BYTES = 30 * 1024 * 1024   # 30MB 上传上限
_CHAT_TEXT_MAX_CHARS = 12000              # 提取文本截断（防上下文溢出，本地模型上下文有限）

@app.route('/api/chat/read_file', methods=['POST'])
def chat_read_file():
    """解析单个文件为纯文本（供对话框「发文件给哈文」）。支持：
    纯文本类直读（txt/md/代码/csv/json/log…utf-8/gbk 自动试）、pdf（pypdf）、docx（python-docx）。
    返回 {'ok': True, 'text': ..., 'truncated': bool} 或 {'ok': False, 'message': ...}"""
    try:
        f = request.files.get('file')
        if f is None:
            return jsonify({'ok': False, 'message': '没有收到文件'})
        name = f.filename or '文件'
        suffix = Path(name).suffix.lower()
        import tempfile as _tmp
        tmp_path = Path(_tmp.mktemp(suffix=suffix or '.bin'))
        try:
            f.save(str(tmp_path))
            if tmp_path.stat().st_size > _CHAT_FILE_MAX_BYTES:
                return jsonify({'ok': False, 'message': f'文件超过 30MB，先拆小一点'})

            text, how = '', ''
            if suffix == '.pdf':
                from pypdf import PdfReader
                reader = PdfReader(str(tmp_path))
                pages = []
                for pg in reader.pages:
                    try:
                        pages.append(pg.extract_text() or '')
                    except Exception:
                        pages.append('')
                text, how = '\n'.join(pages), f'pdf {len(reader.pages)} 页'
            elif suffix == '.docx':
                from docx import Document as _Doc
                doc = _Doc(str(tmp_path))
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                for _tb in doc.tables:
                    for _row in _tb.rows:
                        _cells = [c.text.strip() for c in _row.cells if c.text.strip()]
                        if _cells:
                            paras.append(' | '.join(_cells))
                text, how = '\n'.join(paras), 'docx'
            else:
                # 纯文本类直读（utf-8 优先，gbk 兜底）；doc/xls 等二进制旧格式不支持
                raw = tmp_path.read_bytes()
                for enc in ('utf-8', 'gb18030'):
                    try:
                        text, how = raw.decode(enc), f'文本({enc})'
                        break
                    except UnicodeDecodeError:
                        continue
                if not text:
                    return jsonify({'ok': False,
                                    'message': f'「{name}」是二进制格式，读不出文字。可以转成 txt/pdf/docx 再发，或传到知识库解析'})
            if not text.strip():
                # ★ 2026-09-26 引导话术：不说死路，给两条出路（外脑直读 / 拍照看图）
                return jsonify({'ok': False,
                                'message': f'「{name}」里没读出文字——它大概率是扫描版/图片型文档（文字被扫成了图像）。\n\n'
                                           '两个办法：\n'
                                           '1️⃣ 切到 🤖外脑模式 直接把文件发出去（GLM-5.3-Flash / Gemini 等能直接读文件）\n'
                                           '2️⃣ 把内容截图/拍照发给我，走看图识别'})
            truncated = len(text) > _CHAT_TEXT_MAX_CHARS
            if truncated:
                text = text[:_CHAT_TEXT_MAX_CHARS]
            return jsonify({'ok': True, 'text': text, 'truncated': truncated, 'how': how})
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"[ChatFile] 解析文件失败: {e}", exc_info=True)
        return jsonify({'ok': False,
                        'message': f'「{name}」没能解析出来（{e}）。可以换个格式再试，或把关键内容截图发给我看图识别'})

# ★ #1247（2026-10-27）：查询模式偏好持久化——SPA 切模式时同步写 user_preferences，
#   引擎端 get_preference('query_mode') 兜底链路（手机拍照等不带 mode 的请求）保持一致
@app.route('/api/prefs', methods=['GET', 'POST'])
def api_prefs():
    from src.models.database import get_preference, set_preference
    if request.method == 'GET':
        return jsonify({'success': True, 'query_mode': get_preference('query_mode', '') or ''})
    d = request.get_json(silent=True) or {}
    qm = str(d.get('query_mode', '')).strip()
    if qm in ('direct', 'kb_only'):
        set_preference('query_mode', qm)
        logger.info(f"[Prefs] 查询模式偏好已存: {qm}")
    return jsonify({'success': True, 'query_mode': get_preference('query_mode', '') or ''})


# ★ #1242（2026-10-27）：知识库总开关（kb_search_enabled 偏好）——
#   关=全链路跳过检索（本地+外脑都不查，嵌入模型也不再加载）；开=仅本地模式注入（隐私边界不动）。
#   engine._resolve_query_mode 每消息实时刷新偏好，开关即时生效无需重启。
@app.route('/api/kb/toggle', methods=['GET', 'POST'])
def kb_toggle():
    from src.models.database import get_preference, set_preference
    if request.method == 'GET':
        enabled = str(get_preference('kb_search_enabled', 'true')).lower() != 'false'
        return jsonify({'success': True, 'enabled': enabled})
    try:
        data = request.json or {}
        enabled = bool(data.get('enabled', True))
        set_preference('kb_search_enabled', 'true' if enabled else 'false')
        logger.info(f"[KnowledgeBase] 开关切换: {'开' if enabled else '关'}（即时生效）")
        return jsonify({'success': True, 'enabled': enabled,
                        'msg': '知识库已开启' if enabled else '知识库已关闭（检索全链路跳过，首问更快）'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _preload_knowledge_embedding():
    """★ #1242 嵌入模型后台预热：bge-small 首次加载 3~5 秒原先卡在重启后第一问，
    改为服务启动时后台加载（daemon 线程），第一问直接就绪。失败静默（检索有关键词兜底）。"""
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        kb.search('预热', top_k=1)
        logger.info("[KnowledgeBase] 嵌入模型预热完成（首问无需等待加载）")
    except Exception as _e:
        logger.debug(f"[KnowledgeBase] 嵌入预热跳过: {_e}")


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
            # ★ #918（2026-09-27）图片上传知识库 → 自动进统一图库总账（原地登记不搬家）
            if result.get('success') and Path(f.filename).suffix.lower() in (
                    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'):
                try:
                    from src.core import img_store
                    img_store.register(src_file=path, source='knowledge',
                                       note=f'知识库图片 · {f.filename}')
                    logger.info(f'[Knowledge] 图片已入图库: {f.filename}')
                except Exception as _ie:
                    logger.debug(f'[Knowledge] 图片入图库跳过: {_ie}')
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

@app.route('/api/media/knowledge/<path:filename>', methods=['GET'])
def media_kb_file(filename):
    """★ 2026-09-27 知识库图片直读（实测实锤：view 端点只会 read_text，jpg 必乱码）。
    供知识库前端预览和主聊天气泡内嵌图片展示。仅放行图片扩展名，锁死 data/knowledge 目录。"""
    from flask import send_file as _sendf
    try:
        kb_dir = (PROJECT_ROOT / 'data' / 'knowledge').resolve()
        safe_path = (kb_dir / filename).resolve()
        if not str(safe_path).startswith(str(kb_dir)):
            return jsonify({'error': '路径越权'}), 403
        if not safe_path.exists():
            return jsonify({'error': '文件不存在'}), 404
        ext = safe_path.suffix.lower()
        _mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                 '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}.get(ext)
        if not _mime:
            return jsonify({'error': '仅支持图片文件预览'}), 404
        return _sendf(safe_path, mimetype=_mime, max_age=3600)
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

# ════════════ ★ #916~#920 2026-09-27 统一图库 OneImg API ════════════

@app.route('/api/images/list', methods=['GET'])
def images_list():
    """图库检索：?q=关键词 &album=相册 &source=来源 &panel_id=面板 &tag=精确标签 &trash=1回收站 &limit=N
    ★ #1407-B：&group=组号筛选（'__none__'=只看未成组）；?groups=1 返回现有组清单"""
    try:
        from src.core import img_store
        # ★ #1407-B 组清单模式：?groups=1 → 列现有组（不混图）
        if request.args.get('groups') == '1':
            return jsonify({'success': True, 'groups': img_store.group_list()})
        items = img_store.search(
            q=request.args.get('q', '').strip(),
            album=request.args.get('album', '').strip(),
            source=request.args.get('source', '').strip(),
            panel_id=request.args.get('panel_id', '').strip(),
            tag=request.args.get('tag', '').strip(),
            trash=request.args.get('trash') == '1',
            group_id=request.args.get('group', '').strip(),
            limit=min(int(request.args.get('limit', 200)), 500))
        return jsonify({'success': True, 'images': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1491（2026-11-12）记忆相册按人翻回忆：人物清单 / 按人筛图 / 全量扫描
@app.route('/api/images/face/people', methods=['GET'])
def images_face_people():
    try:
        from src.core import img_store
        return jsonify({'success': True, 'people': img_store.face_people()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'people': []}), 500


@app.route('/api/images/face/people/<name>', methods=['GET'])
def images_face_person(name):
    try:
        from src.core import img_store
        items = img_store.list_by_person(name.strip()[:30])
        return jsonify({'success': True, 'images': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/images/face/scan', methods=['POST', 'GET'])
def images_face_scan():
    try:
        from src.core import img_store
        if request.method == 'POST':
            ok, msg = img_store.face_scan_async()
            return jsonify({'success': ok, 'message': msg})
        return jsonify({'success': True, **img_store.face_scan_status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1377（2026-11-02 定版拍板 A+B+C）账单相册 → 账单模块分析
# A 手动按钮：后台线程分析（视觉调用多张会慢，不拖 HTTP），结果进管家中心待确认
@app.route('/api/images/bills/analyze', methods=['POST'])
def images_bills_analyze():
    try:
        from src.core.bills_scan import scan_bills_async
        ok, msg = scan_bills_async(source='manual')
        return jsonify({'success': ok, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# B 提示行数据源：账单相册还有几张没分析过（前端工具栏常驻提示）
@app.route('/api/images/bills/status', methods=['GET'])
def images_bills_status():
    try:
        from src.core import img_store
        return jsonify({'success': True, 'pending': len(img_store.unprocessed_bills(limit=500))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1379（2026-11-02）误识别黑名单管理：GET 列表 / POST 加词 / DELETE 移除
#   词是用户在手机对话明说「以后别识别 X」进来的；这里供设置页查看/清除
@app.route('/api/assist/blocklist', methods=['GET', 'POST', 'DELETE'])
def assist_blocklist():
    try:
        from src.core import panel_assist as pa
        if request.method == 'GET':
            return jsonify({'success': True, 'words': pa.get_receipt_blocklist()})
        d = request.get_json(silent=True) or {}
        word = (d.get('word') or '').strip()
        if not word:
            return jsonify({'success': False, 'error': 'word 不能为空'}), 400
        if request.method == 'POST':
            added = pa.add_receipt_blocklist(word)
            return jsonify({'success': True, 'added': added})
        ok = pa.remove_receipt_blocklist(word)
        return jsonify({'success': ok, 'error': '' if ok else '词不在黑名单里'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1003（2026-10-05）标签管理：GET 全库标签聚合 / POST 改名合并
@app.route('/api/images/tags', methods=['GET', 'POST'])
def images_tags():
    try:
        from src.core import img_store
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            changed = img_store.rename_tag(d.get('old', ''), d.get('new', ''))
            if changed < 0:
                return jsonify({'success': False, 'error': '新旧标签名都得填'}), 400
            return jsonify({'success': True, 'changed': changed})
        return jsonify({'success': True, 'tags': img_store.list_tags()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1003 批量操作：{action: delete/restore/purge/album/tag/untag, ids: [...], album?, tags?}
@app.route('/api/images/batch', methods=['POST'])
def images_batch():
    try:
        from src.core import img_store
        d = request.get_json(silent=True) or {}
        ok, total = img_store.batch(d.get('action', ''), d.get('ids') or [],
                                    album=d.get('album', ''), tags=d.get('tags', ''))
        return jsonify({'success': ok > 0, 'ok': ok, 'total': total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1003 回收站恢复
@app.route('/api/images/<int:img_id>/restore', methods=['POST'])
def images_restore(img_id):
    try:
        from src.core import img_store
        return jsonify({'success': img_store.restore(img_id)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/albums', methods=['GET', 'POST'])
def images_albums():
    try:
        from src.core import img_store
        if request.method == 'POST':
            d = request.get_json(silent=True) or {}
            ok = img_store.add_album(d.get('name', ''))
            return jsonify({'success': ok})
        return jsonify({'success': True, 'albums': img_store.list_albums()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ★ #930-B6（2026-09-30）批量分类汇报：相册分布 + 待打标 + 建议入面板清单
@app.route('/api/images/report', methods=['GET'])
def images_report():
    try:
        from src.core import img_store
        albums = img_store.list_album_names()   # ★ #1409 字符串形状（原被同名覆盖实锤，改名后显式调用）
        recent = img_store.search(limit=500)
        by_album, untagged, no_panel = {}, [], []
        _PANEL_HINT = {'记账': '记账本', '票据': '记账本', '小票': '记账本',
                       '食物': '冰箱管家', '冰箱': '冰箱管家', '菜': '冰箱管家',
                       '物品': '物品存放', '书房': '物品存放', '玩具': '物品存放',
                       '孩子': '孩子成长档案', '作业': '孩子成长档案', '错题': '孩子成长档案',
                       '车': '车辆保养', '宠物': '宠物档案', '药': '老人用药',
                       '证件': '证件保单', '保单': '证件保单'}
        for it in recent:
            a = it.get('album') or '未分类'
            by_album[a] = by_album.get(a, 0) + 1
            _tags = it.get('tags')
            tags_str = ' '.join(_tags) if isinstance(_tags, list) else (_tags or '')
            if not tags_str.strip():
                untagged.append({'id': it.get('id'), 'thumb_url': it.get('thumb_url'), 'note': it.get('note', '')})
            if not (it.get('panel_id') or '').strip():
                hint = next((v for k, v in _PANEL_HINT.items()
                             if k in tags_str + (it.get('note') or '') + a), None)
                if hint:
                    no_panel.append({'id': it.get('id'), 'thumb_url': it.get('thumb_url'),
                                     'album': a, 'suggest_panel': hint})
        return jsonify({'success': True,
                        'total': len(recent), 'albums': albums, 'by_album': by_album,
                        'untagged_count': len(untagged),
                        'untagged': untagged[:12],
                        'suggest_panel': no_panel[:12]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/upload', methods=['POST'])
def images_upload():
    """图库直传：multipart 文件 file 或 JSON {image_b64, note, album, panel_id}"""
    try:
        from src.core import img_store
        note = request.form.get('note', '') if request.files else (request.get_json(silent=True) or {}).get('note', '')
        album = request.form.get('album', '') if request.files else (request.get_json(silent=True) or {}).get('album', '')
        panel_id = request.form.get('panel_id', '') if request.files else (request.get_json(silent=True) or {}).get('panel_id', '')
        if request.files and 'file' in request.files:
            raw = request.files['file'].read()
        else:
            b64 = (request.get_json(silent=True) or {}).get('image_b64', '')
            if not b64:
                return jsonify({'success': False, 'error': '没有图片'}), 400
            if ',' in b64 and b64.strip().startswith('data:'):
                b64 = b64.split(',', 1)[1]
            import base64 as _b64
            raw = _b64.b64decode(b64)
        r = img_store.register(image_data=raw, source=request.form.get('source', 'upload')
                               if request.files else 'upload',
                               album=album or None, note=note, panel_id=panel_id)
        if not r:
            return jsonify({'success': False, 'error': '登记失败'}), 500
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/<int:img_id>', methods=['POST', 'DELETE'])
def images_update(img_id):
    """改相册/标签/描述/面板关联（POST）或删除（DELETE）"""
    try:
        from src.core import img_store
        if request.method == 'DELETE':
            # ★ #1003 默认软删进回收站；?hard=1 才彻底删除（连库仓文件一起删）
            hard = request.args.get('hard') == '1'
            return jsonify({'success': img_store.remove(img_id, hard=hard)})
        d = request.get_json(silent=True) or {}
        if 'album' in d:
            img_store.set_album(img_id, d['album'])
        if 'note' in d:
            img_store.set_note(img_id, d['note'])
        if 'tags' in d:
            img_store.set_tags(img_id, d['tags'])
        if 'panel_id' in d:
            img_store.link_panel(img_id, d['panel_id'], d.get('record_id'))
        if 'group_id' in d:
            img_store.set_group(img_id, d.get('group_id'))  # ★ #1407-B 照片组
        return jsonify({'success': True, 'image': img_store.get(img_id)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/<int:img_id>/retag', methods=['POST'])
def images_retag(img_id):
    """minicpm-v 本地视觉打标签（手动按钮 + 后台补标共用）"""
    try:
        from src.core import img_store
        tags, model, err = img_store.retag(img_id)
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'tags': tags, 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/file/<int:img_id>', methods=['GET'])
def images_file(img_id):
    """图库文件直读（thumb=1 优先缩略图）。库仓图/原地登记图统一出口。"""
    from flask import send_file as _sendf
    try:
        from src.core import img_store
        p = img_store.get_file(img_id, thumb=request.args.get('thumb') == '1')
        if not p:
            return jsonify({'error': '图片不存在'}), 404
        _mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                 '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}.get(p.suffix.lower())
        if not _mime:
            return jsonify({'error': '非图片文件'}), 404
        return _sendf(p, mimetype=_mime, max_age=3600)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ════════════ ★ #925 T4 2026-09-29 记忆相册：AI 说话 + 修图（一期本地 PIL / 二期 SD img2img） ════════════

@app.route('/api/images/<int:img_id>/edit', methods=['POST'])
def images_edit_local(img_id):
    """本地修图（PIL 零费用秒出）：{action, params:{strength,angle,box,color,width}, replace}
    ★ #994 replace=true 时结果覆盖原文件（面板/相册引用不变自动生效），不另存新图"""
    try:
        from src.core import img_store
        d = request.get_json(silent=True) or {}
        new_id, url, err = img_store.edit_local(img_id, d.get('action', ''), d.get('params') or {},
                                                replace=bool(d.get('replace')))
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'id': new_id, 'url': url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/<int:img_id>/replace', methods=['POST'])
def images_replace(img_id):
    """★ #994 换图：上传新图覆盖原文件（登记行/相册/标签/面板关联全保留）{image_b64}"""
    try:
        from src.core import img_store
        d = request.get_json(silent=True) or {}
        b64 = (d.get('image_b64') or '').strip()
        if not b64:
            return jsonify({'success': False, 'error': '没收到新图'}), 400
        ok, err = img_store.replace_with_upload(img_id, b64)
        if not ok:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'url': f'/api/images/file/{img_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/images/genstatus', methods=['GET'])
def images_genstatus():
    """生成式修图可用性：探测 Stable Diffusion WebUI 是否在跑"""
    try:
        cfg = config.get('image_gen', {})
        sd_url = cfg.get('stable_diffusion_url', 'http://127.0.0.1:7860')
        import requests as _rq
        # ★ 本地服务绝不过系统代理（实锤坑：代理劫持 127.0.0.1 请求导致误判/连不上）
        _rq.get(f'{sd_url}/sdapi/v1/options', timeout=3, proxies={'http': None, 'https': None})
        return jsonify({'success': True, 'available': True, 'backend': 'sd'})
    except Exception:
        return jsonify({'success': True, 'available': False,
                        'msg': '生成式修图需要先启动 Stable Diffusion（画画引擎）。基础修图（滤镜/调亮/裁剪/相框）不受影响，都是免费的。'})

@app.route('/api/images/<int:img_id>/gen_edit', methods=['POST'])
def images_gen_edit(img_id):
    """生成式修图（SD WebUI img2img）：{prompt, strength(0~1 重绘幅度 默认0.4)}。
    依赖 SD WebUI 已启动；没启动则诚实报错（不回落不装死）。"""
    try:
        from src.core import img_store
        d = request.get_json(silent=True) or {}
        prompt = (d.get('prompt') or '').strip()
        if not prompt:
            return jsonify({'success': False, 'error': '请描述想改成什么样，比如「改成水彩画风格」'}), 400
        src = img_store.get_file(img_id)
        if not src:
            return jsonify({'success': False, 'error': '图片不存在'}), 404
        cfg = config.get('image_gen', {})
        sd_url = cfg.get('stable_diffusion_url', 'http://127.0.0.1:7860')
        import base64 as _b64, requests as _rq
        from PIL import Image as _PIL, ImageOps as _Ops
        import io as _io
        img = _PIL.open(src).convert('RGB')
        img.thumbnail((768, 768))  # 控制显存与耗时
        buf = _io.BytesIO()
        img.save(buf, 'PNG')
        init_b64 = _b64.b64encode(buf.getvalue()).decode('ascii')
        strength = max(0.05, min(0.95, float(d.get('strength', 0.4) or 0.4)))
        resp = _rq.post(f'{sd_url}/sdapi/v1/img2img', json={
            'init_images': [init_b64],
            'prompt': prompt,
            'negative_prompt': 'lowres, bad anatomy, watermark',
            'steps': 20, 'cfg_scale': 7.0,
            'denoising_strength': strength,
            'width': img.size[0], 'height': img.size[1],
        }, timeout=300, proxies={'http': None, 'https': None})
        resp.raise_for_status()
        out = resp.json().get('images') or []
        if not out:
            return jsonify({'success': False, 'error': '画画引擎返回了空结果'}), 200
        raw = _b64.b64decode(out[0].split(',', 1)[-1])
        cur = img_store.get(img_id) or {}
        r = img_store.register(image_data=raw, source='edited',
                               album=cur.get('album') or None,
                               note=f"{(cur.get('note') or '图片')[:70]}（AI 修图：{prompt[:30]}）",
                               auto_tag=False)
        if not r:
            return jsonify({'success': False, 'error': '结果登记失败'}), 500
        logger.info(f'[ImageGenEdit] #{img_id} AI 修图 -> #{r["id"]} prompt={prompt[:40]}')
        return jsonify({'success': True, 'id': r['id'], 'url': r['url']})
    except Exception as e:
        return jsonify({'success': False,
                        'error': f'生成式修图失败：{e}。请确认 Stable Diffusion 已启动；基础修图（滤镜/调亮等）不受影响。'}), 200

@app.route('/api/images/<int:img_id>/describe', methods=['POST'])
def images_describe(img_id):
    """AI 看图说话（minicpm-v 本地零费用）：{style: story/poem/kid/desc}"""
    try:
        from src.core import img_store
        d = request.get_json(silent=True) or {}
        text, model, err = img_store.describe(img_id, d.get('style', 'story'))
        if err or not text:
            return jsonify({'success': False, 'error': err or '视觉模型没有返回'}), 200
        return jsonify({'success': True, 'text': text.strip(), 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ════════════ ★ 2026-09-19 (#797) 知识蒸馏 API — 三道门：资格→用户点头→审核入库 ════════════

@app.route('/api/knowledge/distill/settings', methods=['GET', 'POST'])
def distill_settings_api():
    try:
        from src.core import distiller
        if request.method == 'GET':
            return jsonify(distiller.get_distill_settings())
        data = request.get_json(silent=True) or {}
        key = data.get('key', '')
        value = distiller.set_distill_setting(key, data.get('value', ''))
        return jsonify({'success': True, 'key': key, 'value': value})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/knowledge/distill/categories', methods=['GET', 'POST', 'DELETE'])
def distill_categories_api():
    """全局分类目录（省 token 第二支柱）"""
    try:
        from src.core import distiller
        if request.method == 'GET':
            return jsonify({'categories': distiller.list_categories()})
        data = request.get_json(silent=True) or {}
        if request.method == 'POST':
            cat = distiller.create_category(data.get('name', ''))
            return jsonify({'success': True, 'category': cat})
        distiller.delete_category(int(data.get('id', 0)))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/knowledge/distill/sources', methods=['GET'])
def distill_sources_api():
    """门1 预检：可蒸馏资料列表（含资格检查结果与任务状态）"""
    try:
        from src.core import distiller
        return jsonify({'sources': distiller.list_sources(),
                        'settings': distiller.get_distill_settings(),
                        'categories': distiller.list_categories()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/distill/start', methods=['POST'])
def distill_start_api():
    """门2 — 用户点头：明确选择资料与引擎（local/cloud）后启动"""
    try:
        from src.core import distiller
        data = request.get_json(silent=True) or {}
        source = data.get('source', '')
        engine = data.get('engine', 'local')
        resume = bool(data.get('resume', False))
        job_id = distiller.start_job(source, engine=engine,
                                     category_id=data.get('category_id', 0),
                                     resume=resume)
        return jsonify({'success': True, 'job_id': job_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/knowledge/distill/jobs', methods=['GET'])
def distill_jobs_api():
    try:
        from src.core import distiller
        return jsonify({'jobs': distiller.list_jobs()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/distill/jobs/<int:job_id>', methods=['GET', 'DELETE'])
def distill_job_api(job_id):
    try:
        from src.core import distiller
        if request.method == 'DELETE':
            distiller.delete_job(job_id)
            return jsonify({'success': True})
        with distiller._conn() as c:
            row = c.execute("SELECT * FROM kb_distill_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return jsonify({'error': '任务不存在'}), 404
        job = dict(row)
        # 运行中追加实时进度
        if job['status'] == 'running' and job['total_chunks']:
            job['progress'] = round(job['done_chunks'] / job['total_chunks'] * 100)
        return jsonify(job)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/distill/jobs/<int:job_id>/items', methods=['GET'])
def distill_items_api(job_id):
    """门3 — 蒸馏产物预览（待审）"""
    try:
        from src.core import distiller
        pending_only = request.args.get('all', '') != '1'
        return jsonify({'items': distiller.job_items(job_id, pending_only=pending_only)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/knowledge/distill/jobs/<int:job_id>/approve', methods=['POST'])
def distill_approve_api(job_id):
    """门3 — 审核提交：approve 入库（双层存储，来源标注），reject 丢弃"""
    try:
        from src.core import distiller
        data = request.get_json(silent=True) or {}
        added = distiller.approve_items(job_id, data.get('approve', []), data.get('reject', []))
        return jsonify({'success': True, 'added': added})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

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

@app.route('/api/knowledge/list', methods=['GET'])
def api_knowledge_list():
    """★ #1159（2026-10-17）服务抽屉知识库直读（手机本地轻页用）：faiss meta 清单摘要"""
    try:
        import json as _json_kb
        from pathlib import Path as _P
        meta_p = _P(__file__).resolve().parent / 'data' / 'faiss' / 'knowledge_meta.json'
        if not meta_p.exists():
            return jsonify({'total': 0, 'items': []})
        meta = _json_kb.load(open(meta_p, encoding='utf-8'))
        items = []
        for m in (meta if isinstance(meta, list) else list(meta.values()))[:8]:
            items.append({'id': m.get('id', ''), 'text': str(m.get('text', '')).strip()[:48],
                          'src': str(m.get('source', '') or m.get('file', '') or '')[:30]})
        return jsonify({'total': len(meta) if hasattr(meta, '__len__') else len(items), 'items': items})
    except Exception:
        return jsonify({'total': 0, 'items': []})


# ★ 2026-10-20 第二批 2.1: 权限中心路由
@app.route('/api/permissions', methods=['GET'])
def api_permissions_get():
    try:
        from src.core.permission_center import export_ui
        return jsonify(export_ui())
    except Exception as e:
        return jsonify({'error': str(e), 'perms': [], 'net': []}), 500

@app.route('/api/permissions', methods=['POST'])
def api_permissions_set():
    try:
        from src.core.permission_center import set_setting
        data = request.get_json(silent=True) or {}
        key = data.get('key', '')
        value = data.get('value')
        ok, effective, msg = set_setting(key, value)
        return jsonify({'ok': ok, 'value': effective, 'message': msg})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


# ★ 2026-10-20 第二批 2.2: Goals 目标管理路由
@app.route('/api/goals', methods=['GET'])
def api_goals_list():
    try:
        from src.core.goal_manager import list_goals, summary_card
        status = request.args.get('status') or None
        panel_id = request.args.get('panel_id') or None
        goals = [summary_card(g) for g in list_goals(status=status, panel_id=panel_id)]
        return jsonify({'total': len(goals), 'goals': goals})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'total': 0, 'goals': [], 'error': str(e)}), 500


# ★ 2026-10-20 面板内下任务：生成 structured_tasks 带「面板:xx」来源标签，进任务中枢统一列表
@app.route('/api/panel_tasks', methods=['POST'])
def api_panel_task_create():
    try:
        from src.core.task_manager import get_task_manager
        from datetime import datetime as _dt
        data = request.get_json(silent=True) or {}
        title = (data.get('title') or '').strip()
        panel_id = (data.get('panel_id') or '').strip()
        if not title or not panel_id:
            return jsonify({'ok': False, 'message': '缺少 title 或 panel_id'}), 400
        tm = get_task_manager()
        tags = [f'面板:{panel_id}']
        due = (data.get('due') or '').strip()
        task_id = tm.create_task(conversation_id='', title=title,  # ★ conversation_id NOT NULL：面板任务无会话传空串
                                 description=data.get('description', ''),
                                 priority=int(data.get('priority', 2) or 2),
                                 tags=tags, due_date=due)
        return jsonify({'ok': True, 'id': task_id, 'tag': tags[0]})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'message': str(e)}), 500

@app.route('/api/goals', methods=['POST'])
def api_goals_create():
    """body: {title, nodes:[{name,due,metric,target}], panel_id?, token_spent?}"""
    try:
        from src.core.goal_manager import create_goal, get_goal, summary_card
        data = request.get_json(silent=True) or {}
        title = (data.get('title') or '').strip()
        nodes = data.get('nodes') or []
        if not title or not nodes:
            return jsonify({'ok': False, 'message': '缺少 title 或 nodes'}), 400
        gid = create_goal(title, nodes, panel_id=data.get('panel_id', ''),
                          token_spent=int(data.get('token_spent', 0) or 0))
        return jsonify({'ok': True, 'id': gid, 'goal': summary_card(get_goal(gid))})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'message': str(e)}), 500

@app.route('/api/goals/<int:gid>', methods=['GET'])
def api_goals_detail(gid):
    """详情：全节点+每节点硬指标实时值（本地毫秒级）"""
    try:
        from src.core.goal_manager import get_goal, query_metric
        g = get_goal(gid)
        if not g:
            return jsonify({'error': 'not found'}), 404
        nodes = []
        for i, n in enumerate(g.get('plan') or []):
            done, target, metric = query_metric(gid, i)
            nodes.append({**n, 'metric_now': done, 'metric_target': target, 'metric_type': metric})
        return jsonify({'id': g['id'], 'title': g['title'], 'status': g['status'],
                        'panel_id': g.get('panel_id', ''), 'token_spent': g.get('token_spent', 0),
                        'created_at': g.get('created_at', ''), 'nodes': nodes})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/goals/<int:gid>/action', methods=['POST'])
def api_goals_action(gid):
    """操作：body {action: node_done|node_missed|defer|lower|pause|resume|abandon|delete,
                   node_idx?, new_due?, new_target?}"""
    try:
        from src.core.goal_manager import (update_goal, delete_goal, set_node_status,
                                           defer_node, lower_target, get_goal, summary_card)
        data = request.get_json(silent=True) or {}
        act = data.get('action', '')
        nidx = int(data.get('node_idx', -1))
        if act == 'node_done':
            g = set_node_status(gid, nidx, 'done')
        elif act == 'node_missed':
            g = set_node_status(gid, nidx, 'missed')
        elif act == 'defer':
            g = defer_node(gid, nidx, data.get('new_due') or '')
        elif act == 'lower':
            g = lower_target(gid, nidx, data.get('new_target') or 1)
        elif act == 'pause':
            g = update_goal(gid, status='paused')
        elif act == 'resume':
            g = update_goal(gid, status='active')
        elif act == 'abandon':
            g = update_goal(gid, status='abandoned')
        elif act == 'delete':
            delete_goal(gid)
            return jsonify({'ok': True, 'deleted': True})
        else:
            return jsonify({'ok': False, 'message': f'未知操作: {act}'}), 400
        # 全节点收尾→目标完成
        if g and g.get('plan') and all(n.get('status') == 'done' for n in g['plan']):
            g = update_goal(gid, status='done')
        return jsonify({'ok': True, 'goal': summary_card(g) if g else None})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'message': str(e)}), 500


# ★ 2026-10-20 第二批 2.3: 网页比价一期路由
@app.route('/api/price_compare', methods=['POST'])
def api_price_compare():
    """body: {query: '北京周末酒店'} — 参谋不做代购：对比表+deep link，永不自动下单"""
    try:
        from src.core.price_compare import parse_query, compare
        from src.core.permission_center import check_web
        ok, msg = check_web('browse_compare')
        if not ok:
            return jsonify({'ok': False, 'message': msg, 'need_permission': True})
        data = request.get_json(silent=True) or {}
        parsed = parse_query(data.get('query') or '')
        result = compare(parsed['kw'], parsed['kind'])
        result['ok'] = True
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'message': str(e)}), 500


@app.route('/api/goals/parse', methods=['POST'])
def api_goals_parse():
    """AI 拆解：愿望→节点列表（外脑一次性 ~3k token，拆解只跑一次）"""
    try:
        data = request.get_json(silent=True) or {}
        wish = (data.get('wish') or '').strip()
        if not wish:
            return jsonify({'nodes': [], 'message': '缺少愿望'}), 400
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        prompt = (
            "你是目标拆解助手。把用户的愿望拆成 2~5 个带截止日的节点。"
            "只输出 JSON 数组，不要任何其他文字，格式："
            '[{"name":"节点名(10字内)","due":"YYYY-MM-DD","metric":"manual|task_done|record_count","target":1}]。'
            "metric 选 manual(手动确认) 或 task_done(关联任务) 或 record_count(面板记录数)；target=目标数量。\n"
            f"今天 {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}。愿望：{wish}"
        )
        nodes, spent = [], 3000
        try:
            gen = mm.generate_response([{'role': 'user', 'content': prompt}], stream=False)
            text = ''
            for ch in gen:
                if isinstance(ch, dict) and ch.get('type') in ('text', 'content'):
                    text += ch.get('content', '')
            import re as _re
            m = _re.search(r'\[.*\]', text, _re.S)
            if m:
                nodes = json.loads(m.group(0))
                if not isinstance(nodes, list):
                    nodes = []
            nodes = [n for n in nodes if isinstance(n, dict) and n.get('name')][:5]
        except Exception as _pe:
            traceback.print_exc()
            nodes = []
        return jsonify({'nodes': nodes, 'token_spent': spent if nodes else 0,
                        'ok': bool(nodes), 'message': '' if nodes else '拆解失败，可用下方草稿手动改'})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'nodes': [], 'ok': False, 'message': str(e)}), 500


# ★ 2026-10-20 第四批4.3: 视频问答 L2 —— 抽帧→视觉模型直读（哨兵+大脑理念的 L2 步）
@app.route('/api/video/ask', methods=['POST'])
def api_video_ask():
    """body: {video_b64: base64, question: '这是啥情况', frames?: N(1-8)}
    ffmpeg 抽关键帧 → _camp_vision 多图问答（跟随看图路由偏好，含 HarvenEye 层）
    """
    try:
        import base64, tempfile, subprocess, os as _os
        data = request.get_json(silent=True) or {}
        vb64 = data.get('video_b64') or ''
        question = (data.get('question') or '这段视频里发生了什么？').strip()[:300]
        n_frames = max(1, min(8, int(data.get('frames', 4) or 4)))
        if not vb64:
            return jsonify({'ok': False, 'error': '缺少视频数据'}), 400
        # 落临时文件
        tmp_dir = tempfile.mkdtemp(prefix='hv_video_')
        vpath = _os.path.join(tmp_dir, 'in.mp4')
        with open(vpath, 'wb') as f:
            f.write(base64.b64decode(vb64))
        # ffmpeg 抽帧（等间隔）
        frame_paths = []
        for i in range(n_frames):
            ts = (i + 0.5) / n_frames
            fp = _os.path.join(tmp_dir, f'f{i}.jpg')
            r = subprocess.run(['ffmpeg', '-y', '-ss', f'{ts:.2f}', '-i', vpath,
                                '-frames:v', '1', '-q:v', '4', fp],
                               capture_output=True, timeout=30)
            if r.returncode == 0 and _os.path.exists(fp):
                frame_paths.append(fp)
        if not frame_paths:
            return jsonify({'ok': False, 'error': '抽帧失败（需 ffmpeg，或视频格式不支持）'}), 500
        # 组多图 base64 → _camp_vision
        imgs = []
        for fp in frame_paths:
            with open(fp, 'rb') as f:
                imgs.append(base64.b64encode(f.read()).decode('ascii'))
        from src.core.panel_assist import _camp_vision
        text, model, err = _camp_vision(question, imgs, timeout=180)
        # 清理
        try:
            import shutil as _sh
            _sh.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        if err and not text:
            return jsonify({'ok': False, 'error': err})
        return jsonify({'ok': True, 'answer': text, 'model': model, 'frames': len(imgs)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ★ 2026-10-20 第五批5.1: 面板包导出/导入（规范 v1 + 三道闸）
@app.route('/api/panel_packs/export', methods=['POST'])
def api_panel_pack_export():
    try:
        from src.core.panel_package import export_pack
        data = request.get_json(silent=True) or {}
        result = export_pack(data.get('panel_id', ''), include_resources=bool(data.get('include_resources', True)))
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/panel_packs/import', methods=['POST'])
def api_panel_pack_import():
    try:
        from src.core.panel_package import import_pack
        data = request.get_json(silent=True) or {}
        result = import_pack(data.get('pack_path', ''), auto_perms=data.get('auto_perms'))
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ 2026-10-20 第三批3.2 补实装：工作台「即将到来」——三源合一（任务due/随身记提醒/目标节点）
@app.route('/api/home/upcoming', methods=['GET'])
def api_home_upcoming():
    days = int(request.args.get('days', 7) or 7)
    out = []
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        horizon = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
        # ① 任务 due
        from src.core.task_manager import get_task_manager
        for t in get_task_manager().get_tasks(limit=200):
            due = str(t.get('due_date') or '')[:10]
            if due and today <= due <= horizon and t.get('status') != 'completed':
                out.append({'type': 'task', 'date': due, 'title': t.get('title', ''), 'id': t.get('id')})
        # ② 随身记提醒
        try:
            with __import__('sqlite3').connect(__import__('src.models.database', fromlist=['DEFAULT_DB_PATH']).DEFAULT_DB_PATH) as c:
                rows = c.execute("SELECT id, title, remind_at FROM home_reminders WHERE notified=0 AND substr(remind_at,1,10)>=? AND substr(remind_at,1,10)<=?",
                                 (today, horizon)).fetchall()
                for r in rows:
                    out.append({'type': 'reminder', 'date': str(r[2])[:10], 'title': r[1], 'id': r[0]})
        except Exception:
            pass
        # ③ 目标节点
        from src.core.goal_manager import due_nodes
        for n in due_nodes(days_ahead=days):
            out.append({'type': 'goal', 'date': n['due'], 'title': f"{n['goal_title']} · {n['node_name']}", 'id': n['goal_id']})
        # ④ 家人生日
        try:
            from src.core.panel_manager import get_panel_manager
            for b in get_panel_manager().upcoming_birthdays(days):
                d = (datetime.now() + timedelta(days=b['days_until'])).strftime('%Y-%m-%d')
                out.append({'type': 'birthday', 'date': d,
                            'title': f"{b.get('relation','')}{b['name']} 的生日", 'id': b['id']})
        except Exception:
            pass
    except Exception as e:
        traceback.print_exc()
    out.sort(key=lambda x: x['date'])
    return jsonify({'total': len(out), 'items': out[:12]})


@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    try:
        from src.core.task_manager import get_task_manager
        tm = get_task_manager()
        tasks = tm.get_tasks(limit=50)
        # ★ 2026-10-20 第三批3.1: 目标标签 join（goal_id → 🎯目标名）
        goal_titles = {}
        try:
            from src.core.goal_manager import list_goals as _lg
            for _g in _lg(limit=200):
                goal_titles[_g['id']] = _g['title']
        except Exception:
            pass
        # 转换前端需要的字段
        result = []
        for t in tasks:
            status = t.get('status', 'pending')
            gid = t.get('goal_id')
            result.append({
                'id': t['id'],
                'title': t.get('title', ''),
                'content': t.get('description', ''),
                'status': 'done' if status == 'completed' else status,
                'priority': {1: 'low', 2: 'medium', 3: 'high', 4: 'urgent'}.get(t.get('priority', 2), 'medium'),
                'progress': t.get('progress', 0),
                'created_at': t.get('created_at', ''),
                'goal_id': gid,
                'goal_title': ('🎯 ' + goal_titles[gid]) if (gid and gid in goal_titles) else '',
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

@app.route('/api/custom_keywords', methods=['GET', 'POST'])
def custom_keywords_api():
    """★ #1509（2026-11-13）用户自定义口令：config.yaml custom_keywords 段读写。
    每条：{trigger: 命中词, reply: 回复文本(可选), open: 打开路径(可选), remind: bool(可选)}
    只追加不覆盖内置词表；引擎 60s 缓存，保存即热生效。"""
    global config
    if request.method == 'GET':
        items = config.get('custom_keywords') or []
        return jsonify({'success': True, 'items': items})
    data = request.get_json(silent=True) or {}
    items = data.get('items')
    if not isinstance(items, list):
        return jsonify({'success': False, 'error': 'items 必须是列表'}), 400
    clean = []
    for it in items:
        if not isinstance(it, dict):
            continue
        trig = str(it.get('trigger') or '').strip()
        if not trig:
            continue
        entry = {'trigger': trig[:30]}
        reply = str(it.get('reply') or '').strip()
        openth = str(it.get('open') or '').strip()
        if reply:
            entry['reply'] = reply[:300]
        if openth:
            entry['open'] = openth[:260]
        if it.get('remind'):
            entry['remind'] = True
        if len(entry) > 1:
            clean.append(entry)
    config['custom_keywords'] = clean
    try:
        with open(PROJECT_ROOT / 'config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False)
        return jsonify({'success': True, 'items': clean})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
        # ★ #1324（2026-10-30）：hv-pin-* 是钉选 GGUF 的 Ollama 注册件（内容寻址内部名，
        #   钉选链路自动管理），不该出现在用户手动选择列表里造成困惑
        return jsonify([m['name'] for m in models
                        if not str(m.get('name', '')).startswith('hv-pin-')])
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
        # ★ 2026-09-24 反杀修复：三处镜像同步，旧镜像一并写
        config.setdefault('backends', {}).setdefault('local', {})['default_model'] = model
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
            # ★ 2026-09-13 修复：Ollama 返回错误 chunk（模型名不存在等）时，
            #   旧代码只认 'completed'/'success' 字段，错误被吞、误标为 done 假成功
            if r.status_code != 200:
                raise RuntimeError(f"Ollama 服务返回 HTTP {r.status_code}")
            for line in r.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if chunk.get('error'):
                            raise RuntimeError(str(chunk['error']))
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
                # ★ 2026-09-24 反杀修复：旧镜像同步清空
                if 'backends' in config and 'local' in config.get('backends', {}):
                    config['backends']['local']['default_model'] = ''
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


# ★ #1590（2026-11-27）：垂直/专项模型关键字——local-all 标「专项专用」与 set-current 拒设共用
#   同一份名单（此前两处各写各的；HuatuoGPT-o1 GGUF 卡显示「设为当前」却必 400 = 死按钮）
_VERTICAL_KW = ['musicgen', 'bark', 'riffusion', 'audiocraft', 'whisper',
                'stable-diffusion', 'sdxl', 'sd3', 'flux', 'kolors',
                'cogvideo', 'animatediff', 'embed', 'bge-', 'e5-', 'nomic',
                'huatuo', 'self-play']

# ★ #1591（2026-11-27 老谭拍板「垃圾卡」）：data/models 下的哈文内部工作目录不当模型卡列出
#   .cache=魔搭下载缓存 / logs=运行日志 / vision=视觉引擎容器（其中真模型已由
#   上方「本地视觉」区按 👁️ 卡列出）/ embedding=内置 bge 嵌入引擎（三级抽取内部用）
#   点开头的目录一律跳过
_INTERNAL_MODEL_DIRS = {'.cache', 'logs', 'vision', 'embedding'}


@app.route('/api/models/local-all', methods=['GET'])
def models_local_all():
    """统一本地模型列表：Ollama + data/models/ + HuggingFace缓存"""
    models = []
    # ★ 2026-11-27 D2（#1565）：HarvenEye 模型选项已取消显示——底座内部化，不再作为
    #    模型出现在本地列表（用户易误会「要多装一套组合」）。内部通道全保留：
    #    current_model=harveneye 仍可走 Eye 引擎，存量用户零破坏。
    #    （原 #1238 门禁注入段整段撤除）
    # ★ #1571 件1：对话型组合进本地列表（组合即本地大脑，带 🧩 徽标可设为当前）
    try:
        from src.core.chain_engine.registry import get_registry as _get_reg
        for _cc in _get_reg().chat_chains():
            models.append({'name': _cc['label'], 'source': '组合', 'size_gb': None,
                           'type': 'llm', 'icon': '🧩', 'id': _cc['id'],
                           'vision': _cc.get('vision'),
                           'desc': _cc.get('target') or '多环节接力流水线 · 组合即本地大脑',
                           # ★ #1578：组合卡写清组成与调用规则（结构化 steps 摘要前端渲染中文）
                           'steps': _cc.get('steps') or [],
                           'input_type': _cc.get('input_type'),
                           'output_type': _cc.get('output_type'),
                           'on_step_fail': _cc.get('on_step_fail')})
    except Exception as _e:
        logger.debug(f'[WebApp] 对话型组合列举跳过: {_e}')
    # ★ #1577（2026-11-27）：视觉大脑专用分区撤除（老谭拍板「很多余」）——本地视觉模型
    #   归位通用区，带 👁️ 看图专用标注；按钮走「设为看图大脑」钉选，不走通用切换
    try:
        from src.core import vision_link as _vl
        for _vm in _vl.list_local_vision_models():
            if not _vm.get('ready'):
                continue   # 文件不全的不占卡片（补装入口：页顶「视觉引擎」）
            models.append({'name': _vm['name'], 'label': _vl.catalog_label_for(_vm['name']),
                           'source': '本地视觉', 'size_gb': _vm.get('size_gb'),
                           'type': 'llm', 'icon': '👁️', 'id': _vm['id'],
                           'vision': True, 'vision_only': True})
    except Exception as _e:
        logger.debug(f'[WebApp] 本地视觉模型列举跳过: {_e}')
    # 1. Ollama
    # ★ D1 #1564：Ollama 模型识图能力探测——POST /api/show 读 capabilities（含 'vision'）。
    #   进程内永久缓存（能力不随运行变化），避免每次拉列表都逐个打 Ollama
    try:
        from requests import get as rget, post as rpost
        _vshow_cache = globals().setdefault('_OLLAMA_VISION_CAP', {})
        r = rget('http://localhost:11434/api/tags', timeout=5)
        if r.status_code == 200:
            for m in r.json().get('models', []):
                # ★ #1324：hv-pin-* 注册件不进用户模型列表（钉选链路自动管理）
                if str(m.get('name', '')).startswith('hv-pin-'):
                    continue
                _mname = m['name']
                if _mname not in _vshow_cache:
                    try:
                        _sr = rpost('http://localhost:11434/api/show', json={'name': _mname}, timeout=2)
                        _caps = (_sr.json() or {}).get('capabilities', []) if _sr.status_code == 200 else []
                        _vshow_cache[_mname] = 'vision' in [str(c).lower() for c in _caps]
                    except Exception:
                        _vshow_cache[_mname] = None
                _vo = any(k in ('ollama:' + _mname).lower() for k in _VERTICAL_KW)
                models.append({'name': _mname, 'source': 'Ollama', 'size_gb': round(m['size']/1e9,1), 'type': 'llm', 'icon': '🦙', 'id': 'ollama:'+_mname,
                               'vision': _vshow_cache.get(_mname),
                               # ★ #1590：专项模型（如 huatuo 医疗）如实标「不能设为当前」
                               'settable': not _vo, 'vertical_only': _vo,
                               **({'vertical_hint': '专项大脑：去医疗专项面板使用'} if _vo else {})})
    except Exception as _e: logger.debug(f'[WebApp] 忽略: {_e}')
    # 2. data/models/
    mdir = os.path.join(os.path.dirname(__file__), 'data', 'models')
    if os.path.isdir(mdir):
        for d in os.listdir(mdir):
            full = os.path.join(mdir, d)
            if not os.path.isdir(full): continue
            # ★ #1591：内部工作目录不进模型卡（见 _INTERNAL_MODEL_DIRS 注释）
            if d.startswith('.') or d.lower() in _INTERNAL_MODEL_DIRS: continue
            flist = os.listdir(full)[:50]
            gguf = any(f.endswith('.gguf') for f in flist)
            # ★ D1 #1564：本地 GGUF 识图判定 = 主模型 GGUF + mmproj 投影都在（vision_link 同款标准）
            _has_mm = any('mmproj' in f.lower() for f in flist)
            _vis = bool(gguf and _has_mm) if gguf else None
            mtype = 'llm' if gguf else ('music' if any(k in d.lower() for k in ['musicgen','acestep']) else 'other')
            icon = '📦' if gguf else ('🎵' if mtype=='music' else '📁')
            src = '魔搭/GGUF' if gguf else '魔搭/HF'
            size = sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(full) for f in fs if os.path.isfile(os.path.join(r,f)))
            _vid = 'local:' + d
            _vert_only = any(k in _vid.lower() for k in _VERTICAL_KW)
            models.append({'name': d, 'source': src, 'size_gb': round(size/1e9,1), 'type': mtype, 'icon': icon, 'id': _vid, 'vision': _vis,
                           # ★ #1590：专项模型卡如实标「不能设为当前」（set-current 必 400 的死按钮）
                           'settable': not _vert_only, 'vertical_only': _vert_only,
                           **({'vertical_hint': '专项大脑：去对应的专项面板使用'} if _vert_only else {})})
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
                # ★ D1 #1564：HF 缓存 GGUF 目录同样按 mmproj 判识图
                _hf_vis = None
                if has_gguf and mpath and os.path.isdir(mpath):
                    _hf_fl = os.listdir(mpath)[:50]
                    _hf_vis = any('mmproj' in f.lower() for f in _hf_fl)
                models.append({
                    'name': mname,
                    'source': '魔搭/HF' if not has_gguf else 'HF/GGUF',
                    'size_gb': round(msize/1e9, 1),
                    'type': 'llm' if has_gguf else 'other',
                    'icon': '📦' if has_gguf else '📁',
                    'id': 'local:'+mname.split(' ')[0].replace('/','_'),
                    'vision': _hf_vis,
                    # ★ #1590：HF 缓存 GGUF 卡同样标专项专用
                    'settable': not any(k in ('local:'+mname.split(' ')[0]).lower() for k in _VERTICAL_KW),
                    'vertical_only': any(k in ('local:'+mname.split(' ')[0]).lower() for k in _VERTICAL_KW),
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


def _apply_local_model(model_id):
    """★ 2026-10-24 方案A 分槽记忆的公共持久化链（set-current 与 switch-mode 共用）：
    yaml.current_model + by_mode.local 槽 + last_query_mode + ollama/backends 双镜像 +
    json.current_model/mm 单例同步（互踩根治）+ 重置本地引擎单例 + 后台预热。"""
    config['current_model'] = model_id
    try:
        _slots = config.setdefault('current_model_by_mode', {})
        _slots['local'] = model_id
        config['last_query_mode'] = 'local'
    except Exception as _e:
        logger.debug(f"[Models] 分槽记忆写入失败: {_e}")
    # ★ R2 2026-09-23 模型四把钥匙收敛：本地聊天真正读 ollama.default_model
    _actual = model_id
    for _prefix in ('ollama:', 'local:'):
        if _actual.startswith(_prefix):
            _actual = _actual[len(_prefix):]
    if model_id.startswith(('ollama:', 'local:')):
        config.setdefault('ollama', {})['default_model'] = _actual
        # ★ 2026-09-24 反杀修复：补写第三处镜像 backends.local（此前只写两处，旧镜像残留导致重启反杀）
        config.setdefault('backends', {}).setdefault('local', {})['default_model'] = _actual
    # ★ 2026-10-24 补另一半同步（互踩根治）：此前本地选择只写 yaml 不碰 mm 的 json——
    #   引擎重启后按 json.current_model 判定模式（还是旧外脑）→ 与模型中心显示对不上。
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        _mmm = get_multi_model_manager()
        _mmm.current_model = model_id
        _mmm.config['current_model'] = model_id
        _mmm._save_config()
    except Exception as _e:
        logger.warning(f"[Models] 同步 json.current_model 失败（重启后模式判定可能不准）: {_e}")
    global _mm
    try:
        config.save()
        # ★ R2: 重置本地引擎单例，get_mm() 下次重建即用新模型
        with _mm_lock:
            _mm = None
        # ★ R4 2026-09-23 显存换班后台化：预热放后台线程，接口立即返回
        # ★ #1238: HarvenEye 不走 Ollama 预热（Eye 服务自己管模型加载，探门禁即可）
        # ★ #1571 件1：对话型组合同样不走 Ollama 预热（编排器自管加载）
        if model_id != 'harveneye' and not model_id.startswith('chain:'):
            actual_name = _actual
            import threading as _th
            def _warmup(name):
                try:
                    import requests as _rq
                    _rq.post(f'http://localhost:11434/api/generate',
                             json={'model': name, 'prompt': '', 'stream': False, 'keep_alive': -1},
                             timeout=120)
                    logger.info(f"[Models] 模型已后台加载完成: {name}")
                except Exception:
                    logger.debug(f"[Models] 模型后台加载失败（将在首次对话时加载）: {name}")
            _th.Thread(target=_warmup, args=(actual_name,), daemon=True,
                       name=f'model-warmup-{actual_name}').start()
        else:
            try:
                from src.core.model_manager import ModelManager as _MMCls
                _mm2 = get_mm()
                _mm2.warmup()
            except Exception as _we:
                logger.debug(f"[Models] HarvenEye warmup 跳过: {_we}")
    except Exception as e:
        logger.warning(f"[Models] 持久化 current_model 失败: {e}")


@app.route('/api/models/set-current', methods=['POST'])
def models_set_current():
    """设为当前对话模型"""
    data = request.get_json(silent=True) or {}
    model_id = data.get('model_id', '').strip()
    if not model_id:
        return jsonify({'success': False, 'error': '缺少 model_id'}), 400

    # ★ #1571 件1：对话型组合放行——组合即本地大脑（编排器接力，能力门已拦管道型）
    if model_id.startswith('chain:'):
        _apply_local_model(model_id)
        return jsonify({'success': True, 'current': model_id})

    # ★ 2026-07-12: 禁止把垂直模型设为当前通用模型（★ #1590 名单上移到模块级 _VERTICAL_KW，与 local-all 共用）
    _id_lower = model_id.lower()
    for kw in _VERTICAL_KW:
        if kw in _id_lower:
            return jsonify({'success': False, 'error': f'「{model_id}」是垂直模型，不能设为当前通用模型。请在模型配置里选择。'}), 400

    _apply_local_model(model_id)
    return jsonify({'success': True, 'current': model_id})


@app.route('/api/models/eye-ready', methods=['GET'])
def models_eye_ready():
    """★ #1238 HarvenEye 门禁查询（前端模型卡状态灯用）。Eye 离线/模型未装 → ready=false。"""
    try:
        from src.core import eye_client
        r = eye_client.models_ready()
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'ready': False, 'error': str(e)})


# ★ #1276（2026-10-29 v1.3 融合）视觉模型选型三件套——理解槽（批改/看图/问图）的大脑选择。
#   铁律 0：用户钉谁用谁；清空=回退「当前外脑带 vision 位 > 配置默认」。
@app.route('/api/vision/models', methods=['GET'])
def vision_models_list():
    """视觉模型可选清单：外脑（带 vision 能力位）+ 本地已落位的 GGUF。"""
    try:
        from src.core.multi_model_manager import PROVIDERS, get_multi_model_manager
        from src.core import vision_link
        cloud = []
        try:
            mm = get_multi_model_manager()
            enabled = mm.config.get('models', {}) or {}
            for pid, info in (PROVIDERS or {}).items():
                if not info.get('vision') or pid in ('ollama', 'harveneye'):
                    continue   # ★ ollama 是本地后端不是外脑，不能进 api 通道候选
                cloud.append({'id': pid, 'name': info.get('name', pid), 'kind': 'api',
                              'enabled': bool((enabled.get(pid) or {}).get('enabled'))})
        except Exception:
            pass
        local = []
        for m in vision_link.list_local_vision_models():
            # ★ #1543 A3：本地视觉模型一并给 catalog 标准名（name=目录名兜底，label=标准名）
            local.append({'id': m['id'], 'name': m['name'], 'kind': 'local',
                          'label': vision_link.catalog_label_for(m['name']),
                          'size_gb': m.get('size_gb'), 'ready': m.get('ready')})
        pinned = (vision_link._load_mm().get('vision_model') or '').strip()
        return jsonify({'success': True, 'cloud': cloud, 'local': local,
                        'pinned': pinned})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/models/pin', methods=['POST'])
def vision_models_pin():
    """钉选视觉模型（vision_model 字段）。body: {model_id: 'zhipu' | 'local:vision/<名>' | ''}。
    空串=清除钉选（回退当前外脑/默认通道）。钉选后即时重建理解引擎，无需重启。"""
    try:
        from src.core.multi_model_manager import PROVIDERS
        from src.core import vision_link
        data = request.get_json(silent=True) or {}
        model_id = str(data.get('model_id', '')).strip()
        if model_id in ('ollama', 'harveneye', ''):
            if not model_id:
                mm = vision_link._load_mm()
                mm.pop('vision_model', None)
                vision_link._mm_json.write_text(
                    json.dumps(mm, ensure_ascii=False, indent=2), encoding='utf-8')
                vision_link.rebuild_pipeline()
                return jsonify({'success': True, 'pinned': ''})
            return jsonify({'success': False,
                            'error': f'「{model_id}」是本地后端，不是可钉选的 api 视觉模型；'
                                     '本地视觉模型请用 local:vision/<名字> 形式'}), 400
        if model_id and not model_id.startswith('local:vision/'):
            if model_id not in (PROVIDERS or {}) or not (PROVIDERS.get(model_id) or {}).get('vision'):
                return jsonify({'success': False,
                                'error': f'「{model_id}」不是可用的视觉模型（无看图能力或不存在）'}), 400
        mmf = vision_link._mm_json
        mm = vision_link._load_mm()
        if model_id:
            mm['vision_model'] = model_id
        else:
            mm.pop('vision_model', None)
        mmf.write_text(json.dumps(mm, ensure_ascii=False, indent=2), encoding='utf-8')
        vision_link.rebuild_pipeline()   # 换引擎实例，即时生效
        logger.info(f"[Vision] 视觉模型钉选: {model_id or '(清除)'}")
        return jsonify({'success': True, 'pinned': model_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/import', methods=['POST'])
def vision_model_import():
    """★ #1276 模型管家（v1.3 节点⑤）：把用户下载好的视觉模型文件导入哈文。
    body: {name: 'qwen3-vl-8b', gguf_path: '...', mmproj_path: '...'}
    校验存在性/后缀 → 归位 data/models/vision/<name>/ → 立即可选（扫描器自动发现）。
    同名目录已存在且齐备 → 幂等返回成功（重复导入无害）。"""
    try:
        import shutil
        from src.core import vision_link
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        gguf_path = str(data.get('gguf_path', '')).strip().strip('"')
        mmproj_path = str(data.get('mmproj_path', '')).strip().strip('"')
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            return jsonify({'success': False, 'error': '名字不合法（不能带路径分隔符）'}), 400
        for label, p in (('主模型', gguf_path), ('视觉投影', mmproj_path)):
            if not p or not Path(p).exists():
                return jsonify({'success': False, 'error': f'{label}文件不存在: {p or "(空)"}'}), 400
        if not gguf_path.lower().endswith('.gguf') or not mmproj_path.lower().endswith('.gguf'):
            return jsonify({'success': False,
                            'error': '只认 GGUF 格式（主模型 + mmproj 两个 .gguf 文件）；'
                                     '其他格式请先转换或从魔搭/HuggingFace 下载 GGUF 版'}), 400
        dest = vision_link._VISION_MODELS_DIR / name
        dest.mkdir(parents=True, exist_ok=True)
        # 归位（跨盘 move 自动降级 copy+delete；同名文件已一致则跳过）
        import hashlib
        def _md5(p, buf=1 << 20):
            h = hashlib.md5()
            with open(p, 'rb') as f:
                while True:
                    b = f.read(buf)
                    if not b:
                        break
                    h.update(b)
            return h.hexdigest()
        moved = []
        for src in (gguf_path, mmproj_path):
            s, d = Path(src), dest / s.name
            if d.exists() and d.stat().st_size == s.stat().st_size and _md5(d) == _md5(s):
                moved.append({'file': s.name, 'action': '已就位'})
                continue
            shutil.move(str(s), str(d))
            moved.append({'file': s.name, 'action': '已导入'})
        # 校验配对齐备
        ggufs = list(dest.glob('*.gguf'))
        mmproj = list(dest.glob('*mmproj*.gguf'))
        main = [g for g in ggufs if g not in mmproj]
        ready = bool(main and mmproj)
        size_gb = round(sum(g.stat().st_size for g in ggufs) / 1024**3, 2)
        logger.info(f"[Vision] 模型导入: {name} ready={ready} ({size_gb}GB)")
        return jsonify({'success': True, 'name': name, 'ready': ready,
                        'size_gb': size_gb, 'files': moved,
                        'note': '' if ready else '目录内还缺主模型或 mmproj 文件，配对齐备后自动可用'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/models/remove', methods=['POST'])
def vision_model_remove():
    """删除已导入的本地视觉模型目录（body: {name: '...'}）。回收站不可用则直接删，
    删除前校验目录确在视觉模型真家内，防误删任意路径。"""
    try:
        import shutil
        from src.core import vision_link
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            return jsonify({'success': False, 'error': '名字不合法'}), 400
        dest = (vision_link._VISION_MODELS_DIR / name).resolve()
        base = vision_link._VISION_MODELS_DIR.resolve()
        if base not in dest.parents:
            return jsonify({'success': False, 'error': '目标不在视觉模型目录内，拒绝删除'}), 400
        if not dest.exists():
            return jsonify({'success': False, 'error': f'模型目录不存在: {name}'}), 404
        shutil.rmtree(dest)
        from src.core import vision_link as _vl
        _vl.rebuild_pipeline()
        logger.info(f"[Vision] 模型已删除: {name}")
        return jsonify({'success': True, 'removed': name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ★ #1278（2026-10-29 v1.3 节点④）视觉引擎安装面板后端：底座（yolox+clip）一键下载
@app.route('/api/vision/engine/status', methods=['GET'])
def vision_engine_status():
    """底座引擎文件就绪状态 + 安装进度（面板状态卡用）。"""
    try:
        from src.vision import installer
        return jsonify({'success': True, **installer.engine_status(),
                        'progress': installer.progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/engine/install', methods=['POST'])
def vision_engine_install():
    """一键安装底座引擎（yolox+clip ~757MB）。幂等：已就位跳过、已在跑返回进度。"""
    try:
        from src.vision import installer
        r = installer.install_engine_async()
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/models/download', methods=['POST'])
def vision_model_download():
    """★ ⑤ 模型管家·在线下载：用户给 url(+可选 sha256/filename) → 哈文后台下载
    落位 data/models/vision/<name>/，完成即进本地清单可选。哈文不内置不代选任何模型。"""
    try:
        from src.vision import installer
        data = request.get_json(silent=True) or {}
        r = installer.download_model_async(str(data.get('name', '')),
                                           data.get('files') or [])
        return jsonify({'success': bool(r.get('started', False)), **r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/models/download/progress', methods=['GET'])
def vision_model_download_progress():
    """下载进度轮询（与底座安装共用一个进度态）。"""
    try:
        from src.vision import installer
        return jsonify({'success': True, **installer.progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══ ★ #1470 人脸识别 API（2026-11-12，批次四）═════════════════════
# 隐私红线：向量只存本地 face.db；删除级联；一键清除；默认关闭。

@app.route('/api/face/status', methods=['GET'])
def face_status():
    """人脸功能总状态（设置页/状态卡用）。"""
    try:
        from src.vision import face_store, installer
        return jsonify({'success': True,
                        'enabled': face_store.is_enabled(),
                        'threshold': face_store.get_threshold(),
                        'tier': face_store.get_tier(),
                        'models': installer.face_models_status(),
                        'members': len(face_store.list_members()),
                        'embeddings': face_store.count_embeddings(),
                        'download_progress': installer.progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/settings', methods=['POST'])
def face_settings():
    """开关/阈值/档位。开启时模型缺失自动后台下载（37MB，SHA256 校验）。"""
    try:
        from src.vision import face_store, installer
        data = request.get_json(silent=True) or {}
        if 'threshold' in data:
            th = float(data['threshold'])
            if not 0.2 <= th <= 0.9:
                return jsonify({'success': False, 'error': '阈值须在 0.2~0.9 之间'}), 400
            face_store.set_setting('threshold', str(th))
        if 'tier' in data:
            tier = str(data['tier'])
            if tier not in ('lite', 'pro'):
                return jsonify({'success': False, 'error': '档位只支持 lite/pro'}), 400
            face_store.set_setting('tier', tier)
        if 'enabled' in data:
            enabled = bool(data['enabled'])
            face_store.set_setting('enabled', '1' if enabled else '0')
            dl = None
            if enabled and not installer.face_models_status()['ready']:
                dl = installer.install_face_async()
            return jsonify({'success': True, 'enabled': enabled, 'model_download': dl})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/members', methods=['GET'])
def face_members_list():
    try:
        from src.vision import face_store
        return jsonify({'success': True, 'members': face_store.list_members()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/members', methods=['POST'])
def face_members_create():
    """建档成员（录入向量走 /enroll）。"""
    try:
        from src.vision import face_store
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        if not name:
            return jsonify({'success': False, 'error': '名字不能为空'}), 400
        pid = face_store.add_member(name, str(data.get('relation', '')),
                                    str(data.get('note', '')))
        return jsonify({'success': True, 'person_id': pid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/members/<int:pid>', methods=['DELETE'])
def face_members_delete(pid):
    """删成员=级联删全部向量（隐私硬要求）。"""
    try:
        from src.vision import face_store
        r = face_store.delete_member(pid)
        if not r.get('ok'):
            return jsonify({'success': False, 'error': r.get('error', '删除失败')}), 404
        _face_invalidate_cache()
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/members/<int:pid>/enroll', methods=['POST'])
def face_members_enroll(pid):
    """录入：photo_ids（图片库 id，建议 3~5 张不同角度）→ 抽向量 → 剔离群帧 → 入库。
    返回每人脸数/入库数；识别验证走 /api/face/identify。"""
    try:
        from src.vision import face_store
        from src.vision.engines.face_engine import FaceEngine
        from src.core import img_store
        if not face_store.get_member(pid):
            return jsonify({'success': False, 'error': '成员不存在，先建档'}), 404
        data = request.get_json(silent=True) or {}
        photo_ids = data.get('photo_ids') or []
        if not isinstance(photo_ids, list) or not photo_ids:
            return jsonify({'success': False, 'error': 'photo_ids 不能为空'}), 400
        if not FaceEngine.models_ready():
            return jsonify({'success': False,
                            'error': '人脸模型还没下载（开启功能时会自动下载，稍候重试）'}), 409

        engine = FaceEngine()
        engine.load()
        tier = face_store.get_tier()
        kept, kept_src, skipped = [], [], []
        for raw_id in photo_ids[:8]:                      # 上限 8 张（3~5 建议）
            try:
                photo_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            item = img_store.get(photo_id)
            path = (item or {}).get('path', '')
            if not path or not os.path.exists(path):
                skipped.append({'photo_id': photo_id, 'reason': '图片不存在'})
                continue
            r = engine.embed_photo(path)
            if r.get('ok'):
                kept.append(r['vector']); kept_src.append(photo_id)
            else:
                skipped.append({'photo_id': photo_id, 'reason': r.get('error', '')})

        if not kept:
            return jsonify({'success': False,
                            'error': '这些照片里都没找到人脸（换正面清晰照再试）',
                            'skipped': skipped}), 400

        # 离群帧剔除：>=3 帧时，与均值向量夹角过大的剔除（防糊脸/认错人拉低库质量）
        removed = 0
        if len(kept) >= 3:
            import numpy as np
            mat = np.stack(kept)
            mean = mat.mean(axis=0)
            mean = mean / (np.linalg.norm(mean) + 1e-9)
            sims = mat @ mean / (np.linalg.norm(mat, axis=1) + 1e-9)
            keep_mask = sims >= 0.5
            if keep_mask.sum() >= 2:                      # 至少保 2 帧
                removed = int((~keep_mask).sum())
                kept = [v for v, k in zip(kept, keep_mask) if k]
                kept_src = [s for s, k in zip(kept_src, keep_mask) if k]

        n = face_store.add_embeddings(pid, kept, tier, kept_src)
        _face_invalidate_cache()
        member = face_store.get_member(pid)
        return jsonify({'success': True, 'person_id': pid, 'name': member['name'],
                        'enrolled': n, 'outliers_removed': removed,
                        'skipped': skipped, 'tier': tier,
                        'total_embeddings': face_store.count_embeddings(pid)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/check_photos', methods=['POST'])
def face_check_photos():
    """★ 2026-11-12 图片库录入口「当场质检」：勾选即检，不合格当场标红不返工。
    body: {photo_ids:[...]} → results: {photo_id: {grade: ok|warn|bad, reason, face_count}}"""
    try:
        from src.vision.engines.face_engine import FaceEngine
        from src.core import img_store
        if not FaceEngine.models_ready():
            return jsonify({'success': False, 'error': '人脸模型还没下载（先到视觉引擎页开启人脸识别）'}), 409
        data = request.get_json(silent=True) or {}
        ids = (data.get('photo_ids') or [])[:12]
        if not ids:
            return jsonify({'success': False, 'error': 'photo_ids 不能为空'}), 400
        engine = FaceEngine()
        engine.load()
        results = {}
        for raw in ids:
            try:
                pid = int(raw)
            except (TypeError, ValueError):
                continue
            item = img_store.get(pid)
            path = (item or {}).get('path', '')
            if not path or not os.path.exists(path):
                results[str(pid)] = {'grade': 'bad', 'reason': '图片不存在'}
                continue
            try:
                results[str(pid)] = engine.check_photo(path)
            except Exception as ce:
                results[str(pid)] = {'grade': 'bad', 'reason': f'检测失败: {ce}'}
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/identify', methods=['POST'])
def face_identify():
    """识别测试/现场验证：photo_id → 找脸认人（含访客判定）。"""
    try:
        from src.vision.engines.face_engine import FaceEngine
        from src.core import img_store
        data = request.get_json(silent=True) or {}
        item = img_store.get(int(data.get('photo_id', 0)))
        path = (item or {}).get('path', '')
        if not path or not os.path.exists(path):
            return jsonify({'success': False, 'error': '图片不存在'}), 404
        if not FaceEngine.models_ready():
            return jsonify({'success': False, 'error': '人脸模型未就位'}), 409
        engine = FaceEngine()
        if not engine.loaded:
            engine.load()
        r = engine.infer(path)
        return jsonify({'success': True, 'result': r.to_dict(),
                        'faces': r.data.get('faces', []),
                        'matched_names': r.data.get('matched_names', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/wipe', methods=['POST'])
def face_wipe():
    """⚠️ 一键彻底清除所有人脸数据（生物特征零残留）。需 confirm=WIPE。"""
    try:
        from src.vision import face_store
        data = request.get_json(silent=True) or {}
        if str(data.get('confirm', '')) != 'WIPE':
            return jsonify({'success': False,
                            'error': '请传 confirm="WIPE" 确认（此操作不可恢复）'}), 400
        r = face_store.wipe_all()
        _face_invalidate_cache()
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/face/audit', methods=['GET'])
def face_audit():
    """生物特征操作留痕（注册/删除/清除可追溯）。"""
    try:
        from src.vision import face_store
        return jsonify({'success': True, 'log': face_store.audit_log()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _face_invalidate_cache():
    """管线内人脸引擎向量缓存失效（录入/删除/清除后调用）。"""
    try:
        from src.core import vision_link
        pipe = vision_link.get_pipeline()
        fe = getattr(pipe, '_face_engine', None)
        if fe is not None:
            fe.invalidate_cache()
    except Exception:
        pass


# ══════════ 能力五件套（2026-11-12）：OCR识字 / 扫码 / 车牌 / 活体 / 语义搜图 ══════════

@app.route('/api/vision/capabilities', methods=['GET'])
def vision_capabilities():
    """能力卡状态总览（视觉引擎页「🧰 视觉能力」区用）。"""
    try:
        from src.vision.engines import ocr_engine, lpr_engine, alive_engine
        from src.vision import photosearch
        return jsonify({'success': True,
                        'ocr': ocr_engine.available(),
                        'qr': True,
                        'lpr': lpr_engine.available(),
                        'alive': alive_engine.models_ready(),
                        'semsearch': photosearch.status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/capability/<cap>', methods=['POST'])
def vision_capability_run(cap):
    """对单张图跑指定能力。body: {photo_id}。"""
    try:
        from src.core import img_store
        data = request.get_json(silent=True) or {}
        item = img_store.get(int(data.get('photo_id', 0)))
        path = (item or {}).get('path', '')
        if not path or not os.path.exists(path):
            return jsonify({'success': False, 'error': '图片不存在'}), 404
        if cap == 'ocr':
            from src.vision.engines import ocr_engine
            return jsonify({'success': True, 'result': ocr_engine.infer(path)})
        if cap == 'qr':
            from src.vision.engines import qr_engine
            return jsonify({'success': True, 'result': qr_engine.infer(path)})
        if cap == 'lpr':
            from src.vision.engines import lpr_engine
            return jsonify({'success': True, 'result': lpr_engine.infer(path)})
        if cap == 'alive':
            from src.vision.engines import alive_engine
            return jsonify({'success': True, 'result': alive_engine.infer(path)})
        return jsonify({'success': False, 'error': '未知能力'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/photos/semsearch', methods=['GET'])
def photos_semsearch():
    """能力②：一句话搜照片。?q=老谭在海边&limit=40"""
    try:
        from src.vision import photosearch
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': '缺 q'}), 400
        limit = min(int(request.args.get('limit', 40)), 100)
        items = photosearch.search(q, limit=limit)
        return jsonify({'success': True, 'items': items, 'count': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/photos/semindex', methods=['GET', 'POST'])
def photos_semindex():
    """能力②：语义索引状态查询(GET) / 启动后台建索引(POST)。"""
    try:
        from src.vision import photosearch
        if request.method == 'POST':
            return jsonify({'success': True, **photosearch.index_all()})
        return jsonify({'success': True, **photosearch.status()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/tasks', methods=['GET'])
def vision_tasks_list():
    """★ ⑨ 队列通道任务清单（管家/面板用）。?status=pending 过滤。"""
    try:
        from src.vision import tasks
        return jsonify({'success': True,
                        'tasks': tasks.list_tasks(status=str(request.args.get('status', '')))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/vision/tasks/enqueue', methods=['POST'])
def vision_tasks_enqueue():
    """手动入队深识任务 {image_path 或 img_id, kind, note}。幂等。"""
    try:
        from src.vision import tasks
        d = request.get_json(silent=True) or {}
        tid = tasks.enqueue(str(d.get('image_path', '')), kind=str(d.get('kind', 'general')),
                            note=str(d.get('note', '')), img_id=str(d.get('img_id', '')))
        tasks.ensure_loop()
        return jsonify({'success': True, 'task_id': tid,
                        'queued': bool(tid)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _platform_info():
    """★ 节点⑧ 跨平台动力信息（检测只建议不代装）。失败返回极简兜底。"""
    try:
        from src.vision.platform import detect
        return detect()
    except Exception:
        return {"os": "unknown", "backend": "cpu", "wheel_hint": ""}


@app.route('/api/vision/status', methods=['GET'])
def vision_status():
    """视觉子系统状态总览（面板用）：通道/引擎/本地模型/组件齐备情况。"""
    try:
        from src.core import vision_link
        from src.vision import config as vcfg
        ch = vision_link.resolve_understand_channel()
        # ★ #1543 A3：resolved 附标准名 label（目录名 → catalog 标准名），前端统一展示
        try:
            if ch.get('provider') == 'llama_cpp' and ch.get('gguf'):
                _dir = Path(ch['gguf']).parent.name
                ch['label'] = vision_link.catalog_label_for(_dir)
            elif ch.get('provider') == 'ollama':
                # ★ #1543：#1323 钉选 GGUF 注册进 Ollama（model=hv-pin-*）→ 走标准名换算；
                #   真 Ollama 模型（qwen3-vl:8b-instruct 等）原样展示
                _std = vision_link.human_label_for(ch.get('model', ''))
                ch['label'] = _std if _std != ch.get('model') else (ch.get('model', '') or '')
            elif ch.get('provider') == 'api':
                ch['label'] = ch.get('label') or ch.get('api_model', '')
        except Exception:
            pass
        u = vcfg.ENGINES.get('understand', {})
        try:
            import llama_cpp  # noqa: F401
            _llamacpp = True
        except ImportError:
            _llamacpp = False
        return jsonify({'success': True,
                        'resolved': ch,
                        'configured_provider': u.get('provider', 'ollama'),
                        'llama_cpp_installed': _llamacpp,
                        'local_models': vision_link.list_local_vision_models(),
                        'platform': _platform_info()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/models/switch-mode', methods=['POST'])
def models_switch_mode():
    """★ 2026-10-24 方案A 分槽记忆：前端切 🏠本地/🤖外脑 胶囊时调用——
    自动换回该模式上次选的模型（各槽各记，互不覆盖）；重启后恢复最后用的模式+对应模型。
    槽为空时惰性迁移：local=当前 current_model（若是本地）、cloud=collab_foreign_model。"""
    data = request.get_json(silent=True) or {}
    mode = str(data.get('mode', '')).strip().lower()
    if mode not in ('local', 'cloud'):
        return jsonify({'success': False, 'error': 'mode 必须是 local 或 cloud'}), 400
    try:
        slots = config.get('current_model_by_mode') or {}
        if not slots:
            slots = {}
            _cur = (config.get('current_model') or '')
            if _cur.startswith(('ollama:', 'local:')):
                slots['local'] = _cur
            _cfm = str(config.get('collab_foreign_model') or '').strip()
            if _cfm and _cfm != 'ollama':
                slots['cloud'] = _cfm
            config['current_model_by_mode'] = slots
        target = str(slots.get(mode) or '').strip()
        # ★ #1275 节点⑦（2026-10-29）阵营对齐检查：只提醒不代选（铁律 0）——
        #   外脑槽记着本地模型 / 本地槽记着外脑 = 历史污染，切过去必撞路由报错
        _LOCAL_IDS = ('ollama', 'harveneye')
        _is_local = target.startswith(('ollama:', 'local:')) or target in _LOCAL_IDS
        if target and mode == 'cloud' and _is_local:
            config['last_query_mode'] = mode
            config.save()
            return jsonify({'success': True, 'mode': mode, 'switched': False,
                            'current_model': target,
                            'hint': f'⚠️ 外脑模式记录的「{target}」是本地模型，外脑模式用不了它。'
                                    '请到 设置→模型管理 选一个外脑（如 智谱AI），或切 🏠本地 模式使用它'})
        if target and mode == 'local' and not _is_local:
            config['last_query_mode'] = mode
            config.save()
            return jsonify({'success': True, 'mode': mode, 'switched': False,
                            'current_model': target,
                            'hint': f'⚠️ 本地模式记录的「{target}」是外脑模型，本地模式用不了它。'
                                    '请到 设置→模型管理 的本地列表选一个，或切 🤖外脑 模式使用它'})
        config['last_query_mode'] = mode
        if not target:
            config.save()
            mm_cur = ''
            try:
                from src.core.multi_model_manager import get_multi_model_manager
                mm_cur = get_multi_model_manager().current_model or ''
            except Exception:
                pass
            return jsonify({'success': True, 'mode': mode, 'switched': False,
                            'current_model': mm_cur or config.get('current_model'),
                            'note': '该模式还没记住模型，保持现状'})
        # ★ 2026-10-24 不设「target==current_model 短路」：yaml/json 两持久层可能历史错位
        #   （开发者机器现状：yaml=本地、json=zhipu），切换动作必须总是执行——两条链都幂等，
        #   重复写无害，顺带把错位对齐（自愈）。
        if mode == 'cloud':
            from src.core.multi_model_manager import get_multi_model_manager
            j = get_multi_model_manager().set_current_model(target)
            if not (j or {}).get('success'):
                return jsonify({'success': False, 'error': (j or {}).get('error', '外脑切换失败')}), 400
            # set_current_model 内部已同步 yaml 镜像 / by_mode.cloud / last_query_mode
            return jsonify({'success': True, 'mode': mode, 'switched': True,
                            'current_model': target})
        _apply_local_model(target)
        return jsonify({'success': True, 'mode': mode, 'switched': True,
                        'current_model': target})
    except Exception as e:
        logger.exception('[Models] switch-mode 失败')
        return jsonify({'success': False, 'error': str(e)[:150]}), 500


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
        current = APP_VERSION
        try:
            r = ver_requests.get(UPDATE_CHECK_URL, timeout=8, params={'cur': APP_VERSION})
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
                    # ★ #1361 透传更新包指纹（Hub 清单 package_md5/package_file）
                    'package_md5': latest.get('package_md5', ''),
                    'package_file': latest.get('package_file', ''),
                    # ★ #1398（2026-11-02 定版拍板 A+B）增量更新通道 + 完整包直链
                    'download_url': latest.get('download_url', ''),
                    'package_size': latest.get('package_size', 0),
                    'update_channel': latest.get('update_channel') or None,
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
        return jsonify({'current': APP_VERSION, 'error': str(e)[:200], 'has_update': False})


@app.route('/api/version/update', methods=['POST'])
def version_download():
    """★ #1361 安全一键更新：下载 → MD5 校验 → 备份数据 → Zip Slip 安全解压 → 清废弃文件 → 提示重启

    支持两种包格式：
      A) pack_update.py 增量更新包（内含 update_payload.zip + delete_list.txt）— 首选
      B) 老式整包（文件平铺 zip）— 兼容
    """
    global _update_download_progress
    data = request.json or {}
    download_url = data.get('url', '')
    expect_md5 = (data.get('md5') or '').strip().lower()
    if not download_url:
        return jsonify({'error': '下载地址为空'}), 400
    if not download_url.lower().startswith(('http://', 'https://')):
        return jsonify({'error': '下载地址不合法'}), 400

    _update_download_progress = {'status': 'downloading', 'progress': 0, 'error': None, 'file': None}

    def _fail(msg):
        _update_download_progress['status'] = 'error'
        _update_download_progress['error'] = msg
        logger.error(f"[Update] 更新失败: {msg}")

    def _safe_extract(zf, dest_root):
        """Zip Slip 防护解压（同 panel_manager._safe_extractall 口径）"""
        n = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace('\\', '/').lstrip('/')
            if not rel or rel.startswith('/') or (len(rel) > 1 and rel[1] == ':'):
                raise ValueError(f'压缩包内出现非法路径: {info.filename}')
            parts = [p for p in rel.split('/') if p not in ('.', '..')]
            if not parts:
                continue
            target = Path(dest_root).joinpath(*parts).resolve()
            if os.path.commonpath([str(target), str(dest_root)]) != str(dest_root):
                raise ValueError(f'路径越界（Zip Slip 拦截）: {info.filename}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            n += 1
        return n

    def _apply_deletes(delete_list_path):
        """应用废弃文件清单（铁律：永不动 data/、config.yaml、.venv、python/）"""
        removed = 0
        with open(delete_list_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                rel = line.strip()
                if not rel or rel.startswith('#'):
                    continue
                rel_n = rel.replace('\\', '/').strip('/')
                # 保护性拒删：用户数据/运行时永不进删除范围
                if (rel_n == 'config.yaml' or rel_n.startswith(('data/', '.venv/', 'python/'))
                        or rel_n.split('/')[0] in ('.venv', 'python')):
                    logger.warning(f"[Update] 删除清单命中保护区，跳过: {rel_n}")
                    continue
                target = (Path(PROJECT_ROOT) / rel_n).resolve()
                root_s = str(Path(PROJECT_ROOT).resolve())
                if os.path.commonpath([str(target), root_s]) != root_s:
                    logger.warning(f"[Update] 删除清单路径越界，跳过: {rel_n}")
                    continue
                try:
                    if target.is_file():
                        target.unlink()
                        removed += 1
                except Exception as _e:
                    logger.debug(f"[Update] 删除失败（忽略）: {rel_n}: {_e}")
        return removed

    def _backup_data(bak_dir):
        """备份用户数据：data/ 全量排除可再生大件（models/media/downloads）+ config.yaml"""
        src_data = Path(PROJECT_ROOT) / 'data'
        skip_dirs = {'models', 'media', 'downloads'}
        if src_data.is_dir():
            for root_d, dirs_d, files_d in os.walk(src_data):
                dirs_d[:] = [d for d in dirs_d if d not in skip_dirs]
                rel_d = os.path.relpath(root_d, src_data)
                out_d = bak_dir / 'data' / rel_d if rel_d != '.' else bak_dir / 'data'
                out_d.mkdir(parents=True, exist_ok=True)
                for f_d in files_d:
                    try:
                        shutil.copy2(os.path.join(root_d, f_d), out_d / f_d)
                    except Exception as _e:
                        logger.debug(f"[Update] 备份跳过 {f_d}: {_e}")
        cfg = Path(PROJECT_ROOT) / 'config.yaml'
        if cfg.is_file():
            shutil.copy2(cfg, bak_dir / 'config.yaml')

    def _do_update():
        global _update_download_progress
        try:
            import requests as dl_requests
            zip_path = os.path.join(tempfile.gettempdir(), 'harven_update.zip')
            r = dl_requests.get(download_url, stream=True, timeout=600)
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            md5h = hashlib.md5()
            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
                        md5h.update(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            _update_download_progress['progress'] = min(int(downloaded * 95 / total), 90)

            # [2] MD5 校验
            _update_download_progress['progress'] = 92
            _update_download_progress['status'] = 'verifying'
            got_md5 = md5h.hexdigest()
            if expect_md5 and got_md5 != expect_md5:
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
                _fail(f'更新包校验不符（可能下载不完整），已删除。期望 {expect_md5[:8]}…，实际 {got_md5[:8]}…')
                return
            logger.info(f"[Update] 下载完成 {downloaded} 字节，MD5 {got_md5}")

            # [3] 备份用户数据
            _update_download_progress['progress'] = 94
            _update_download_progress['status'] = 'backup'
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            bak_dir = Path(PROJECT_ROOT) / f'backup_update_{stamp}'
            bak_dir.mkdir(parents=True, exist_ok=True)
            _backup_data(bak_dir)
            logger.info(f"[Update] 数据已备份: {bak_dir}")

            # [4] 解包应用
            _update_download_progress['progress'] = 96
            _update_download_progress['status'] = 'extracting'
            import zipfile as zf_lib
            extract_dir = os.path.join(tempfile.gettempdir(), 'harven_update_extract')
            shutil.rmtree(extract_dir, ignore_errors=True)  # ★ 清上次残留，防旧文件混包
            n_files = 0
            with zf_lib.ZipFile(zip_path) as zf:
                names = zf.namelist()
                if 'update_payload.zip' in names:
                    # A) pack_update.py 增量包格式
                    with zf.open('update_payload.zip') as pf:
                        import io as _io
                        with zf_lib.ZipFile(_io.BytesIO(pf.read())) as inner:
                            n_files = _safe_extract(inner, str(PROJECT_ROOT))
                    if 'delete_list.txt' in names:
                        with zf.open('delete_list.txt') as dl:
                            dl_path = os.path.join(tempfile.gettempdir(), 'harven_update_deletes.txt')
                            with open(dl_path, 'wb') as out:
                                out.write(dl.read())
                            n_del = _apply_deletes(dl_path)
                            try:
                                os.remove(dl_path)
                            except Exception:
                                pass
                            logger.info(f"[Update] 清理废弃文件 {n_del} 个")
                else:
                    # B) 老式整包（平铺或单层目录包裹）
                    top = {n.split('/')[0] for n in names if n}
                    src = extract_dir
                    zf.extractall(extract_dir)
                    if len(top) == 1 and os.path.isdir(os.path.join(extract_dir, list(top)[0])):
                        src = os.path.join(extract_dir, list(top)[0])
                    for root2, _dirs2, files2 in os.walk(src):
                        rel2 = os.path.relpath(root2, src)
                        dest_dir = Path(PROJECT_ROOT) / rel2 if rel2 != '.' else Path(PROJECT_ROOT)
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        for f2 in files2:
                            shutil.copy2(os.path.join(root2, f2), dest_dir / f2)
                            n_files += 1

            # [5] 收尾
            try:
                shutil.rmtree(extract_dir, ignore_errors=True)
                os.remove(zip_path)
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")

            _update_download_progress['status'] = 'done'
            _update_download_progress['progress'] = 100
            _update_download_progress['file'] = str(PROJECT_ROOT)
            _update_download_progress['backup'] = bak_dir.name
            _update_download_progress['applied'] = n_files
            logger.info(f"[Update] 更新完成（应用 {n_files} 个文件），重启后生效")

        except Exception as e:
            _fail(str(e)[:300])

    threading.Thread(target=_do_update, daemon=True).start()
    return jsonify({'ok': True, 'message': '开始下载更新...'})


@app.route('/api/version/update-progress', methods=['GET'])
def version_update_progress():
    """查询更新进度"""
    return jsonify(_update_download_progress)


@app.route('/api/version/restart', methods=['POST'])
def version_restart():
    """★ #1361 一键重启：派生独立进程等 3 秒后拉起 启动HarvenAI.bat，本进程退出"""
    bat = Path(PROJECT_ROOT) / '启动HarvenAI.bat'
    if not bat.is_file():
        return jsonify({'error': '没找到启动脚本，请手动双击桌面「Harven AI」重启'}), 404
    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        flags = 0x00000008 | 0x00000200 | 0x08000000
        # ★ #1591（2026-11-27）：timeout /t 3 /nobreak 在无窗口提权环境下永挂
        #   （僵尸 cmd 25840 从 21:48 卡死实锤）——ping 不需要控制台 stdin，
        #   -n 4 ≈ 3 秒，语义等价。同时启动脚本已加「等端口释放」循环防旧实例未死透
        subprocess.Popen(
            ['cmd', '/c', 'ping', '-n', '4', '127.0.0.1', '>nul', '&', 'start', '""', str(bat)],
            cwd=str(PROJECT_ROOT), creationflags=flags, close_fds=True)
    except Exception as e:
        return jsonify({'error': f'重启派生失败: {e}'}), 500

    def _bye():
        logger.info('[Update] 一键重启：进程退出，等待启动脚本拉起新实例')
        os._exit(0)
    threading.Timer(1.0, _bye).start()
    return jsonify({'ok': True, 'message': '哈文正在重启，约 10 秒后页面自动恢复'})


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

    # ★ 2026-09-20 (#804): 局域网 IP（设置页「手机与云端」提示手机访问地址用）
    try:
        import socket as _sock
        _s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        _s.connect(('8.8.8.8', 80))
        info['lan_ip'] = _s.getsockname()[0]
        _s.close()
    except Exception:
        info['lan_ip'] = ''

    # ★ 2026-09-22 (#851): 实例名（同一台电脑跑多个哈文 / 多台电脑时区分「哈文开发者」「哈文1」）
    try:
        from src.utils.config import Config as _Cfg
        _name = str(_Cfg().get('instance_name') or '').strip()
        info['instance_name'] = _name or '哈文'
    except Exception:
        info['instance_name'] = '哈文'

    return jsonify(info)


@app.route('/api/system/name', methods=['POST'])
def set_instance_name():
    """★ 2026-09-22 (#851): 改实例名（非安全项，不要求密码；1~20 字，空则恢复默认「哈文」）"""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    if name and len(name) > 20:
        return jsonify({'success': False, 'error': '名字太长了，最多 20 个字'}), 400
    try:
        from src.utils.config import Config
        cfg = Config()
        cfg.set('instance_name', name or '哈文')
        cfg.save()
        return jsonify({'success': True, 'instance_name': name or '哈文'})
    except Exception as e:
        logger.error(f"[API] 保存实例名失败: {e}")
        return jsonify({'success': False, 'error': '保存失败'}), 500

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

    # ★ 2026-09-17 收口：统一走 security_guard.change_admin_password
    # （同步写 security.admin_password + 顶层 admin_password，保险箱/面板/全权模式全部生效）
    from src.core.security_guard import change_admin_password
    result = change_admin_password(current_pwd, new_pwd)
    if result.get('success'):
        return jsonify({'success': True, 'message': '密码已修改，保险箱/面板/全权模式同步生效'})
    return jsonify({'error': result.get('error', '修改失败')}), 400


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
            'password_protected_actions': ['evolution'],
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
            resp = {'success': True, 'message': '验证通过'}
            # ★ 2026-09-22 (#853): 仅登录页请求（register_device=True）时登记电脑设备并发放设备令牌，
            # 面板解锁/全权模式等其它调 verify-password 的地方不受影响
            try:
                if data.get('register_device'):
                    import secrets as _secrets
                    from datetime import datetime as _dt
                    devs = _load_computer_devices()
                    tok = (data.get('device_token') or '').strip()
                    rec = next((d for d in devs if d.get('token') == tok), None) if tok else None
                    if rec is None:
                        tok = _secrets.token_hex(16)
                        ua = (request.headers.get('User-Agent') or '')
                        browser = '电脑'
                        for key, label in (('Edg/', 'Edge'), ('OPR/', 'Opera'), ('Chrome/', 'Chrome'), ('Firefox/', 'Firefox')):
                            if key in ua:
                                browser = label
                                break
                        rec = {
                            'id': _secrets.token_hex(6),
                            'token': tok,
                            'name': f'{browser}-{tok[:4]}',
                            'added': _dt.now().strftime('%Y-%m-%d %H:%M'),
                            'last_seen': _dt.now().strftime('%Y-%m-%d %H:%M'),
                        }
                        devs.append(rec)
                    rec['last_seen'] = _dt.now().strftime('%Y-%m-%d %H:%M')
                    _save_computer_devices(devs)
                    resp['device_token'] = tok
                    resp['device_name'] = rec.get('name', '')
            except Exception as e:
                logger.warning(f'[Security] 电脑设备登记失败（不影响登录）: {e}')
            logger.info('[Security] 密码验证通过')
            return jsonify(resp)
        else:
            logger.warning('[Security] 密码验证失败')
            return jsonify({'success': False, 'error': '密码错误'}), 401

    except Exception as e:
        logger.error(f'[Security] 密码验证异常: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== ★ 2026-09-22 (#853) 电脑设备管理 =====

def _computer_devices_path():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'computer_devices.json')


def _load_computer_devices():
    import json
    try:
        with open(_computer_devices_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_computer_devices(devs):
    import json, os
    p = _computer_devices_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(devs, f, ensure_ascii=False, indent=2)


def _check_admin_pwd(pwd):
    """校验管理员密码（设备管理操作用）"""
    import secrets
    cfg = _load_config()
    stored = ((cfg.get('security', {}) or {}).get('admin_password') or cfg.get('admin_password') or '').strip()
    if not stored:
        return False
    return secrets.compare_digest((pwd or '').strip(), stored)


@app.route('/api/devices/computers', methods=['GET'])
def devices_computers_list():
    """已登录过的电脑列表（不含令牌本体）"""
    try:
        devs = _load_computer_devices()
        out = [{'id': d.get('id', ''), 'name': d.get('name', ''), 'added': d.get('added', ''), 'last_seen': d.get('last_seen', '')} for d in devs]
        return jsonify({'success': True, 'devices': out})
    except Exception as e:
        logger.error(f'[Devices] 列表失败: {e}')
        return jsonify({'success': False, 'error': '读取失败'}), 500


@app.route('/api/devices/computer/check', methods=['GET'])
def devices_computer_check():
    """前端启动时校验本地存的设备令牌是否还有效（被移除则失效）"""
    tok = (request.args.get('token') or '').strip()
    if not tok:
        return jsonify({'success': True, 'valid': False})
    devs = _load_computer_devices()
    rec = next((d for d in devs if d.get('token') == tok), None)
    return jsonify({'success': True, 'valid': rec is not None, 'name': (rec or {}).get('name', '')})


@app.route('/api/devices/computer/rename', methods=['POST'])
def devices_computer_rename():
    """给某台电脑改名（免密：名字只是显示标签，方便区分谁是谁）"""
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:20]
    if not name:
        return jsonify({'success': False, 'error': '名字不能为空'}), 400
    devs = _load_computer_devices()
    rec = next((d for d in devs if d.get('id') == data.get('id')), None)
    if rec is None:
        return jsonify({'success': False, 'error': '找不到这台电脑（可能已被移除）'}), 404
    rec['name'] = name
    _save_computer_devices(devs)
    return jsonify({'success': True})


@app.route('/api/devices/computer/revoke', methods=['POST'])
def devices_computer_revoke():
    """移除某台电脑的登录记录（需管理员密码；该浏览器下次登录会重新登记）"""
    data = request.get_json(silent=True) or {}
    if not _check_admin_pwd(data.get('password')):
        return jsonify({'success': False, 'error': '管理员密码不对'}), 403
    devs = _load_computer_devices()
    before = len(devs)
    devs = [d for d in devs if d.get('id') != data.get('id')]
    if len(devs) == before:
        return jsonify({'success': False, 'error': '找不到这台电脑（可能已被移除）'}), 404
    _save_computer_devices(devs)
    return jsonify({'success': True})


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
        _mm_items = []
        mm_path = PROJECT_ROOT / 'data' / 'multi_models.json'
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                for pid, entry in mm_config.get('models', {}).items():
                    if pid == 'ollama': continue
                    _mm_items.append((pid, entry))
                    if entry.get('api_key'):
                        mkeys[pid] = entry['api_key']
            except Exception as _e:
                logger.debug(f"[WebApp] 忽略错误: {_e}")
        
        # ★ #1575 方案C（2026-11-27）：硬编码 8 家 → PROVIDERS 全家福（25 家全放出来）。
        #   此前 openai/claude/gemini/硅基流动/OpenRouter/混元/MiniMax/文心/星火/xAI 等 17 家
        #   在注册表里躺着但页面永远看不到，用户想配都没入口。
        result = {}
        try:
            from src.core.multi_model_manager import PROVIDERS as _ALL_PROV
            _builtin_ids = [pid for pid in _ALL_PROV if pid not in ('ollama', 'harveneye')]
        except Exception:
            _builtin_ids = ['deepseek', 'doubao', 'qwen', 'kimi', 'zhipu', 'yi', 'baichuan', 'stepfun']
        for k in _builtin_ids:
            v = mkeys.get(k, '')
            result[k] = {
                'configured': bool(v),
                'masked': (v[:4] + '****' + v[-4:]) if len(v) > 8 else (v or '')
            }
        # ★ #1575 方案C：custom_providers 全列出（此前只有配了钥匙的才漏出来，没配的直接消失）
        try:
            _mm_custom = {}
            if mm_path.exists():
                try:
                    _mm_custom = json.loads(mm_path.read_text(encoding='utf-8')).get('custom_providers', {}) or {}
                except Exception:
                    _mm_custom = {}
            for cid, cinfo in _mm_custom.items():
                if cid in result:
                    result[cid]['is_custom'] = True
                    continue
                _cv = mkeys.get(cid, '') or (cinfo.get('api_key', '') if isinstance(cinfo, dict) else '')
                result[cid] = {
                    'configured': bool(_cv),
                    'masked': (_cv[:4] + '****' + _cv[-4:]) if len(_cv) > 8 else (_cv or ''),
                    'name': (cinfo.get('name', cid) if isinstance(cinfo, dict) else cid),
                    'is_custom': True,
                }
        except Exception as _e:
            logger.debug(f"[WebApp] custom_providers 列出跳过: {_e}")
        # 兜底：model_keys 里有但上面两轮都没认领的（历史残留 key）
        for k in mkeys:
            if k not in result and k != 'ollama':
                v = mkeys.get(k, '')
                result[k] = {
                    'configured': bool(v),
                    'masked': (v[:4] + '****' + v[-4:]) if len(v) > 8 else (v or ''),
                    'name': k
                }
        # ★ 2026-09-13 用户反馈#9：外脑卡补"型号"回显（读 multi_models.json 的 model_name）
        for pid, entry in _mm_items:
            if pid in result and isinstance(entry, dict):
                result[pid]['model_name'] = entry.get('model_name', '') or ''
        # ★ D1 #1564（2026-11-27）：外脑识图能力位——模型中心打「👁️ 支持识图/📄 仅文字对话」徽标。
        #   数据源=PROVIDERS.vision（模型中立：True/False 如实标注，未标注 None 前端不显示）
        try:
            from src.core.multi_model_manager import PROVIDERS as _PROV
            for k in result:
                _v = (_PROV.get(k) or {}).get('vision')
                result[k]['vision'] = True if _v is True else (False if _v is False else None)
        except Exception as _e:
            logger.debug(f'[WebApp] vision 能力位跳过: {_e}')
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
        # ★ 2026-09-13: 支持 data.delete=["provider",...] 显式删除钥匙（此前空值会被忽略、旧key永远保留，用户删不掉）
        delete_ids = data.get('delete') or []
        old_keys0 = config.get('model_keys', {}) or {}
        if delete_ids:
            import json as _json
            old_keys0 = {k: v for k, v in old_keys0.items() if k not in delete_ids}
            config.set('model_keys', old_keys0)
            config.save()
            mm_path0 = PROJECT_ROOT / 'data' / 'multi_models.json'
            if mm_path0.exists():
                try:
                    mm0 = _json.loads(mm_path0.read_text(encoding='utf-8'))
                    for pid in delete_ids:
                        if pid in mm0.get('models', {}):
                            mm0['models'][pid].pop('api_key', None)
                            mm0['models'][pid]['enabled'] = False
                    mm_path0.write_text(_json.dumps(mm0, ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass
        # ★ 兼容旧存储：保存所有传入的key（不再硬编码4个）
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
        model = (data.get('model') or '').strip()
        # ★ v9 加固（2026-10-04）：空参曾静默切成 ollama 本地（model or 'ollama'）——
        #   万一被空体调用会把用户选好的外脑悄悄清掉。现在空参直接 400 拒绝；
        #   想切回本地模式必须显式传 model="ollama"，不再有隐式行为
        if not model:
            return jsonify({'success': False, 'error': '缺少 model 参数；切回本地模式请显式传 "ollama"'}), 400
        mm = get_multi_model_manager()
        # ★ 使用 set_current_model 持久化
        result = mm.set_current_model(model)
        return jsonify({'success': True, 'current': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/models/current-foreign', methods=['GET'])
def current_foreign_model():
    """获取当前外脑（★ #1326：只报云端槽；★ #1327：改读权威 yaml——json 槽位镜像
    可能陈旧（历史欠账），yaml 的 current_model_by_mode.cloud 才是用户真实选择）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager, is_local_model
        from src.utils.config import get_config
        mm = get_multi_model_manager()
        cloud = (((get_config() or {}).get('current_model_by_mode') or {}).get('cloud') or '').strip()
        if not cloud:   # yaml 没有则退 json 槽
            cloud = (((mm.config or {}).get('current_model_by_mode') or {}).get('cloud') or '').strip()
        active = (mm.current_model or '').strip()
        _local = is_local_model(active) if active else True
        return jsonify({'model': cloud if cloud else (active if not _local else ''),
                        'active_is_local': _local})
    except Exception:
        return jsonify({'model': ''})

@app.route('/api/models/foreign-model-name', methods=['POST'])
def set_foreign_model_name():
    """★ 2026-09-13 用户反馈#9：设置外脑型号（如 deepseek-chat / deepseek-reasoner）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        data = request.get_json(force=True, silent=True) or {}
        pid = (data.get('provider') or '').strip()
        name = (data.get('model_name') or '').strip()
        if not pid or not name:
            return jsonify({'success': False, 'error': '缺少 provider 或 model_name'}), 400
        mm = get_multi_model_manager()
        models_cfg = mm.config.setdefault('models', {})
        entry = models_cfg.setdefault(pid, {})
        entry['model_name'] = name
        if not entry.get('base_url'):
            providers = mm._get_all_providers()
            if pid in providers:
                entry['base_url'] = providers[pid].get('base_url', '')
        mm._save_config()
        logger.info(f"[Models] 外脑型号已设置: {pid} → {name}")
        return jsonify({'success': True, 'provider': pid, 'model_name': name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/models/test-connect', methods=['POST'])
def test_model_connect():
    """★ #1516 连接测试改造（2026-11-14）：逐个测完全量返回 + 当前外脑必测置顶。
    旧版「测到第一个成功就 return」实锤（老谭检测抓包）：配了 deepseek+zhipu 两个 key、
    当前用的是 zhipu 而 zhipu key 失效时，端点测到 deepseek 通就报 ✅——误报连接正常。
    兼容三种调用：
      1. 无 body        → 逐个测所有已配置 key，全量结果（当前外脑排第一并带 current 标记）
      2. body.model_id  → 只测该模型（body.api_key/base_url 优先，未传则用已保存配置）
      3. 响应兼容旧前端：success / model / error 三字段语义不变，新增 results 数组
    """
    try:
        from src.utils.config import Config
        import json
        body = request.get_json(force=True, silent=True) or {}
        keys = Config().get('model_keys', {})

        # ★ 合并 multi_models.json 中的 Key
        mm_path = PROJECT_ROOT / 'data' / 'multi_models.json'
        mm_models = {}
        if mm_path.exists():
            try:
                mm_config = json.loads(mm_path.read_text(encoding='utf-8'))
                for pid, entry in mm_config.get('models', {}).items():
                    if pid == 'ollama': continue
                    if entry.get('api_key'):
                        keys[pid] = entry['api_key']
                mm_models = mm_config.get('models', {})
            except Exception as _e: logger.debug(f"[WebApp] 忽略错误: {_e}")

        # 各厂商默认测试端点（模型名优先从用户选择读取）
        test_configs = {
            'deepseek': ('https://api.deepseek.com/v1/chat/completions', 'deepseek-chat'),
            'doubao':   ('https://ark.cn-beijing.volces.com/api/v3/chat/completions', 'doubao-seed-2-0-pro-260215'),
            'qwen':     ('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', 'qwen-turbo'),
            'kimi':     ('https://api.moonshot.cn/v1/chat/completions', 'moonshot-v1-8k'),
            'zhipu':    ('https://open.bigmodel.cn/api/paas/v4/chat/completions', 'glm-4-flash'),
            'yi':       ('https://api.lingyiwanwu.com/v1/chat/completions', 'yi-lightning'),
            'baichuan': ('https://api.baichuan-ai.com/v1/chat/completions', 'Baichuan4-Turbo'),
            'stepfun':  ('https://api.stepfun.com/v1/chat/completions', 'step-1-flash'),
        }

        def _probe(name, key, url, model):
            """单家探测：返回 {name, ok, error, model}"""
            import time as _t
            t0 = _t.time()
            try:
                import requests as _r
                resp = _r.post(url,
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1},
                    timeout=10)
                ms = int((_t.time() - t0) * 1000)
                if resp.status_code == 200:
                    return {'name': name, 'ok': True, 'error': '', 'model': model, 'ms': ms}
                err_info = f'HTTP {resp.status_code}'
                try:
                    err_body = resp.json()
                    err_info += f' ({err_body.get("error", {}).get("message", "")})'
                except Exception as _e: logger.debug(f"[WebApp] 忽略: {_e}")
                return {'name': name, 'ok': False, 'error': err_info[:120], 'model': model, 'ms': ms}
            except Exception as e:
                return {'name': name, 'ok': False, 'error': str(e)[:120], 'model': model,
                        'ms': int((_t.time() - t0) * 1000)}

        # ── 模式 2：body.model_id 单测（传入 key/base_url 优先，兼容旧调用方）──
        if body.get('model_id'):
            mid = body['model_id'].strip()
            key = (body.get('api_key') or '').strip() or keys.get(mid, '')
            url = (body.get('base_url') or '').strip().rstrip('/')
            if url:
                url = url + '/chat/completions' if not url.endswith('/chat/completions') else url
            else:
                url = test_configs.get(mid, ('', ''))[0]
            model = mm_models.get(mid, {}).get('model_name') or test_configs.get(mid, ('', ''))[1] or 'gpt-3.5-turbo'
            if not url:
                return jsonify({'success': False, 'error': f'未知模型 {mid} 且未提供 base_url'})
            if not key:
                return jsonify({'success': False, 'error': '请先输入 API 密钥'})
            r = _probe(mid, key, url, model)
            return jsonify({'success': r['ok'], 'model': f"{mid} ✅" if r['ok'] else '',
                            'error': '' if r['ok'] else r['error'], 'results': [r]})

        # ── 模式 1：无 body 全量逐测（当前外脑置顶）──
        current_pid = ''
        try:
            from src.utils.config import get_config
            cloud = (((get_config() or {}).get('current_model_by_mode') or {}).get('cloud') or '').strip()
            current_pid = (cloud.split('/')[0] if cloud else '').strip()
        except Exception as _e: logger.debug(f"[WebApp] 当前外脑读取失败: {_e}")

        tested = []
        for name, (url, default_model) in test_configs.items():
            key = keys.get(name, '')
            if not key:
                continue
            model = mm_models.get(name, {}).get('model_name') or default_model
            tested.append(_probe(name, key, url, model))
        # ★ 当前外脑排第一（哪怕测挂了也必须在最显眼的位置）
        tested.sort(key=lambda r: (r['name'] != current_pid,))
        for r in tested:
            if r['name'] == current_pid:
                r['current'] = True

        if not tested:
            return jsonify({'success': False, 'error': '请先配置至少一个 API Key'})

        ok_list = [r for r in tested if r['ok']]
        bad_list = [r for r in tested if not r['ok']]
        if ok_list and not bad_list:
            summary, success = ' / '.join(f"{r['name']} ✅ {r['ms']}ms" for r in tested), True
        elif ok_list and bad_list:
            summary, success = ' / '.join(f"{r['name']} {'✅' if r['ok'] else '❌ ' + r['error']}" for r in tested), True
        else:
            summary, success = '; '.join(f"{r['name']}: {r['error']}" for r in tested), False
        return jsonify({'success': success, 'model': summary,
                        'error': '' if success else summary, 'tested': [r['name'] for r in tested],
                        'current': current_pid, 'results': tested})
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

    # ★ 2026-09-13 修复：目录模型统一标 ollama 型但没有真名，导致 pull 用错 id
    #   推导规则：有 ollama_model 用之；否则把结尾的 -14b/-6.7b 等规格段换成冒号（qwen2.5-14b → qwen2.5:14b）
    import re as _re
    def _ollama_name(mid):
        return _re.sub(r'-(\d+(?:\.\d+)?b)$', r':\1', (mid or ''), flags=_re.IGNORECASE)

    # 本地 Ollama 已装列表（拉一次，供 installed 判断）
    _local_tags = []
    try:
        import requests as _tags_rq
        _base = config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        _local_tags = [x.get('name', '') for x in _tags_rq.get(f"{_base}/api/tags", timeout=2).json().get('models', [])]
    except Exception:
        pass

    for m in catalog_models:
        name_lower = m.get('name', '').lower()
        cat_lower = _norm_cat(m.get('category', ''))

        # 跳过已存在的（来自安装器的同名模型）
        if m.get('name') in seen_names:
            continue

        if category != 'all' and cat_lower != category:
            continue

        _pkg = m.get('ollama_model') or _ollama_name(m.get('id', '')) or m.get('id', '')
        entry = {
            'id': m.get('id', name_lower.replace(' ', '_').replace('-', '_')),
            'name': m.get('name', ''),
            'category': m.get('category', ''),
            'install': {
                'type': 'ollama',
                'package': _pkg,
                'desc': m.get('description', ''),
            },
            'model_size': m.get('size', m.get('size_gb', '?')),
            'requirements': {
                'ram': f"{m.get('ram_gb', '?')}GB",
                'gpu': m.get('vram_gb', 0) > 0 and '需要' or '可选',
                'disk': '未知',
            },
            'installed': _pkg in _local_tags if _pkg else False,
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

@app.route('/api/models/custom-provider/<provider_id>', methods=['DELETE'])
def del_custom_provider(provider_id):
    """★ #1572 删除自定义外脑（内置模型拒绝，由 mm 层把关）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        result = mm.delete_custom_provider(provider_id)
        # 顺手清 config.yaml model_keys 里的残留钥匙（回退层），避免幽灵卡片
        if result.get('success'):
            try:
                import yaml as _yaml
                _cp = PROJECT_ROOT / 'config.yaml'
                if _cp.exists():
                    cfg = _yaml.safe_load(_cp.read_text(encoding='utf-8')) or {}
                    mk = cfg.get('model_keys') or {}
                    if provider_id in mk:
                        mk.pop(provider_id, None)
                        cfg['model_keys'] = mk
                        with open(_cp, 'w', encoding='utf-8') as f:
                            _yaml.dump(dict(cfg), f, allow_unicode=True, sort_keys=False)
                        logger.info(f"[custom-provider] 已清 {provider_id} 在 config.yaml 的残留钥匙")
            except Exception as _e:
                logger.debug(f"[custom-provider] 清理 model_keys 忽略: {_e}")
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/models/test-connection', methods=['POST'])
def test_model_connection():
    """★ #1572 外脑连通测试（支持未保存的 key/url 即测，添加表单用）"""
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        data = request.get_json(force=True, silent=True) or {}
        provider_id = (data.get('provider_id') or '').strip()
        api_key = (data.get('api_key') or '').strip() or None
        base_url = (data.get('base_url') or '').strip() or None
        if not provider_id:
            return jsonify({'success': False, 'error': '缺少 provider_id'})
        mm = get_multi_model_manager()
        # 已知外脑（内置 25 家 + 已保存自定义）→ 走完整测试链
        if provider_id in mm._get_all_providers():
            return jsonify(mm.test_connection(provider_id, api_key=api_key, base_url=base_url))
        # 未保存的新外脑 → base_url 必须现场给，内联直测 OpenAI 协议
        if not base_url:
            return jsonify({'success': False, 'error': f'未知外脑 {provider_id}，且未提供接口地址'})
        import requests as _requests
        from src.core.independence import bypass_independence
        url = base_url.rstrip('/') + '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        payload = {
            'model': (data.get('model_name') or '').strip() or 'test',
            'messages': [{'role': 'user', 'content': 'test'}],
            'max_tokens': 10,
        }
        with bypass_independence():
            resp = _requests.post(url, headers=headers, json=payload, timeout=(10, 15))
        if resp.status_code in [200, 201]:
            return jsonify({'success': True})
        if resp.status_code == 401:
            return jsonify({'success': False, 'error': 'API 密钥无效（401 Unauthorized）'})
        if resp.status_code == 403:
            return jsonify({'success': False, 'error': 'API 密钥无权访问（403 Forbidden）'})
        if resp.status_code == 404:
            return jsonify({'success': False, 'error': f'接口地址不正确(404)：{base_url} 可能不支持 /chat/completions，请检查地址'})
        return jsonify({'success': False, 'error': f'HTTP {resp.status_code}: {resp.text[:200]}'})
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
        # ★ 2026-11-15 受保护动作密码门（方案A①+②）：AI 自动写代码的扳机，必须输管理员密码
        from src.core.security_guard import is_protected_action, verify_admin_password
        if is_protected_action('evolution'):
            data_pwd = request.get_json(silent=True) or {}
            pwd = (str(data_pwd.get('password') or '').strip()
                   or (request.headers.get('X-Admin-Password') or '').strip())
            if not pwd:
                return jsonify({'success': False,
                                'error': '应用代码提案受管理员密码保护，请输入密码后重试',
                                'code': 'password_required'}), 401
            if not verify_admin_password(pwd):
                return jsonify({'success': False, 'error': '密码错误', 'code': 'password_wrong'}), 403
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

@app.route('/api/agents/<agent_id>/toggle', methods=['POST'])
def agent_toggle(agent_id):
    """★ 2026-09-13: 停用/启用开关（核心 3 位拒绝，其余可随时切换）"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if 'enabled' not in data:
            return jsonify({'success': False, 'error': '缺少 enabled'}), 400
        from src.core.agent_registry import get_registry
        return jsonify(get_registry().set_enabled(agent_id, bool(data['enabled'])))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ★ 2026-07-14: /api/learner/* 路由已删除 — learner.py 模块不存在

# ═══ ★ 2026-09-13: 邮箱配置页路由（修复 MailPage 调 /api/email/setup 404）═══
@app.route('/api/email/setup', methods=['GET'])
def email_setup_get():
    """读取邮箱配置（脱敏：不回传授权码明文，只告知是否已配置）"""
    try:
        from src.core.builtin_tools import _get_email_config
        cfg = _get_email_config() or {}
        return jsonify({
            'success': True,
            'config': {
                'email': cfg.get('address', ''),
                'provider': cfg.get('provider', ''),
                'imap_server': cfg.get('imap_server', ''),
                'smtp_server': cfg.get('smtp_server', ''),
                'has_token': bool(cfg.get('password')),
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/email/setup', methods=['POST'])
def email_setup_post():
    """保存邮箱配置（页面表单：email/provider/token → 复用 FC 工具的 server 自动推断）"""
    try:
        from src.core.builtin_tools import email_setup as _do_setup, _get_email_config, _save_email_config
        data = request.get_json(force=True, silent=True) or {}
        address = (data.get('email') or '').strip()
        token = (data.get('token') or '').strip()
        provider = (data.get('provider') or 'other').strip()
        if not address or not token:
            return jsonify({'success': False, 'error': '邮箱地址和授权码都要填'}), 400
        r = _do_setup(address, token)
        if not r.get('success'):
            return jsonify({'success': False, 'error': r.get('error', '保存配置失败')}), 500
        # provider 一并存入（页面回显用；email_setup 本身不存这个字段）
        cfg = _get_email_config() or {}
        cfg['provider'] = provider
        _save_email_config(cfg)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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


# ==================== ★ 2026-09-18 #743 画像增强：时间线 / 手动编辑 / 主动服务 ====================
@app.route('/api/profile/timeline', methods=['GET'])
def profile_timeline():
    """画像时间线：全部特征平铺按时间倒序（含证据/来源/时间）"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        items = get_profile_updater().get_profile_timeline()
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/insights', methods=['GET'])
def profile_insights():
    """最近自动学到的新特征（画像页「最近新学到」卡片）"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        items = get_profile_updater().get_recent_insights(days=7)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/trait', methods=['POST'])
def profile_add_trait():
    """手动添加画像特征"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        result = get_profile_updater().add_manual_trait(
            data.get('dimension', ''), data.get('label', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/trait', methods=['PUT'])
def profile_update_trait():
    """修改画像特征标签"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        result = get_profile_updater().update_trait(
            data.get('dimension', ''), data.get('label', ''), data.get('new_label', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/profile/trait', methods=['DELETE'])
def profile_delete_trait():
    """删除画像特征（哈文学错了就删掉）"""
    try:
        from src.core.profile_auto_updater import get_profile_updater
        data = request.get_json(silent=True) or {}
        result = get_profile_updater().delete_trait(
            data.get('dimension', ''), data.get('label', ''))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
    """获取市场统计 — ★ 2026-09-13: 云端优先（与 browse 同源，修分类计数全 0 的口径分裂）"""
    try:
        from src.core.harvenhub_client import hub_stats
        cloud = hub_stats()
        if cloud.get('success') and cloud.get('stats'):
            cs = cloud['stats']
            # ★ 2026-09-13: 云端响应是 {"stats": {...}} 双层包裹，需解包
            if isinstance(cs.get('stats'), dict):
                cs = cs['stats']
            _CAT_NAMES = {
                'code': '💻 开发', 'communication': '💬 沟通', 'creative': '🎨 创意',
                'education': '📚 学习', 'finance': '💰 财务', 'general': '📦 通用',
                'health': '🏥 健康', 'info': 'ℹ️ 资讯', 'life': '🏠 生活',
                'office': '📄 办公', 'other': '📦 其他', 'reasoning': '🧠 推理',
                'tech': '🔧 技术', 'tool': '🛠️ 工具', 'vertical': '🎯 垂直',
            }
            return jsonify({
                'total': cs.get('total_items', 0),
                'by_type': {t['type']: t['cnt'] for t in cs.get('type_stats', [])},
                'total_downloads': cs.get('total_downloads', 0),
                'total_ratings': 0,
                'categories': [
                    {
                        'id': c['category'],
                        'name': _CAT_NAMES.get(c['category'], c['category']),
                        'item_count': c['cnt'],
                        'description': '',
                    }
                    for c in cs.get('category_stats', [])
                ],
                'source': 'cloud',
            })

        # 云端不可用 → 本地统计兜底
        from src.core.harvenhub_market import get_market_stats, get_featured_items
        stats = get_market_stats()
        featured = get_featured_items(6)
        stats['featured'] = featured
        stats['total'] = stats.get('total_items', 0)
        stats['source'] = 'local'
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
    """管理员密码 SHA256 哈希（★ 2026-09-17 收口：改从磁盘读，改密后管理后台登录/会话立即同步）"""
    try:
        from src.core.security_guard import get_admin_password
        pwd = get_admin_password()
    except Exception:
        pwd = config.get('security', {}).get('admin_password', '') or 'harven2026'
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

        # 更新 config.yaml（★ 2026-09-17 收口：统一走 security_guard，同步顶层 admin_password）
        try:
            from src.core.security_guard import change_admin_password
            result = change_admin_password(current_pwd, new_pwd)
            if not result.get('success'):
                return jsonify({'error': result.get('error', '保存失败')}), 400
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
    # 判断是否是初始密码（★ 2026-09-17 改磁盘读，改密后登录页立即同步）
    try:
        from src.core.security_guard import get_admin_password
        stored_pwd = get_admin_password()
    except Exception:
        stored_pwd = config.get('security', {}).get('admin_password', '') or 'harven2026'
    is_initial = (stored_pwd == 'harven2026')
    
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
    import socket  # ★ 2026-09-19: webbrowser 移除（方案A，开浏览器归 bat 管）
    port = 7000  # ★ 2026-07-29: 5000被Windows/macOS占用，改为7000

    # ★ 2026-07-09: 启动时后台异步扫描系统知识
    try:
        from src.core.system_knowledge import scan_async
        scan_async()
    except Exception as e:
        print(f"[警告] 系统知识扫描启动失败: {e}")

    # ★ #1242（2026-10-27）: 嵌入模型后台预热——重启后第一问不再等 bge 加载 3~5 秒
    import threading as _th
    _th.Thread(target=_preload_knowledge_embedding, daemon=True, name='kb-embed-preload').start()

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

    # ★ 2026-09-19 方案A（定版拍板）：删掉自动开浏览器——由 启动HarvenAI.bat 全权负责。
    #   背景：bat 第50行也会 start http://localhost:7000/spa，旧版下线后 / 重定向 /spa，
    #   两处各开一次 = 双开窗口。这里删掉后只在「已在运行时再点快捷方式」
    #   由文件头端口预检（web_app.py 顶部）开一次浏览器，双击启动永远只弹一个窗口。

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

    # ===== ★ 2026-10-05 #1012~#1015 冰箱深化（档案/食谱/买菜/拍照识别） =====
    try:
        from src.core.fridge_hub import bp as fridge_bp
        app.register_blueprint(fridge_bp)
        logger.info("[WebApp] 冰箱深化路由已注册 (/api/panels/fridge)")
    except Exception as e:
        logger.warning(f"冰箱深化路由注册失败: {e}")

    # ===== ★ #1133（2026-10-14）随身记收件箱 =====
    try:
        from src.core.inbox import bp as inbox_bp
        app.register_blueprint(inbox_bp)
        logger.info("[WebApp] 随身记收件箱路由已注册 (/api/inbox)")
    except Exception as e:
        logger.warning(f"随身记路由注册失败: {e}")

    # ===== ★ 2026-10-11 #1071~#1073 健康中枢（档案/药箱/提醒/预警） =====
    try:
        from src.core.health_profile import bp as health_bp
        app.register_blueprint(health_bp)
        logger.info("[WebApp] 健康中枢路由已注册 (/api/panels/health)")
    except Exception as e:
        logger.warning(f"健康中枢路由注册失败: {e}")

    # ===== ★ 2026-10-11 #1072 健康中枢四面板出厂（缺哪个补哪个 + 旧体检数据迁入） =====
    try:
        from src.core.health_panels import ensure_health_panels
        ensure_health_panels()
    except Exception as e:
        logger.warning(f"健康中枢面板出厂失败: {e}")

    # ★ #1088 物品存放：复用现成 home_items「物品存放台账」（三级位置+照片+借出提醒全有），
    #   不重复建设；随安装包 data/panels 自带，panel_manager 扫描即就绪，无需出厂逻辑。

    # ===== ★ 2026-09-14 注册孩子成长档案路由 =====
    try:
        from src.core.kid_archive_routes import bp as kid_archive_bp
        app.register_blueprint(kid_archive_bp)
        logger.info("[WebApp] 孩子成长档案路由已注册 (/api/kid)")
    except Exception as e:
        logger.warning(f"孩子成长档案路由注册失败: {e}")

    # ===== ★ 2026-09-18 宠物系统（档案/喂养/疫苗驱虫/洗澡就医） =====
    try:
        from src.core.pet_routes import bp as pet_bp
        app.register_blueprint(pet_bp)
        logger.info("[WebApp] 宠物系统路由已注册 (/api/pet)")
    except Exception as e:
        logger.warning(f"宠物系统路由注册失败: {e}")

    # ===== ★ 2026-09-21 (#825) 语音识别模型管理（查看/下载/删除 Whisper） =====
    try:
        from src.core.voice_routes import bp as voice_bp
        app.register_blueprint(voice_bp)
        logger.info("[WebApp] 语音识别模型管理路由已注册 (/api/voice)")
    except Exception as e:
        logger.warning(f"语音识别模型管理路由注册失败: {e}")

    # ===== ★ 2026-09-18 照片中心（全模块照片统一索引） =====
    try:
        from src.core.photo_center import bp as photo_bp
        app.register_blueprint(photo_bp)
        logger.info("[WebApp] 照片中心路由已注册 (/api/photos)")
    except Exception as e:
        logger.warning(f"照片中心路由注册失败: {e}")

    # ===== ★ 2026-09-16 注册对话即录入路由 =====
    try:
        from src.core.panel_assist_routes import bp as panel_assist_bp
        app.register_blueprint(panel_assist_bp)
        logger.info("[WebApp] 对话即录入路由已注册 (/api/assist)")
    except Exception as e:
        logger.warning(f"对话即录入路由注册失败: {e}")

    # ===== ★ 2026-09-16 注册家庭提醒/日历路由 =====
    try:
        from src.core.home_routes import bp as home_reminder_bp
        app.register_blueprint(home_reminder_bp)
        logger.info("[WebApp] 家庭提醒/日历路由已注册 (/api/home)")
    except Exception as e:
        logger.warning(f"家庭提醒/日历路由注册失败: {e}")

    # ===== ★ 2026-10-15 #1150 提醒主动触达：蓝图 + 30s 扫描守护线程 =====
    try:
        from src.core.reminder_daemon import bp as reminder_daemon_bp, ensure_daemon_loop
        app.register_blueprint(reminder_daemon_bp)
        ensure_daemon_loop()
        logger.info("[WebApp] 提醒主动触达已注册 (/api/reminders/due) + 守护线程已启动")
    except Exception as e:
        logger.warning(f"提醒主动触达启动失败: {e}")

    # ===== ★ 2026-10-21 学习成长中心：批作业/错题本/学情周报 =====
    try:
        from src.core.study_coach import bp as study_coach_bp
        app.register_blueprint(study_coach_bp)
        logger.info("[WebApp] 学习成长中心已注册 (/api/study/*)")
    except Exception as e:
        logger.warning(f"学习成长中心注册失败: {e}")

    # ===== ★ 2026-10-22 面板教练引擎：观察/行动按钮/早报 =====
    try:
        from src.core.coach_engine import bp as coach_bp
        app.register_blueprint(coach_bp)
        logger.info("[WebApp] 面板教练引擎已注册 (/api/coach/*)")
    except Exception as e:
        logger.warning(f"面板教练引擎注册失败: {e}")

    # ===== ★ 2026-10-24 #1176 管家议程：声明扫描+统一议程+日两次简报 =====
    try:
        from src.core.butler_agenda import bp as butler_bp, ensure_butler_loop
        app.register_blueprint(butler_bp)
        ensure_butler_loop()
        logger.info("[WebApp] 管家议程已注册 (/api/butler/*) + 心跳线程已启动")
    except Exception as e:
        logger.warning(f"管家议程启动失败: {e}")

    # ===== ★ #1212（2026-10-25）Hub 安装确认卡：搜索+装前征求用户同意 =====
    try:
        from src.core.hub_install import bp as hub_install_bp
        app.register_blueprint(hub_install_bp)
        logger.info("[WebApp] Hub 安装引擎已注册 (/api/hub/search + /api/hub/install/*)")
    except Exception as e:
        logger.warning(f"Hub 安装引擎启动失败: {e}")

    # ===== ★ #1207/#1208/#1210 数据中枢（2026-10-25）：成员全景 / 全家事件流 / 画像写回 =====
    try:
        from src.core.member_hub import get_bp as get_member_bp
        app.register_blueprint(get_member_bp())
        logger.info("[WebApp] 数据中枢已注册 (/api/members/*)")
    except Exception as e:
        logger.warning(f"数据中枢注册失败: {e}")

    # ===== ★ 2026-10-21 速度包：启动后台预热（FC注册/CLI探测缓存/语义路由锚点） =====
    #   实锤：重启后首条消息被冷启动拖 12s（FC 注册）+ 收笔后被锚点计算拖 66s（阻塞入库）
    def _warmup_background():
        def _run():
            import time as _t
            _t.sleep(4)   # 等主服务先起稳
            try:
                from src.core.fc_wrapper_loader import load_fc_wrappers
                t, _e = load_fc_wrappers()
                logger.info(f"[Warmup] FC 封装预热完成: {len(t)} 个")
            except Exception as e:
                logger.debug(f"[Warmup] FC 预热跳过: {e}")
            try:
                from src.core.builtin_tools import _check_cli_anything_installed
                _check_cli_anything_installed()   # 结果进 1h 缓存，首条消息不再跑 pip show
                logger.info("[Warmup] CLI-Anything 探测已缓存")
            except Exception as e:
                logger.debug(f"[Warmup] CLI 探测跳过: {e}")
            try:
                from src.core import db_router
                if db_router._ensure_anchor_embeddings():
                    logger.info("[Warmup] 语义路由锚点向量预热完成（首条消息不再等 60s+）")
            except Exception as e:
                logger.debug(f"[Warmup] 锚点预热跳过: {e}")
        threading.Thread(target=_run, daemon=True, name='warmup-bg').start()

    try:
        _warmup_background()
    except Exception as e:
        logger.warning(f"预热线程启动失败: {e}")

    # ===== ★ 2026-09-19 #769 定时汇总三件套（梦境周/记账月/成长月）守护线程 =====
    try:
        from src.core.panel_summary import ensure_summary_loop
        ensure_summary_loop()
    except Exception as e:
        logger.warning(f"定时汇总线程启动失败: {e}")

    # ===== ★ 2026-09-19 #771 照片自动打标签守护线程（minicpm-v，每30分钟一批） =====
    try:
        from src.core.photo_center import ensure_tag_loop
        ensure_tag_loop()
    except Exception as e:
        logger.warning(f"照片打标签线程启动失败: {e}")

    # ===== ★ #916 统一图库后台补标线程（2026-09-27，images_index 没标签的图） =====
    try:
        from src.core.img_store import ensure_auto_tag_loop
        ensure_auto_tag_loop()
    except Exception as e:
        logger.warning(f"图库补标线程启动失败: {e}")


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
            # ★ 2026-09-16 面板文件禁缓存：面板升级换版后浏览器必须立刻拿新页，不许吃旧 DOM
            resp = send_from_directory(panel_dir, 'index.html')
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp

        @app.route('/panel/<panel_id>/<path:filename>')
        def serve_panel_assets(panel_id, filename):
            panel_dir = os.path.join(PANELS_DIR, panel_id)
            resp = send_from_directory(panel_dir, filename)
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp

        logger.info("[Startup] 面板静态文件路由已注册")
    except Exception as _e:
        logger.error(f"[Startup] 面板静态文件路由注册失败: {_e}")

    # ★ 2026-08-09: 启动定时任务调度器
    try:
        from src.core.task_scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"[Startup] 定时调度器启动失败: {e}")

    # ★ 2026-09-20 (#803/#804): 云端通道 + 设备配对
    # enabled=true 时哈文主动外连 HarvenHub 长连接；默认关，设置页可开
    try:
        from src.core.cloud_link import register_cloud_link_routes, start_cloud_link
        register_cloud_link_routes(app)
        start_cloud_link()
    except Exception as e:
        logger.warning(f"[Startup] 云端通道启动失败: {e}")

    # ★ #1257（2026-10-28）HarvenEye 合并托管：桌面单一图标，点一次全有了。
    # 后台拉起不阻塞启动；Eye 起不来只降级视觉功能（eye_supervisor 内置守护）
    try:
        # ★ #1280 节点⑥（2026-10-29）v1.3 融合退役：视觉子系统已并入哈文进程内
        #   （src/vision + vision_link），7001 独立进程/supervisor 托管/心跳全部作废。
        #   不再拉起旧 Eye；eye_catalog.db 索引库已迁移 data/vision/ 真家。
        import threading as _th
        logger.info("[Startup] HarvenEye 融合版（进程内）就绪——旧 7001 独立进程已退役")

        # ★ #1257 批4：存量照片重跑归位（延迟 90s；只动未分类，幂等可重复）
        def _reauto_bg():
            time.sleep(90)
            try:
                # ★ #1320 对话优先闸门：开发者手快重启后 1 分钟就提问（10-30 实锤），对话活跃则等
                from src.core import dialog_gate as _dg
                _waited = 0
                while _dg.is_busy() and _waited < 180:
                    time.sleep(10)
                    _waited += 10
                from src.core.img_store import reauto_place_all
                reauto_place_all()
            except Exception as _re:
                logger.warning(f"[Startup] 存量归位重跑失败（不影响使用）: {_re}")
        threading.Thread(target=_reauto_bg, daemon=True).start()

        # ★ #1322 选择即换血：启动热身——把用户当前选的本地模型预载进显存（20s 错峰，
        #   让硬件检测/路由先跑完；外脑选择/未选/解析不出则诚实跳过）
        def _warm_bg():
            time.sleep(20)
            try:
                from src.core.multi_model_manager import get_multi_model_manager
                _cur = str(get_multi_model_manager().current_model or '').strip()
                if _cur in ('', 'ollama'):
                    # ollama 阵营：具体模型名取用户配置的 default_model
                    from src.utils.config import get_config as _gcfg
                    _target = str((_gcfg() or {}).get('default_model', '') or '').strip()
                elif _cur == 'harveneye':
                    _target = 'harveneye'
                elif _cur.startswith('chain:'):
                    _target = ''        # ★ #1571 件1：对话型组合无需预载（编排器自管加载）
                else:
                    _target = ''        # 用户选的外脑，走 API 无需预载
                if _target:
                    from src.core import local_model_swap
                    local_model_swap.swap_in_async(_target, reason='startup_warmup')
            except Exception as _we:
                logger.debug(f"[Startup] 启动模型热身跳过（不影响使用）: {_we}")
        threading.Thread(target=_warm_bg, daemon=True).start()

        # ★ #1279 节点⑨：拍照深识队列循环（30s 一轮，无理解通道时静默等待）
        try:
            from src.vision import tasks as _vt
            _vt.ensure_loop()
        except Exception as _te:
            logger.warning(f"[Startup] 深识队列启动失败（不影响使用）: {_te}")
    except Exception as e:
        logger.warning(f"[Startup] 视觉子系统初始化告警（不阻塞）: {e}")

    # ★ #1362（2026-11-02）: 默认密码提示改为条件打印——原先是 bat 固定横幅，
    # 已改密用户每次启动还看到"Default password: harven2026"属误导死文案
    try:
        from src.core.security_guard import get_admin_password
        if get_admin_password() == 'harven2026':
            print('\nDefault password: harven2026 - please change it after first login.\n')
    except Exception:
        pass

    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
