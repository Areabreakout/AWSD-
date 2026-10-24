# -*- coding: utf-8 -*-
"""
scrcpy 客户端实现（兼容 scrcpy 4.x）
- 连接方式：反向隧道（adb reverse），PC 端监听，设备端连接
- 视频通道：读取 H.264 流，用 PyAV 解码为 RGB 帧
- 控制通道：发送触屏/按键事件
"""
import struct
import socket
import threading
import time
import os
from datetime import datetime
from typing import Optional, Tuple, Callable

import numpy as np

import config

# ============ scrcpy 控制消息类型 ============
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_SCROLL_EVENT = 3
TYPE_BACK_OR_SCREEN_ON = 4
TYPE_EXPAND_NOTIFICATION_PANEL = 5
TYPE_EXPAND_SETTINGS_PANEL = 6
TYPE_COLLAPSE_PANELS = 7
TYPE_GET_CLIPBOARD = 8
TYPE_SET_CLIPBOARD = 9
TYPE_SET_SCREEN_POWER_MODE = 10
TYPE_ROTATE_DEVICE = 11

# 触屏动作
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2

# Android keycode
AKEYCODE_BACK = 4
AKEYCODE_HOME = 3
AKEYCODE_MENU = 82
AKEYCODE_VOLUME_UP = 24
AKEYCODE_VOLUME_DOWN = 25
AKEYCODE_POWER = 26


class ScrcpyClient:
    """scrcpy 客户端：管理视频解码与控制指令发送"""

    def __init__(self, adb_manager, serial: Optional[str] = None):
        self.adb = adb_manager
        self.serial = serial

        # sockets
        self._server_sock: Optional[socket.socket] = None
        self.video_sock: Optional[socket.socket] = None
        self.control_sock: Optional[socket.socket] = None

        # 设备信息
        self.device_name = ""
        self.screen_width = config.SCREEN_WIDTH    # 物理分辨率（用户坐标基准）
        self.screen_height = config.SCREEN_HEIGHT
        self.frame_width = config.SCREEN_WIDTH     # 视频帧实际分辨率（scrcpy要求）
        self.frame_height = config.SCREEN_HEIGHT

        # 视频解码
        self._video_thread: Optional[threading.Thread] = None
        self._running = False
        self._latest_frame = None
        self._frame_lock = threading.Lock()

        # FPS 统计
        self.fps = 0.0
        self._frame_count = 0
        self._fps_time = time.time()

        # 自动重连
        self._reconnect_thread: Optional[threading.Thread] = None
        self.on_disconnected: Optional[Callable[[], None]] = None
        self._manual_disconnect = False

        # 控制锁
        self._control_lock = threading.Lock()

        # 最后一条错误（供 GUI 读取）
        self.last_error = ""

        # 回调
        self.on_frame: Optional[Callable[[np.ndarray], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    # ============ 连接 ============
    def connect(self, port: int = config.SCRCPY_PORT, timeout: float = 15.0) -> bool:
        """启动 scrcpy-server 并连接视频+控制通道（反向隧道模式）"""
        # 1. 推送 server
        if not self.adb.push_server(self.serial):
            self._error("推送 scrcpy-server 失败")
            return False

        # 2. 清理旧的 reverse
        self.adb.remove_reverse(self.serial)

        # 3. 在 PC 端监听端口
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.bind(("127.0.0.1", port))
        except OSError as e:
            self._error(f"端口 {port} 绑定失败: {e}")
            return False
        self._server_sock.listen(4)
        self._server_sock.settimeout(timeout)

        # 4. 设置反向转发：设备 localabstract:scrcpy -> PC tcp:port
        if not self.adb.reverse_port(self.serial, port):
            self._error("反向端口转发失败")
            return False

        # 5. 启动 server（默认 tunnel_forward=false，即反向模式）
        self.adb.start_scrcpy_server(self.serial, tunnel_forward=False)

        # 6. 等待设备连接视频通道（第一个连接）
        try:
            self.video_sock, _ = self._server_sock.accept()
        except socket.timeout:
            self._error("等待视频连接超时")
            return False

        # === 视频通道也关 Nagle ===
        self.video_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # 7. 等待设备连接控制通道（第二个连接）
        try:
            self.control_sock, _ = self._server_sock.accept()
        except socket.timeout:
            self._error("等待控制连接超时")
            return False

        # === 低延迟 socket 设置 ===
        self.control_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.control_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
        self.control_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)

        # 8. 读取设备名（64 字节，无 dummy byte）
        self.video_sock.settimeout(10.0)
        name_bytes = self._recv_exact(self.video_sock, 64)
        if not name_bytes:
            self._error("读取设备名失败")
            return False
        self.device_name = name_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")
        self.video_sock.settimeout(None)

        # 从 adb 获取真实物理分辨率
        try:
            import subprocess
            serial = self.adb.get_devices()[0] if self.adb else None
            if serial:
                out = subprocess.run(
                    [self.adb.adb_path, "-s", serial,
                     "shell", "wm", "size"],
                    capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="replace")
                # 输出如 "Physical size: 1080x2400"
                for line in out.stdout.splitlines():
                    if "Physical size:" in line:
                        parts = line.split(":")[-1].strip().split("x")
                        if len(parts) == 2:
                            self.screen_width = int(parts[0])
                            self.screen_height = int(parts[1])
                            print(f"[SCRCPY] 物理分辨率: {self.screen_width}x{self.screen_height}")
                            print(f"[SCRCPY] FRAME分辨率: {self.frame_width}x{self.frame_height}")
                            # 关键: 如果 screen 是横屏 (sw > sh), 说明游戏在横屏
                            if self.screen_width > self.screen_height:
                                print(f"[SCRCPY] 注意: screen 分辨率是横屏 sw>sh, aspect_fix 需要考虑")
                            break
        except Exception as e:
            print(f"[SCRCPY] 获取物理分辨率失败: {e}")

        # 9. 启动视频解码线程
        self._running = True
        self._video_thread = threading.Thread(target=self._video_loop, daemon=True)
        self._video_thread.start()

        print(f"[SCRCPY] 已连接设备: {self.device_name}")
        return True

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    # ============ 视频解码 ============
    def _video_loop(self):
        """视频接收与解码循环"""
        try:
            import av
            codec = av.CodecContext.create("h264", "r")
            self._seen_keyframe = False  # h264 必须从 I 帧(keyframe)开始解码
            self._green_streak = 0       # 连续绿色蒙版帧计数（切后台后流失步检测）
            self._frame_count = 0
            self._fps_time = time.time()

            while self._running:
                try:
                    data = self.video_sock.recv(65536)
                    if not data:
                        break
                    for packet in codec.parse(data):
                        try:
                            frames = codec.decode(packet)
                            for frame in frames:
                                # h264 必须从 I 帧(keyframe)开始, 跳过之前的 P 帧(全绿/垃圾帧)
                                if not frame.key_frame:
                                    if not self._seen_keyframe:
                                        continue
                                else:
                                    self._seen_keyframe = True
                                img = frame.to_ndarray(format="bgr24")
                                # 有效性检查: 跳过全零/全绿/全红等单色异常帧
                                _ok = True
                                for _c in range(3):
                                    _ch = img[:,:,_c]
                                    if _ch.max() - _ch.min() < 3:
                                        _ok = False; break
                                if not _ok:
                                    continue
                                # 绿色蒙版检测: 切后台后 H.264 流失步 → 解码输出
                                # G 通道有细节、R/B 通道无效的单色绿帧。
                                # 连续 ~1.5 秒即判定流损坏，触发自动重连恢复。
                                _chG = img[:,:,1]; _chR = img[:,:,0]; _chB = img[:,:,2]
                                if (_chG.mean() > 60
                                        and _chR.mean() < _chG.mean() * 0.35
                                        and _chB.mean() < _chG.mean() * 0.35
                                        and _chG.std() > 10):
                                    self._green_streak += 1
                                    if self._green_streak >= 45:
                                        self._error("检测到视频流损坏(绿色蒙版)，触发自动重连...")
                                        self._running = False  # 退出循环 → 走断连回调重连
                                        break
                                    continue  # 不让绿帧上屏，保留最后正常画面
                                self._green_streak = 0
                                with self._frame_lock:
                                    self._latest_frame = img
                                    h, w = img.shape[:2]
                                    if w != self.frame_width or h != self.frame_height:
                                        self.frame_width = w
                                        self.frame_height = h
                                if self.on_frame:
                                    self.on_frame(img)
                                self._frame_count += 1
                                now = time.time()
                                if now - self._fps_time >= 1.0:
                                    self.fps = self._frame_count / (now - self._fps_time)
                                    self._frame_count = 0
                                    self._fps_time = now
                        except Exception:
                            continue
                    continue
                except (ConnectionResetError, OSError):
                    break
                except Exception as e:
                    if self._running:
                        self._error(f"视频解码错误: {e}")
                    break
        except ImportError:
            self._error("PyAV (av) 未安装，无法解码视频")
        except Exception as e:
            self._error(f"视频循环异常: {e}")

        # 视频循环退出 → 触发断连回调（非手动断开才触发重连）
        self._running = False
        if self.on_disconnected and not self._manual_disconnect:
            self.on_disconnected()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def take_screenshot(self) -> Optional[str]:
        """截取当前画面保存为 PNG，返回文件路径"""
        frame = self.get_latest_frame()
        if frame is None:
            return None
        os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)
        fname = datetime.now().strftime("screenshot_%Y%m%d_%H%M%S.png")
        path = os.path.join(config.SCREENSHOT_DIR, fname)
        try:
            import cv2
            cv2.imwrite(path, frame)
            return path
        except Exception as e:
            self._error(f"截图失败: {e}")
            return None

    # ============ 控制指令 ============
    def _send_control(self, data: bytes) -> bool:
        if not self.control_sock:
            self._error("控制通道未建立 (control_sock is None)")
            return False
        with self._control_lock:
            try:
                self.control_sock.sendall(data)
                return True
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                self._error(f"控制通道已断开: {type(e).__name__}: {e}")
                return False
            except Exception as e:
                self._error(f"发送控制指令失败: {type(e).__name__}: {e}")
                return False

    def inject_touch(self, action: int, x: float, y: float,
                     pointer_id: int = -1, pressure: float = 1.0) -> bool:
        """
        发送触屏事件 (scrcpy 4.x, 32 bytes)
        坐标基于物理分辨率，内部自动缩放到视频帧分辨率
        [type:1][action:1][pointerId:8 BE uint64][x:4 BE uint32][y:4 BE uint32]
        [frameWidth:2 BE uint16][frameHeight:2 BE uint16]
        [pressure:2 BE uint16 FP][actionButton:4 BE uint32][buttons:4 BE uint32]
        """
        if not self.control_sock:
            self._error("inject_touch: control socket not ready")
            return False
        # Rate-limited debug print
        if not hasattr(self, '_dbg_touch_count'):
            self._dbg_touch_count = 0
        self._dbg_touch_count += 1
        tc = self._dbg_touch_count
        if tc <= 50 or tc % 100 == 0:
            act_name = {0:'DOWN', 1:'UP', 2:'MOVE'}.get(action, str(action))
            print(f"[DBG-SCRCPY] #{tc} touch={act_name} physXY=({x:.0f},{y:.0f}) ptr={pointer_id} sock=OK frameWH=({self.frame_width}x{self.frame_height})")
        try:
            if self.screen_width > 0 and self.screen_height > 0:
                fx = x * self.frame_width / self.screen_width
                fy = y * self.frame_height / self.screen_height
            else:
                fx, fy = x, y
            pressure_fp = int(max(0.0, min(1.0, pressure)) * 0xFFFF)
            buf = struct.pack(
                ">BBQIIHHHII",
                TYPE_INJECT_TOUCH_EVENT,
                action & 0xFF,
                pointer_id & 0xFFFFFFFFFFFFFFFF,
                int(fx) & 0xFFFFFFFF,
                int(fy) & 0xFFFFFFFF,
                int(self.frame_width) & 0xFFFF,
                int(self.frame_height) & 0xFFFF,
                pressure_fp & 0xFFFF,
                1,  # actionButton: BUTTON_PRIMARY
                1,  # buttons: BUTTON_PRIMARY
            )
        except Exception as e:
            self._error(f"inject_touch pack 失败: {e} (frame={self.frame_width}x{self.frame_height})")
            return False
        return self._send_control(buf)

    def inject_scroll(self, x: float, y: float, h_scroll: float,
                      v_scroll: float) -> bool:
        """
        发送滚轮事件 (scrcpy 4.x, 21 bytes)
        [type:1][x:4 BE uint32][y:4 BE uint32]
        [screenWidth:2 BE uint16][screenHeight:2 BE uint16]
        [hScroll:2 BE int16][vScroll:2 BE int16][buttons:4 BE uint32]
        """
        buf = struct.pack(
            ">BIIHHhhI",
            TYPE_INJECT_SCROLL_EVENT,
            int(x) & 0xFFFFFFFF,
            int(y) & 0xFFFFFFFF,
            int(self.screen_width) & 0xFFFF,
            int(self.screen_height) & 0xFFFF,
            int(h_scroll) & 0xFFFF,
            int(v_scroll) & 0xFFFF,
            0,  # buttons
        )
        return self._send_control(buf)

    def inject_keycode(self, keycode: int, action: int = 0,
                       repeat: int = 0, metastate: int = 0) -> bool:
        buf = struct.pack(">BBiii", TYPE_INJECT_KEYCODE, action & 0xFF,
                          keycode, repeat, metastate)
        return self._send_control(buf)

    def press_back(self):
        self.inject_keycode(AKEYCODE_BACK, 0)
        time.sleep(0.02)
        self.inject_keycode(AKEYCODE_BACK, 1)

    def press_home(self):
        self.inject_keycode(AKEYCODE_HOME, 0)
        time.sleep(0.02)
        self.inject_keycode(AKEYCODE_HOME, 1)

    # ============ 生命周期 ============
    def disconnect(self):
        """手动断开（不触发重连）"""
        self._manual_disconnect = True
        self._running = False
        if self.video_sock:
            try:
                self.video_sock.close()
            except Exception:
                pass
            self.video_sock = None
        if self.control_sock:
            try:
                self.control_sock.close()
            except Exception:
                pass
            self.control_sock = None
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._video_thread and self._video_thread.is_alive():
            self._video_thread.join(timeout=2)
        self.adb.cleanup(self.serial)

    def _error(self, msg: str):
        self.last_error = msg
        print(f"[SCRCPY] {msg}")
        if self.on_error:
            self.on_error(msg)


