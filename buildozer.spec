[app]
# 应用标题与包名
title = LanServer
package.name = lanserver
package.domain = org.marvis

# 源码目录与版本
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0.0

# 依赖：仅 Kivy（极简宿主界面），socket / threading / os / subprocess 均为标准库
requirements = python3,kivy

# 界面方向与全屏
orientation = portrait
fullscreen = 0

# 权限：联网 + 前台服务通知 + 读写存储（文件浏览 / 取回 / 保存均无需 root）
android.permissions = INTERNET,FOREGROUND_SERVICE,POST_NOTIFICATIONS,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,REQUEST_INSTALL_PACKAGES

# 目标与最低 API
android.api = 33
android.minapi = 21

# 固定 build-tools 版本（37.0.0 过新且许可证常被 sdkmanager 拒绝，34.0.0 兼容性最稳）
android.build_tools_version = 34.0.0

# CPU 架构
android.archs = arm64-v8a,armeabi-v7a

# 入口：lan_server.py 内置安卓 APK 环境检测，自动启动 Kivy 极简宿主，
# 并在后台线程运行 LanServer socket 服务（文件端口 50001 / 指令端口 50002）
android.entrypoint = lan_server.py

# 数据备份
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 0

# 锁定 python-for-android 到 PR #3360 修复版本（d2ee8c54）：
# - venv 创建时 --clear，清理上次失败残留的损坏 pip
# - 移除 pip install -U pip 自升级，避免新版 pip 移除
#   BuildDependencyInstallError 后 p4a import 崩溃（issue #3364）
p4a.branch = develop
p4a.commit = d2ee8c54d9d42375a95f18159e950a119671cf63
