# -*- coding: utf-8 -*-
"""A1 前置：后台下载 whisper-small 到 data/models/whisper-small（ModelScope 直连）"""
import os, shutil, sys
from pathlib import Path

for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
    os.environ.pop(k, None)

ROOT = Path(r'H:\HarvenAI')
TARGET = ROOT / 'data' / 'models' / 'whisper-small'

def main():
    if (TARGET / 'model.bin').exists():
        print('ALREADY_OK')
        return
    from modelscope import snapshot_download
    src = snapshot_download('pengzhendong/faster-whisper-small')
    TARGET.mkdir(parents=True, exist_ok=True)
    for f in Path(src).iterdir():
        if f.is_file():
            shutil.copy2(str(f), str(TARGET / f.name))
    ok = (TARGET / 'model.bin').exists()
    print('DONE_OK' if ok else 'DONE_NO_MODEL_BIN')

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FAILED:', e)
        sys.exit(1)
