"""三槽位引擎适配层 — 槽位接口永不变，槽内模型随便换。

三个槽位（第四章）:
  detect      目标检测     YOLOX (Apache 2.0)
  index       图文索引     Chinese-CLIP
  understand  视觉理解     provider 三通道: ollama | llama_cpp | api（用户选谁用谁）
"""
