# 哈文 APP v0.1-test · APK 构建指引

> 项目：`H:\HarvenAI\mobile_app`（Capacitor 6 壳）
> 现状：安卓工程已生成、壳页面已同步（`cap sync` 已跑过），**只差编译环境出 APK**。

---

## 一、这个壳是怎么工作的（30 秒看懂）

1. APP 打开 → 显示「连接地址」页（官方鸭子图 + 输入框）
2. 填电脑上哈文的地址（如 `192.168.1.57:7000`）→ 自动跳 `http://地址/spa/`
3. 之后 APP 里看到的就是电脑上同一套哈文界面——哈文升级，APP 无需重装（H5 热更）
4. 换地址：长按鸭子图标 3 秒回到填写页

## 二、本机出包（方案 A，推荐先试）

### 1. 装 JDK 17（必须 17，21 不兼容当前 Gradle）
- 下载 Eclipse Temurin 17（JDK）：https://adoptium.net/temurin/releases/?version=17
- 安装时勾选「Set JAVA_HOME」

### 2. 装 Android Studio（自带 SDK，最省事）
- 下载：https://developer.android.com/studio （或国内镜像站搜 Android Studio）
- 首次启动按默认安装 SDK Platform 34 即可

### 3. 编译（PowerShell 里执行）
```powershell
cd H:\HarvenAI\mobile_app\android
.\gradlew.bat assembleDebug
```
- 第一次会自动下载 Gradle 8.x，约 10-20 分钟，属正常
- 成功后 APK 在：
  `H:\HarvenAI\mobile_app\android\app\build\outputs\apk\debug\app-debug.apk`

### 4. 装到手机
- 把 `app-debug.apk` 发到华为手机（微信/QQ/数据线均可）
- 手机上点开安装（需允许「安装未知应用」）
- HarmonyOS 2/3/4 可装；HarmonyOS NEXT 不支持 APK

## 三、不装环境：交龙虾/服务器出包（方案 B）

把 `H:\HarvenAI\mobile_app` 整个目录打包发给龙虾，服务器上执行：
```bash
cd mobile_app/android
./gradlew assembleDebug
```
服务器需：JDK 17 + Android SDK 34。产物同样是 `app-debug.apk`。

## 四、以后改壳页面怎么更新

1. 改 `mobile_app\www\` 里的文件
2. 在 `mobile_app` 目录执行：
```powershell
C:\Users\usera\.workbuddy\binaries\node\versions\22.22.2-3\npx.cmd cap sync android
```
3. 重新 `gradlew assembleDebug` 出新 APK

（真正的功能更新不用重装 APP——哈文电脑端升级后，APP 里看到的界面自动就是新的。）

## 五、测试版注意

- APP 首次使用需手机和电脑在**同一 WiFi** 下
- 电脑哈文必须开着（端口 7000）
- 壳页面内置默认流程与 Hub 扫码兼容：地址带 `?server=` 参数可自动填入
