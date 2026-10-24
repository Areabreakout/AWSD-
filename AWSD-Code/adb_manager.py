# -*- coding: utf-8 -*-
"""
ADB 设备连接管理器
负责设备发现、连接、端口转发、scrcpy-server 推送与启动
"""
import os
import subprocess
import threading
import time
import socket
import struct
import shutil
from typing import List, Optional, Tuple

import config


def _find_adb() -> str:
    """优先使用 PATH 中的 adb（与用户已运行的 adb server 一致），
    找不到时回退到 scrcpy 目录下的 adb.exe。"""
    found = shutil.which("adb")
    if found:
        return found
    if os.path.isfile(config.ADB_PATH):
        return config.ADB_PATH
    # 最后兜底：直接返回配置值（启动时可能不存在，由调用方报错）
    return config.ADB_PATH


class AdbManager:
    """封装 ADB 常用操作"""

    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = adb_path or _find_adb()
        self._server_started = False
        self._server_proc: Optional[subprocess.Popen] = None
        self._shell_proc: Optional[subprocess.Popen] = None
        self._scrcpy_proc: Optional[subprocess.Popen] = None

    # ---------- 基础 ADB 命令 ----------
    def _run(self, args: List[str], timeout: float = 10.0) -> Tuple[int, str, str]:
        """执行 adb 命令，返回 (returncode, stdout, stderr)"""
        cmd = [self.adb_path] + args
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace"
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def _check_server_running(self) -> bool:
        """检测 adb server 是否在运行 (通过端口)"""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", 5037))
            s.close()
            return result == 0
        except Exception:
            return False

    def start_server(self) -> bool:
        """启动 adb server (幂等: 已经在运行就跳过)"""
        if self._check_server_running():
            self._server_started = True
            return True
        rc, out, err = self._run(["start-server"], timeout=10)
        if rc == 0 and self._check_server_running():
            self._server_started = True
            return True
        # 重试一次
        time.sleep(1)
        rc, out, err = self._run(["start-server"], timeout=10)
        if rc == 0:
            time.sleep(0.5)
            self._server_started = self._check_server_running()
        else:
            self._server_started = False
        return self._server_started

    def kill_server(self):
        """关闭 adb server"""
        self._run(["kill-server"], timeout=5)
        self._server_started = False

    def get_devices(self) -> List[str]:
        """获取已连接设备序列号列表"""
        rc, out, err = self._run(["devices"], timeout=5)
        # 端口检测 + 输出检测双重确认
        server_ok = self._check_server_running() and rc == 0 and ("List of devices" in out or "devices" in out.lower())
        if not server_ok:
            # 只在完全挂掉时才重启, 避免每次临时超时都重启
            if not self._server_started or not self._check_server_running():
                print("[ADB] server 未运行, 启动中...")
                self.start_server()
            else:
                # server 在运行但 devices 超时, 只提示不重启
                print(f"[ADB] devices 命令超时或失败 (rc={rc}), 重试...")
            rc, out, err = self._run(["devices"], timeout=5)
        devices = []
        all_lines = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, status = line.split("\t", 1)
            serial = serial.strip()
            status = status.strip()
            all_lines.append(f"  {serial} -> {status}")
            if status == "device":
                devices.append(serial)
            elif status in ("offline", "unauthorized"):
                print(f"[ADB] 设备 {serial} 状态为 {status}，尝试重连...")
                # unauthorized 通常需要在手机上点"允许调试"
                if status == "unauthorized":
                    print(f"[ADB] 请在手机上点击「允许 USB 调试」对话框")
                else:
                    self._run(["disconnect", serial], timeout=5)
                    time.sleep(0.5)
                    self._run(["connect", serial], timeout=5)
        if not all_lines:
            print("[ADB] 未检测到任何设备！可能原因：")
            print("[ADB]   1. 手机未开启「USB 调试」（开发者选项中）")
            print("[ADB]   2. USB 连接模式为「仅充电」，需改为「传输文件 (MTP)」")
            print("[ADB]   3. 手机上未点击「允许 USB 调试」授权弹窗")
            print("[ADB]   4. HarmonyOS NEXT 不支持 ADB，需要安装 hdc 工具")
            print("[ADB]   5. USB 驱动未安装或数据线不支持数据传输")
        return devices

    def get_all_devices_with_status(self) -> List[Tuple[str, str]]:
        """获取所有设备及其状态（用于诊断）"""
        rc, out, err = self._run(["devices", "-l"], timeout=5)
        results = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                results.append((serial, status))
        return results

    def wait_for_device(self, timeout: float = 30.0) -> bool:
        """等待设备连接"""
        rc, out, err = self._run(["wait-for-device"], timeout=timeout)
        return rc == 0

    # ---------- scrcpy 相关 ----------
    def push_server(self, serial: Optional[str] = None) -> bool:
        """推送 scrcpy-server 到设备"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["push", config.SCRCPY_SERVER_PATH, config.REMOTE_SERVER_PATH]
        rc, out, err = self._run(args, timeout=30)
        if rc != 0:
            print(f"[ADB] push scrcpy-server 失败: {err}")
        return rc == 0

    def forward_port(self, serial: Optional[str] = None,
                     local_port: int = config.SCRCPY_PORT) -> bool:
        """转发本地端口到设备 scrcpy socket"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["forward", f"tcp:{local_port}", "localabstract:scrcpy"]
        rc, out, err = self._run(args, timeout=10)
        if rc != 0:
            print(f"[ADB] forward 失败: {err}")
        return rc == 0

    def remove_forward(self, serial: Optional[str] = None,
                       local_port: int = config.SCRCPY_PORT):
        """移除端口转发"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["forward", "--remove", f"tcp:{local_port}"]
        self._run(args, timeout=5)

    def reverse_port(self, serial: Optional[str] = None,
                     local_port: int = config.SCRCPY_PORT) -> bool:
        """反向端口转发：设备 localabstract:scrcpy -> PC tcp:port"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["reverse", "localabstract:scrcpy", f"tcp:{local_port}"]
        rc, out, err = self._run(args, timeout=10)
        if rc != 0:
            print(f"[ADB] reverse 失败: {err}")
        return rc == 0

    def remove_reverse(self, serial: Optional[str] = None):
        """移除反向端口转发"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["reverse", "--remove", "localabstract:scrcpy"]
        self._run(args, timeout=5)

    def start_scrcpy_server(self, serial: Optional[str] = None,
                            max_size: int = config.MAX_SIZE,
                            bit_rate: int = config.BIT_RATE,
                            max_fps: int = config.MAX_FPS,
                            video_codec: str = config.VIDEO_CODEC,
                            tunnel_forward: bool = False) -> subprocess.Popen:
        """
        在设备上启动 scrcpy-server（非阻塞）
        tunnel_forward=False: 反向隧道（PC 监听，设备连接）- scrcpy 4.x 默认
        tunnel_forward=True:  正向隧道（设备监听，PC 连接）
        """
        args = []
        if serial:
            args += ["-s", serial]

        # 构建 scrcpy server 启动参数
        server_args = (
            f"CLASSPATH={config.REMOTE_SERVER_PATH} "
            f"app_process / com.genymobile.scrcpy.Server "
            f"{config.SCRCPY_VERSION} "
            f"log_level={config.LOG_LEVEL} "
            f"max_size={max_size} "
            f"video_bit_rate={bit_rate} "
            f"max_fps={max_fps} "
            f"video_codec={video_codec} "
            f"audio=false "
            f"tunnel_forward={'true' if tunnel_forward else 'false'}"
        )
        args += ["shell", server_args]

        cmd = [self.adb_path] + args
        # 使用 Popen 非阻塞启动，stderr 用于读取服务端日志
        self._scrcpy_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        # 后台线程持续读取 stderr，防止管道缓冲区满导致 server 阻塞
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        return self._scrcpy_proc

    def _drain_stderr(self):
        """持续读取 scrcpy-server 的 stderr 日志，避免管道阻塞"""
        if not self._scrcpy_proc:
            return
        try:
            for line in self._scrcpy_proc.stderr:
                try:
                    msg = line.decode("utf-8", errors="ignore").rstrip()
                    if msg:
                        print(f"[scrcpy-server] {msg}")
                except Exception:
                    pass
        except (ValueError, OSError):
            pass

    def stop_scrcpy_server(self):
        """停止 scrcpy-server 进程"""
        if self._scrcpy_proc and self._scrcpy_proc.poll() is None:
            self._scrcpy_proc.terminate()
            try:
                self._scrcpy_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._scrcpy_proc.kill()
        self._scrcpy_proc = None

    # ---------- 设备信息 ----------
    def get_screen_size(self, serial: Optional[str] = None) -> Tuple[int, int]:
        """获取设备屏幕分辨率 (width, height)"""
        args = []
        if serial:
            args += ["-s", serial]
        args += ["shell", "wm", "size"]
        rc, out, err = self._run(args, timeout=5)
        if rc == 0 and "Physical size" in out:
            size_str = out.split("Physical size:")[-1].strip()
            try:
                w, h = size_str.split("x")
                return int(w), int(h)
            except ValueError:
                pass
        return config.SCREEN_WIDTH, config.SCREEN_HEIGHT

    # ---------- 连接控制 socket ----------
    def connect_scrcpy_socket(self, port: int = config.SCRCPY_PORT,
                              timeout: float = 10.0) -> Optional[socket.socket]:
        """连接到本地转发的 scrcpy socket（视频用第一个，控制用第二个）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                return sock
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.2)
        return None

    # ---------- 清理 ----------
    def cleanup(self, serial: Optional[str] = None):
        """清理资源"""
        self.stop_scrcpy_server()
        self.remove_forward(serial)
        self.remove_reverse(serial)
