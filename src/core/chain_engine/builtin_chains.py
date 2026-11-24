# -*- coding: utf-8 -*-
"""内置组合（官方预置 · 精装修样板间，拆不得）。

第一号 HarvenEye：哈文视觉图像组合——搬家不装修（v1.9 零破坏迁移轨）：
配方单环节照登记（L0/L1/LF/L2/L3），执行走打包执行器包装现有
vision.Pipeline（同一引擎单例，C3 六秒红线零风险）。
原件保护：builtin 只能 fork 另存，不能覆盖（registry.register_chain 守）。
"""
from __future__ import annotations

from .recipe import Recipe, Step, BIND_DYNAMIC, DAMAGE_READONLY, DAMAGE_DB


def harveneye_recipe() -> Recipe:
    return Recipe(
        id="harveneye",
        name="HarvenEye",
        version="1.0.0",
        target="哈文视觉图像组合：拍照查重→检测→认人→打标→按门控理解→入库。"
               "底座预置全本地，理解环跟用户所选（本地或外脑，绝不偷换）。",
        origin="builtin",
        license="Apache-2.0",
        input_type="image_path",
        output_type="table_row",
        steps=[
            Step(id="l0_dedup", name="L0 指纹查重", capability="dedup",
                 model="harven.base.dhash", engine="internal",
                 input_type="image_path", output_type="image_path",
                 damage=DAMAGE_READONLY, note="近重复毫秒级秒回"),
            Step(id="l1_detect", name="L1 目标检测", capability="detect",
                 model="harven.base.yolox", engine="internal",
                 input_type="image_path", output_type="boxes",
                 damage=DAMAGE_READONLY),
            Step(id="lf_face", name="LF 人脸支线", capability="face",
                 model="harven.base.sface", engine="internal", optional=True,
                 input_type="boxes", output_type="image_path",
                 damage=DAMAGE_READONLY, note="检出 person 才启动；主链透传图片，身份标签走旁路"),
            Step(id="l2_embed", name="L2 语义打标/检索", capability="embed",
                 model="harven.base.clip", engine="internal",
                 input_type="image_path", output_type="image_path",
                 damage=DAMAGE_READONLY, note="向量进旁路元数据，主链透传图片给 L3"),
            Step(id="l3_understand", name="L3 理解环（跟用户所选）", capability="vision_understand",
                 binding=BIND_DYNAMIC, dynamic_cap_filter=["vision_understand"],
                 engine="internal", optional=True,
                 input_type="image_path", output_type="text",
                 damage=DAMAGE_READONLY, note="门控决策：高置信直答跳过 L3 省时"),
            Step(id="sink_index", name="入库（分级路由）", capability="",
                 engine="program", packed="harveneye_pipeline",
                 input_type="any", output_type="table_row",
                 damage=DAMAGE_DB, note="打包执行器：整个瀑布经 vision.Pipeline 原样跑（搬家不装修），trace 按环展开物流单"),
        ],
        on_step_fail="skip_optional",
        deps=[],
        access={"input": "image_path", "output_schema": "recognize dict",
                "sink": "images 索引库 / 图片库 / 相册归位"},
        meta={"packed_executor": "harveneye_pipeline",
              "icon": "static/icon-duck.png",
              "note": "打包执行=迁移期「搬家不装修」，配方结构照登记，trace 按环展开物流单"},
    )


BUILTIN_FACTORIES = {
    "harveneye": harveneye_recipe,
}


def register_builtin_chains(reg) -> int:
    """注册内置组合（幂等；落盘 data/chains/ 供工坊查看照改）。"""
    n = 0
    for rid, factory in BUILTIN_FACTORIES.items():
        r = factory()
        res = reg.register_chain(r, overwrite_builtin=True)
        if res.get("ok"):
            n += 1
    return n
