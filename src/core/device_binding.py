# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
设备绑定 & 防克隆模块 — Harven AI
通过硬件指纹确保 AI 只能在本机运行，防止被复制到其他设备。

绑定策略：
1. MAC 地址绑定（主标识）
2. CPU 序列号（辅助）
3. 主板 UUID（辅助）
4. 多因素校验（至少 2/3 匹配）
"""
import hashlib
import json
import os
import subprocess
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
BINDING_FILE = PROJECT_ROOT / 'data' / 'device_binding.json'


def _shell(cmd: str) -> str:
    """执行命令并返回输出（Windows）"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace"
        )
        # ★ 2026-09-22 修复(#814)：不再把 stderr 拼进返回值。
        #   旧逻辑 stdout+stderr 拼接，命令缺失/失败时 stderr 报错文本
        #   （如 "...operable program or batch file."）会被当成指纹值
        #   存进绑定文件，导致重启发疯式 CPU/BIOS/主板 ❌ 不匹配。
        out = (result.stdout or "").strip()
        if result.returncode != 0 or not out:
            return ""
        low = out.lower()
        if ("not recognized" in low or "operable program" in low
                or "不是内部或外部命令" in out):
            return ""
        return out
    except Exception:
        return ""


# ─── 硬件指纹采集 ───

class HardwareFingerprint:
    """硬件指纹采集器"""
    
    @staticmethod
    def get_mac_addresses() -> list:
        """获取所有物理网卡 MAC 地址"""
        try:
            output = _shell("getmac /v /fo csv")
            macs = []
            for line in output.split("\n"):
                if "N/A" in line or "bios" in line.lower():
                    continue
                parts = [p.strip('"') for p in line.split(",")]
                for p in parts:
                    cleaned = p.replace("-", ":").replace(" ", "").upper()
                    if ":" in cleaned and len(cleaned) == 17:
                        if "00-00-00-00" not in cleaned:
                            macs.append(cleaned)
            # 去重
            seen = set()
            result = []
            for m in macs:
                if m not in seen:
                    seen.add(m)
                    result.append(m)
            return result
        except Exception:
            return []
    
    @staticmethod
    def get_cpu_id() -> str:
        """获取 CPU 序列号（Windows）"""
        try:
            output = _shell("wmic cpu get ProcessorId")
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if len(lines) >= 2:
                return lines[1]
        except Exception as _e: logger.debug(f"[device_binding] 忽略: {_e}")
        
        # 备选：通过注册表
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            val = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            winreg.CloseKey(key)
            return hashlib.sha256(val.encode()).hexdigest()[:16]
        except Exception:
            return ""
    
    @staticmethod
    def get_bios_uuid() -> str:
        """获取 BIOS UUID"""
        try:
            output = _shell("wmic csproduct get UUID")
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if len(lines) >= 2:
                return lines[1]
        except Exception as _e: logger.debug(f"[device_binding] 忽略: {_e}")
        return ""
    
    @staticmethod
    def get_motherboard_serial() -> str:
        """获取主板序列号"""
        try:
            # Windows
            output = _shell("wmic baseboard get SerialNumber")
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if len(lines) >= 2 and lines[1].upper() != "Tobefilledbyo.e.m":
                return lines[1]
        except Exception as _e: logger.debug(f"[device_binding] 忽略: {_e}")
        
        # 备选：DMI decode
        try:
            output = _shell("wmic csproduct get IdentifyingNumber")
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if len(lines) >= 2:
                return lines[1]
        except Exception as _e: logger.debug(f"[device_binding] 忽略: {_e}")
        return ""
    
    @staticmethod
    def get_disk_serial() -> str:
        """获取系统盘序列号"""
        try:
            output = _shell("wmic diskdrive where DeviceID='\\\\\\\\.\\\\PHYSICALDRIVE0' get SerialNumber")
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            if len(lines) >= 2:
                return lines[1].strip()
        except Exception as _e: logger.debug(f"[device_binding] 忽略: {_e}")
        return ""
    
    @classmethod
    def collect_all(cls) -> Dict:
        """采集所有硬件指纹"""
        return {
            "mac_addresses": cls.get_mac_addresses(),
            "cpu_id": cls.get_cpu_id(),
            "bios_uuid": cls.get_bios_uuid(),
            "motherboard_serial": cls.get_motherboard_serial(),
            "disk_serial": cls.get_disk_serial(),
        }
    
    @classmethod
    def generate_device_id(cls) -> str:
        """生成设备唯一标识（不可逆哈希）"""
        fp = cls.collect_all()
        
        # 使用最可靠的标识
        components = []
        macs = fp.get("mac_addresses", [])
        if macs:
            components.append(sorted(macs)[0])
        cpu = fp.get("cpu_id", "")
        if cpu:
            components.append(cpu)
        mb = fp.get("motherboard_serial", "")
        if mb:
            components.append(mb)
        
        if not components:
            # 完全获取不到硬件信息，使用机器名
            import socket
            components.append(socket.gethostname())
        
        raw = "|".join(components)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── 设备绑定管理 ───

class DeviceBinding:
    """
    设备绑定管理器
    
    绑定策略：生成设备指纹时记录多个维度，
    验证时至少需要 2/3 维度匹配才通过。
    """
    
    def __init__(self):
        self.fingerprint = HardwareFingerprint()
        self._loaded = False
        self._binding_data = None
    
    def _ensure_loaded(self):
        if not self._loaded:
            self._binding_data = self._load_binding()
            self._loaded = True
    
    def _load_binding(self) -> Optional[Dict]:
        """加载已保存的绑定数据"""
        if BINDING_FILE.exists():
            try:
                return json.loads(BINDING_FILE.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, IOError):
                pass
        return None
    
    def _save_binding(self, data: Dict):
        """保存绑定数据"""
        BINDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        BINDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding='utf-8')
    
    def is_bound(self) -> bool:
        """检查是否已经绑定"""
        self._ensure_loaded()
        return self._binding_data is not None and self._binding_data.get("bound", False)
    
    def bind(self, force: bool = False) -> Tuple[bool, str]:
        """
        执行设备绑定
        返回 (success, message)
        """
        self._ensure_loaded()
        
        if self._binding_data and self._binding_data.get("bound") and not force:
            return False, "设备已绑定。如需重新绑定请使用 force=True"
        
        fp = self.fingerprint.collect_all()
        device_id = self.fingerprint.generate_device_id()
        
        binding = {
            "bound": True,
            "device_id": device_id,
            "bound_at": __import__('time').time(),
            "fingerprint": fp,
            "version": 1,
        }
        
        self._save_binding(binding)
        self._binding_data = binding
        return True, f"设备绑定成功。设备ID: {device_id[:8]}..."
    
    def verify(self) -> Tuple[bool, str]:
        """
        验证当前设备是否匹配绑定记录
        返回 (is_valid, message)
        """
        self._ensure_loaded()
        
        if not self._binding_data or not self._binding_data.get("bound"):
            return False, "设备未绑定。请先执行设备绑定。"
        
        current_fp = self.fingerprint.collect_all()
        saved_fp = self._binding_data.get("fingerprint", {})
        
        # 多维度匹配检查
        match_count = 0
        total_checks = 0
        details = []
        
        # 1. MAC 地址（权重高）
        saved_macs = set(m.strip().upper().replace("-", ":") 
                        for m in saved_fp.get("mac_addresses", []))
        current_macs = set(m.strip().upper().replace("-", ":") 
                          for m in current_fp.get("mac_addresses", []))
        if saved_macs and current_macs:
            total_checks += 1
            common = saved_macs & current_macs
            if common:
                match_count += 1
                details.append("MAC地址: ✅")
            else:
                details.append("MAC地址: ❌ 不匹配")
        
        # 2. CPU ID
        saved_cpu = saved_fp.get("cpu_id", "").strip()
        current_cpu = current_fp.get("cpu_id", "").strip()
        if saved_cpu and current_cpu:
            total_checks += 1
            if saved_cpu == current_cpu:
                match_count += 1
                details.append("CPU: ✅")
            else:
                details.append("CPU: ❌ 不匹配")
        
        # 3. BIOS UUID
        saved_bios = saved_fp.get("bios_uuid", "").strip()
        current_bios = current_fp.get("bios_uuid", "").strip()
        if saved_bios and current_bios:
            total_checks += 1
            if saved_bios == current_bios:
                match_count += 1
                details.append("BIOS UUID: ✅")
            else:
                details.append("BIOS UUID: ❌ 不匹配")
        
        # 4. 主板序列号（如果可用）
        saved_mb = saved_fp.get("motherboard_serial", "").strip()
        current_mb = current_fp.get("motherboard_serial", "").strip()
        if saved_mb and current_mb:
            total_checks += 1
            if saved_mb == current_mb:
                match_count += 1
                details.append("主板: ✅")
            else:
                details.append("主板: ❌ 不匹配")
        
        # 判定：至少 2 项匹配
        if total_checks >= 2 and match_count >= 2:
            return True, f"设备验证通过 ({match_count}/{total_checks})"
        elif total_checks < 2:
            # 无法采集足够信息时放宽
            return True, "设备信息不足，跳过验证"
        else:
            detail_str = "; ".join(details)
            return False, f"设备验证失败: {detail_str}。请检查硬件是否变更。"
    
    def unbind(self) -> Tuple[bool, str]:
        """解除设备绑定"""
        self._ensure_loaded()
        if BINDING_FILE.exists():
            BINDING_FILE.unlink()
        self._binding_data = None
        return True, "设备绑定已解除"
    
    def get_device_info(self) -> Dict:
        """获取当前设备信息（不包含敏感数据）"""
        fp = self.fingerprint.collect_all()
        return {
            "device_id": self.fingerprint.generate_device_id()[:12] + "...",
            "mac_count": len(fp.get("mac_addresses", [])),
            "has_cpu_id": bool(fp.get("cpu_id")),
            "has_bios_uuid": bool(fp.get("bios_uuid")),
            "has_mb_serial": bool(fp.get("motherboard_serial")),
            "bound": self.is_bound(),
        }


# ─── 启动时校验 ───

def startup_verify() -> Tuple[bool, str]:
    """
    启动时校验设备绑定
    如果未绑定，自动执行首次绑定
    返回 (ok, message)
    """
    binding = DeviceBinding()
    
    if not binding.is_bound():
        # 首次启动：自动绑定
        ok, msg = binding.bind()
        if ok:
            logger.info(f"[DeviceBinding] 首次绑定成功: {msg}")
            return True, ""
        else:
            logger.error(f"[DeviceBinding] 绑定失败: {msg}")
            return False, f"设备绑定失败: {msg}"
    
    # 已绑定：验证
    ok, msg = binding.verify()
    if ok:
        return True, ""
    else:
        logger.warning(f"[DeviceBinding] 验证失败: {msg}")
        return False, f"⚠️ 设备验证失败: {msg}\n\nHarven AI 只能在原始绑定的设备上运行。如果您更换了硬件，请删除 data/device_binding.json 后重新启动以重新绑定。"


# ─── 单例 ───
_binding: Optional[DeviceBinding] = None
_binding_lock = threading.Lock()

def get_device_binding() -> DeviceBinding:
    global _binding
    if _binding is None:
        with _binding_lock:
            if _binding is None:
                _binding = DeviceBinding()
    return _binding
