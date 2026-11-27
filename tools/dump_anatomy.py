# -*- coding: utf-8 -*-
"""#1581 崩溃 dump 解剖 v2：异常线程 + 非系统模块清单 + 崩溃线程栈回溯候选"""
import struct
from minidump.minidumpfile import MinidumpFile

DMP = r"H:\HarvenAI\data\logs\crash_9424.dmp"

f = MinidumpFile.parse(DMP)

# --- exception：动态内省，避免版本差异 ---
print("== exception ==")
exc = getattr(f, "exception", None)
tid = None
if exc is not None:
    recs = getattr(exc, "exception_records", None) or []
    print("  异常记录数:", len(recs))
    for r in recs:
        print("  record attrs:", [a for a in dir(r) if not a.startswith("_")][:20])
        inner = getattr(r, "ExceptionRecord", None) or r
        code = getattr(inner, "ExceptionCode", None)
        code = getattr(code, "name", code)
        addr = int(getattr(inner, "ExceptionAddress", 0) or 0)
        tid = getattr(r, "ThreadId", None)
        if code is not None:
            print("  code=%s addr=0x%X thread=%s" % (code, addr or 0, tid))
else:
    print("  无异常流")

# --- 非系统模块 ---
mods = list(f.modules.modules) if f.modules else []
print("\n== 模块总数 %d，非系统/相关模块 ==" % len(mods))
interesting = []
for m in mods:
    name = str(m.name)
    low = name.lower()
    if (".venv" in low or "harven" in low or "torch" in low or "onnx" in low
            or "llama" in low or "ggml" in low or "opencv" in low or "cv2" in low
            or "numpy" in low or "openmp" in low or "workbuddy" in low
            or "flash_attn" in low or "xformers" in low or "pillow" in low):
        interesting.append((m.baseaddress, getattr(m, "size", 0) or getattr(m, "SizeOfImage", 0) or 0x1000000, name))
for base, size, name in sorted(interesting):
    print("  0x%012X +0x%06X %s" % (base, size, name))

# --- 崩溃线程栈扫描 ---
print("\n== 崩溃线程栈扫描 ==")
thr = None
threads = list(f.threads.threads) if f.threads else []
print("  线程总数:", len(threads))
for t in threads:
    if tid is not None and getattr(t, "ThreadId", None) == tid:
        thr = t
        break
if thr is None:
    thr = threads[0] if threads else None
    print("  [warn] 异常线程未命中，用首个线程兜底")
if thr is None:
    raise SystemExit("无线程可分析")
print("  分析线程 id:", getattr(thr, "ThreadId", "?"))
stk = getattr(thr, "Stack", None)
print("  stack attrs:", [a for a in dir(stk) if not a.startswith("_")][:15])
start = getattr(stk, "StartofMemoryRange", None)
mem = getattr(stk, "Memory", None) or stk
rva = getattr(mem, "Rva", None) or getattr(mem, "rva", None)
size = getattr(mem, "DataSize", None) or getattr(mem, "data_size", None)
print("  base=0x%X rva=0x%X size=%d" % (start or 0, rva or 0, size or 0))
if not (rva and size):
    raise SystemExit("拿不到栈内存描述符")
with open(DMP, "rb") as fh:
    fh.seek(rva)
    data = fh.read(min(size, 4 * 1024 * 1024))
print("  栈数据已读 %d 字节" % len(data))
hits = []
n = len(data) // 8
for i in range(n):
    v = struct.unpack_from("<Q", data, i * 8)[0]
    for base, sz, name in interesting:
        if base <= v < base + sz:
            hits.append((i * 8, v - base, name.rsplit("\\", 1)[-1]))
            break
print("  模块地址命中 %d 个" % len(hits))
seen = None
out = []
for off, delta, name in reversed(hits):  # 栈底方向 = 更早的调用者
    key = (name, delta // 0x1000)
    if key != seen:
        out.append("  栈+0x%-6X %s+0x%X" % (off, name, delta))
        seen = key
    if len(out) >= 50:
        break
print("\n".join(out) if out else "  栈内无相关模块地址")
