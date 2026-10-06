# -*- coding: utf-8 -*-
"""
lan_server.py —— 被控端远程服务（跨平台，无 GUI，纯标准库）
================================================================
常驻后台，监听两个 TCP 端口：
  文件传输端口（默认 50001）：接收控制端发来的文件，保存到指定目录
  指令端口     （默认 50002）：接收控制端指令并执行，支持：
    - OPEN_FILE : 打开指定文件/URL（用系统方式）
    - RUN_CMD   : 启动程序 / 执行命令（电脑与安卓按平台分发）
    - SHUTDOWN  : 关闭被控设备（电脑直接执行；安卓需 root 或设备管理器权限）
    - REBOOT    : 重启被控设备（同上）

适用平台：Windows / Linux / macOS / 安卓 Termux / 安卓 APK（Kivy 打包）
本文件严禁 import tkinter，可在无图形环境的后台运行。

────────────────────────────────────────────────────
运行方法（命令行）
────────────────────────────────────────────────────
  # 默认参数运行：文件端口 50001、指令端口 50002、密钥 888888、保存到脚本目录
  python lan_server.py

  # 修改基础端口（文件端口为 50001，指令端口自动为 50002，即 port+1）
  python lan_server.py --port 50001

  # 分别指定两个端口、连接密钥、保存目录
  python lan_server.py --file-port 50001 --cmd-port 50002 --auth-key 888888 --save-dir D:/received

  # 安卓 Termux 示例（保存到手机下载目录）
  python lan_server.py --save-dir /sdcard/Download

────────────────────────────────────────────────────
连接协议（控制端 lan_client.py 遵循同一协议）
────────────────────────────────────────────────────
  1) 任何连接必须先发认证包：  AUTH:<连接密钥>\n
     服务端校验通过回复 OK\n；失败回复 ERR:auth\n 并断开。
  2) 文件传输（连接文件端口）：
     认证后发送  SEND:<文件大小字节>:<相对路径/文件名>\n
     服务端回复 OK\n 后开始接收二进制数据；收完回复 OK:已保存到 <绝对路径>。
  3) 指令通道（连接指令端口），认证后可连发多条指令，每行一条：
     OPEN_FILE:<对方设备上的绝对路径>     -> 打开文件，回执 OK:已打开 <路径> / ERR:...
     RUN_CMD:<命令字符串或程序路径>       -> 启动程序/执行命令
         电脑（Windows）：支持 notepad、C:/Windows/System32/notepad.exe 等
         安卓（APK/Termux）：支持 shell 命令（ls、df）、am start 命令、
             或直接填应用包名（如 com.android.chrome）
         回执 OK:已启动，PID=<pid> / ERR:...
     LIST_DIR:<目录绝对路径>           -> 列出目录内容（无需 root，安卓从 /sdcard 开始）
     SHUTDOWN                          -> 关闭设备，回执 OK:已执行关机
     REBOOT                            -> 重启设备，回执 OK:已执行重启
         安卓/Linux 无 root 权限时如实回执 ERR:权限不足，无法关机（需 root 或设备管理器权限）
     LIST_DIR 成功：先回 OK:LIST:<条目数>，随后逐行返回条目（名称\t类型(dir/file/other)\t大小\t修改时间），最后以 END 结束；失败：ERR:路径不存在 / ERR:权限不足
     DOWNLOAD 成功：回 OK:DOWNLOAD:<文件名>:<大小>，等控制端回 OK 后分块发送数据，发完回 OK:DL-DONE；失败：ERR:文件不存在 / ERR:权限不足
  ★ 提示：普通安卓 APK 无法直接关机/重启（需 root 或设备管理器权限），
    无权限时被控端会如实返回"权限不足"；启动程序/打开文件用 am start 正常可用。

────────────────────────────────────────────────────
★ 开机自启 / 常驻设置
────────────────────────────────────────────────────
【Windows】
  方法一（启动文件夹）：Win+R 输入 shell:startup 回车，把本脚本快捷方式放入，
       目标设为： pythonw "C:\你的路径\lan_server.py"
  方法二（任务计划程序）：
       schtasks /create /tn "LanServer" /tr "pythonw \"C:\你的路径\lan_server.py\"" /sc onstart /ru SYSTEM
       （或 taskschd.msc 图形界面：触发器=启动时，操作=启动程序 pythonw.exe）
  注：pythonw 无控制台窗口，适合后台静默；调试用 python 可看到日志。

【安卓 Termux】
  方法一：安装 Termux:Boot（Play/F-Droid），把启动命令写入 ~/.termux/boot/ 下的 .sh 文件：
       mkdir -p ~/.termux/boot
       echo 'python /data/data/com.termux/files/home/lan_server.py --save-dir /sdcard/Download' > ~/.termux/boot/start_lan.sh
       chmod +x ~/.termux/boot/start_lan.sh
       （需保持 Termux 通知常驻权限）
  方法二：手动常驻——Termux 里运行 nohup python lan_server.py >/dev/null 2>&1 &

【安卓 APK（buildozer 打包）】
  安卓系统对后台进程有严格限制，纯 socket 服务会被系统杀掉。
  建议：将 App 设为自启动应用，并把服务线程放进前台服务 + 常驻通知（见 README_APK.md），
       并引导用户在系统设置中把应用设为"不受电池优化限制"。
"""

import os
import re
import sys
import time
import shutil
import socket
import threading
import subprocess
import platform
import argparse

# ============ 可配置常量（★ 修改请直接改这里） ============
AUTH_KEY = "888888"          # 连接密钥：控制端必须与此一致，否则拒绝接入
DEFAULT_FILE_PORT = 50001    # 文件传输端口
DEFAULT_CMD_PORT = 50002     # 指令端口
BUFFER_SIZE = 64 * 1024      # 传输缓冲（64KB）
AUTH_TIMEOUT = 10            # 认证等待超时（秒）
_last_logs = []               # 日志缓冲（供 Kivy 宿主界面 lan_server._last_logs 读取）

# 测试专用开关：仅当环境变量 LAN_SERVER_DRY_POWER=1 时，SHUTDOWN/REBOOT 只返回回执
# 而不真正关机/重启（用于端到端冒烟测试防误伤）。正常运行不设置该变量。
_DRY_POWER = os.environ.get("LAN_SERVER_DRY_POWER", "").strip() == "1"


def detect_default_save_dir():
    """自动判断默认保存目录：安卓优先 /sdcard/Download，其它平台用程序所在目录。"""
    if os.path.exists("/sdcard/Download"):
        return "/sdcard/Download"
    # PyInstaller frozen 环境下 __file__ 指向临时解包目录（_MEIPASS），
    # 必须改用 sys.executable（即 exe 本身）所在目录，用户才能找到接收的文件。
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _which(cmd):
    """检查系统 PATH 中是否存在某命令（跨平台 shutil.which 封装）。"""
    try:
        return shutil.which(cmd)
    except Exception:
        return None


# 常见扩展名 -> MIME（安卓 am start 打开文件时需要）
MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg", ".wav": "audio/x-wav", ".flac": "audio/flac",
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip", ".rar": "application/vnd.rar",
    ".html": "text/html", ".apk": "application/vnd.android.package-archive",
}


def guess_mime(path):
    """根据文件扩展名猜测 MIME 类型（默认 application/octet-stream）。"""
    ext = os.path.splitext(path)[1].lower()
    return MIME_MAP.get(ext, "application/octet-stream")


def looks_like_android_package(cmd):
    """
    判断字符串是否像安卓包名或 包名/Activity 形式：
      com.android.chrome                      -> True
      com.android.chrome/.MainActivity        -> True
      org.example.app/com.example.app.Main    -> True
      notepad / ls / python3                  -> False
    """
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_.]*(\.[A-Za-z0-9_]+)+(/[\w.]+)?$", cmd))


class LanServer:
    """
    被控端核心服务：监听文件端口 + 指令端口。
    可被命令行直接运行，也可被 Kivy 宿主（main.py）import 后在线程中启动。
    """

    def __init__(self, file_port=DEFAULT_FILE_PORT, cmd_port=DEFAULT_CMD_PORT,
                 auth_key=AUTH_KEY, save_dir=None):
        self.file_port = file_port
        self.cmd_port = cmd_port
        self.auth_key = auth_key
        self.save_dir = save_dir or detect_default_save_dir()
        os.makedirs(self.save_dir, exist_ok=True)
        self._running = False
        self._sockets = []
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self):
        """启动两个端口的监听线程。"""
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._listen_loop,
                         args=(self.file_port, self._handle_file_conn), daemon=True).start()
        threading.Thread(target=self._listen_loop,
                         args=(self.cmd_port, self._handle_cmd_conn), daemon=True).start()
        self._log(f"LanServer 已启动：文件端口 {self.file_port} / 指令端口 {self.cmd_port} / "
                  f"保存目录 {self.save_dir}")

    def stop(self):
        """停止服务：关闭所有监听 socket。"""
        self._running = False
        with self._lock:
            socks = list(self._sockets)
        for s in socks:
            try:
                s.close()
            except Exception:
                pass
        self._log("LanServer 已停止")

    def _log(self, msg):
        """打印带时间戳的日志（无 GUI），同时写入模块级缓冲供宿主界面读取。"""
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        _last_logs.append(line)
        if len(_last_logs) > 100:
            _last_logs.pop(0)

    # ---------- 监听循环 ----------
    def _listen_loop(self, port, handler):
        """在某端口循环 accept，每个连接交给独立线程处理。"""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", port))
            srv.listen(16)
        except OSError as e:
            self._log(f"端口 {port} 绑定失败：{e}（可能被占用或权限不足）")
            return
        with self._lock:
            self._sockets.append(srv)
        self._log(f"监听端口 {port} 已就绪")
        while self._running:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=handler, args=(conn, addr), daemon=True).start()
        with self._lock:
            try:
                self._sockets.remove(srv)
            except ValueError:
                pass
        self._log(f"监听端口 {port} 已关闭")

    # ---------- 协议基础 ----------
    @staticmethod
    def _recv_line(conn):
        """从 socket 读取一行（以 \\n 结尾），返回解码后的字符串。"""
        buf = b""
        while True:
            c = conn.recv(1)
            if not c:
                raise ConnectionError("连接中断")
            if c == b"\n":
                return buf.decode("utf-8", errors="replace")
            buf += c
            if len(buf) > 8192:
                raise ConnectionError("协议头过长")

    def _auth(self, conn):
        """认证握手：读 AUTH:<key>，正确回复 OK，否则 ERR:auth 并返回 False。"""
        try:
            conn.settimeout(AUTH_TIMEOUT)
            line = self._recv_line(conn)
        except Exception:
            return False
        if line.startswith("AUTH:") and line[5:].strip() == self.auth_key:
            try:
                conn.sendall(b"OK\n")
                return True
            except Exception:
                return False
        try:
            conn.sendall(b"ERR:auth\n")
        except Exception:
            pass
        return False

    # ---------- 文件接收 ----------
    def _resolve_save_path(self, rel_parts):
        """
        根据相对路径段（如 ["docs", "2026", "a.pdf"]）解析最终保存路径：
        1) 目录自动创建；2) 清洗掉 ../ 等穿越段；3) 同名自动加序号防覆盖。
        """
        if len(rel_parts) == 1:
            dir_path = self.save_dir
            name = rel_parts[0]
        else:
            dir_path = os.path.join(self.save_dir, *rel_parts[:-1])
            name = rel_parts[-1]
        os.makedirs(dir_path, exist_ok=True)
        base, ext = os.path.splitext(name)
        candidate = os.path.join(dir_path, name)
        n = 1
        while os.path.exists(candidate):
            candidate = os.path.join(dir_path, f"{base}_{n}{ext}")
            n += 1
        return candidate

    def _handle_file_conn(self, conn, addr):
        """处理文件传输连接：认证 -> 读协议头 -> 收数据 -> 回执。"""
        try:
            if not self._auth(conn):
                self._log(f"[文件] {addr[0]} 认证失败，已拒绝")
                return
            conn.settimeout(None)  # 传输阶段不设超时（大文件）
            header = self._recv_line(conn)
            if not header.startswith("SEND:"):
                conn.sendall(b"ERR:bad-header\n")
                return
            # 协议头格式：SEND:<相对路径/文件名>:<大小>
            rest, size_str = header[5:].rsplit(":", 1)
            try:
                size = int(size_str)
            except ValueError:
                conn.sendall(b"ERR:bad-size\n")
                return
            if size < 0 or size > 10 * 1024 * 1024 * 1024:  # 上限 10GB 防御
                conn.sendall(b"ERR:size-too-large\n")
                return
            rel_path = rest.replace("\\", "/").strip("/")
            rel_parts = [p for p in rel_path.split("/") if p and p not in (".", "..")]
            if not rel_parts:
                conn.sendall(b"ERR:bad-path\n")
                return
            save_path = self._resolve_save_path(rel_parts)
            conn.sendall(b"OK\n")  # 通知对方可以开始发送数据
            received = 0
            with open(save_path, "wb") as f:
                while received < size:
                    chunk = conn.recv(BUFFER_SIZE)
                    if not chunk:
                        raise ConnectionError("传输中断")
                    f.write(chunk)
                    received += len(chunk)
            conn.sendall(("OK:已保存到 " + save_path + "\n").encode("utf-8"))
            self._log(f"[文件] 来自 {addr[0]} 已保存 {received} 字节 -> {save_path}")
        except Exception as e:
            try:
                conn.sendall(("ERR:" + str(e) + "\n").encode("utf-8"))
            except Exception:
                pass
            self._log(f"[文件] 接收失败：{e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---------- 文件浏览：列目录（LIST_DIR） ----------
    def _list_dir(self, path):
        """
        列出目录内容（只读，无需 root）：
          成功返回 (True, 条目行列表)，每行：名称\t类型(dir/file/other)\t大小(字节,目录为0)\t修改时间
          失败返回 (False, ERR:...)
        支持 Windows 盘符根（C:\ / D:\）与安卓 /sdcard、/storage/emulated/0；
        无权限目录如实返回“权限不足”。
        """
        if not os.path.isdir(path):
            return False, "ERR:路径不存在或不是目录 " + path
        try:
            names = os.listdir(path)
        except PermissionError:
            return False, "ERR:权限不足，无法读取该目录（安卓请从 /sdcard 或 /storage/emulated/0 浏览）"
        except OSError as e:
            return False, "ERR:" + str(e)
        rows = []
        for name in names:
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full):
                    ftype = "dir"
                    size = 0
                elif os.path.isfile(full):
                    ftype = "file"
                    size = os.path.getsize(full)
                else:
                    ftype = "other"
                    size = 0
                mtime = os.path.getmtime(full)
            except OSError:
                ftype = "other"
                size = 0
                mtime = 0
            t = time.localtime(mtime)
            mtime_str = "{0:04d}-{1:02d}-{2:02d} {3:02d}:{4:02d}".format(
                t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min)
            rows.append(name + "\t" + ftype + "\t" + str(size) + "\t" + mtime_str)
        # 目录在前，再按名称排序（忽略大小写），便于浏览
        rows.sort(key=lambda r: (r.split("\t")[1] != "dir", r.split("\t")[0].lower()))
        return True, rows

    # ---------- 文件取回：DOWNLOAD（被控端充当发送方） ----------
    def _send_download(self, conn, path):
        """
        DOWNLOAD：校验对方设备上的文件，按 SEND 同款分块协议把文件发送给控制端。
        流程：发 OK:DOWNLOAD:<文件名>:<大小> -> 等控制端回 OK 就绪 -> 分块发数据 -> 回 OK:DL-DONE。
        失败返回错误回执字符串（由调用方发送）；成功返回 None。
        """
        if not os.path.exists(path):
            return "ERR:文件不存在 " + path
        if not os.path.isfile(path):
            return "ERR:不是文件（目录不可下载） " + path
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as _probe:
                _probe.read(1)
        except PermissionError:
            return "ERR:权限不足，无法读取该文件"
        except OSError as e:
            return "ERR:" + str(e)
        name = os.path.basename(path) or path
        conn.sendall(("OK:DOWNLOAD:" + name + ":" + str(size) + "\n").encode("utf-8"))
        ready = self._recv_line(conn)
        if ready != "OK":
            raise RuntimeError("对方未就绪（回执：" + (ready or "无响应") + "）")
        sent = 0
        with open(path, "rb") as f:
            while sent < size:
                chunk = f.read(BUFFER_SIZE)
                if not chunk:
                    break
                conn.sendall(chunk)
                sent += len(chunk)
        conn.sendall(b"OK:DL-DONE\n")
        return None

    # ---------- 打开文件指令 ----------
    def _open_with_platform(self, path):
        """
        跨平台打开文件：
          Windows  -> os.startfile
          macOS    -> open
          Linux    -> termux-open（Termux）/ xdg-open / am start（Android）
        """
        system = platform.system()
        if system == "Windows":
            os.startfile(path)
            return
        # 1) Termux 环境
        if _which("termux-open"):
            subprocess.run(["termux-open", path], check=True)
            return
        # 2) 常规 Linux 桌面
        if _which("xdg-open"):
            subprocess.run(["xdg-open", path], check=True)
            return
        # 3) 安卓（Kivy APK 内）：用 am start 发 VIEW Intent 打开常见文件
        am_bin = _which("am") or ("/system/bin/am" if os.path.exists("/system/bin/am") else None)
        if am_bin:
            uri = "file://" + path
            mime = guess_mime(path)
            cmd = 'am start -a android.intent.action.VIEW -d "{uri}" -t "{mime}"'
            ret = subprocess.run(cmd, shell=True)
            if ret.returncode == 0:
                return
            raise RuntimeError("am start 打开失败（可能没有可处理该文件类型的应用）")
        raise RuntimeError("当前平台没有可用的打开方式")

    # ---------- 远程操作：启动程序 / 执行命令 ----------
    def _run_cmd(self, cmd):
        """
        执行 RUN_CMD 指令（按平台分发）：
          Windows：若命令是已存在的文件路径 -> os.startfile 兜底打开/启动；
                   否则 subprocess.Popen(shell=True) 执行命令。
          安卓/Linux：am 开头 -> shell 执行 am start；纯包名 -> am start -n 启动应用；
                      否则按 shell 命令 Popen 执行。
        返回回执字符串（成功带 PID，失败带原因）。
        """
        cmd = cmd.strip()
        if not cmd:
            return "ERR:命令为空"
        system = platform.system()
        try:
            if system == "Windows":
                expanded = os.path.expandvars(cmd)
                # 命令是本地文件路径（含 exe/脚本/文档）：用 os.startfile 兜底启动
                if os.path.isfile(expanded):
                    os.startfile(expanded)
                    return "OK:已启动 " + expanded
                # 否则作为命令行执行（支持 notepad、calc、python x.py 等）
                proc = subprocess.Popen(cmd, shell=True)
                return "OK:已启动，PID=" + str(proc.pid)
            # ---------- 安卓 / Linux / macOS ----------
            # 1) 显式 am 指令：如 am start -a android.intent.action.VIEW -d file:///sdcard/Download/xx.pdf
            if cmd.startswith("am "):
                ret = subprocess.run(cmd, shell=True, capture_output=True, timeout=60)
                if ret.returncode == 0:
                    return "OK:已执行 " + cmd
                err = (ret.stderr or b"").decode("utf-8", errors="replace").strip()
                return "ERR:执行失败 " + (err or ("返回码 " + str(ret.returncode)))
            # 2) 纯包名 / 包名+Activity：用 am start -n 启动应用
            if looks_like_android_package(cmd):
                am_bin = _which("am") or ("/system/bin/am" if os.path.exists("/system/bin/am") else None)
                if am_bin:
                    ret = subprocess.run([am_bin, "start", "-n", cmd],
                                         capture_output=True, timeout=60)
                    if ret.returncode == 0:
                        return "OK:已尝试启动应用 " + cmd
                    err = (ret.stderr or b"").decode("utf-8", errors="replace").strip()
                    return "ERR:启动失败 " + (err or ("返回码 " + str(ret.returncode)))
                return "ERR:当前环境没有 am 命令，无法启动应用"
            # 3) 其它视为 shell 命令
            proc = subprocess.Popen(cmd, shell=True)
            return "OK:已执行，PID=" + str(proc.pid)
        except Exception as e:
            return "ERR:执行失败 " + str(e)

    # ---------- 远程操作：关机 / 重启 ----------
    def _exec_power(self, kind):
        """
        执行关机/重启（kind: 'shutdown' | 'reboot'）。
          Windows：shutdown /s|/r /t 5，立即返回"已执行"回执。
          安卓/Linux/macOS：尝试 su -c 关机/重启命令；无 root 权限如实返回
                           "权限不足，无法关机/重启（需 root 或设备管理器权限）"。
        返回回执字符串（不伪造成功）。
        """
        label = "关机" if kind == "shutdown" else "重启"
        if _DRY_POWER:
            return "OK:测试模式已请求" + label + "（未真正执行）"
        system = platform.system()
        if system == "Windows":
            try:
                subprocess.run(["shutdown", "/s" if kind == "shutdown" else "/r", "/t", "5"],
                               timeout=15)
                return "OK:已执行" + label
            except Exception as e:
                return "ERR:执行失败 " + str(e)
        # 安卓 / Linux / macOS：需要 root 权限
        power_cmd = "poweroff" if kind == "shutdown" else "reboot"
        # 尝试 su -c
        try:
            ret = subprocess.run(["su", "-c", power_cmd], capture_output=True, timeout=10)
            if ret.returncode == 0:
                return "OK:已请求" + label
        except Exception:
            pass
        # 尝试 sudo（部分 Linux）
        try:
            ret = subprocess.run(["sudo", power_cmd], capture_output=True, timeout=10)
            if ret.returncode == 0:
                return "OK:已请求" + label
        except Exception:
            pass
        return "ERR:权限不足，无法" + label + "（需 root 或设备管理器权限）"

    def _handle_cmd_conn(self, conn, addr):
        """处理指令连接：认证 -> 循环接收并处理 OPEN_FILE / RUN_CMD / SHUTDOWN / REBOOT。"""
        try:
            if not self._auth(conn):
                self._log(f"[指令] {addr[0]} 认证失败，已拒绝")
                return
            conn.settimeout(None)
            # 循环处理指令：支持控制端保持同一通道连续发送多条指令
            while True:
                line = self._recv_line(conn)
                if line.startswith("OPEN_FILE:"):
                    path = line[len("OPEN_FILE:"):].strip()
                    if not os.path.exists(path):
                        conn.sendall(("ERR:路径不存在 " + path + "\n").encode("utf-8"))
                        self._log(f"[指令] 打开失败（路径不存在）：{path}")
                        continue
                    try:
                        self._open_with_platform(path)
                        conn.sendall(("OK:已打开 " + path + "\n").encode("utf-8"))
                        self._log(f"[指令] 已打开文件：{path}")
                    except Exception as e:
                        conn.sendall(("ERR:" + str(e) + "\n").encode("utf-8"))
                        self._log(f"[指令] 打开失败：{path} -> {e}")
                elif line.startswith("RUN_CMD:"):
                    cmd = line[len("RUN_CMD:"):].strip()
                    self._log(f"[指令] 收到 RUN_CMD：{cmd}")
                    ack = self._run_cmd(cmd)
                    conn.sendall((ack + "\n").encode("utf-8"))
                    self._log(f"[指令] RUN_CMD 回执：{ack}")
                elif line.startswith("SHUTDOWN"):
                    self._log(f"[指令] 收到 SHUTDOWN（来自 {addr[0]}）")
                    ack = self._exec_power("shutdown")
                    conn.sendall((ack + "\n").encode("utf-8"))
                    self._log(f"[指令] SHUTDOWN 回执：{ack}")
                elif line.startswith("REBOOT"):
                    self._log(f"[指令] 收到 REBOOT（来自 {addr[0]}）")
                    ack = self._exec_power("reboot")
                    conn.sendall((ack + "\n").encode("utf-8"))
                    self._log(f"[指令] REBOOT 回执：{ack}")
                elif line.startswith("LIST_DIR:"):
                    dpath = line[len("LIST_DIR:"):].strip()
                    self._log(f"[指令] 收到 LIST_DIR：{dpath}")
                    ok, payload = self._list_dir(dpath)
                    if not ok:
                        conn.sendall((payload + "\n").encode("utf-8"))
                        self._log(f"[指令] LIST_DIR 失败：{payload}")
                        continue
                    conn.sendall(("OK:LIST:" + str(len(payload)) + "\n").encode("utf-8"))
                    for row in payload:
                        conn.sendall((row + "\n").encode("utf-8"))
                    conn.sendall(b"END\n")
                    self._log(f"[指令] LIST_DIR 成功：{dpath} 共 {len(payload)} 项")
                elif line.startswith("DOWNLOAD:"):
                    dpath = line[len("DOWNLOAD:"):].strip()
                    self._log(f"[指令] 收到 DOWNLOAD：{dpath}")
                    try:
                        ack = self._send_download(conn, dpath)
                    except Exception as e:
                        ack = "ERR:" + str(e)
                    if ack is not None:
                        try:
                            conn.sendall((ack + "\n").encode("utf-8"))
                        except Exception:
                            pass
                        self._log(f"[指令] DOWNLOAD 失败：{ack}")
                    else:
                        self._log(f"[指令] DOWNLOAD 完成：{dpath}")
                else:
                    conn.sendall(b"ERR:unknown\n")
        except Exception as e:
            self._log(f"[指令] 处理异常：{e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="LanServer 被控端服务（无 GUI，后台常驻）")
    parser.add_argument("--file-port", type=int, default=DEFAULT_FILE_PORT,
                        help=f"文件传输端口（默认 {DEFAULT_FILE_PORT}）")
    parser.add_argument("--cmd-port", type=int, default=None,
                        help=f"指令端口（默认 文件端口+1，即 {DEFAULT_CMD_PORT}）")
    parser.add_argument("--port", type=int, default=None,
                        help="基础端口快捷设置：文件端口=该值，指令端口=该值+1")
    parser.add_argument("--auth-key", default=AUTH_KEY,
                        help=f"连接密钥（默认 {AUTH_KEY}）")
    parser.add_argument("--save-dir", default=None,
                        help="接收文件保存目录（默认自动判断：安卓 /sdcard/Download，其它为脚本目录）")
    args = parser.parse_args()

    file_port = args.port if args.port is not None else args.file_port
    cmd_port = args.cmd_port if args.cmd_port is not None else file_port + 1
    save_dir = args.save_dir

    server = LanServer(file_port=file_port, cmd_port=cmd_port,
                       auth_key=args.auth_key, save_dir=save_dir)
    server.start()

    print()
    print("=" * 60)
    print(" LanServer 被控端服务已启动")
    print(f"  文件传输端口 : {server.file_port}")
    print(f"  指令端口     : {server.cmd_port}")
    print(f"  连接密钥     : {server.auth_key}")
    print(f"  保存目录     : {server.save_dir}")
    print(" 控制端请运行 lan_client.py 并填写本机 IP 与上述端口/密钥")
    print(" 支持指令：OPEN_FILE / RUN_CMD / SHUTDOWN / REBOOT（安卓关机/重启需 root 权限）")
    print(" 按 Ctrl+C 停止服务")
    print("=" * 60)
    print(flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("收到退出信号，正在停止...")
        server.stop()


# =====================================================================
# Kivy 极简宿主（安卓 APK 打包用；普通 CLI 环境自动跳过）
# 同一份代码：Windows EXE 走上面 main() 的 CLI 常驻；
# 安卓 APK 里作为入口创建极简界面，后台线程跑 LanServer socket 服务，
# 界面实时显示运行日志、端口与保存目录。
# =====================================================================
def _is_android_apk_env():
    """判断是否运行在安卓 APK 环境（存在 /sdcard/Download 或 ANDROID_ARGUMENT）。"""
    return os.path.exists("/sdcard/Download") or bool(os.environ.get("ANDROID_ARGUMENT", ""))


def run_kivy_host(server):
    """创建极简 Kivy App，后台启动 LanServer，界面实时显示日志。"""
    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    from kivy.uix.textinput import TextInput
    from kivy.clock import Clock

    class LanServerApp(App):
        def build(self):
            box = BoxLayout(orientation="vertical", padding=16, spacing=8)
            info = (f"LanServer 被控端\n"
                    f"文件端口 {server.file_port} / 指令端口 {server.cmd_port}\n"
                    f"连接密钥 {server.auth_key}\n"
                    f"保存目录 {server.save_dir}")
            box.add_widget(Label(text=info, size_hint_y=0.35))
            self.log_view = TextInput(text="", readonly=True, multiline=True, size_hint_y=0.65)
            box.add_widget(self.log_view)
            server.start()
            Clock.schedule_interval(self._tick, 0.5)
            return box

        def _tick(self, dt):
            if _last_logs:
                self.log_view.text = "\n".join(_last_logs[-20:])
                self.log_view.cursor = (0, len(self.log_view.text))

    LanServerApp().run()


if __name__ == "__main__":
    if _is_android_apk_env():
        try:
            import kivy  # noqa: F401
            _srv = LanServer()
            run_kivy_host(_srv)
        except Exception as e:
            print("Kivy 宿主启动失败，回退命令行模式：", e, flush=True)
            main()
    else:
        main()
