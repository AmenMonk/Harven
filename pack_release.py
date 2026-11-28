# -*- coding: utf-8 -*-
"""r 系列安装包规范打包器（★ 2026-10-07 方案B 落地）

用法：python pack_release.py <版本号如0.3.1> <r序号如r24> [上架] [--tier lite|standard|full] [--update-base <上一版完整包zip>]
     --tier：★ 三档安装包（2026-11-03 立项，定版拍板）：
       lite     = yolox + 人脸底座（识图走外脑，约 90MB）
       standard = yolox + chinese-clip + 人脸底座（相册自动分类，约 0.5~0.7GB）← 默认（向后兼容 r44）
       full     = standard + Qwen3-VL-4B 视觉大脑（开箱即满血，约 4GB）
     铁律：三档共用同一棵代码树，档位差异=只带哪些权重文件，权重当插件；
           version.json 注入 tier 字段供更新器按档推送（#1360 扩展）。
     --update-base：★ #1398（2026-11-02 定版拍板 A+B）出包后自动调 pack_update.py
       生成增量更新包（对比上一版完整包，KB~MB 级），并在桌面落
       _update_channel_fragment.json（含 from/file/md5/size，url 留空待上传后填 item_id）。
功能：
  1. 校验 src/version.py 版本号与入参一致（单一户口，改版只改 version.py）
  2. 从 _package_staging 打 zip（全层级排 __pycache__/.git/python/node_modules；仅 data/ 下排 models/media/sqlite/downloads）
     ★ r43 B 方案（10-30 定版拍板）：打包后自动注入两样——
       ① 捆绑裸 Python：staging/python/ 完整 3.13 运行时裁掉 site-packages/_excl_spack_old/
         _excl_libs/test/idlelib/turtledemo/__pycache__ 肥肉（~2.3G→~150MB），装完零 Python 依赖
       ② YoloX 检测权重：live 树 yolox_s.onnx（35,858,002 字节，与 src/vision/installer.py
         清单 size 一字不差，存在即跳过 GitHub 下载），首次视觉检测零等待
  3. 算 MD5，打印终验清单（包内新代码标记自查需另行确认）
  4. 自动生成「云端清单同步包」HarvenHub_version_同步包_<版本>_日期.zip 到桌面
     ——内含 version.json + 龙虾部署说明，转龙虾覆盖服务器挂载卷清单（hubdata/version_manifest.json）即可
  5. 可选参数「上架」= 顺带上传 Hub（不做删除旧版，删除仍走后台）
"""
import os, sys, zipfile, hashlib, json, datetime, fnmatch

STAGING = 'H:/HarvenAI/_package_staging'
DESKTOP = os.path.join(os.environ.get('USERPROFILE', r'C:\Users\usera'), 'Desktop')
# ★ 2026-10-07 r25 事故修复：排除规则拆两组（旧写法全层级匹配 'models' 误杀了 src/models 程序代码）
#   组1 全层级排除：构建产物/运行时，任何位置都不该进包
EXCLUDE_DIRS_ANY = {'__pycache__', '.git', 'python', 'node_modules'}
#   组2 仅 data/ 下排除：用户数据/大件（src 等代码区不受影响）
EXCLUDE_DIRS_IN_DATA = {'models', 'media', 'sqlite', 'downloads'}
EXCLUDE_EXT = ('.pyc', '.pyo', '.log')
# ★ r25 逐项检测沉淀（10-07）：测试/构建/重复配置残留，任何目录命中都不进包
EXCLUDE_FILES = {'_writetest.txt', '_build_exit.txt', '_build_log.txt', '_build_out.txt',
                 'build_log.txt', 'build_out_utf8.txt', 'build_out.txt', 'assets_list.txt',
                 'config.yaml.example'}  # 示例配置只保留 config.example.yaml
# ★ #1289（2026-10-29）：检测临时脚本模式排除——live 根目录 90+ log/txt、32 个 check*/test_*
#   脚本，曾 1 条 check_harven.py 混进 staging（质检实锤）。fnmatch 对文件名匹配，任何目录
#   命中都不进包。注意：故意不加 '_*.py'（会误杀全部 __init__.py）；README/requirements.txt 不命中。
EXCLUDE_PATTERNS = ('check*', 'test_*', 'selfcheck*', '_tmp*', '_fix*', '_diag*',
                    '_fridge*', 'flask*', 'dns*', 'curltest*', 'flush*',
                    '_verify_*', '_rescue_*', 'full_health_check*', 'vite.config.js.timestamp*')
# ★ r51 小白包瘦身（2026-11-28 老谭令「一键安装+清废料」）：仅顶层整剪（static/spa、
#   data/skills 等同名子目录不受影响）。spa=前端源码（运行时只用 static/spa 成品）、
#   tests=开发自检、tools=AR诊断/文档工具（开发者机自留）
EXCLUDE_TOP_DIRS = {'spa', 'tests', 'tools'}
#   开发者专用出包/部署件：小白装完用不上，clean_before_pack 尤其不能给（误跑删数据）
EXCLUDE_FILES = EXCLUDE_FILES | {'pack_release.py', 'pack_installer.py', 'pack_update.py',
                                 'clean_before_pack.py', 'install.sh', 'start.sh',
                                 'Dockerfile', 'docker-compose.yml', 'DOCKER.md',
                                 '.dockerignore', 'INSTALL_GUIDE_v2.0.md'}
# ★ zip 中文文件名说明：本打包器只出 Windows 用户安装包，Python zipfile 对非 ASCII 名
#   自动打 UTF-8 标志位，Win10/11 资源管理器解压正常（r25 实测用户可装）。
#   「中文文件名必须 ASCII」仅适用于 Linux 服务器部署包场景，勿在此误加（10-07 自检门当场抓到会砍掉启动HarvenAI.bat 等 28 个必需文件）。

# ★ r43 B 方案注入配置（10-30）
#   捆绑 Python 裁剪清单：Lib/ 直下整目录排除（三方库备份/非必需组件），其余全带
BUNDLED_PY_EXCL_TOP = {'site-packages', '_excl_spack_old', '_excl_libs',
                       'test', 'idlelib', 'turtledemo'}
#   YoloX 权重：从 live 树取（staging 不存 data/models），size 必须与 installer.py 清单一字不差
YOLOX_WEIGHT_SRC = 'H:/HarvenAI/data/models/vision/yolox/yolox_s.onnx'
YOLOX_WEIGHT_SIZE = 35_858_002
YOLOX_WEIGHT_IN_ZIP = 'data/models/vision/yolox/yolox_s.onnx'
#   ★ r44 修宪（2026-11-01 定版拍板）：宪法 v2 边界=「轻量底座（<1GB）进包，大模型按需下载」
#     chinese-clip（753MB）随包内置——下载源 hf-mirror 是公益镜像无可用性保障，
#     用户自己下容易出错；总量不变，只是把下载从不可控环节挪进自家安装包。
#     大模型（qwen3-vl 3.1~18.4GB）仍绝不进包，按显存推荐、用户点头才下。
CLIP_DIR_SRC = 'H:/HarvenAI/data/models/vision/chinese-clip'
CLIP_BIN_SIZE = 753_177_983   # 与 src/vision/installer.py 清单一字不差
CLIP_IN_ZIP_DIR = 'data/models/vision/chinese-clip'
CLIP_FILES = ('pytorch_model.bin', 'config.json', 'preprocessor_config.json', 'vocab.txt')
#   ★ 2026-11-27 拍板：人脸底座（YuNet 227KB + SFace 37MB）三档全随包——
#     原来是首次用人脸功能才从 GitHub 临时下载，国内网络常卡死用户；37MB 入包换掉一个卡死点
FACE_DIR_SRC = 'H:/HarvenAI/data/models/vision/face'
FACE_IN_ZIP_DIR = 'data/models/vision/face'
FACE_FILES = {  # 与 src/vision/installer.py #1470 清单一字不差
    'face_detection_yunet_2023mar.onnx': 232_589,
    'face_recognition_sface_2021dec.onnx': 38_696_353,
}
#   ★ 三档 full 档注入配置（2026-11-03 立项）：Qwen3-VL-4B 视觉大脑（Q4_K_M 主模型 + F16 视觉层）
#     来源魔搭官方 GGUF（Qwen/Qwen3-VL-4B-Instruct-GGUF），size 门与下载清单一字不差
QWEN_DIR_SRC = 'H:/HarvenAI/data/models/vision/qwen3-vl-4b'
QWEN_IN_ZIP_DIR = 'data/models/vision/qwen3-vl-4b'
QWEN_FILES = {
    'Qwen3VL-4B-Instruct-Q4_K_M.gguf': 2_497_281_664,
    'mmproj-Qwen3VL-4B-Instruct-F16.gguf': 836_180_256,
}
#   ★ #1425（2026-11-04）full 档捆绑 llama-cpp-python 运行组件（离线 wheel）：
#     实锤——完全体捆了 Qwen3-VL 权重但 venv 没装 llama-cpp-python，understand 槽报「缺组件」；
#     PyPI 官方无 cp313 win 预编译（只有 sdist），此 cu124 wheel 与 live venv 一字不差
#     （sha256 8bb06d66…，direct_url 实锤同源），常住 packer_assets/wheels/，full 档随包。
LLAMA_WHEEL_SRC = 'H:/HarvenAI/packer_assets/wheels/llama_cpp_python-0.4.5+cu124-cp313-cp313-win_amd64.whl'
LLAMA_WHEEL_NAME = 'llama_cpp_python-0.4.5+cu124-cp313-cp313-win_amd64.whl'
LLAMA_WHEEL_SIZE = 219_685_787
LLAMA_WHEEL_IN_ZIP = 'wheels/llama_cpp/llama_cpp_python-0.4.5+cu124-cp313-cp313-win_amd64.whl'
# ★ #1428（2026-11-04）：llama wheel 元数据钉 numpy<=2.3.2，而 requirements 装的是 numpy 2.4.x，
#   --no-index 离线模式下 pip 解不了冲突直接拒装（full 档实测实锤）。修法：bat 改 --no-deps，
#   运行时唯一缺的 diskcache（纯 Python）随包捆入。依赖链实测：diskcache/jinja2/Pillow/
#   typing-extensions（requirements 已有）+ numpy（--no-deps 跳过，2.4.x 上 import 全绿）。
DISKCACHE_WHEEL_SRC = 'H:/HarvenAI/packer_assets/wheels/diskcache-5.6.3-py3-none-any.whl'
DISKCACHE_WHEEL_NAME = 'diskcache-5.6.3-py3-none-any.whl'
DISKCACHE_WHEEL_SIZE = 45_550
DISKCACHE_WHEEL_IN_ZIP = 'wheels/llama_cpp/diskcache-5.6.3-py3-none-any.whl'
TIERS = ('lite', 'standard', 'full')


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    ver, rtag = sys.argv[1], sys.argv[2]
    do_upload = len(sys.argv) > 3 and sys.argv[3] == '上架'
    # ★ 三档参数（2026-11-03 立项）：默认 standard（r44 行为不变）
    tier = 'standard'
    if '--tier' in sys.argv:
        _ti = sys.argv.index('--tier')
        if _ti + 1 < len(sys.argv):
            tier = sys.argv[_ti + 1].lower()
    if tier not in TIERS:
        print(f'❌ tier 必须是 {TIERS} 之一，收到：{tier}'); sys.exit(1)
    # ★ #1398 增量基准包参数（可落在任意位置）
    update_base = None
    if '--update-base' in sys.argv:
        _ui = sys.argv.index('--update-base')
        if _ui + 1 < len(sys.argv):
            update_base = sys.argv[_ui + 1]
    today = datetime.date.today().strftime('%Y%m%d')

    # 1. 版本户口校验
    sys.path.insert(0, 'H:/HarvenAI')
    from src.version import APP_VERSION
    if APP_VERSION != ver:
        print(f'❌ 版本不一致：src/version.py 是 {APP_VERSION}，入参是 {ver}。\n'
              f'   发新版请先改 src/version.py，再跑打包器。')
        sys.exit(1)
    print(f'✅ 版本户口一致：{APP_VERSION}（{rtag}）')

    # 2. 打包（★ 三档：包名带 tier 段）
    zip_path = os.path.join(DESKTOP, f'HarvenAI_v{ver}_{tier}_Windows_{today}_{rtag}.zip')
    if os.path.exists(zip_path):
        os.remove(zip_path)
    n = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(STAGING):
            rel_root = os.path.relpath(root, STAGING).replace('\\', '/')
            if rel_root.split('/')[0] in EXCLUDE_TOP_DIRS:
                continue
            in_data = (rel_root == 'data' or rel_root.startswith('data/'))
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS_ANY
                       and not (in_data and d in EXCLUDE_DIRS_IN_DATA)]
            for f in files:
                if f.endswith(EXCLUDE_EXT) or f in EXCLUDE_FILES:
                    continue
                if any(fnmatch.fnmatch(f, _p) for _p in EXCLUDE_PATTERNS):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, STAGING)
                if rel == 'version.json':
                    # ★ 三档终检补丁（2026-11-04）：包内 version.json 原件注入 tier
                    #   （此前只注清单包副本，包内漏 tier——D2 终检项实锤后修）
                    try:
                        with open(full, encoding='utf-8') as _f:
                            _vj0 = json.load(_f)
                        _vj0['tier'] = tier
                        zf.writestr('version.json', json.dumps(_vj0, ensure_ascii=False, indent=2))
                    except Exception:
                        zf.write(full, rel)
                else:
                    zf.write(full, rel)
                n += 1
    # ★ r43 2.2 捆绑注入（B 方案）：裸 Python + YoloX 权重（直接续写同一 zip）
    n_py = 0
    py_src = os.path.join(STAGING, 'python')
    py_exe = os.path.join(py_src, 'python.exe')
    with zipfile.ZipFile(zip_path, 'a', zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(py_exe):
            for root, dirs, files in os.walk(py_src):
                rel_root = os.path.relpath(root, py_src).replace('\\', '/')
                dirs[:] = [d for d in dirs if d != '__pycache__'
                           and not (rel_root == 'Lib' and d in BUNDLED_PY_EXCL_TOP)
                           and not (rel_root == '.' and d in {'_excl_libs'})]  # 根下链接器 .lib 亦裁
                for f in files:
                    if f.endswith(EXCLUDE_EXT):
                        continue
                    full = os.path.join(root, f)
                    zf.write(full, 'python/' + os.path.relpath(full, py_src).replace('\\', '/'))
                    n_py += 1
            print(f'✅ 捆绑 Python 注入：{n_py} 个文件（裸运行时，装完零 Python 依赖）')
        else:
            print(f'⛔ 捆绑 Python 缺失：{py_exe} 不存在，B 方案要求必带，拒绝出包！')
            sys.exit(1)
        if os.path.exists(YOLOX_WEIGHT_SRC):
            actual = os.path.getsize(YOLOX_WEIGHT_SRC)
            if actual != YOLOX_WEIGHT_SIZE:
                print(f'⛔ YoloX 权重 size 不符：本地 {actual} ≠ installer 清单 {YOLOX_WEIGHT_SIZE}，'
                      f'带错权重会让安装向导重复下载或校验失败，拒绝出包！')
                sys.exit(1)
            zf.write(YOLOX_WEIGHT_SRC, YOLOX_WEIGHT_IN_ZIP)
            zf.writestr('data/models/vision/yolox/WEIGHTS-NOTE.txt',
                        'yolox_s.onnx 来源：YOLOX 官方 release 0.1.1rc0\n'
                        '许可：YOLOX 代码 Apache-2.0（https://github.com/Megvii-BaseDetection/YOLOX）\n'
                        '说明：哈文视觉检测底座权重，已随安装包内置，无需联网下载。\n')
            print(f'✅ YoloX 权重注入：yolox_s.onnx {YOLOX_WEIGHT_SIZE} 字节（首次视觉检测免下载）')
        else:
            print(f'⛔ YoloX 权重缺失：{YOLOX_WEIGHT_SRC} 不存在，B 方案要求必带，拒绝出包！')
            sys.exit(1)
        # ★ r44 修宪：chinese-clip 底座随包（bin 753MB + 3 个小配置文件，装完零下载）
        #   ★ 三档：lite 档不带 CLIP（识图走外脑），standard/full 必带
        clip_bin = os.path.join(CLIP_DIR_SRC, 'pytorch_model.bin')
        if tier == 'lite':
            print(f'ℹ️ lite 档：跳过 CLIP 底座（识图理解走外脑，相册自动分类后补可原地升级）')
        elif os.path.exists(clip_bin):
            actual = os.path.getsize(clip_bin)
            if actual != CLIP_BIN_SIZE:
                print(f'⛔ CLIP 权重 size 不符：本地 {actual} ≠ installer 清单 {CLIP_BIN_SIZE}，'
                      f'带错权重会让视觉引擎校验失败，拒绝出包！')
                sys.exit(1)
            for _fname in CLIP_FILES:
                _src = os.path.join(CLIP_DIR_SRC, _fname)
                if not os.path.exists(_src):
                    print(f'⛔ CLIP 配套文件缺失：{_src}，拒绝出包！')
                    sys.exit(1)
                zf.write(_src, f'{CLIP_IN_ZIP_DIR}/{_fname}')
            zf.writestr('data/models/vision/chinese-clip/WEIGHTS-NOTE.txt',
                        'chinese-clip-vit-base-patch16 来源：OFA-Sys 官方权重（经 hf-mirror 核对 SHA256）\n'
                        '许可：Chinese-CLIP Apache-2.0（https://github.com/OFA-Sys/Chinese-CLIP）\n'
                        '说明：哈文视觉理解底座，已随安装包内置，无需联网下载。\n')
            print(f'✅ CLIP 底座注入：chinese-clip {len(CLIP_FILES)} 文件（bin {CLIP_BIN_SIZE} 字节，首次视觉理解免下载）')
        else:
            print(f'⛔ CLIP 权重缺失：{clip_bin} 不存在，r44 修宪要求必带，拒绝出包！')
            sys.exit(1)
        # ★ 2026-11-27 拍板：人脸底座三档全随包（YuNet+SFace 共 37MB）——
        #   首用人脸功能不再临时从 GitHub 下载（国内常卡死），开箱即用
        _face_ok = True
        for _ff, _fsize in FACE_FILES.items():
            _fsrc = os.path.join(FACE_DIR_SRC, _ff)
            if not os.path.exists(_fsrc):
                print(f'⛔ 人脸底座权重缺失：{_fsrc}，拒绝出包！'); _face_ok = False; break
            if os.path.getsize(_fsrc) != _fsize:
                print(f'⛔ 人脸底座 size 不符：{_ff} 本地 {os.path.getsize(_fsrc)} ≠ installer 清单 {_fsize}，拒绝出包！')
                _face_ok = False; break
            zf.write(_fsrc, f'{FACE_IN_ZIP_DIR}/{_ff}')
        if _face_ok:
            zf.writestr('data/models/vision/face/WEIGHTS-NOTE.txt',
                        'YuNet 人脸检测 + SFace 人脸识别 来源：OpenCV Zoo 官方（opencv_zoo）\n'
                        '许可：Apache-2.0（https://github.com/opencv/opencv_zoo）\n'
                        '说明：哈文人脸底座（检测+识别，共 37MB），已随安装包内置，无需联网下载。\n')
            print(f'✅ 人脸底座注入：YuNet+SFace {len(FACE_FILES)} 文件（37MB，人脸功能开箱即用）')
        else:
            sys.exit(1)
        # ★ 三档 full：Qwen3-VL-4B 视觉大脑随包（主模型 + 视觉层，装完即满血）
        if tier == 'full':
            _qwen_ok = True
            for _qf, _qsize in QWEN_FILES.items():
                _qsrc = os.path.join(QWEN_DIR_SRC, _qf)
                if not os.path.exists(_qsrc):
                    print(f'⛔ full 档权重缺失：{_qsrc}，拒绝出包！'); _qwen_ok = False; break
                if os.path.getsize(_qsrc) != _qsize:
                    print(f'⛔ full 档权重 size 不符：{_qf} 本地 {os.path.getsize(_qsrc)} ≠ 清单 {_qsize}，拒绝出包！')
                    _qwen_ok = False; break
                zf.write(_qsrc, f'{QWEN_IN_ZIP_DIR}/{_qf}')
            if _qwen_ok:
                zf.writestr('data/models/vision/qwen3-vl-4b/WEIGHTS-NOTE.txt',
                            'Qwen3-VL-4B-Instruct GGUF 来源：魔搭 Qwen/Qwen3-VL-4B-Instruct-GGUF（官方）\n'
                            '许可：Apache-2.0（https://github.com/QwenLM/Qwen3-VL）\n'
                            '说明：哈文完全体视觉大脑（Q4_K_M 主模型 + F16 视觉层），已随安装包内置。\n')
                print(f'✅ Qwen3-VL-4B 视觉大脑注入：{len(QWEN_FILES)} 文件（full 档开箱即满血）')
            else:
                sys.exit(1)
        # ★ 档位说明文件进包（防呆：拿到包的人一眼知道这是哪档）
        zf.writestr('TIER-NOTE.txt',
                    f'档位：{tier}\n'
                    + ('完全体：yolox + Chinese-CLIP + Qwen3-VL-4B 视觉大脑 + 人脸底座全内置，开箱即满血（需 8GB+ 显存）\n' if tier == 'full' else
                       '标准版：yolox + Chinese-CLIP + 人脸底座内置（相册自动分类/人脸识别开箱即用）；视觉大脑按需一键装/导入\n' if tier == 'standard' else
                       '轻装版：yolox + 人脸底座内置（识图理解走外脑，人脸识别可用）；换高配机可原地升级满血，无需重装\n'))
        print(f'✅ 档位说明注入：TIER-NOTE.txt（{tier}）')
        # ★ #1425：机器可读档位标记（silent_install.bat 用它决定要不要离线装 llama-cpp wheel）
        zf.writestr('TIER', tier)
        print(f'✅ 档位标记注入：TIER（{tier}，安装脚本机器可读）')
        # ★ #1425：full 档捆绑 llama-cpp-python 离线 wheel（安装脚本本地装，不走网络）
        if tier == 'full':
            if not os.path.exists(LLAMA_WHEEL_SRC):
                print(f'⛔ full 档 wheel 缺失：{LLAMA_WHEEL_SRC}，拒绝出包！'); sys.exit(1)
            if os.path.getsize(LLAMA_WHEEL_SRC) != LLAMA_WHEEL_SIZE:
                print(f'⛔ full 档 wheel size 不符：本地 {os.path.getsize(LLAMA_WHEEL_SRC)} ≠ 清单 {LLAMA_WHEEL_SIZE}，拒绝出包！')
                sys.exit(1)
            zf.write(LLAMA_WHEEL_SRC, LLAMA_WHEEL_IN_ZIP)
            # ★ #1428：diskcache 随包（llama_cpp 运行时必需，requirements 无此项，离线装）
            if not os.path.exists(DISKCACHE_WHEEL_SRC):
                print(f'⛔ full 档 diskcache wheel 缺失：{DISKCACHE_WHEEL_SRC}，拒绝出包！'); sys.exit(1)
            if os.path.getsize(DISKCACHE_WHEEL_SRC) != DISKCACHE_WHEEL_SIZE:
                print(f'⛔ full 档 diskcache wheel size 不符：本地 {os.path.getsize(DISKCACHE_WHEEL_SRC)} ≠ 清单 {DISKCACHE_WHEEL_SIZE}，拒绝出包！')
                sys.exit(1)
            zf.write(DISKCACHE_WHEEL_SRC, DISKCACHE_WHEEL_IN_ZIP)
            zf.writestr('wheels/llama_cpp/WHEEL-NOTE.txt',
                        'llama-cpp-python 0.4.5+cu124 cp313 win_amd64 离线安装组件\n'
                        '来源：与哈文 live 环境同源 wheel（sha256 8bb06d66…）\n'
                        '说明：完全体视觉大脑（Qwen3-VL）的进程内推理引擎，安装时自动本地装入，无需联网。\n'
                        '附带：diskcache 5.6.3（推理引擎运行时依赖，#1428）。\n')
            print(f'✅ llama-cpp 离线组件注入：{LLAMA_WHEEL_NAME}（{LLAMA_WHEEL_SIZE} 字节，full 档）')
            print(f'✅ diskcache 离线组件注入：{DISKCACHE_WHEEL_NAME}（{DISKCACHE_WHEEL_SIZE} 字节，full 档）')
    size_mb = os.path.getsize(zip_path) / 1024 / 1024

    # ★ 2.5 包后自检门（r25 事故沉淀）：关键文件缺一即删包拒出，杜绝「打完才发现少文件」
    MUST_HAVE = [
        'src/models/__init__.py',      # r25 误杀事故主角（web_app.py 第435行 import）
        'src/models/database.py',
        'web_app.py',
        'src/version.py',
        'static/spa/index.html',
        'static/spa/sw.js',
        'config.example.yaml',
        '启动HarvenAI.bat',
        'requirements.txt',
        # ★ r43 B 方案捆绑件哨兵（裸 Python + YoloX 权重必须随包）
        'python/python.exe',
        'python/python313.dll',
        'python/Lib/os.py',            # 标准库抽检（venv/ensurepip 依赖 stdlib 完整）
        'data/models/vision/yolox/yolox_s.onnx',
        # ★ 2026-11-27 拍板：人脸底座三档全随包（YuNet+SFace），缺一拒出包
        'data/models/vision/face/face_detection_yunet_2023mar.onnx',
        'data/models/vision/face/face_recognition_sface_2021dec.onnx',
        'TIER-NOTE.txt',               # ★ 三档：档位说明必随包
    ]
    # ★ 三档：CLIP 哨兵仅 standard/full；Qwen3-VL 哨兵仅 full
    if tier != 'lite':
        MUST_HAVE += [
            'data/models/vision/chinese-clip/pytorch_model.bin',
            'data/models/vision/chinese-clip/config.json',
            'data/models/vision/chinese-clip/preprocessor_config.json',
            'data/models/vision/chinese-clip/vocab.txt',
        ]
    if tier == 'full':
        MUST_HAVE += [
            'data/models/vision/qwen3-vl-4b/Qwen3VL-4B-Instruct-Q4_K_M.gguf',
            'data/models/vision/qwen3-vl-4b/mmproj-Qwen3VL-4B-Instruct-F16.gguf',
            # ★ #1425：离线 llama-cpp 组件哨兵（full 档必有）
            LLAMA_WHEEL_IN_ZIP,
            DISKCACHE_WHEEL_IN_ZIP,
        ]
    # ★ #1425：机器可读档位标记三档必有（安装脚本依赖；TIER-NOTE.txt 已在通用清单）
    MUST_HAVE += ['TIER']
    # ★ 出厂自带面板哨兵（10-07 补齐 12 面板出厂：定制页只能进出厂，模块库 TPL 复刻不了）
    MUST_HAVE += [
        'data/panels/fridge_stock_1/panel.json',
        'data/panels/kid_archive/panel.json',
        'data/panels/family_todo/panel.json',
        'data/panels/dream_journal/panel.json',
    ]
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    missing = [p for p in MUST_HAVE if p.replace('/', '\\') not in names and p not in names]
    if missing:
        os.remove(zip_path)
        print(f'⛔ 自检门拦截：包内缺少关键文件，已删除坏包！缺失清单：')
        for p in missing:
            print(f'   - {p}')
        print('   请检查 staging 源头后重跑打包器。')
        sys.exit(1)
    # 反向抽查：data/models（模型库）绝不该进包——修宪豁免按档：
    #   standard/full：yolox + chinese-clip + face（2026-11-27 拍板三档全随包）；
    #   full 另豁免 qwen3-vl-4b（均带 WEIGHTS-NOTE.txt）
    _allow_models = ['data/models/vision/yolox/', 'data/models/vision/face/']
    if tier != 'lite':
        _allow_models.append('data/models/vision/chinese-clip/')
    if tier == 'full':
        _allow_models.append('data/models/vision/qwen3-vl-4b/')
    leaked = [p for p in names if p.startswith('data/models/')
              and not any(p.startswith(_a) for _a in _allow_models)]
    if leaked:
        os.remove(zip_path)
        print(f'⛔ 自检门拦截：data/models 模型库泄漏进包（{len(leaked)} 个文件），已删除坏包！')
        for p in leaked:
            print(f'   - {p}')
        print('   请检查 staging 源头后重跑打包器。')
        sys.exit(1)
    # ★ 反向多余文件检查（10-10 全面检测沉淀）：脏副本混入 staging 会被打进包。
    #   r28/r29/r30 三包全带 src/core/web_app.py（09-27 误拷残留，396KB 旧版主程序副本，
    #   与根目录真身同名易混淆）。命中即拦：删包拒出。
    FORBIDDEN = [
        'src/core/web_app.py',   # 旧版主程序误拷残留（真身在根目录 web_app.py）
    ]
    dirty = [p for p in FORBIDDEN if p in names]
    if dirty:
        os.remove(zip_path)
        print(f'⛔ 自检门拦截：包内混入多余的脏文件，已删除坏包！清单：')
        for p in dirty:
            print(f'   - {p}')
        print('   请检查 staging 源头（多为误拷残留），删除后重跑打包器。')
        sys.exit(1)
    print(f'✅ 反向检查通过：{len(FORBIDDEN)} 个已知脏文件未混入')
    md5 = md5_of(zip_path)
    print(f'✅ 自检门通过：{len(MUST_HAVE)} 关键文件齐 + data/models 未泄漏')
    print(f'✅ 打包完成：{os.path.basename(zip_path)}  {size_mb:.2f}MB  {n} 条目  MD5 {md5}')

    # 3. 云端清单同步包（version.json + 龙虾说明）
    # ★ 2026-10-10 定版拍板「不要精简版」：云端清单直接复用安装包内的完整 version.json
    #   （带日期/详细 changelog/min_harven_version），老用户检查更新能看到完整更新内容。
    #   ★ 逐字节原样复制（loads→dump 会变序列化格式），读不到才退回精简兜底，出包永不中断。
    vj_raw = None
    try:
        with zipfile.ZipFile(zip_path) as _z:
            if 'version.json' in _z.namelist():
                vj_raw = _z.read('version.json')
    except Exception as _e:
        print(f'⚠️ 安装包内 version.json 读取失败（{_e}），清单退回精简版')
    vdir = os.path.join(DESKTOP, f'_version_sync_{rtag}_{tier}')
    os.makedirs(vdir, exist_ok=True)
    vj_path = os.path.join(vdir, 'version.json')
    if vj_raw and json.loads(vj_raw).get('version'):
        # ★ r37 清单修复（10-11）：清单副本注入 package_md5/package_file 供龙虾覆盖后核对。
        #   安装包内原件不注入——zip 自身 MD5 无法写进 zip 内的文件（鸡生蛋）；
        #   清单包在安装包建成后生成，此时 MD5 已知，只注清单侧。
        try:
            _vj = json.loads(vj_raw)
            _vj['package_md5'] = md5
            _vj['package_file'] = os.path.basename(zip_path)
            _vj['tier'] = tier   # ★ 三档：更新器按档推送（#1360 扩展，立项铁律）
            with open(vj_path, 'w', encoding='utf-8') as f:
                json.dump(_vj, f, ensure_ascii=False, indent=2)
        except Exception:
            with open(vj_path, 'wb') as f:
                f.write(vj_raw)
    else:
        vj = {
            'version': APP_VERSION,
            'url': 'https://harvenhub.com',
            'changelog': f'HarvenAI v{APP_VERSION} {rtag}（{today}）',
        }
        with open(vj_path, 'w', encoding='utf-8') as f:
            json.dump(vj, f, ensure_ascii=False)
    readme = os.path.join(vdir, 'README.txt')  # ★ Linux 解压中文文件名乱码（统计包教训），部署类说明一律 ASCII 名
    with open(readme, 'w', encoding='utf-8') as f:
        f.write(
            '【龙虾部署 · 云端版本清单更新】\n'
            '【2026-10-10 更新：version.json 已动态化（统计AB），改挂载卷文件，不是 web 根目录！】\n'
            '1. 把本目录 version.json 覆盖到服务器 Docker 挂载卷：\n'
            '       <hub目录>/hubdata/version_manifest.json\n'
            '   （即容器内 /app/app/storage/version_manifest.json；ls 同目录应能见到 harvenhub.db）\n'
            f'2. 验证：浏览器打开 https://harvenhub.com/version.json 应显示 version={APP_VERSION}\n'
            f'   核对：version.json 的 package_md5 应为 {md5}\n'
            '3. 即刻生效，无需重启容器（每次请求现读该文件）\n')
    sync_zip = os.path.join(DESKTOP, f'HarvenHub_version清单_{rtag}_{tier}_v{APP_VERSION}_{today}.zip')
    if os.path.exists(sync_zip):
        os.remove(sync_zip)
    with zipfile.ZipFile(sync_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(vj_path, 'version.json')
        zf.write(readme, 'README.txt')
    print(f'✅ 云端清单同步包：{os.path.basename(sync_zip)}（转龙虾覆盖即生效）')

    # 4. 可选上架
    if do_upload:
        import requests
        form = {
            'name': f'HarvenAI v{ver} Windows 安装包 {rtag}',
            'type': 'bundle', 'category': 'system', 'author_id': 'system',
            'version': ver,
            'description': f'{rtag}（{today}）。版本 {ver}，MD5 {md5}。',
        }
        with open(zip_path, 'rb') as f:
            r = requests.post('https://harvenhub.com/api/hub/upload', data=form,
                              files={'file': (os.path.basename(zip_path), f, 'application/zip')}, timeout=300)
        print(f'上架结果：HTTP {r.status_code} {r.text[:200]}')
    print('\n完成。开发者侧待办：重启哈文生效（如本次包含 py 改动）。')

    # 5. ★ #1398 增量更新包（可选，--update-base 传入上一版完整包）
    if update_base:
        if not os.path.isfile(update_base):
            print(f'⚠️ --update-base 文件不存在，跳过增量包：{update_base}')
        else:
            print(f'\n── 5. 生成增量更新包（基准：{os.path.basename(update_base)}）──')
            import subprocess as _sp
            _r = _sp.run([sys.executable, 'H:/HarvenAI/pack_update.py', update_base],
                         capture_output=True, text=True, cwd='H:/HarvenAI')
            print((_r.stdout or '')[-800:])
            if _r.returncode != 0:
                print(f'❌ pack_update 失败：{(_r.stderr or "")[-400:]}')
            else:
                # 找桌面最新的更新包（pack_update.py 命名：HarvenAI_更新包_from<rN>_<日期>.zip）
                import glob as _glob, re as _re
                _cands = sorted(_glob.glob(os.path.join(DESKTOP, 'HarvenAI_更新包_from*.zip')), key=os.path.getmtime)
                if not _cands:
                    print('⚠️ 桌面未找到增量包产物，请手查 pack_update.py 输出')
                else:
                    delta_path = _cands[-1]
                    # 基准包 r 序号（从旧包文件名解析）
                    _m = _re.search(r'_r(\d+)\.zip$', os.path.basename(update_base))
                    from_rtag = f'r{_m.group(1)}' if _m else 'prev'
                    d_md5, d_size = md5_of(delta_path), os.path.getsize(delta_path)
                    frag = {
                        'from': from_rtag,
                        'file': os.path.basename(delta_path),
                        'md5': d_md5,
                        'size': d_size,
                        'url': '',   # ★ 上传 Hub 后跑 _finalize_manifest.py 填 item_id 下载直链
                    }
                    frag_path = os.path.join(DESKTOP, '_update_channel_fragment.json')
                    with open(frag_path, 'w', encoding='utf-8') as f:
                        json.dump(frag, f, ensure_ascii=False, indent=2)
                    print(f'✅ 增量包：{os.path.basename(delta_path)}  {d_size:,}B  MD5 {d_md5}')
                    print(f'✅ 增量通道片段：{frag_path}')
                    print('   下一步：① 完整包+增量包都传 Hub 拿 item_id → ② python _finalize_manifest.py <完整id> <增量id> → ③ 清单同步包转龙虾')


if __name__ == '__main__':
    main()
