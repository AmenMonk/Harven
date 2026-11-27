# -*- coding: utf-8 -*-
"""
★ #1581 哈文卡死现场自动抓捕器 v2（2026-11-27）
v2 改进（18:27 实锤教训）：
  1. 死机是「瞬断自愈」型：7000 失联 >=6s 又自己活过来 → v2 首次探测失败就立即
     抓一份 py-spy 快照（卡过的线程痕迹还在），不再死等连续 3 次；
  2. 连续 3 次失败仍走全量抓捕（3 次栈 + 内存 + GPU）；
  3. 自愈也记录（瞬断时长/恢复时间），瞬断序列本身就是证据。
必须用管理员身份跑（哈文是提权进程，普通权限 py-spy 钉不进去 os error 5）。
用法：双击 tools/_elev_freeze_trap.bat（自提权）→ 戴眼镜正常用 → 死机别重启。
"""
import subprocess, time, urllib.request, os, datetime

VENV = r"H:\HarvenAI\.venv\Scripts"
SPY = os.path.join(VENV, "py-spy.exe")
LOGDIR = r"H:\HarvenAI\data\logs"
PROBE_URL = "http://127.0.0.1:7000/static/spa/index.html"
TIMEOUT = 6          # 单次探测超时秒
FAILS_NEEDED = 3     # 连续失败次数判定卡死（全量抓捕门槛）
DUMPS = 3            # 卡死后抓几次栈
DUMP_GAP = 10        # 抓栈间隔秒

def log(msg, path):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def harven_pid():
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(":7000"):
            return parts[4]
    return None

def probe():
    try:
        urllib.request.urlopen(PROBE_URL, timeout=TIMEOUT)
        return True
    except Exception:
        return False

def spy_dump(pid, out, tag):
    dump_file = out.replace(".log", f"_{tag}.txt")
    try:
        r = subprocess.run([SPY, "dump", "--pid", pid, "--locals"],
                           capture_output=True, text=True, timeout=60)
        with open(dump_file, "w", encoding="utf-8", errors="replace") as f:
            f.write(r.stdout + "\n---STDERR---\n" + r.stderr)
        return f"{dump_file}（{len(r.stdout)} 字符）"
    except Exception as e:
        return f"失败: {e}"

def sysinfo_snap(out):
    f_path = out.replace(".log", "_sysinfo.txt")
    with open(f_path, "w", encoding="utf-8", errors="replace") as f:
        for cmd in (["tasklist", "/FI", "IMAGENAME eq python.exe"],
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                f.write(f"\n=== {' '.join(cmd[:3])} ===\n{r.stdout}\n{r.stderr}\n")
            except Exception as e:
                f.write(f"\n=== {' '.join(cmd[:3])} === 失败: {e}\n")
    return f_path

def main():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(LOGDIR, f"freeze_{stamp}.log")
    log("陷阱v2已布好（管理员）：每2s探测7000；首败即快照，连败3次全量抓捕", out)
    fails = 0
    snap_no = 0
    while True:
        ok = probe()
        if ok:
            if fails:
                log(f"自愈：失联 {fails} 次探测后恢复（瞬断序列已留痕）", out)
            fails = 0
            time.sleep(2)
            continue
        fails += 1
        pid = harven_pid()
        if fails == 1:
            snap_no += 1
            log(f"★ 首次失联！立即快照 #{snap_no}（PID={pid}）", out)
            log(f"快照结果: {spy_dump(pid, out, f'snap{snap_no}')}", out)
            log(f"系统信息: {sysinfo_snap(out)}", out)
            time.sleep(2)
            continue
        log(f"探测失败 {fails}/{FAILS_NEEDED}", out)
        if fails < FAILS_NEEDED:
            time.sleep(2)
            continue
        log("★ 连续3次失联=真死机！全量抓捕", out)
        for i in range(DUMPS):
            log(f"全量栈 dump{i+1}/3: {spy_dump(pid, out, f'dump{i+1}')}", out)
            if i < DUMPS - 1:
                time.sleep(DUMP_GAP)
        log(f"系统信息: {sysinfo_snap(out)}", out)
        log("全量抓捕完成，收工。请把结果告诉控制塔。", out)
        return

if __name__ == "__main__":
    main()
