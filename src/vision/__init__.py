"""哈文视觉子系统 — HarvenEye 底座融合版（v1.3 融合定版）

★ 2026-10-29 定版拍板融合：HarvenEye 不再是独立产品/独立进程，
  底座（YOLOX 检测 + Chinese-CLIP 索引）并入哈文代码树成为内置子系统；
  理解槽无专属大脑 —— 走用户在哈文选的模型（外脑 API 或本地 GGUF 进程内直载）。

规则依据: 桌面《HarvenEye制作规则说明 v1.3》
  - 零原图入库（视觉索引库 catalog 模式）
  - AGPL 隔离（Ultralytics 全系禁用，全栈 Apache 2.0 / MIT）
  - 三槽位引擎适配：检测/索引/理解 —— 槽位接口永不变，槽内模型随便换
旧形态退役清单: 7001 独立进程 / api.py / eye_supervisor / eye_client HTTP
"""

__version__ = "1.3.0"
__product__ = "HarvenEye(哈文视觉子系统)"
