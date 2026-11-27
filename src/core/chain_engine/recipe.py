# -*- coding: utf-8 -*-
"""配方单 schema（骨架核心 · 2026-11-24 开工）。

设计稿 v1.15 落地：
- YAML 配方单（11-24 定版格式）；
- A1 环节双模式：fixed（保存时钉死）/ dynamic_follow（实时跟用户所选+能力过滤）；
- B4 破坏力等级：readonly / db / action 三级，safe 模式动作环自动跳过；
- A2/A3：组合=单次激活内配方单驱动自动连续执行；显存门槛=聚段取各段峰值最大；
- B6：semver 版本号；B13：license 字段（MIT/Apache/Proprietary/NonCommercial）；
- 身份标签：pure_local / mixed（含云依赖环即 mixed，诚实标注）。
"""
from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

# ══ 常量 ═══════════════════════════════════════════════════════
BIND_FIXED = "fixed"                # A1：保存时钉死
BIND_DYNAMIC = "dynamic_follow"     # A1：实时跟用户所选

DAMAGE_READONLY = "readonly"        # B4：只读
DAMAGE_DB = "db"                    # B4：落库
DAMAGE_ACTION = "action"            # B4：动作级（safe 自动跳过）

IDENT_PURE_LOCAL = "pure_local"
IDENT_MIXED = "mixed"

VALID_DAMAGE = {DAMAGE_READONLY, DAMAGE_DB, DAMAGE_ACTION}
VALID_BIND = {BIND_FIXED, BIND_DYNAMIC}
# 数据类型（结构门用，gates.py 引用）
TYPES = {"image", "image_path", "text", "boxes", "vector", "table_row", "audio", "video", "any"}

# ★ #1583 图像类能力集合：试跑端点按能力补样例 + 编排器缺图人话报错共用
IMAGE_CAPS = {"dedup", "detect", "face", "embed", "tag", "locate", "classify", "vision_understand"}

_RE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")
_RE_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Step:
    """组合环节。"""
    id: str
    name: str
    capability: str                    # detect/vision_understand/text_gen/embed/face/locate/...
    binding: str = BIND_FIXED          # A1 双模式
    model: str = ""                    # fixed 模式的模型 id（底座内部件用 harven.base.*）
    backup_models: list = field(default_factory=list)   # v1.6 修④：备选链（用户预先点头）
    dynamic_cap_filter: list = field(default_factory=list)  # dynamic 模式的能力过滤
    engine: str = "internal"           # internal/llama_cpp/ollama/cloud/program
    input_type: str = "any"
    output_type: str = "any"
    damage: str = DAMAGE_READONLY      # B4 破坏力等级
    packed: str = ""                   # 打包执行器名（迁移期「搬家不装修」用）
    keep_alive_s: int = 0              # 驻留保活秒数（0=随池策略）
    optional: bool = False             # 支线缺失不瘫主链（三轮定版第 4 条）
    prompt: str = ""                   # ★ #1568 环节提示词：vision_understand/text_gen 环生效；
                                       #   配方环优先于运行时 payload；空=维持原状（运行时给/引擎默认）
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Recipe:
    """组合配方单。"""
    id: str                            # slug，唯一（文件名=此 id）
    name: str                          # 显示名，唯一命名空间（B7）
    version: str = "1.0.0"             # semver（B6）
    target: str = ""                   # 目标描述（双读者：AI 参谋 + Hub 用户，v1.6 修②）
    origin: str = "user"               # builtin / user / forked
    forked_from: str = ""              # fork 源（示范田照改另存）
    license: str = "Apache-2.0"        # B13/Hub 许可证字段（11-24 A-3）
    input_type: str = "image_path"
    output_type: str = "text"
    steps: list = field(default_factory=list)   # [Step]
    on_step_fail: str = "abort"        # abort / skip_optional / use_backup
    deps: list = field(default_factory=list)    # 依赖声明：[{model_id, channel, min_vram_gb, size_gb, note}]
    access: dict = field(default_factory=dict)  # 接入声明：{input, output_schema, sink}
    enabled: bool = True
    meta: dict = field(default_factory=dict)

    # ── 基础校验（结构门之前的自检；细节类型匹配在 gates.py）──
    def validate(self) -> list:
        errs = []
        if not _RE_SLUG.match(self.id or ""):
            errs.append(f"id 非法（小写字母数字-_，2~64 位）: {self.id!r}")
        if not (self.name or "").strip():
            errs.append("name 不能为空（组合必须命名才能被点名，v1.4）")
        if not _RE_SEMVER.match(self.version or ""):
            errs.append(f"version 须为 semver: {self.version!r}")
        if not self.steps:
            errs.append("steps 不能为空")
        seen_ids = set()
        for i, s in enumerate(self.steps):
            tag = f"step[{i}]({s.id or '?'})"
            if not s.id or s.id in seen_ids:
                errs.append(f"{tag}: 环节 id 缺失或重复")
            seen_ids.add(s.id)
            if s.binding not in VALID_BIND:
                errs.append(f"{tag}: binding 非法 {s.binding!r}")
            if s.binding == BIND_FIXED and s.engine != "program" and not s.model:
                errs.append(f"{tag}: fixed 环节必须钉死模型")
            if s.damage not in VALID_DAMAGE:
                errs.append(f"{tag}: damage 非法 {s.damage!r}")
            if s.capability == "understand" and s.binding == BIND_DYNAMIC \
                    and not s.dynamic_cap_filter:
                errs.append(f"{tag}: dynamic_follow 环节必须给能力过滤（A1）")
        if self.on_step_fail not in ("abort", "skip_optional", "use_backup"):
            errs.append(f"on_step_fail 非法: {self.on_step_fail!r}")
        return errs

    # ── 身份标签：出现云依赖环即 mixed（v1.2 诚实标注）──
    def identity(self) -> str:
        for s in self.steps:
            if s.engine == "cloud":
                return IDENT_MIXED
        return IDENT_PURE_LOCAL

    # ── 能力标签 = 各引擎环能力的交集（诚实登记）──
    def capabilities(self) -> set:
        caps = None
        for s in self.steps:
            if s.engine == "program":
                continue
            caps = set(s.dynamic_cap_filter) if (s.binding == BIND_DYNAMIC and s.dynamic_cap_filter) \
                else ({s.capability} if s.capability else set())
            break   # 交集取链路主干环（首环能力为门户能力，主干环不一致时取交集）
        for s in self.steps:
            if s.engine == "program":
                continue
            c = set(s.dynamic_cap_filter) if (s.binding == BIND_DYNAMIC and s.dynamic_cap_filter) \
                else ({s.capability} if s.capability else set())
            if c:
                caps = (caps & c) if caps else c
        return caps or set()

    # ── A3 显存门槛：按聚段取各段峰值最大（同段内模型相加）──
    def segment_vram_peak_gb(self, vram_of) -> float:
        """vram_of(model_id)->GB；连续同模型段合并，峰值=各段和的最大。"""
        peak, cur, cur_model = 0.0, 0.0, None
        for s in self.steps:
            if s.engine == "program":
                continue
            m = s.model or "dynamic"
            if m == cur_model:
                cur += float(vram_of(m) or 0)
            else:
                peak = max(peak, cur)
                cur, cur_model = float(vram_of(m) or 0), m
        return max(peak, cur)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        steps = [Step.from_dict(s) for s in (d.get("steps") or [])]
        known = {f for f in cls.__dataclass_fields__ if f != "steps"}
        r = cls(**{k: v for k, v in d.items() if k in known}, steps=steps)
        return r

    def copy(self) -> "Recipe":
        return Recipe.from_dict(copy.deepcopy(self.to_dict()))


# ══ 文件读写（data/chains/*.yaml，一份配方一份文件）══════════════
_LOCK = threading.Lock()


def save(r: Recipe, chains_dir: str | Path) -> Path:
    p = Path(chains_dir) / f"{r.id}.yaml"
    with _LOCK:
        payload = {"recipe": r.to_dict()}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")
    return p


def load(chains_dir: str | Path, recipe_id: str) -> Recipe | None:
    p = Path(chains_dir) / f"{recipe_id}.yaml"
    if not p.exists():
        return None
    return load_file(p)


def load_file(p: str | Path) -> Recipe | None:
    data = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    rd = data.get("recipe") or data
    if not rd.get("id"):
        return None
    return Recipe.from_dict(rd)


def load_all(chains_dir: str | Path) -> list:
    out = []
    for p in sorted(Path(chains_dir).glob("*.yaml")):
        r = load_file(p)
        if r:
            out.append(r)
    return out


def delete(chains_dir: str | Path, recipe_id: str) -> bool:
    """卸载组合只删配方单，共享底座模型保留（三轮定版第 2 条）。"""
    p = Path(chains_dir) / f"{recipe_id}.yaml"
    if p.exists():
        with _LOCK:
            p.unlink()
        return True
    return False
