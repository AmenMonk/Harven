# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
B站视频上传管理器（Biliup Manager）

支持两种上传模式：
  Mode 1 — B站 HTTP API 直传（cookie 认证，默认）
  Mode 2 — biliup CLI 子进程（需 pip install biliup，可选）

认证方式：
  - Cookie 模式：从浏览器获取 SESSDATA / bili_jct / DedeUserID
  - 扫码模式：生成二维码让用户扫码登录（TODO）

上传流程：
  1. 用户配置 Cookie 凭证 → 存入 config/bilibili_auth.json
  2. 选择视频 + 填写元数据（标题/描述/标签/分类/版权）
  3. 预上传 → 获取分片上传 URL
  4. 分片上传视频文件（带进度回调）
  5. 提交元数据 → 发布稿件
  6. 返回 BV 号 / 稿件链接
"""

import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
AUTH_FILE = PROJECT_ROOT / "config" / "bilibili_auth.json"
UPLOAD_LOG_DIR = PROJECT_ROOT / "data" / "logs" / "bilibili"
MEDIA_DIR = PROJECT_ROOT / "data" / "media"

AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
UPLOAD_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════

BILIBILI_API = {
    "preupload": "https://member.bilibili.com/x/vu/web/preupload",
    "upload_chunk": "",  # 由 preupload 返回
    "submit": "https://member.bilibili.com/x/vu/web/add",
    "cover_upload": "https://member.bilibili.com/x/vu/web/cover/up",
    "user_info": "https://api.bilibili.com/x/space/myinfo",
    "login_qr": "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
    "login_poll": "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
    "nav": "https://api.bilibili.com/x/web-interface/nav",
}

# B站视频分类 tid
BILIBILI_CATEGORIES = {
    "动画": 1, "音乐": 3, "游戏": 4, "娱乐": 5, "电视剧": 11,
    "番剧": 13, "电影": 23, "知识": 36, "科技": 188, "运动": 234,
    "汽车": 223, "生活": 160, "美食": 211, "动物圈": 217, "鬼畜": 119,
    "时尚": 155, "资讯": 202, "纪录片": 177, "影视": 181, "舞蹈": 129,
    "国创": 167, "数码": 207,
}

# 视频格式白名单（B站支持的上传格式）
SUPPORTED_FORMATS = {".mp4", ".flv", ".avi", ".wmv", ".mov", ".webm", ".mkv", ".m4v"}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10GB (B站限制)
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB 分片

# 稿件状态
DRAFT_STATUS = {"open": 1, "close": 0}


# ═══════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════

class UploadStatus(Enum):
    PENDING = "pending"         # 等待上传
    PREUPLOADING = "preuploading"  # 预上传中
    UPLOADING = "uploading"     # 上传中
    SUBMITTING = "submitting"   # 提交元数据中
    COMPLETED = "completed"     # 上传完成
    FAILED = "failed"           # 上传失败
    CANCELLED = "cancelled"     # 已取消


@dataclass
class BilibiliCredentials:
    """B站认证凭证"""
    sessdata: str = ""
    bili_jct: str = ""
    dedeuserid: str = ""
    buvid3: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    uid: str = ""
    uname: str = ""
    updated_at: str = ""

    def is_valid(self) -> bool:
        """检查凭证是否有效（至少需要 SESSDATA + bili_jct）"""
        return bool(self.sessdata and self.bili_jct)

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return time.time() > self.expires_at

    def to_cookie_string(self) -> str:
        """生成 Cookie 字符串"""
        parts = []
        if self.sessdata:
            parts.append(f"SESSDATA={self.sessdata}")
        if self.bili_jct:
            parts.append(f"bili_jct={self.bili_jct}")
        if self.dedeuserid:
            parts.append(f"DedeUserID={self.dedeuserid}")
        if self.buvid3:
            parts.append(f"buvid3={self.buvid3}")
        return "; ".join(parts)

    def to_cookie_dict(self) -> dict:
        """生成 Cookie 字典（requests 用）"""
        c = {}
        if self.sessdata:
            c["SESSDATA"] = self.sessdata
        if self.bili_jct:
            c["bili_jct"] = self.bili_jct
        if self.dedeuserid:
            c["DedeUserID"] = self.dedeuserid
        if self.buvid3:
            c["buvid3"] = self.buvid3
        return c


@dataclass
class UploadTask:
    """上传任务"""
    task_id: str = ""
    file_path: str = ""
    file_size: int = 0
    title: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    tid: int = 188  # 默认科技
    cover_path: str = ""
    copyright: int = 2  # 1=自制 2=转载
    source: str = ""    # 转载来源
    no_reprint: int = 0
    status: UploadStatus = UploadStatus.PENDING
    progress: float = 0.0  # 0-100
    uploaded_bytes: int = 0
    bvid: str = ""
    aid: int = 0
    error_msg: str = ""
    created_at: str = ""
    completed_at: str = ""
    thread_id: Optional[str] = None


# ═══════════════════════════════════════════════════
# 凭证管理
# ═══════════════════════════════════════════════════

def load_credentials() -> BilibiliCredentials:
    """加载保存的凭证"""
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            return BilibiliCredentials(**{k: data.get(k, "") for k in BilibiliCredentials.__dataclass_fields__})
        except Exception as _e: logger.debug(f"[biliup_manager] 忽略: {_e}")
    return BilibiliCredentials()


def save_credentials(creds: BilibiliCredentials) -> bool:
    """保存凭证到文件"""
    try:
        creds.updated_at = datetime.now().isoformat()
        d = {k: getattr(creds, k) for k in BilibiliCredentials.__dataclass_fields__}
        AUTH_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(AUTH_FILE, 0o600)  # 只有 owner 可读写
        return True
    except Exception as e:
        logger.error(f"保存凭证失败: {e}")
        return False


def validate_credentials(creds: BilibiliCredentials) -> dict:
    """验证凭证是否可用（调用 B站 API 检查登录状态）"""
    if not creds.is_valid():
        return {"success": False, "error": "凭证不完整：需要 SESSDATA + bili_jct"}

    try:
        resp = requests.get(
            BILIBILI_API["nav"],
            cookies=creds.to_cookie_dict(),
            headers=_default_headers(),
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0 and data.get("data", {}).get("isLogin"):
            user = data["data"]
            creds.uid = str(user.get("mid", ""))
            creds.uname = user.get("uname", "")
            save_credentials(creds)
            return {
                "success": True,
                "uid": creds.uid,
                "uname": creds.uname,
                "message": f"已登录: {creds.uname}",
            }
        else:
            return {"success": False, "error": f"凭证已过期或无效: {data.get('message', '未知错误')}"}
    except Exception as e:
        return {"success": False, "error": f"网络请求失败: {e}"}


# ═══════════════════════════════════════════════════
# 视频验证
# ═══════════════════════════════════════════════════

def validate_video(file_path: str) -> dict:
    """验证视频文件是否可上传"""
    path = Path(file_path)

    if not path.exists():
        return {"valid": False, "error": "文件不存在"}
    if not path.is_file():
        return {"valid": False, "error": "路径不是文件"}

    ext = path.suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        return {"valid": False, "error": f"不支持的格式: {ext}，支持: {', '.join(SUPPORTED_FORMATS)}"}

    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        return {"valid": False, "error": f"文件过大: {size / 1024 / 1024 / 1024:.1f}GB > 10GB 限制"}
    if size < 1024:
        return {"valid": False, "error": "文件太小 (< 1KB)"}

    # 用 ffprobe 获取视频信息
    info = _get_video_info(file_path)

    return {
        "valid": True,
        "file_name": path.name,
        "file_size": size,
        "file_size_mb": round(size / 1024 / 1024, 1),
        "format": ext.lstrip("."),
        **info,
    }


def _get_video_info(file_path: str) -> dict:
    """获取视频信息（ffprobe）"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"duration": 0, "width": 0, "height": 0, "codec": "unknown"}
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        return {
            "duration": round(float(fmt.get("duration", 0)), 1),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "codec": video_stream.get("codec_name", "unknown"),
            "bitrate": int(fmt.get("bit_rate", 0)) // 1000,  # kbps
            "size": int(fmt.get("size", 0)),
        }
    except Exception:
        return {"duration": 0, "width": 0, "height": 0, "codec": "unknown"}


# ═══════════════════════════════════════════════════
# 上传核心
# ═══════════════════════════════════════════════════

# 全局上传任务存储（内存，重启丢失）
_upload_tasks: Dict[str, UploadTask] = {}
_upload_lock = threading.Lock()


def _default_headers(extra: dict = None) -> dict:
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://member.bilibili.com/",
        "Origin": "https://member.bilibili.com",
    }
    if extra:
        h.update(extra)
    return h


def create_upload_task(
    file_path: str,
    title: str,
    description: str = "",
    tags: List[str] = None,
    tid: int = 188,
    cover_path: str = "",
    copyright: int = 2,
    source: str = "",
) -> dict:
    """创建一个上传任务并验证"""
    # 验证视频
    validation = validate_video(file_path)
    if not validation["valid"]:
        return {"success": False, "error": validation["error"]}

    # 验证标题
    title = title.strip()
    if not title:
        return {"success": False, "error": "标题不能为空"}
    if len(title) > 80:
        return {"success": False, "error": "标题不能超过80字"}

    task_id = f"bili_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    task = UploadTask(
        task_id=task_id,
        file_path=file_path,
        file_size=validation["file_size"],
        title=title,
        description=description[:2000],  # B站限制2000字
        tags=tags or [],
        tid=tid,
        cover_path=cover_path,
        copyright=copyright,
        source=source if copyright == 2 else "",
        created_at=datetime.now().isoformat(),
    )

    with _upload_lock:
        _upload_tasks[task_id] = task

    logger.info(f"[BiliUp] 创建上传任务: {task_id} - {title}")
    return {
        "success": True,
        "task_id": task_id,
        "file_name": validation["file_name"],
        "file_size_mb": validation["file_size_mb"],
        "video_info": {
            k: v for k, v in validation.items()
            if k in ("duration", "width", "height", "codec")
        },
    }


def start_upload(task_id: str, credentials: BilibiliCredentials = None,
                 progress_callback: Callable = None) -> dict:
    """开始上传任务（异步）"""
    with _upload_lock:
        task = _upload_tasks.get(task_id)
        if not task:
            return {"success": False, "error": "任务不存在"}

    if credentials is None:
        credentials = load_credentials()

    if not credentials.is_valid():
        return {"success": False, "error": "未配置B站凭证，请先在设置中配置"}

    # 在后台线程执行上传
    thread = threading.Thread(
        target=_do_upload,
        args=(task, credentials, progress_callback),
        daemon=True,
    )
    thread.start()
    task.thread_id = thread.ident

    return {"success": True, "message": "上传已开始", "task_id": task_id}


def _do_upload(task: UploadTask, creds: BilibiliCredentials,
               progress_callback: Callable = None):
    """执行上传流程"""
    session = requests.Session()
    session.cookies.update(creds.to_cookie_dict())
    session.headers.update(_default_headers())

    def _update_progress(progress: float, status: UploadStatus, msg: str = ""):
        task.progress = progress
        task.status = status
        if msg:
            task.error_msg = msg
        if progress_callback:
            try:
                progress_callback(task.task_id, progress, status.value, msg)
            except Exception as _e: logger.debug(f"[biliup_manager] 忽略: {_e}")

    try:
        # ===== Step 1: 预上传 =====
        _update_progress(5, UploadStatus.PREUPLOADING, "预上传中...")
        preupload_result = _bilibili_preupload(session, task)
        if not preupload_result.get("success"):
            _update_progress(0, UploadStatus.FAILED, preupload_result.get("error", "预上传失败"))
            return
        upload_url = preupload_result["upload_url"]
        upload_auth = preupload_result.get("auth", "")
        chunk_count = preupload_result.get("chunks", 1)

        # ===== Step 2: 分片上传 =====
        _update_progress(10, UploadStatus.UPLOADING, f"上传中 (0/{chunk_count})...")
        _bilibili_chunked_upload(
            session, task, upload_url, upload_auth, chunk_count, _update_progress
        )

        # ===== Step 3: 上传封面（如有） =====
        cover_url = ""
        if task.cover_path and Path(task.cover_path).exists():
            cover_url = _upload_cover(session, task.cover_path)

        # ===== Step 4: 提交稿件 =====
        _update_progress(90, UploadStatus.SUBMITTING, "提交稿件中...")
        submit_result = _bilibili_submit(session, task, cover_url)
        if not submit_result.get("success"):
            _update_progress(0, UploadStatus.FAILED, submit_result.get("error", "提交失败"))
            return

        task.bvid = submit_result.get("bvid", "")
        task.aid = submit_result.get("aid", 0)
        task.completed_at = datetime.now().isoformat()
        _update_progress(100, UploadStatus.COMPLETED, f"上传完成: {task.bvid}")
        _save_upload_log(task)

    except Exception as e:
        logger.exception(f"[BiliUp] 上传异常: {e}")
        _update_progress(0, UploadStatus.FAILED, str(e))


def _bilibili_preupload(session: requests.Session, task: UploadTask) -> dict:
    """B站预上传：获取分片上传 URL"""
    file_name = Path(task.file_path).name
    file_size = task.file_size

    # 计算文件 SHA1（前1MB + 后1MB）
    sha1_hash = _calc_file_partial_sha1(task.file_path)

    params = {
        "name": file_name,
        "size": file_size,
        "r": "upos",
        "profile": "ugcupos/bup",
        "ssl": 0,
        "version": "2.14.0",
        "build": 2140000,
        "upcdn": "bda2",
    }

    try:
        resp = session.get(
            BILIBILI_API["preupload"],
            params=params,
            timeout=15,
        )
        result = resp.json()
        if result.get("OK") != 1:
            return {"success": False, "error": f"预上传失败: {result}"}

        # 尝试不同字段名
        upload_url = result.get("endpoint") or result.get("url") or result.get("upos_uri", "")
        auth = result.get("upos_auth", "") or result.get("auth", "")

        if not upload_url:
            upload_url = f"https:{result.get('bili_filename', '')}" if result.get("bili_filename") else ""

        # 分片数量
        chunk_size = result.get("chunk_size", CHUNK_SIZE)
        chunks = (file_size + chunk_size - 1) // chunk_size if chunk_size > 0 else 1

        return {
            "success": True,
            "upload_url": upload_url,
            "auth": auth,
            "chunk_size": chunk_size,
            "chunks": chunks,
        }
    except Exception as e:
        return {"success": False, "error": f"预上传网络错误: {e}"}


def _bilibili_chunked_upload(
    session: requests.Session,
    task: UploadTask,
    upload_url: str,
    auth: str,
    chunk_count: int,
    progress_handler: Callable,
):
    """分片上传视频文件"""
    chunk_size = CHUNK_SIZE
    uploaded = 0
    file_size = task.file_size

    with open(task.file_path, "rb") as f:
        for i in range(chunk_count):
            # 检查任务是否被取消
            if task.status == UploadStatus.CANCELLED:
                return

            chunk_data = f.read(chunk_size)
            start = i * chunk_size
            end = min(start + len(chunk_data), file_size)

            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(chunk_data)),
            }
            if auth:
                headers["X-Upos-Auth"] = auth

            params = {
                "partNumber": i + 1,
                "uploadId": "",
                "chunk": i,
                "chunks": chunk_count,
                "size": len(chunk_data),
                "start": start,
                "end": end,
                "total": file_size,
            }

            # 构建分片上传 URL
            chunk_url = f"{upload_url}?{urlencode(params)}"

            retries = 3
            for attempt in range(retries):
                try:
                    resp = session.put(chunk_url, data=chunk_data, headers=headers, timeout=120)
                    if resp.status_code in (200, 201, 204):
                        break
                    if attempt < retries - 1:
                        time.sleep(2 * (attempt + 1))
                except Exception as e:
                    if attempt == retries - 1:
                        raise e
                    time.sleep(2 * (attempt + 1))

            uploaded += len(chunk_data)
            progress_pct = 10 + int((uploaded / file_size) * 75)  # 10% → 85%
            progress_handler(
                min(progress_pct, 85),
                UploadStatus.UPLOADING,
                f"上传中 ({i + 1}/{chunk_count})... {uploaded / 1024 / 1024:.0f}/{file_size / 1024 / 1024:.0f}MB",
            )


def _upload_cover(session: requests.Session, cover_path: str) -> str:
    """上传封面图，返回封面 URL"""
    try:
        with open(cover_path, "rb") as f:
            cover_data = f.read()
        resp = session.post(
            BILIBILI_API["cover_upload"],
            data={"cover": cover_data},
            files={"file": (Path(cover_path).name, cover_data, "image/jpeg")},
            timeout=30,
        )
        result = resp.json()
        if result.get("code") == 0:
            return result.get("data", {}).get("url", "")
    except Exception as e:
        logger.warning(f"[BiliUp] 封面上传失败: {e}")
    return ""


def _bilibili_submit(session: requests.Session, task: UploadTask, cover_url: str = "") -> dict:
    """提交稿件元数据"""
    payload = {
        "copyright": task.copyright,
        "source": task.source if task.copyright == 2 else "",
        "title": task.title,
        "tid": task.tid,
        "tag": ",".join(task.tags[:10]) if task.tags else "",
        "desc": task.description,
        "no_reprint": task.no_reprint,
        "cover": cover_url,
        "videos": json.dumps([{
            "title": task.title,
            "filename": Path(task.file_path).name,
            "desc": task.description,
        }]),
        "desc_format_id": 0,
        "dynamic": "",
        "subtitle": {"open": 0, "lan": ""},
        "dtime": 0,
        "mission_id": 0,
        "interactive": 0,
    }

    try:
        resp = session.post(
            BILIBILI_API["submit"],
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        result = resp.json()
        if result.get("code") == 0 and result.get("data"):
            data = result["data"]
            return {
                "success": True,
                "aid": data.get("aid", 0),
                "bvid": data.get("bvid", ""),
                "message": f"发布成功 BV号: {data.get('bvid', 'N/A')}",
            }
        return {"success": False, "error": f"提交失败: {result.get('message', '未知错误')}"}
    except Exception as e:
        return {"success": False, "error": f"提交网络错误: {e}"}


def _calc_file_partial_sha1(file_path: str, head_size: int = 1024 * 1024,
                             tail_size: int = 1024 * 1024) -> str:
    """计算文件部分 SHA1（首尾各1MB）"""
    file_size = os.path.getsize(file_path)
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        sha1.update(f.read(min(head_size, file_size)))
        if file_size > head_size + tail_size:
            f.seek(file_size - tail_size)
            sha1.update(f.read(tail_size))
    return sha1.hexdigest()


def _save_upload_log(task: UploadTask):
    """保存上传记录"""
    log_entry = {
        "task_id": task.task_id,
        "title": task.title,
        "file_name": Path(task.file_path).name,
        "file_size_mb": round(task.file_size / 1024 / 1024, 1),
        "bvid": task.bvid,
        "aid": task.aid,
        "tags": task.tags,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }
    log_file = UPLOAD_LOG_DIR / f"{task.task_id}.json"
    log_file.write_text(json.dumps(log_entry, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════

def cancel_upload(task_id: str) -> dict:
    with _upload_lock:
        task = _upload_tasks.get(task_id)
        if not task:
            return {"success": False, "error": "任务不存在"}
        task.status = UploadStatus.CANCELLED
        return {"success": True, "message": "已取消"}


def get_task_status(task_id: str) -> Optional[dict]:
    with _upload_lock:
        task = _upload_tasks.get(task_id)
        if not task:
            return None
        return {
            "task_id": task.task_id,
            "title": task.title,
            "file_name": Path(task.file_path).name,
            "file_size_mb": round(task.file_size / 1024 / 1024, 1),
            "status": task.status.value,
            "progress": task.progress,
            "bvid": task.bvid,
            "aid": task.aid,
            "error_msg": task.error_msg,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
        }


def list_tasks(limit: int = 20) -> List[dict]:
    with _upload_lock:
        tasks = sorted(_upload_tasks.values(), key=lambda t: t.created_at, reverse=True)
        return [get_task_status(t.task_id) for t in tasks[:limit] if get_task_status(t.task_id)]


def get_categories() -> dict:
    return BILIBILI_CATEGORIES


def get_upload_history(limit: int = 20) -> List[dict]:
    """读取上传历史记录"""
    history = []
    if not UPLOAD_LOG_DIR.exists():
        return history
    for log_file in sorted(UPLOAD_LOG_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            history.append(json.loads(log_file.read_text(encoding="utf-8")))
        except Exception as _e: logger.debug(f"[biliup_manager] 忽略: {_e}")
    return history


def install_biliup_check() -> dict:
    """检查 biliup CLI 是否可用"""
    biliup_path = shutil.which("biliup")
    if biliup_path:
        try:
            result = subprocess.run([biliup_path, "--version"], capture_output=True, text=True, timeout=5)
            return {"installed": True, "path": biliup_path, "version": result.stdout.strip()}
        except Exception:
            return {"installed": True, "path": biliup_path, "version": "unknown"}
    return {"installed": False, "message": "biliup 未安装，运行: pip install biliup"}
