# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - Skills System (Markdown-based)
技能系统：用户可以用 Markdown 写技能文件，AI 自动加载并应用

技能文件格式（存放在 data/skills/ 目录）:
---
name: 写作助手
description: 帮助用户改善写作
triggers:
  - 帮我写
  - 改写
  - 润色
author: user
version: 1.0
---
你是一个专业的写作助手。收到写作请求时，请以优美、专业的语言提供帮助。
每次回复都要提供多个版本供选择。
"""

import re
import json
import yaml
import time
import threading
from pathlib import Path
from datetime import datetime
import logging

# 热更新依赖为可选，安装 watchdog 后启用
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = object  # 占位基类

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Skill 热更新监控
# ═══════════════════════════════════════════════════════════════════════

class SkillHotReloader(FileSystemEventHandler):
    """监控 skills 目录，文件变化时自动重新加载技能"""

    def __init__(self, skills_manager: 'SkillsManager', debounce_seconds: float = 1.0):
        self._skills_manager = skills_manager
        self._debounce_seconds = debounce_seconds
        self._last_reload_time = 0.0
        self._pending_reload = False
        self._lock = threading.Lock()
        self._observer = None
        self._monitor_thread = None
        self._running = False

    def start(self, skills_dir: Path):
        """启动文件监控"""
        if self._running:
            return
        self._running = True
        self._skills_dir = str(skills_dir)
        self._observer = Observer()
        self._observer.schedule(self, str(skills_dir), recursive=True)
        self._observer.start()
        logger.info(f'[SkillHotReloader] 已启动，监控目录: {skills_dir}')

    def stop(self):
        """停止文件监控"""
        if not self._running:
            return
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        logger.info('[SkillHotReloader] 已停止')

    def on_modified(self, event):
        if event.is_directory:
            return
        if not str(event.src_path).endswith('.md'):
            return
        self._debounced_reload()

    def on_created(self, event):
        if event.is_directory:
            return
        if not str(event.src_path).endswith('.md'):
            return
        self._debounced_reload()

    def on_deleted(self, event):
        if event.is_directory:
            return
        if not str(event.src_path).endswith('.md'):
            return
        self._debounced_reload()

    def _debounced_reload(self):
        """防抖重新加载（避免短时间内多次触发）"""
        now = time.time()
        with self._lock:
            self._last_reload_time = now
            if not self._pending_reload:
                self._pending_reload = True
                threading.Timer(self._debounce_seconds, self._do_reload).start()

    def _do_reload(self):
        with self._lock:
            self._pending_reload = False
        try:
            logger.info('[SkillHotReloader] 检测到技能文件变化，重新加载...')
            self._skills_manager.reload()
            logger.info('[SkillHotReloader] 技能重新加载完成')
        except Exception as e:
            logger.error(f'[SkillHotReloader] 重新加载失败: {e}')


SKILLS_DIR = Path(__file__).parent.parent.parent / 'data' / 'skills'
SKILLS_CONFIG = Path(__file__).parent.parent.parent / 'data' / 'skills_config.json'

# 内置技能模板（用于展示和初始化）
BUILTIN_SKILLS = [
    {
        'id': 'builtin_writer',
        'name': '✍️ 写作助手',
        'description': '帮你改善写作、润色文字、生成各类文章',
        'triggers': ['帮我写', '写一篇', '写一首', '改写', '润色', '文章', '作文'],
        'prompt': '你是一个专业的写作助手。收到写作请求时，用优美专业的语言提供帮助，必要时提供多个版本供选择。',
        'builtin': True,
        'enabled': True,
    },
    {
        'id': 'builtin_code',
        'name': '💻 编程助手',
        'description': '代码编写、调试、解释、优化',
        'triggers': ['帮我写代码', '写一段', '调试', 'python', 'javascript', 'bug', '报错', '代码'],
        'prompt': '你是一个资深的全栈工程师。提供清晰、高效、有注释的代码，并解释关键逻辑。',
        'builtin': True,
        'enabled': True,
    },
    {
        'id': 'builtin_translate',
        'name': '🌐 翻译助手',
        'description': '多语言互译，精准专业',
        'triggers': ['翻译', '帮我翻', 'translate', '英文', '中文', '日文'],
        'prompt': '你是一个专业翻译，精通中、英、日、韩等多种语言。翻译时保持原文语气和风格，对专业术语给出说明。',
        'builtin': True,
        'enabled': True,
    },
    {
        'id': 'builtin_summarize',
        'name': '📝 总结助手',
        'description': '快速提炼文本核心，生成摘要',
        'triggers': ['总结', '摘要', '提炼', '概括', '帮我看看这段'],
        'prompt': '你是一个擅长信息提炼的助手。将长文本总结为清晰的要点，先给出3-5条核心要点，再提供完整摘要。',
        'builtin': True,
        'enabled': True,
    },
    {
        'id': 'builtin_math',
        'name': '🔢 数学助手',
        'description': '数学解题、公式推导、数据分析',
        'triggers': ['计算', '解题', '数学', '公式', '方程', '积分', '导数'],
        'prompt': '你是一个数学专家。解题时展示完整步骤，解释每一步的数学原理，并用简单语言让非专业人士也能理解。',
        'builtin': True,
        'enabled': True,
    },
]


class Skill:
    """技能对象"""

    def __init__(self, skill_id, name, description, triggers, prompt,
                 builtin=False, enabled=True, author='user', version='1.0',
                 file_path=None, created_at=None, has_scripts=False,
                 scripts_config=None, platforms=None):
        self.id = skill_id
        self.name = name
        self.description = description
        self.triggers = triggers  # list of str
        self.prompt = prompt
        self.builtin = builtin
        self.enabled = enabled
        self.author = author
        self.version = version
        self.file_path = file_path
        self.created_at = created_at or datetime.now().isoformat()
        self.has_scripts = has_scripts  # ★ 是否有可执行脚本
        self.scripts_config = scripts_config or {}  # ★ 脚本配置 {entry, actions, allowed_modules, timeout}
        # ★ 2026-08-17: 跨平台支持 — platforms: [windows, linux, macos] 或 ['all']
        self.platforms = platforms or ['all']

    @property
    def is_intermediate(self) -> bool:
        """是否为中级Skill（有可执行脚本）"""
        return self.has_scripts

    def to_dict(self):
        d = {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'triggers': self.triggers,
            'prompt': self.prompt,
            'builtin': self.builtin,
            'enabled': self.enabled,
            'author': self.author,
            'version': self.version,
            'file_path': self.file_path,
            'created_at': self.created_at,
            'has_scripts': self.has_scripts,
            'platforms': self.platforms,  # ★ 2026-08-17
        }
        if self.scripts_config:
            d['scripts_config'] = self.scripts_config
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(
            skill_id=d.get('id', ''),
            name=d.get('name', ''),
            description=d.get('description', ''),
            triggers=d.get('triggers', []),
            prompt=d.get('prompt', ''),
            builtin=d.get('builtin', False),
            enabled=d.get('enabled', True),
            author=d.get('author', 'user'),
            version=d.get('version', '1.0'),
            file_path=d.get('file_path'),
            created_at=d.get('created_at'),
            platforms=d.get('platforms', ['all']),
        )


# ═══════════════════════════════════════════════════
# 格式检测：自动区分 SKILL.md 标准 vs Harven 自有格式
# ═══════════════════════════════════════════════════
# SKILL.md 标准的特征字段（agentskills.io）：
#   license, compatibility, metadata, metadata.openclaw, ...
# Harven 自有格式的特征字段：
#   triggers, author
#
# 策略：如果 YAML frontmatter 包含 license/compatibility/metadata
# → 按 SKILL.md 标准解析；否则按 Harven 格式解析。

_SKILLMD_STANDARD_KEYS = {'license', 'compatibility', 'metadata'}

def _detect_format(meta: dict) -> str:
    """检测技能文件格式类型"""
    if any(k in meta for k in _SKILLMD_STANDARD_KEYS):
        return 'skillmd_standard'
    return 'harven'


def _generate_triggers_from_description(description: str, name: str = '') -> list[str]:
    """从 description 提取关键词作为 fallback triggers"""
    triggers = []
    # 分词提取中文关键词
    try:
        import jieba
        words = jieba.cut(description)
        meaningful = [w for w in words if len(w) >= 2 and not w.isspace()]
        triggers = list(dict.fromkeys(meaningful))[:10]  # 去重，最多10个
    except ImportError as _e:
        logger.debug(f"[skills_manager] 忽略: {_e}")
    # 如果 jieba 不可用，用简单空格/标点分词
    if not triggers:
        parts = re.split(r'[，,。；;、\s]+', description)
        triggers = [p.strip() for p in parts if len(p.strip()) >= 2][:10]
    # 加入名字作为触发词
    if name:
        triggers.append(name)
    return triggers


def _parse_skill_file(file_path: Path) -> Skill | None:
    """解析 Markdown 技能文件（兼容 SKILL.md 标准 + Harven 自有格式）"""
    try:
        content = file_path.read_text(encoding='utf-8').strip()
        # 提取 YAML front matter
        meta = {}
        body = content
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
        if fm_match:
            try:
                meta = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                meta = {}
            body = fm_match.group(2).strip()

        fmt = _detect_format(meta)

        skill_id = 'file_' + file_path.stem
        name = meta.get('name', file_path.stem)
        description = meta.get('description', '')

        if fmt == 'skillmd_standard':
            # ── SKILL.md 标准格式 ──
            # triggers: 从 description 自动提取（标准没有 triggers 字段）
            triggers = meta.get('triggers', None)
            if not triggers:
                triggers = _generate_triggers_from_description(description, name)

            # license/compatibility/metadata → 存储到 prompt 头部注释
            license_info = meta.get('license', '')
            compatibility = meta.get('compatibility', '')
            metadata_extra = meta.get('metadata', {})

            # 构建元数据注释块（注入到 body 开头，供 AI 理解 Skill 条款）
            meta_comment = []
            if license_info:
                meta_comment.append(f'<!-- License: {license_info} -->')
            if compatibility:
                meta_comment.append(f'<!-- Compatibility: {compatibility} -->')
            if metadata_extra:
                meta_comment.append(f'<!-- Metadata: {json.dumps(metadata_extra, ensure_ascii=False)} -->')
            if meta_comment:
                body = '\n'.join(meta_comment) + '\n' + body

            author = meta.get('author', 'community')
            version = str(meta.get('version', '1.0'))
            logger.info(f'[SkillsManager] 加载 SKILL.md 标准技能: {name} (license={license_info})')
        else:
            # ── Harven 自有格式 ──
            triggers = meta.get('triggers', [])
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(',')]
            author = meta.get('author', 'user')
            version = str(meta.get('version', '1.0'))

        # ★ 检测可执行脚本
        scripts_config = meta.get('scripts', None)
        has_scripts = False
        # ★ 2026-08-17: 跨平台支持 — 读取 platforms 字段
        platforms = meta.get('platforms', ['all'])
        if isinstance(platforms, str):
            platforms = [p.strip() for p in platforms.split(',')]
        if scripts_config and isinstance(scripts_config, dict):
            has_scripts = True
        elif file_path.parent.name != 'skills' and file_path.parent != SKILLS_DIR:
            # 子目录技能 → 检查是否有 scripts/ 或 .py 文件
            skill_dir = file_path.parent
            has_scripts_dir = (skill_dir / 'scripts').is_dir()
            has_py_files = any(skill_dir.glob('*.py'))
            if has_scripts_dir or has_py_files:
                has_scripts = True
                scripts_config = scripts_config or {
                    'entry': 'scripts/main.py' if has_scripts_dir else None,
                    'description': '可执行脚本技能',
                }

        return Skill(
            skill_id=skill_id,
            name=name,
            description=description,
            triggers=triggers,
            prompt=body,
            builtin=False,
            enabled=True,
            author=author,
            version=version,
            file_path=str(file_path),
            has_scripts=has_scripts,
            scripts_config=scripts_config,
            platforms=platforms,
        )
    except Exception as e:
        logger.error(f'[SkillsManager] Failed to parse {file_path}: {e}')
        return None


class SkillsManager:
    """技能管理器（单例）"""

    def __init__(self, hot_reload: bool = None):
        self._lock = threading.Lock()
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._skills: dict[str, Skill] = {}
        self._config: dict = {}  # 保存 enabled 状态
        self._hot_reloader = None
        self._last_matched_skill: Skill | None = None  # ★ 供engine获取匹配的技能详情
        self._load_config()
        self._load_all_skills()
        # 启动热更新监控
        # 默认：watchdog 可用时启用，不可用时静默跳过
        if hot_reload is None:
            hot_reload = _WATCHDOG_AVAILABLE
        if hot_reload and _WATCHDOG_AVAILABLE:
            self._start_hot_reload()
        elif hot_reload and not _WATCHDOG_AVAILABLE:
            logger.info('[SkillsManager] watchdog 未安装，热更新未启用（pip install watchdog 可启用）')

    def _start_hot_reload(self):
        """启动热更新监控"""
        try:
            self._hot_reloader = SkillHotReloader(self)
            self._hot_reloader.start(SKILLS_DIR)
        except Exception as e:
            logger.warning(f'[SkillsManager] 热更新启动失败: {e}')

    def stop_hot_reload(self):
        """停止热更新监控"""
        if self._hot_reloader:
            self._hot_reloader.stop()
            self._hot_reloader = None

    # -------- Config I/O --------

    def _load_config(self):
        if SKILLS_CONFIG.exists():
            try:
                self._config = json.loads(SKILLS_CONFIG.read_text(encoding='utf-8'))
            except Exception:
                self._config = {}

    def _save_config(self):
        data = {sid: {'enabled': s.enabled} for sid, s in self._skills.items()}
        SKILLS_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    # -------- Load --------

    def _load_all_skills(self):
        with self._lock:
            self._skills = {}
            # 1. 内置技能
            for b in BUILTIN_SKILLS:
                s = Skill.from_dict(b)
                # 读取保存的 enabled 状态
                if s.id in self._config:
                    s.enabled = self._config[s.id].get('enabled', True)
                self._skills[s.id] = s
            # 2. 用户自定义 Markdown 技能文件
            # 2a. Flat 文件（Harven 自有格式）
            for md in SKILLS_DIR.glob('*.md'):
                s = _parse_skill_file(md)
                if s:
                    if s.id in self._config:
                        s.enabled = self._config[s.id].get('enabled', True)
                    self._skills[s.id] = s
            # 2b. 目录形式技能（SKILL.md 标准：有 SKILL.md 的子目录）
            for subdir in SKILLS_DIR.iterdir():
                if not subdir.is_dir():
                    continue
                skill_md = subdir / 'SKILL.md'
                if skill_md.exists():
                    s = _parse_skill_file(skill_md)
                    if s:
                        # 用目录名作为 id（更可读）
                        s.id = 'file_' + subdir.name
                        if s.id in self._config:
                            s.enabled = self._config[s.id].get('enabled', True)
                        self._skills[s.id] = s
                    # 另外也加载目录下的 .skill.md 文件
                    for alt_md in subdir.glob('*.skill.md'):
                        s2 = _parse_skill_file(alt_md)
                        if s2:
                            s2.id = 'file_' + subdir.name + '_' + alt_md.stem.replace('.skill', '')
                            if s2.id in self._config:
                                s2.enabled = self._config[s2.id].get('enabled', True)
                            self._skills[s2.id] = s2

    def reload(self):
        self._load_all_skills()

    # -------- CRUD --------

    def list_skills(self) -> list[dict]:
        return [s.to_dict() for s in self._skills.values()]

    def get_skill(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def create_skill(self, name: str, description: str, triggers: list,
                     prompt: str, author='user', version='1.0') -> dict:
        """创建新技能（写入 Markdown 文件）"""
        # 生成文件名
        safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', name)[:20]
        file_path = SKILLS_DIR / f'{safe_name}.md'
        counter = 1
        while file_path.exists():
            file_path = SKILLS_DIR / f'{safe_name}_{counter}.md'
            counter += 1

        # 写 Markdown
        front_matter = {
            'name': name,
            'description': description,
            'triggers': triggers,
            'author': author,
            'version': version,
        }
        content = f'---\n{yaml.dump(front_matter, allow_unicode=True, default_flow_style=False)}---\n{prompt}\n'
        file_path.write_text(content, encoding='utf-8')

        # 加载
        skill = _parse_skill_file(file_path)
        if skill:
            with self._lock:
                self._skills[skill.id] = skill
            self._save_config()
            return {'success': True, 'skill': skill.to_dict()}
        return {'success': False, 'error': '技能文件写入失败'}

    def update_skill(self, skill_id: str, **kwargs) -> dict:
        """更新技能（仅用户自定义技能可编辑内容）"""
        skill = self._skills.get(skill_id)
        if not skill:
            return {'success': False, 'error': '技能不存在'}
        for k, v in kwargs.items():
            if hasattr(skill, k):
                setattr(skill, k, v)
        # 如果有文件则重写
        if skill.file_path and Path(skill.file_path).exists():
            front_matter = {
                'name': skill.name,
                'description': skill.description,
                'triggers': skill.triggers,
                'author': skill.author,
                'version': skill.version,
            }
            content = f'---\n{yaml.dump(front_matter, allow_unicode=True, default_flow_style=False)}---\n{skill.prompt}\n'
            Path(skill.file_path).write_text(content, encoding='utf-8')
        self._save_config()
        return {'success': True, 'skill': skill.to_dict()}

    def delete_skill(self, skill_id: str) -> dict:
        """删除技能（内置技能只能禁用不能删除）"""
        skill = self._skills.get(skill_id)
        if not skill:
            return {'success': False, 'error': '技能不存在'}
        if skill.builtin:
            return {'success': False, 'error': '内置技能不能删除，只能禁用'}
        # 删除文件
        if skill.file_path and Path(skill.file_path).exists():
            Path(skill.file_path).unlink()
        with self._lock:
            del self._skills[skill_id]
        self._save_config()
        return {'success': True}

    def toggle_skill(self, skill_id: str, enabled: bool) -> dict:
        """启用/禁用技能"""
        skill = self._skills.get(skill_id)
        if not skill:
            return {'success': False, 'error': '技能不存在'}
        skill.enabled = enabled
        self._save_config()
        return {'success': True}

    # -------- Match --------

    # ──── 技能安装 ────

    # ═══════════════════════════════════════════════════════════════
    # HarvenHub 市场接口（预留，仅提供上传/下载 API 端点定义）
    # ═══════════════════════════════════════════════════════════════

    HARVENHUB_API = {
        'base_url': 'https://hub.harven.ai/api/v1',
        'endpoints': {
            'upload':    {'method': 'POST', 'path': '/skills/upload', 'desc': '上传技能到 HarvenHub'},
            'download':  {'method': 'GET',  'path': '/skills/{name}', 'desc': '从 HarvenHub 下载技能'},
            'search':    {'method': 'GET',  'path': '/skills/search', 'desc': '搜索技能市场'},
            'list':      {'method': 'GET',  'path': '/skills/list', 'desc': '列出热门技能'},
            'delete':    {'method': 'DELETE', 'path': '/skills/{name}', 'desc': '删除已上传的技能'},
        }
    }

    def _get_harvenhub_config(self) -> dict:
        """获取 HarvenHub 接口配置（URL+端点定义），供前端展示或未来连接使用"""
        return {
            'enabled': False,  # HarvenHub 未上线
            'base_url': self.HARVENHUB_API['base_url'],
            'endpoints': self.HARVENHUB_API['endpoints'],
            'message': 'HarvenHub 市场暂未上线，技能通过本地文件或 GitHub 安装',
        }

    def install_from_harvenhub(self, skill_name: str) -> dict:
        """从 HarvenHub 安装技能（预留接口，待市场上线后启用）"""
        logger.info(f'[SkillsManager] HarvenHub 安装请求: {skill_name}（接口预留）')
        return {
            'success': False,
            'error': 'HarvenHub 市场暂未上线，请使用 GitHub 或本地文件安装',
            'harvenhub_api': self._get_harvenhub_config(),
        }

    def install_from_github(self, url: str) -> dict:
        """从 GitHub 仓库安装技能（兼容 SKILL.md 标准 + Harven 自有格式）"""
        try:
            import subprocess
            import tempfile

            skill_name = url.rstrip('/').split('/')[-1].replace('.git', '')
            skills_dir = SKILLS_DIR

            # 克隆到临时目录
            with tempfile.TemporaryDirectory() as tmpdir:
                result = subprocess.run(
                    ['git', 'clone', '--depth', '1', url, tmpdir],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    return {'success': False, 'error': f'克隆失败: {result.stderr.strip()}'}

                tmp = Path(tmpdir)
                # 查找 SKILL.md（标准格式）或 .skill.md（Harven 格式）
                skill_files = list(tmp.glob('SKILL.md')) + list(tmp.glob('*.skill.md'))
                if not skill_files:
                    return {'success': False, 'error': '仓库中未找到 SKILL.md 或 .skill.md 文件'}

                skill_md = skill_files[0]

                # 检测格式：如果是 SKILL.md 标准（有 scripts/references/assets 子目录），
                # 以目录形式安装；否则作为扁平文件安装
                has_subdirs = any(
                    (tmp / d).is_dir() for d in ['scripts', 'references', 'assets']
                )

                if has_subdirs:
                    # SKILL.md 标准：整个目录复制到 skills/skill_name/
                    target_dir = skills_dir / skill_name
                    if target_dir.exists():
                        import shutil
                        shutil.rmtree(str(target_dir))
                    import shutil
                    shutil.copytree(str(tmp), str(target_dir))
                    logger.info(f'[SkillsManager] 从 GitHub 安装 SKILL.md 标准技能(目录): {skill_name}')
                else:
                    # 扁平格式：仅复制 .md 文件
                    target_path = skills_dir / f'{skill_name}.md'
                    import shutil
                    shutil.copy2(str(skill_md), str(target_path))
                    logger.info(f'[SkillsManager] 从 GitHub 安装技能(文件): {skill_name}')

                # 重新加载
                self.reload()
                return {'success': True, 'skill_name': skill_name, 'message': f'技能 "{skill_name}" 安装成功'}

        except subprocess.TimeoutExpired:
            return {'success': False, 'error': '克隆超时（60秒），请检查网络或仓库地址'}
        except Exception as e:
            logger.error(f'[SkillsManager] GitHub 安装失败: {e}')
            return {'success': False, 'error': str(e)}

    def install_from_local(self, path: str) -> dict:
        """从本地路径安装技能（兼容 SKILL.md 标准 + Harven 自有格式）"""
        try:
            src = Path(path)
            if not src.exists():
                return {'success': False, 'error': f'路径不存在: {path}'}

            if src.is_file():
                skill_name = src.stem
                target = SKILLS_DIR / f'{skill_name}.md'
                import shutil
                shutil.copy2(str(src), str(target))
            elif src.is_dir():
                skill_name = src.name
                skill_files = list(src.glob('SKILL.md')) + list(src.glob('*.skill.md'))
                if not skill_files:
                    return {'success': False, 'error': '目录中未找到 SKILL.md 或 .skill.md 文件'}
                # 检测是否 SKILL.md 标准（有 scripts/references/assets 子目录）
                has_subdirs = any(
                    (src / d).is_dir() for d in ['scripts', 'references', 'assets']
                )
                if has_subdirs:
                    # 目录形式安装
                    target_dir = SKILLS_DIR / skill_name
                    if target_dir.exists():
                        import shutil
                        shutil.rmtree(str(target_dir))
                    import shutil
                    shutil.copytree(str(src), str(target_dir))
                else:
                    # 扁平文件安装
                    target = SKILLS_DIR / f'{skill_name}.md'
                    import shutil
                    shutil.copy2(str(skill_files[0]), str(target))
            else:
                return {'success': False, 'error': f'路径类型不支持: {path}'}

            self.reload()
            logger.info(f'[SkillsManager] 从本地安装技能成功: {skill_name}')
            return {'success': True, 'skill_name': skill_name, 'message': f'技能 "{skill_name}" 安装成功'}

        except Exception as e:
            logger.error(f'[SkillsManager] 本地安装失败: {e}')
            return {'success': False, 'error': str(e)}

    def install_from_url(self, url: str, skill_name: str = None) -> dict:
        """从 URL 安装技能（统一入口，自动识别格式）

        支持：
        - .md 文件直链（纯提示词Skill）
        - .zip 文件（SKILL.md + scripts/ 中级Skill）
        - GitHub/HuggingFace/ModelScope raw URL
        """
        try:
            import tempfile
            from urllib.parse import urlparse, unquote
            from urllib.request import urlopen, Request

            # 下载文件
            req = Request(url, headers={'User-Agent': 'HarvenAI/1.0'})
            with urlopen(req, timeout=30) as resp:
                content = resp.read()
                content_type = resp.headers.get('Content-Type', '')

            # 推断文件名
            parsed = urlparse(url)
            filename = unquote(parsed.path.split('/')[-1]) or 'skill_install'

            # ── 判断类型 ──
            if filename.endswith('.zip') or content_type == 'application/zip':
                # ZIP 包：解压后安装
                return self._install_from_zip_bytes(content, skill_name or filename.replace('.zip', ''))

            elif filename.endswith('.md') or content_type.startswith('text/'):
                # Markdown 文件：直接保存
                name = skill_name or filename.replace('.md', '').replace('.skill', '')
                text = content.decode('utf-8')

                # 检测是否为 SKILL.md 标准格式
                if text.strip().startswith('---') and ('name:' in text[:500] or 'id:' in text[:500]):
                    # 带 YAML frontmatter → 提取 name
                    import yaml
                    try:
                        parts = text.split('---', 2)
                        if len(parts) >= 3:
                            fm = yaml.safe_load(parts[1])
                            if fm and isinstance(fm, dict):
                                name = fm.get('name') or fm.get('id') or name
                    except Exception as _e: logger.debug(f"[skills_manager] 忽略: {_e}")

                target = SKILLS_DIR / f'{name}.md'
                target.write_text(text, encoding='utf-8')
                self.reload()
                return {'success': True, 'skill_name': name, 'message': f'技能 "{name}" 安装成功（Markdown）'}

            else:
                return {'success': False, 'error': f'不支持的文件类型: {content_type}，仅支持 .md 和 .zip'}

        except Exception as e:
            logger.error(f'[SkillsManager] URL 安装失败: {e}')
            return {'success': False, 'error': f'下载或安装失败: {e}'}

    def _install_from_zip_bytes(self, zip_data: bytes, skill_name: str) -> dict:
        """从 ZIP 字节流安装中级Skill"""
        import zipfile, shutil, tempfile
        from io import BytesIO

        skills_dir = SKILLS_DIR
        skill_dir = skills_dir / skill_name

        if skill_dir.exists():
            return {'success': False, 'error': f'技能 {skill_name} 已存在，请先卸载'}

        temp_dir = Path(tempfile.mkdtemp(prefix='harven_install_'))
        try:
            with zipfile.ZipFile(BytesIO(zip_data), 'r') as zf:
                zf.extractall(temp_dir)

            # 查找 SKILL.md
            skill_md = None
            for f in temp_dir.rglob('SKILL.md'):
                skill_md = f
                break
            if not skill_md:
                # 也接受 *.skill.md
                for f in temp_dir.rglob('*.skill.md'):
                    skill_md = f
                    break
            if not skill_md:
                return {'success': False, 'error': 'ZIP中未找到 SKILL.md 或 *.skill.md 文件'}

            # 复制到 skills 目录
            parent = skill_md.parent
            if parent == temp_dir:
                # 根目录直接包含 SKILL.md
                for item in temp_dir.iterdir():
                    dest = skill_dir / item.name
                    if item.is_dir():
                        shutil.copytree(str(item), str(dest))
                    else:
                        skill_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(item), str(dest))
            else:
                # SKILL.md 在子目录
                shutil.copytree(str(parent), str(skill_dir))

            self.reload()
            return {'success': True, 'skill_name': skill_name, 'message': f'技能 "{skill_name}" 安装成功（ZIP）'}

        finally:
            shutil.rmtree(str(temp_dir), ignore_errors=True)

    def search_harvenhub(self, query: str) -> list:
        """搜索 HarvenHub 技能市场（预留接口，待市场上线后启用）"""
        logger.info(f'[SkillsManager] HarvenHub 搜索: {query}（接口预留）')
        return []

    def check_updates(self) -> list:
        """检查已安装技能的更新"""
        updates = []
        for skill_id, skill in self._skills.items():
            # 检查 skills 目录中的文件是否有更新版本
            file_path = SKILLS_DIR / f'{skill_id}.md'
            if file_path.exists():
                file_mtime = file_path.stat().st_mtime
                if file_mtime > time.time() - 86400:  # 24小时内修改过
                    updates.append({
                        'skill_id': skill_id,
                        'name': skill.name,
                        'has_update': False,
                        'message': '已是最新版本',
                    })
        logger.info(f'[SkillsManager] 检查更新完成，{len(updates)} 个技能')
        return updates

    # ──── 技能匹配 ────

    def match_skill(self, message: str) -> Skill | None:
        """根据消息匹配最合适的技能（返回 None 表示不触发）"""
        message_lower = message.lower()
        best = None
        best_score = 0
        for skill in self._skills.values():
            if not skill.enabled:
                continue
            score = 0
            for trigger in skill.triggers:
                if trigger.lower() in message_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best = skill
        return best if best_score > 0 else None

    def list_scripted_skills(self) -> list:
        """列出所有有可执行脚本的技能"""
        return [
            s.to_dict() for s in self._skills.values()
            if s.has_scripts and s.enabled
        ]

    def get_skill_scripts_info(self, skill_id: str) -> dict | None:
        """获取技能的脚本信息"""
        skill = self._skills.get(skill_id)
        if not skill or not skill.has_scripts:
            return None
        info = skill.to_dict()
        info['scripts_config'] = skill.scripts_config
        return info

    def get_skill_prompt(self, message: str) -> str | None:
        """获取匹配技能的系统提示词（供 engine 注入）"""
        skill = self.match_skill(message)
        if skill:
            self._last_matched_skill = skill  # ★ 存储匹配的技能
            return skill.prompt
        self._last_matched_skill = None
        return None


# -------- 全局单例 --------
_skills_manager: SkillsManager | None = None
_sm_lock = threading.Lock()


def get_skills_manager() -> SkillsManager:
    global _skills_manager
    if _skills_manager is None:
        with _sm_lock:
            if _skills_manager is None:
                _skills_manager = SkillsManager()
    return _skills_manager


def reset_skills_manager():
    """重置技能管理器单例（安装新技能后调用）"""
    global _skills_manager
    with _sm_lock:
        _skills_manager = None
