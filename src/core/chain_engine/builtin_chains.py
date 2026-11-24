# -*- coding: utf-8 -*-
"""内置组合（官方预置 · 精装修样板间，拆不得）。

第一号 HarvenEye：哈文视觉图像组合——搬家不装修（v1.9 零破坏迁移轨）：
配方单环节照登记（L0/L1/LF/L2/L3），执行走打包执行器包装现有
vision.Pipeline（同一引擎单例，C3 六秒红线零风险）。
第二号 账单链：#1377 账单相册分析管线原样复用（packed_bills，四环真实计时，
试跑 dry 不污染账本，正式跑与 A 按钮/C 心跳同锁同落点）。
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


def bills_chain_recipe() -> Recipe:
    return Recipe(
        id="bills_chain",
        name="账单链",
        version="1.0.0",
        target="账单相册照片→视觉抽取消费信息→#913 查重→管家中心待确认卡。"
               "抽取环跟用户所选视觉模型（铁律0：HarvenEye→Eye，外脑→外脑视觉），"
               "照片全生命周期只扫一次（成功/失败/重复都打标记）。",
        origin="builtin",
        license="Apache-2.0",
        input_type="image_path",
        output_type="table_row",
        steps=[
            Step(id="bills_collect", name="取待分析账单照片", capability="",
                 engine="program",
                 input_type="any", output_type="image_path",
                 damage=DAMAGE_READONLY, note="账单相册+未标记+未软删，单批最多30张"),
            Step(id="bills_extract", name="账单视觉抽取（跟用户所选）", capability="vision_understand",
                 binding=BIND_DYNAMIC, dynamic_cap_filter=["vision_understand"],
                 engine="internal",
                 input_type="image_path", output_type="table_row",
                 damage=DAMAGE_READONLY, note="_camp_vision 统一入口：执行走 bills_scan.extract_bill_records"),
            Step(id="bills_dedup", name="重复票查重（#913）", capability="",
                 engine="program",
                 input_type="table_row", output_type="table_row",
                 damage=DAMAGE_READONLY, note="同日+同金额+近似名直接丢弃"),
            Step(id="bills_pending", name="待确认卡+照片标记", capability="",
                 engine="program", packed="bills_scan_pipeline",
                 input_type="table_row", output_type="table_row",
                 damage=DAMAGE_DB, note="打包执行器入口：四环计时展开；试跑 dry 不落库，正式跑 payload.official=1"),
        ],
        on_step_fail="skip_optional",
        deps=[],
        access={"input": "账单相册照片", "output_schema": "消费记录 list[dict]",
                "sink": "管家中心待确认→记账面板 insert_records"},
        meta={"packed_executor": "bills_scan_pipeline",
              "icon": "static/icon-duck.png",
              "user_facing": False,   # ★ #1537c 后台链：相册按钮/心跳自动触发，不占垂直页身份证
              "note": "复用 #1377 bills_scan 管线；试跑默认 dry，不写卡不打标记"},
    )


def video_brief_recipe() -> Recipe:
    return Recipe(
        id="video_brief",
        name="视频速览",
        version="1.0.0",
        target="视频→均匀抽帧→dhash 关键帧去重→逐关键帧识别（HarvenEye 瀑布提速版）"
               "→VLM 汇总成稿（跟用户所选视觉模型，铁律0）。"
               "第一个用户侧示范组合：大白话讲清一段视频讲了什么。",
        origin="builtin",
        license="Apache-2.0",
        input_type="video",
        output_type="text",
        steps=[
            Step(id="v_frames", name="均匀抽帧", capability="",
                 engine="program",
                 input_type="video", output_type="frames",
                 damage=DAMAGE_READONLY, note="cv2 均匀取样，缺省 8 帧（payload.max_frames 可调），临时文件跑完即清"),
            Step(id="v_keyframes", name="关键帧去重", capability="dedup",
                 model="harven.base.dhash", engine="internal",
                 input_type="frames", output_type="frames",
                 damage=DAMAGE_READONLY, note="dhash 帧间去重，静态镜头/场景切换只留关键帧"),
            Step(id="v_recognize", name="逐关键帧识别", capability="vision_pipeline",
                 engine="program",
                 input_type="frames", output_type="table_row",
                 damage=DAMAGE_READONLY, note="共享 Pipeline（vision_link 单例防双载），skip_vlm 提速；底座经 vision_link 就绪"),
            Step(id="v_summarize", name="VLM 汇总成稿（跟用户所选）", capability="vision_understand",
                 binding=BIND_DYNAMIC, dynamic_cap_filter=["vision_understand"],
                 engine="program", packed="video_brief_pipeline",
                 input_type="table_row", output_type="text",
                 damage=DAMAGE_READONLY, note="打包执行器入口；成稿跟用户所选视觉模型（_camp_vision）"),
        ],
        on_step_fail="skip_optional",
        deps=[],
        access={"input": "video_path", "output_schema": "{summary, frames_detail}",
                "sink": "工坊试跑/聊天框直答（组合绑定落点见 §5）"},
        meta={"packed_executor": "video_brief_pipeline",
              "icon": "static/icon-duck.png",
              "note": "样例视频 data/chains/samples/sample_video.mp4；试跑自动取"},
    )


BUILTIN_FACTORIES = {
    "harveneye": harveneye_recipe,
    "bills_chain": bills_chain_recipe,
    "video_brief": video_brief_recipe,
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
