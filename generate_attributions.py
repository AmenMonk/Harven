#!/usr/bin/env python3
"""
Harven AI — 第三方依赖许可清单自动生成
扫描 requirements.txt，查询每个包的许可协议，生成 attributions.json

使用方法：
    python generate_attributions.py

输出：
    data/attributions.json — 完整的第三方依赖许可清单
"""

import json
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent if __file__.startswith('archive') else Path(__file__).parent
REQ_FILE = PROJECT_ROOT / "requirements.txt"
OUTPUT_FILE = PROJECT_ROOT / "data" / "attributions.json"

# 手动维护的许可映射（pip show 查不到 License 时回退）
KNOWN_LICENSES = {
    "flask": ("BSD-3-Clause", "safe"),
    "flask-cors": ("MIT", "safe"),
    "werkzeug": ("BSD-3-Clause", "safe"),
    "openai": ("MIT", "safe"),
    "modelscope": ("Apache-2.0", "safe"),
    "llama-cpp-python": ("MIT", "safe"),
    "litellm": ("MIT", "safe"),
    "faiss-cpu": ("MIT", "safe"),
    "numpy": ("BSD-3-Clause", "safe"),
    "pyyaml": ("MIT", "safe"),
    "requests": ("Apache-2.0", "safe"),
    "beautifulsoup4": ("MIT", "safe"),
    "pillow": ("HPND", "safe"),
    "tiktoken": ("MIT", "safe"),
    "faster-whisper": ("MIT", "safe"),
    "opencv-python-headless": ("Apache-2.0", "safe"),
    "PySide6": ("LGPL-3.0", "notice"),
    "pypdf": ("BSD-3-Clause", "safe"),
    "pdfplumber": ("MIT", "safe"),
    "python-dotenv": ("BSD-3-Clause", "safe"),
    "tqdm": ("MIT", "safe"),
    "playwright": ("Apache-2.0", "safe"),
    "apscheduler": ("MIT", "safe"),
    "watchdog": ("Apache-2.0", "safe"),
}

# 许可证安全等级
LICENSE_CATEGORY = {
    "MIT": "safe",
    "BSD-3-Clause": "safe",
    "BSD-2-Clause": "safe",
    "Apache-2.0": "safe",
    "ISC": "safe",
    "HPND": "safe",
    "LGPL-3.0": "notice",
    "LGPL-2.1": "notice",
    "MPL-2.0": "notice",
    "GPL-3.0": "warning",
    "GPL-2.0": "warning",
    "AGPL-3.0": "danger",
}


def parse_requirements():
    """解析 requirements.txt，提取包名和版本"""
    packages = []
    if not REQ_FILE.exists():
        return packages

    content = REQ_FILE.read_text(encoding='utf-8')
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # 去掉行内注释
        if '#' in line:
            line = line.split('#')[0].strip()
        # 解析包名和版本
        match = re.match(r'^([a-zA-Z0-9_-]+)\s*([><=~!]+\s*[\w.]+)?', line)
        if match:
            name = match.group(1).lower()
            version_spec = (match.group(2) or '').strip()
            packages.append((name, version_spec))
    return packages


def get_license_from_pip(package_name):
    """通过 pip show 查询包的许可信息"""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'show', package_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None, None, None

        license_str = None
        version = None
        summary = None
        for line in result.stdout.split('\n'):
            if line.startswith('License:'):
                license_str = line.split(':', 1)[1].strip()
            elif line.startswith('Version:'):
                version = line.split(':', 1)[1].strip()
            elif line.startswith('Summary:'):
                summary = line.split(':', 1)[1].strip()

        return license_str, version, summary
    except Exception:
        return None, None, None


def normalize_license(raw_license):
    """标准化许可协议名称"""
    if not raw_license:
        return "UNKNOWN"

    raw_lower = raw_license.lower()

    # 常见映射
    mappings = [
        (['mit', ], 'MIT'),
        (['bsd', '3-clause', 'bsd-3'], 'BSD-3-Clause'),
        (['bsd', '2-clause', 'bsd-2'], 'BSD-2-Clause'),
        (['apache', '2.0', 'apache-2'], 'Apache-2.0'),
        (['apache software'], 'Apache-2.0'),
        (['lgpl', '3', 'lesser'], 'LGPL-3.0'),
        (['lgpl', '2.1', 'lesser'], 'LGPL-2.1'),
        (['mpl', 'mozilla'], 'MPL-2.0'),
        (['gpl', '3', 'gnu general'], 'GPL-3.0'),
        (['gpl', '2'], 'GPL-2.0'),
        (['agpl', 'affero'], 'AGPL-3.0'),
        (['isc'], 'ISC'),
        (['hpnd'], 'HPND'),
        (['psf', 'python software'], 'PSF-2.0'),
        (['unlicense'], 'Unlicense'),
        (['cc0'], 'CC0-1.0'),
    ]

    for keywords, canonical in mappings:
        if all(kw in raw_lower for kw in keywords if kw):
            return canonical

    return raw_license[:50]  # 返回原始文本


def categorize_license(canonical_license):
    """获取许可协议的安全等级"""
    for key, category in LICENSE_CATEGORY.items():
        if key in canonical_license:
            return category
    return "unknown"


def generate():
    """生成 attributions.json"""
    print(f"扫描 {REQ_FILE} ...")
    packages = parse_requirements()
    print(f"找到 {len(packages)} 个依赖包\n")

    attributions = []

    for name, version_spec in packages:
        print(f"  查询: {name}")

        # 1. 先从 pip show 获取
        license_str, installed_version, summary = get_license_from_pip(name)

        # 2. 标准化许可协议
        if license_str and license_str != 'UNKNOWN':
            canonical = normalize_license(license_str)
        else:
            # 3. 回退到手动映射（大小写不敏感）
            known = None
            for k, v in KNOWN_LICENSES.items():
                if k.lower() == name.lower():
                    known = v
                    break
            if known:
                canonical, _ = known
            else:
                canonical = "UNKNOWN"

        category = categorize_license(canonical)

        entry = {
            "name": name,
            "version": installed_version or version_spec or "",
            "license": canonical,
            "license_category": category,
            "summary": summary or "",
            "source": "pypi",
            "verified_at": datetime.now().isoformat(),
        }
        attributions.append(entry)

        status_icon = "✅" if category == "safe" else "⚠️" if category == "notice" else "🔴" if category == "danger" else "❓"
        print(f"    {status_icon} {canonical} ({category})")

    # 写入文件
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(attributions, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    # 统计
    safe = sum(1 for a in attributions if a['license_category'] == 'safe')
    notice = sum(1 for a in attributions if a['license_category'] == 'notice')
    warning = sum(1 for a in attributions if a['license_category'] == 'warning')
    danger = sum(1 for a in attributions if a['license_category'] == 'danger')
    unknown = sum(1 for a in attributions if a['license_category'] == 'unknown')

    print(f"\n{'='*50}")
    print(f"✅ 生成完成: {OUTPUT_FILE}")
    print(f"{'='*50}")
    print(f"总计: {len(attributions)} 个依赖")
    print(f"  ✅ Safe (MIT/BSD/Apache): {safe}")
    print(f"  ⚠️ Notice (LGPL/MPL):    {notice}")
    print(f"  🔴 Warning (GPL):         {warning}")
    print(f"  ❌ Danger (AGPL):         {danger}")
    print(f"  ❓ Unknown:               {unknown}")

    if danger > 0:
        print(f"\n🔴 警告: 发现 AGPL 依赖，开源分发需谨慎！")
        for a in attributions:
            if a['license_category'] == 'danger':
                print(f"   - {a['name']}: {a['license']}")

    return attributions


if __name__ == '__main__':
    generate()
