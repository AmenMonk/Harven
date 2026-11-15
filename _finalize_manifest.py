# -*- coding: utf-8 -*-
"""★ #1398（2026-11-02 定版拍板 A+B）增量更新线上闭环：清单终版工具

用法：python _finalize_manifest.py <完整包item_id> <增量包item_id> [清单包zip路径]

流程定位：pack_release.py 出包（清单同步包无下载直链，鸡生蛋）→ 控制塔把完整包+
增量包都传 Hub 拿 item_id → 跑本工具把 download_url / update_channel.url 写进清单
并重打清单同步包 → 转龙虾覆盖（任务三，流程不变）。

清单字段定版：
  download_url     = https://harvenhub.com/api/hub/download/<完整包item_id>
  package_size     = 完整包字节数
  update_channel   = { from, file, md5, size, url }（增量通道，url 同上格式）
"""
import glob, hashlib, json, os, sys, zipfile

DESKTOP = os.path.join(os.environ.get('USERPROFILE', r'C:\Users\usera'), 'Desktop')
HUB_DL = 'https://harvenhub.com/api/hub/download/'


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def find_sync_zip(rtag):
    """按 rtag 找桌面清单同步包（HarvenHub_version清单_r44_*.zip），退化找最新"""
    hits = sorted(glob.glob(os.path.join(DESKTOP, f'HarvenHub_version清单_{rtag}_*.zip')),
                  key=os.path.getmtime)
    if hits:
        return hits[-1]
    allz = sorted(glob.glob(os.path.join(DESKTOP, 'HarvenHub_version清单_*.zip')),
                  key=os.path.getmtime)
    return allz[-1] if allz else None


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    full_id, delta_id = sys.argv[1].strip(), sys.argv[2].strip()
    sync_zip = sys.argv[3] if len(sys.argv) > 3 else None

    # 1. 定位清单同步包 + 读 version.json
    if not sync_zip:
        # 从清单 package_file 解析 rtag（HarvenAI_..._r44.zip → r44）
        probe = sorted(glob.glob(os.path.join(DESKTOP, 'HarvenHub_version清单_r*_*.zip')),
                       key=os.path.getmtime)
        if not probe:
            print('❌ 桌面找不到清单同步包 HarvenHub_version清单_r*.zip')
            sys.exit(1)
        sync_zip = probe[-1]
    with zipfile.ZipFile(sync_zip) as z:
        vj = json.loads(z.read('version.json'))
    pkg_file = vj.get('package_file', '')
    rtag = pkg_file.rsplit('_r', 1)[-1].replace('.zip', '') if '_r' in pkg_file else ''
    rtag = f'r{rtag}' if rtag else 'r?'

    # 2. 增量片段（pack_release --update-base 产物），缺席则不写增量通道
    frag_path = os.path.join(DESKTOP, '_update_channel_fragment.json')
    frag = None
    if os.path.isfile(frag_path):
        frag = json.load(open(frag_path, encoding='utf-8'))
        if not frag.get('url') and frag.get('file'):
            pass  # url 由本工具填
    else:
        print('⚠️ 桌面无 _update_channel_fragment.json，只写完整包直链（无增量通道）')

    # 3. 本地完整包对账（可选校验：package_file 对应文件在桌面则核 MD5）
    local_full = os.path.join(DESKTOP, pkg_file)
    if os.path.isfile(local_full):
        real = md5_of(local_full)
        if vj.get('package_md5') and real != vj['package_md5']:
            print(f'❌ 桌面完整包 MD5 与清单不符（{real} ≠ {vj["package_md5"]}），拒绝写清单')
            sys.exit(1)
        vj['package_size'] = os.path.getsize(local_full)
        print(f'✅ 完整包对账：{pkg_file} {vj["package_size"]:,}B MD5 一致')

    # 4. 写直链
    vj['download_url'] = HUB_DL + full_id
    if frag:
        vj['update_channel'] = {
            'from': frag.get('from', ''),
            'file': frag.get('file', ''),
            'md5': frag.get('md5', ''),
            'size': frag.get('size', 0),
            'url': HUB_DL + delta_id,
        }
        print(f"✅ 增量通道：from {vj['update_channel']['from']} · {vj['update_channel']['file']}"
              f" {vj['update_channel']['size']:,}B")

    # 5. 重打清单同步包（覆盖原 zip）
    tmp = sync_zip + '.new'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('version.json', json.dumps(vj, ensure_ascii=False, indent=2))
        # 原 README 原样带上
        try:
            with zipfile.ZipFile(sync_zip) as zo:
                z.writestr('README.txt', zo.read('README.txt'))
        except KeyError:
            pass
    os.replace(tmp, sync_zip)
    print(f'✅ 终版清单已写回：{os.path.basename(sync_zip)}（rtag={rtag}）')
    print('   下一步：清单同步包转龙虾覆盖 hubdata/version_manifest.json（任务三）')
    print(f'   终验：curl -s https://harvenhub.com/version.json 应含 download_url 尾号 {full_id[-8:]}')


if __name__ == '__main__':
    main()
