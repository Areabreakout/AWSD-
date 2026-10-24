# -*- coding: utf-8 -*-
"""
AWSD+++ - 主程序
功能：设备连接、投屏显示、普通/游戏双模式、悬浮窗键位编辑、
      连点器、压枪、宏、画质调节、截图、FPS显示、自动重连
"""
import sys
import os
import time
import threading

import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QListWidget, QGroupBox,
    QTextEdit, QSplitter, QMessageBox, QInputDialog, QSlider,
    QSpinBox, QFormLayout, QTabWidget, QLineEdit, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox,
    QFileDialog, QRadioButton, QButtonGroup,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint, QRect, QSize
from PyQt5.QtGui import QImage, QPixmap, QFont, QPainter, QPen, QColor, QBrush

import ctypes
from ctypes import wintypes

import config
from adb_manager import AdbManager
from scrcpy_client import ScrcpyClient
from input_mapper import InputMapper
import profiles as profile_mgr

# ============ Raw Input 结构体（模块级，避免每次 nativeEvent 重建） ============
class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]

class _RAW_BUTTONS(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("_btn_struct", type("_Btn", (ctypes.Structure,), {
            "_fields_": [
                ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT),
            ]
        })),
    ]

class _RAWMOUSE(ctypes.Structure):
    """RAWMOUSE: usFlags(2) + pad(2) + buttons(4) + rawButtons(4) + lLastX(4) + lLastY(4) + extra(4)"""
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("_pad", wintypes.USHORT),
        ("buttons", _RAW_BUTTONS),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", wintypes.ULONG),
    ]

class _RAWKEYBOARD(ctypes.Structure):
    """RAWKEYBOARD: MakeCode(2) Flags(2) Reserved(2) VKey(2) Message(4) Extra(4) = 16 bytes"""
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _RAWINPUTDATA(ctypes.Union):
    _fields_ = [
        ("mouse", _RAWMOUSE),
        ("keyboard", _RAWKEYBOARD),
    ]

class _RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", _RAWINPUTHEADER),
        ("data", _RAWINPUTDATA),
    ]

# Mouse button flags from RAWINPUT
RI_MOUSE_BUTTON_1_DOWN = 0x0001
RI_MOUSE_BUTTON_1_UP   = 0x0002
RI_MOUSE_BUTTON_2_DOWN = 0x0004
RI_MOUSE_BUTTON_2_UP   = 0x0008
RI_MOUSE_BUTTON_3_DOWN = 0x0010
RI_MOUSE_BUTTON_3_UP   = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP   = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP   = 0x0200

# Keyboard flags
RI_KEY_BREAK = 0x01  # key released
RI_KEY_PREV_KEY_STATE = 0x04  # key was previously down (auto-repeat)


class WorkerSignals(QObject):
    log = pyqtSignal(str)
    frame_ready = pyqtSignal(np.ndarray)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error = pyqtSignal(str)
    reconnecting = pyqtSignal(int)
    devices_list = pyqtSignal(list)
    refresh_key_buttons = pyqtSignal()


class FloatingKeyButton(QWidget):
    """悬浮在画面上的可拖拽按键按钮"""

    position_changed = pyqtSignal(str, int, int)  # key_name, screen_x, screen_y

    def __init__(self, key_name: str, screen_x: int, screen_y: int, parent=None):
        super().__init__(parent)
        self.key_name = key_name
        self.screen_x = screen_x
        self.screen_y = screen_y
        self.setFixedSize(44, 44)
        self.setWindowTitle("")
        self.setMouseTracking(True)
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._editing = True  # 编辑模式下可拖拽

        self._label = QLabel(key_name.upper(), self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 200, 0, 180);
                color: #000;
                border-radius: 20px;
                border: 2px solid #ff9800;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        self._label.setFixedSize(44, 44)

        self._update_pos()

    def set_editing(self, editing: bool):
        self._editing = editing
        if editing:
            self._label.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 200, 0, 180);
                    color: #000;
                    border-radius: 20px;
                    border: 2px solid #ff9800;
                    font-weight: bold;
                    font-size: 13px;
                }
            """)
            self.show()
        else:
            self._label.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 200, 0, 50);
                    color: rgba(255, 255, 255, 100);
                    border-radius: 20px;
                    border: 1px solid rgba(255, 152, 0, 60);
                    font-size: 11px;
                }
            """)

    def set_screen_pos(self, x: int, y: int):
        self.screen_x = x
        self.screen_y = y
        self._update_pos()

    def _update_pos(self):
        """根据物理屏幕坐标和视频显示区域更新位置"""
        parent = self.parent()
        if parent and hasattr(parent, '_display_rect') and parent._display_rect.isValid():
            r = parent._display_rect
            pw, ph = getattr(parent, '_physical_size', (0, 0))
            if pw > 0 and ph > 0:
                sx = r.width() / pw
                sy = r.height() / ph
                px = r.x() + int(self.screen_x * sx) - 22
                py = r.y() + int(self.screen_y * sy) - 22
                self.move(px, py)
        else:
            self.move(self.screen_x - 22, self.screen_y - 22)

    def mousePressEvent(self, event):
        if self._editing and event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            parent = self.parent()
            if parent and hasattr(parent, '_display_rect') and parent._display_rect.isValid():
                r = parent._display_rect
                pw, ph = getattr(parent, '_physical_size', (0, 0))
                if pw > 0 and ph > 0:
                    new_pos = self.mapToParent(event.pos() - self._drag_offset)
                    # 限制在视频区域内
                    new_pos.setX(max(r.x(), min(r.right() - 44, new_pos.x())))
                    new_pos.setY(max(r.y(), min(r.bottom() - 44, new_pos.y())))
                    self.move(new_pos)
                    # 转换为物理屏幕坐标
                    self.screen_x = int((new_pos.x() + 22 - r.x()) * (pw / r.width()))
                    self.screen_y = int((new_pos.y() + 22 - r.y()) * (ph / r.height()))
                    self.position_changed.emit(self.key_name, self.screen_x, self.screen_y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.ArrowCursor if self._editing else Qt.ArrowCursor)
        super().mouseReleaseEvent(event)


class VideoLabel(QWidget):
    """视频显示控件（支持叠加层 + 悬浮按钮 + 点击触控）"""

    coordinate_picked = pyqtSignal(int, int)
    normal_touch = pyqtSignal(int, int, int)  # x, y, action (0=down, 1=up, 2=move)
    # QtScrcpy 方式: 游戏模式下用 Qt 鼠标事件
    game_mouse_delta = pyqtSignal(int, int)     # dx, dy
    game_mouse_button = pyqtSignal(str, bool)    # button_name, pressed

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 360)
        self.setStyleSheet("background-color: #1a1a1a;")
        self._pixmap = None
        self._video_size = (0, 0)
        self._physical_size = (config.SCREEN_WIDTH, config.SCREEN_HEIGHT)
        self._screen_w = config.SCREEN_WIDTH
        self._screen_h = config.SCREEN_HEIGHT
        self._display_rect = QRect()

        # 叠加层
        self.show_overlay = True
        self.show_fps = True
        self.fps = 0.0
        self.touch_points = []
        self.mapper = None

        # 坐标拾取
        self.pick_mode = False
        self.pick_hint = ""

        # 模式
        self.operation_mode = "game"  # "normal" or "game"
        self._editing_keys = True  # 是否在编辑悬浮按键

        # 悬浮按钮列表
        self._floating_buttons = []
        self._normal_touch_active = False
        self._game_capturing = False
        self._game_overlay_visible = False  # 摇杆圈/视角/悬浮按键总开关

        self.setMouseTracking(True)

    def set_screen_size(self, w, h):
        self._physical_size = (w, h)

    def set_video_size(self, w, h):
        self._video_size = (w, h)

    def update_frame(self, frame: np.ndarray):
        if frame is None:
            return
        h, w, ch = frame.shape
        self._video_size = (w, h)
        bytes_per_line = ch * w
        # .copy() 必须加! QImage(frame.data, ...) 只是借用 numpy 裸指针, 下一帧 GC 旧数组会花屏
        q_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        q_img = q_img.rgbSwapped().copy()  # 保险起见再 copy 一次
        self._pixmap = QPixmap.fromImage(q_img)
        self.update()
        self._update_floating_positions()

    def add_floating_button(self, btn: FloatingKeyButton):
        btn.setParent(self)
        # 遵循叠加层总开关：未进游戏模式/未激活映射时保持隐藏
        btn.setVisible(self._game_overlay_visible)
        btn._update_pos()
        self._floating_buttons.append(btn)

    def remove_floating_button(self, btn: FloatingKeyButton):
        btn.hide()
        btn.setParent(None)
        if btn in self._floating_buttons:
            self._floating_buttons.remove(btn)

    def clear_floating_buttons(self):
        for btn in list(self._floating_buttons):
            self.remove_floating_button(btn)

    def get_floating_buttons(self):
        return self._floating_buttons

    def _update_floating_positions(self):
        for btn in self._floating_buttons:
            btn._update_pos()
            # 强制跟随总开关（防止刷新/新增时被重新 show 出来）
            if btn.isVisible() != self._game_overlay_visible:
                btn.setVisible(self._game_overlay_visible)

    def set_game_overlay_visible(self, visible: bool):
        """控制游戏模式叠加层：摇杆圈、视角十字、悬浮按键"""
        self._game_overlay_visible = bool(visible)
        for btn in self._floating_buttons:
            btn.setVisible(self._game_overlay_visible)
        self.update()

    def _compute_display_rect(self):
        if not self._pixmap or self._video_size == (0, 0):
            self._display_rect = QRect()
            return
        vw, vh = self._video_size
        cw, ch = self.width(), self.height()
        scale = min(cw / vw, ch / vh)
        dw, dh = int(vw * scale), int(vh * scale)
        dx = (cw - dw) // 2
        dy = (ch - dh) // 2
        self._display_rect = QRect(dx, dy, dw, dh)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 背景
        painter.fillRect(self.rect(), QColor(0x1a, 0x1a, 0x1a))

        if self._pixmap:
            self._compute_display_rect()
            r = self._display_rect
            if r.isValid():
                painter.drawPixmap(r, self._pixmap)
                if self.show_overlay:
                    self._draw_overlay(painter)
        else:
            painter.setPen(QColor(0x88))
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.drawText(self.rect(), Qt.AlignCenter, "等待设备连接...")

        painter.end()

        # 每帧都强制刷新悬浮按钮可见性（不依赖视频是否渲染）
        self._update_floating_positions()

    def _draw_overlay(self, painter):
        r = self._display_rect
        vw, vh = self._video_size
        if vw == 0:
            return
        pw, ph = self._physical_size
        if pw > 0 and ph > 0:
            sx = r.width() / pw
            sy = r.height() / ph
        else:
            sx = r.width() / vw
            sy = r.height() / vh

        pen = QPen()
        pen.setWidth(2)

        # 编辑模式叠加层：摇杆圈 + 视角中心（由"编辑模式"开关控制）
        if self._game_overlay_visible:
            # 摇杆圈
            jx = r.x() + config.JOYSTICK_CENTER_X * sx
            jy = r.y() + config.JOYSTICK_CENTER_Y * sy
            jr = config.JOYSTICK_RADIUS * sx
            pen.setColor(QColor(0, 255, 0, 180))
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(0, 255, 0, 30)))
            painter.drawEllipse(QPoint(int(jx), int(jy)), int(jr), int(jr))
            painter.drawText(int(jx - 30), int(jy - jr - 5), "摇杆")

            # 视角中心
            lx = r.x() + config.LOOK_CENTER_X * sx
            ly = r.y() + config.LOOK_CENTER_Y * sy
            pen.setColor(QColor(255, 0, 0, 180))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(int(lx - 10), int(ly), int(lx + 10), int(ly))
            painter.drawLine(int(lx), int(ly - 10), int(lx), int(ly + 10))
            painter.drawText(int(lx + 5), int(ly - 5), "视角")

        # 触控反馈点（同样由"编辑模式"开关控制）
        if self._game_overlay_visible:
            for (tx, ty, color) in self.touch_points:
                px = r.x() + tx * sx
                py = r.y() + ty * sy
                pen.setColor(color)
                painter.setPen(pen)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPoint(int(px), int(py)), 8, 8)
        elif self.touch_points:
            self.touch_points = []

        # FPS
        if self.show_fps and self.fps > 0:
            pen.setColor(QColor(0, 255, 0))
            painter.setPen(pen)
            painter.setFont(QFont("Consolas", 10, QFont.Bold))
            painter.drawText(r.x() + 8, r.y() + 20, f"FPS: {self.fps:.1f}")

        # 模式指示
        pen.setColor(QColor(0, 200, 255))
        painter.setPen(pen)
        painter.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        mode_text = "普通模式" if self.operation_mode == "normal" else "游戏模式"
        painter.drawText(r.x() + 8, r.y() + 40, f"模式: {mode_text}")

        # 坐标拾取提示
        if self.pick_mode:
            pen.setColor(QColor(255, 255, 0))
            painter.setPen(pen)
            painter.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
            painter.drawText(r.x() + 8, r.bottom() - 8,
                             f"[拾取模式] 点击画面设置: {self.pick_hint}")

    def mousePressEvent(self, event):
        from PyQt5.QtCore import Qt
        # QtScrcpy 方式: 游戏模式下鼠标按钮映射
        if self._game_capturing:
            if event.button() == Qt.LeftButton:
                self.game_mouse_button.emit("left", True)
            elif event.button() == Qt.RightButton:
                self.game_mouse_button.emit("right", True)
            elif event.button() == Qt.MiddleButton:
                self.game_mouse_button.emit("middle", True)
            return
        # 拾取模式优先：即使没有视频画面（未连接设备），也用整个控件区域按
        # 手机屏幕比例映射，保证无视频时也能拾取坐标
        if self.pick_mode:
            pos = event.pos()
            sw = self._screen_w or config.SCREEN_WIDTH
            sh = self._screen_h or config.SCREEN_HEIGHT
            vw, vh = self._video_size
            if self._display_rect.isValid() and vw > 0 and vh > 0:
                r = self._display_rect
                screen_x = int((pos.x() - r.x()) * (vw / r.width()) * sw / vw)
                screen_y = int((pos.y() - r.y()) * (vh / r.height()) * sh / vh)
            else:
                # 无视频：控件区域 = 完整手机屏幕
                screen_x = int(pos.x() * sw / max(1, self.width()))
                screen_y = int(pos.y() * sh / max(1, self.height()))
            print(f"[DBG-PICK] pos=({pos.x()},{pos.y()}) -> screen=({screen_x},{screen_y})")
            self.coordinate_picked.emit(screen_x, screen_y)
            self.pick_mode = False
            self.pick_hint = ""
            self.setCursor(Qt.ArrowCursor)
            self.update()
            super().mousePressEvent(event)
            return
        if not self._display_rect.isValid():
            super().mousePressEvent(event)
            return
        r = self._display_rect
        vw, vh = self._video_size

        if r.contains(event.pos()):
            sx = vw / r.width()
            sy = vh / r.height()
            frame_x = int((event.pos().x() - r.x()) * sx)
            frame_y = int((event.pos().y() - r.y()) * sy)

            if self.operation_mode == "normal":
                # 普通模式触控也需要物理坐标
                sw = self._screen_w or config.SCREEN_WIDTH
                sh = self._screen_h or config.SCREEN_HEIGHT
                if vw > 0 and vh > 0:
                    screen_x = int(frame_x * sw / vw)
                    screen_y = int(frame_y * sh / vh)
                else:
                    screen_x = frame_x
                    screen_y = frame_y
                self._normal_touch_active = True
                self.normal_touch.emit(screen_x, screen_y, 0)  # ACTION_DOWN
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # QtScrcpy 方式: 游戏模式下用 widget 内鼠标位移
        if self._game_capturing:
            c = self.rect().center()
            dx = event.pos().x() - c.x()
            dy = event.pos().y() - c.y()
            if abs(dx) > 0 or abs(dy) > 0:
                print(f"[DBG-MMEVENT] dx={dx} dy={dy}")
                self.game_mouse_delta.emit(int(dx), int(dy))
                # 立即复位到 widget 中心 (QtScrcpy 核心做法)
                from PyQt5.QtGui import QCursor
                QCursor.setPos(self.mapToGlobal(c))
            return

        # 普通模式: 触屏跟踪
        if (self._display_rect.isValid() and self.operation_mode == "normal"
                and getattr(self, '_normal_touch_active', False)):
            r = self._display_rect
            vw, vh = self._video_size
            ex = max(r.x(), min(r.right(), event.pos().x()))
            ey = max(r.y(), min(r.bottom(), event.pos().y()))
            sx = vw / r.width()
            sy = vh / r.height()
            frame_x = int((ex - r.x()) * sx)
            frame_y = int((ey - r.y()) * sy)
            sw = self._screen_w or config.SCREEN_WIDTH
            sh = self._screen_h or config.SCREEN_HEIGHT
            if vw > 0 and vh > 0:
                screen_x = int(frame_x * sw / vw)
                screen_y = int(frame_y * sh / vh)
            else:
                screen_x = frame_x
                screen_y = frame_y
            self.normal_touch.emit(screen_x, screen_y, 2)  # ACTION_MOVE
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # QtScrcpy 方式: 游戏模式下鼠标按钮释放
        if self._game_capturing:
            from PyQt5.QtCore import Qt
            if event.button() == Qt.LeftButton:
                self.game_mouse_button.emit("left", False)
            elif event.button() == Qt.RightButton:
                self.game_mouse_button.emit("right", False)
            elif event.button() == Qt.MiddleButton:
                self.game_mouse_button.emit("middle", False)
            return
        if self._display_rect.isValid() and self.operation_mode == "normal":
            if getattr(self, '_normal_touch_active', False):
                self._normal_touch_active = False
                r = self._display_rect
                vw, vh = self._video_size
                ex = max(r.x(), min(r.right(), event.pos().x()))
                ey = max(r.y(), min(r.bottom(), event.pos().y()))
                sx = vw / r.width()
                sy = vh / r.height()
                frame_x = int((ex - r.x()) * sx)
                frame_y = int((ey - r.y()) * sy)
                sw = self._screen_w or config.SCREEN_WIDTH
                sh = self._screen_h or config.SCREEN_HEIGHT
                if vw > 0 and vh > 0:
                    screen_x = int(frame_x * sw / vw)
                    screen_y = int(frame_y * sh / vh)
                else:
                    screen_x = frame_x
                    screen_y = frame_y
                self.normal_touch.emit(screen_x, screen_y, 1)  # ACTION_UP
        super().mouseReleaseEvent(event)

    def enter_pick_mode(self, hint: str):
        self.pick_mode = True
        self.pick_hint = hint
        self.setCursor(Qt.CrossCursor)
        self.update()

    def cancel_pick_mode(self):
        self.pick_mode = False
        self.pick_hint = ""
        self.setCursor(Qt.ArrowCursor)
        self.update()


class ProfileCombo(QComboBox):
    """点开时自动重新扫描 profiles 目录，外部放入的 JSON 立即可见"""
    def showPopup(self):
        win = self.window()
        if hasattr(win, "_refresh_profile_list"):
            win._refresh_profile_list()
        super().showPopup()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.WINDOW_TITLE)
        self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        self.adb = AdbManager()
        self.scrcpy = None
        self.mapper = None
        self._reconnect_count = 0
        self._reconnecting = False

        self.signals = WorkerSignals()
        self.signals.log.connect(self._append_log)
        self.signals.frame_ready.connect(self._on_frame)
        self.signals.connected.connect(self._on_connected)
        self.signals.disconnected.connect(self._on_disconnected)
        self.signals.error.connect(self._on_error)
        self.signals.reconnecting.connect(self._on_reconnecting)
        self.signals.devices_list.connect(self._update_device_combo)
        self.signals.refresh_key_buttons.connect(self._refresh_floating_buttons_from_mapper)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._poll_frame)
        self._frame_timer.start(16)

        self._build_ui()
        self._apply_dark_theme()

        self._raw_registered = False

        # 自动加载上次配置
        self._load_autosave()

        threading.Thread(target=self._init_adb, daemon=True).start()

    # ============ UI ============
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        left = self._build_left_panel()
        left.setMinimumWidth(240)
        left.setMaximumWidth(320)
        self.left_panel = left

        self.video_label = VideoLabel()
        self.video_label.coordinate_picked.connect(self._on_coordinate_picked)
        self.video_label.normal_touch.connect(self._on_normal_touch)
        self.video_label.game_mouse_delta.connect(self._on_game_mouse_delta)
        self.video_label.game_mouse_button.connect(self._on_game_mouse_button)

        right = self._build_right_panel()
        right.setMinimumWidth(280)
        right.setMaximumWidth(380)
        self.right_panel = right
        # 初始叠加层状态与"编辑模式"勾选框同步
        self.video_label.set_game_overlay_visible(self.chk_edit_keys.isChecked())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.video_label)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        main_layout.addWidget(splitter)
        self.statusBar().showMessage("就绪")

    def _build_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # 模式选择
        mode_box = QGroupBox("操作模式")
        ml = QVBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        self.rb_normal = QRadioButton("普通模式（点击触控）")
        self.rb_game = QRadioButton("游戏模式（键鼠映射）")
        self.rb_game.setChecked(True)
        self.mode_group.addButton(self.rb_normal, 0)
        self.mode_group.addButton(self.rb_game, 1)
        self.rb_normal.toggled.connect(self._on_mode_changed)
        self.rb_game.toggled.connect(self._on_mode_changed)
        ml.addWidget(self.rb_normal)
        ml.addWidget(self.rb_game)
        layout.addWidget(mode_box)

        # 设备
        dev = QGroupBox("设备连接")
        dl = QVBoxLayout(dev)
        dl.addWidget(QLabel("已连接设备:"))
        self.device_combo = QComboBox()
        dl.addWidget(self.device_combo)
        self.btn_refresh = QPushButton("刷新设备")
        self.btn_refresh.clicked.connect(self._refresh_devices)
        dl.addWidget(self.btn_refresh)
        self.btn_connect = QPushButton("连接设备")
        self.btn_connect.clicked.connect(self._connect_device)
        dl.addWidget(self.btn_connect)
        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.clicked.connect(self._disconnect_device)
        self.btn_disconnect.setEnabled(False)
        dl.addWidget(self.btn_disconnect)
        layout.addWidget(dev)

        # 自动重连
        rc = QGroupBox("自动重连")
        rl = QVBoxLayout(rc)
        self.chk_autoreconnect = QCheckBox("断连后自动重连")
        self.chk_autoreconnect.setChecked(config.AUTO_RECONNECT)
        rl.addWidget(self.chk_autoreconnect)
        hb = QHBoxLayout()
        hb.addWidget(QLabel("延迟(秒):"))
        self.spin_reconnect_delay = QDoubleSpinBox()
        self.spin_reconnect_delay.setRange(0.5, 10.0)
        self.spin_reconnect_delay.setSingleStep(0.5)
        self.spin_reconnect_delay.setValue(config.RECONNECT_DELAY)
        hb.addWidget(self.spin_reconnect_delay)
        rl.addLayout(hb)
        layout.addWidget(rc)

        # 映射控制
        mp = QGroupBox("键鼠映射 (游戏模式)")
        ml2 = QVBoxLayout(mp)
        self.btn_capture = QPushButton("▶ 激活映射 (F9)")
        self.btn_capture.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; padding: 8px;")
        self.btn_capture.clicked.connect(self._toggle_capture)
        self.btn_capture.setEnabled(False)
        ml2.addWidget(self.btn_capture)
        self.capture_status = QLabel("状态: 未激活")
        self.capture_status.setAlignment(Qt.AlignCenter)
        ml2.addWidget(self.capture_status)
        layout.addWidget(mp)

        # 悬浮按键编辑
        fk = QGroupBox("悬浮按键编辑")
        fl = QVBoxLayout(fk)
        self.chk_edit_keys = QCheckBox("编辑模式（拖动调整位置）")
        self.chk_edit_keys.setChecked(True)
        self.chk_edit_keys.stateChanged.connect(self._on_edit_keys_toggled)
        fl.addWidget(self.chk_edit_keys)
        kr = QHBoxLayout()
        self.btn_add_fkey = QPushButton("添加按键")
        self.btn_add_fkey.clicked.connect(self._add_floating_key)
        kr.addWidget(self.btn_add_fkey)
        self.btn_del_fkey = QPushButton("删除选中")
        self.btn_del_fkey.clicked.connect(self._del_floating_key)
        kr.addWidget(self.btn_del_fkey)
        fl.addLayout(kr)
        self.fkey_combo = QComboBox()
        self.fkey_combo.setEditable(True)
        self.fkey_combo.addItems(["left", "right", "space", "r", "f", "g", "c", "z", "m", "tab"])
        fl.addWidget(self.fkey_combo)
        layout.addWidget(fk)

        # 截图
        ss = QGroupBox("截图")
        sl = QVBoxLayout(ss)
        self.btn_screenshot = QPushButton("截图 (Ctrl+S)")
        self.btn_screenshot.clicked.connect(self._take_screenshot)
        self.btn_screenshot.setEnabled(False)
        sl.addWidget(self.btn_screenshot)
        layout.addWidget(ss)

        # 配置
        cfg = QGroupBox("键位配置")
        cl = QVBoxLayout(cfg)
        cl.addWidget(QLabel("当前配置:"))
        self.profile_combo = ProfileCombo()
        self.profile_combo.addItem("默认")
        # 加载已保存的配置列表
        for pname in profile_mgr.list_profiles():
            self.profile_combo.addItem(pname)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        cl.addWidget(self.profile_combo)
        br = QHBoxLayout()
        self.btn_save_profile = QPushButton("保存")
        self.btn_save_profile.clicked.connect(self._save_profile)
        br.addWidget(self.btn_save_profile)
        self.btn_load_profile = QPushButton("加载")
        self.btn_load_profile.clicked.connect(self._load_profile)
        br.addWidget(self.btn_load_profile)
        cl.addLayout(br)
        layout.addWidget(cfg)

        # 日志
        lg = QGroupBox("日志")
        ll = QVBoxLayout(lg)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "background-color: #0d0d0d; color: #0f0; font-family: Consolas;")
        ll.addWidget(self.log_text)
        layout.addWidget(lg, stretch=1)
        return panel

    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.tabs = QTabWidget()

        # 摇杆（游戏模式）
        jt = QWidget()
        jf = QFormLayout(jt)
        self.spin_js_x = QSpinBox()
        self.spin_js_x.setRange(0, 5000)
        self.spin_js_x.setValue(config.JOYSTICK_CENTER_X)
        jf.addRow("摇杆中心 X:", self._with_pick_btn(self.spin_js_x, "摇杆中心"))
        self.spin_js_y = QSpinBox()
        self.spin_js_y.setRange(0, 5000)
        self.spin_js_y.setValue(config.JOYSTICK_CENTER_Y)
        jf.addRow("摇杆中心 Y:", self.spin_js_y)
        self.spin_js_r = QSpinBox()
        self.spin_js_r.setRange(20, 500)
        self.spin_js_r.setValue(config.JOYSTICK_RADIUS)
        jf.addRow("摇杆半径:", self.spin_js_r)
        self.tabs.addTab(jt, "摇杆")

        # 视角（游戏模式）
        lt = QWidget()
        lf = QFormLayout(lt)
        self.spin_look_x = QSpinBox()
        self.spin_look_x.setRange(0, 5000)
        self.spin_look_x.setValue(config.LOOK_CENTER_X)
        lf.addRow("视角中心 X:", self._with_pick_btn(self.spin_look_x, "视角中心"))
        self.spin_look_y = QSpinBox()
        self.spin_look_y.setRange(0, 5000)
        self.spin_look_y.setValue(config.LOOK_CENTER_Y)
        lf.addRow("视角中心 Y:", self.spin_look_y)
        self.slider_sens = QSlider(Qt.Horizontal)
        self.slider_sens.setRange(1, 200)
        self.slider_sens.setValue(int(config.MOUSE_SENSITIVITY * 100))
        self.lbl_sens = QLabel(f"{config.MOUSE_SENSITIVITY:.2f}")
        self.slider_sens.valueChanged.connect(
            lambda v: self.lbl_sens.setText(f"{v / 100:.2f}"))
        row = QHBoxLayout()
        row.addWidget(self.slider_sens)
        row.addWidget(self.lbl_sens)
        lf.addRow("鼠标灵敏度:", self._wrap(row))
        self.chk_overlay = QCheckBox("显示叠加层(摇杆/视角)")
        self.chk_overlay.setChecked(config.SHOW_OVERLAY)
        self.chk_overlay.stateChanged.connect(
            lambda v: setattr(self.video_label, 'show_overlay', v == Qt.Checked))
        lf.addRow(self.chk_overlay)
        self.chk_fps = QCheckBox("显示 FPS")
        self.chk_fps.setChecked(config.SHOW_FPS)
        self.chk_fps.stateChanged.connect(
            lambda v: setattr(self.video_label, 'show_fps', v == Qt.Checked))
        lf.addRow(self.chk_fps)
        self.tabs.addTab(lt, "视角")

        # 连点器
        at = QWidget()
        af = QFormLayout(at)
        self.chk_autoclick = QCheckBox("启用连点器")
        self.chk_autoclick.setChecked(config.AUTO_CLICK_ENABLED)
        af.addRow(self.chk_autoclick)
        self.combo_ac_btn = QComboBox()
        self.combo_ac_btn.addItems(["left", "right"])
        self.combo_ac_btn.setCurrentText(config.AUTO_CLICK_BUTTON)
        af.addRow("连点按键:", self.combo_ac_btn)
        self.spin_ac_interval = QDoubleSpinBox()
        self.spin_ac_interval.setRange(0.01, 1.0)
        self.spin_ac_interval.setSingleStep(0.01)
        self.spin_ac_interval.setValue(config.AUTO_CLICK_INTERVAL)
        af.addRow("连点间隔(秒):", self.spin_ac_interval)
        self.spin_ac_hold = QDoubleSpinBox()
        self.spin_ac_hold.setRange(0.005, 0.5)
        self.spin_ac_hold.setSingleStep(0.005)
        self.spin_ac_hold.setValue(config.AUTO_CLICK_HOLD_TIME)
        af.addRow("按下时长(秒):", self.spin_ac_hold)
        self.tabs.addTab(at, "连点器")

        # 压枪
        rt = QWidget()
        rf = QFormLayout(rt)
        self.chk_recoil = QCheckBox("启用压枪辅助")
        self.chk_recoil.setChecked(config.RECOIL_CONTROL_ENABLED)
        rf.addRow(self.chk_recoil)
        self.spin_recoil_str = QDoubleSpinBox()
        self.spin_recoil_str.setRange(0.5, 50.0)
        self.spin_recoil_str.setSingleStep(0.5)
        self.spin_recoil_str.setValue(config.RECOIL_STRENGTH)
        rf.addRow("压枪强度:", self.spin_recoil_str)
        self.spin_recoil_int = QDoubleSpinBox()
        self.spin_recoil_int.setRange(0.01, 0.5)
        self.spin_recoil_int.setSingleStep(0.01)
        self.spin_recoil_int.setValue(config.RECOIL_INTERVAL)
        rf.addRow("下压间隔(秒):", self.spin_recoil_int)
        self.tabs.addTab(rt, "压枪")

        # 宏
        mt = QWidget()
        ml2 = QVBoxLayout(mt)
        ml2.addWidget(QLabel("宏列表:"))
        self.macro_list = QListWidget()
        ml2.addWidget(self.macro_list)
        mr1 = QHBoxLayout()
        self.btn_add_macro = QPushButton("新建宏")
        self.btn_add_macro.clicked.connect(self._add_macro)
        mr1.addWidget(self.btn_add_macro)
        self.btn_del_macro = QPushButton("删除宏")
        self.btn_del_macro.clicked.connect(self._del_macro)
        mr1.addWidget(self.btn_del_macro)
        ml2.addLayout(mr1)
        ml2.addWidget(QLabel("宏步骤 (按键,时长秒):"))
        self.macro_steps = QTextEdit()
        self.macro_steps.setPlaceholderText("每行一个步骤，格式: 按键,时长\n例如:\nr,0.1\nf,0.1\nspace,0.2")
        ml2.addWidget(self.macro_steps)
        mr2 = QHBoxLayout()
        mr2.addWidget(QLabel("触发键:"))
        self.macro_trigger = QLineEdit()
        self.macro_trigger.setPlaceholderText("如: f1")
        mr2.addWidget(self.macro_trigger)
        ml2.addLayout(mr2)
        self.btn_save_macro = QPushButton("保存宏步骤")
        self.btn_save_macro.clicked.connect(self._save_macro_steps)
        ml2.addWidget(self.btn_save_macro)
        self.tabs.addTab(mt, "宏")

        # 画质
        qt = QWidget()
        qf = QFormLayout(qt)
        self.spin_max_size = QSpinBox()
        self.spin_max_size.setRange(0, 4096)
        self.spin_max_size.setSingleStep(128)
        self.spin_max_size.setValue(config.MAX_SIZE)
        qf.addRow("最大分辨率:", self.spin_max_size)
        self.spin_bitrate = QSpinBox()
        self.spin_bitrate.setRange(1, 50)
        self.spin_bitrate.setValue(config.BIT_RATE // 1_000_000)
        qf.addRow("码率(Mbps):", self.spin_bitrate)
        self.spin_fps_q = QSpinBox()
        self.spin_fps_q.setRange(15, 120)
        self.spin_fps_q.setValue(config.MAX_FPS)
        qf.addRow("帧率:", self.spin_fps_q)
        self.combo_codec = QComboBox()
        self.combo_codec.addItems(["h264", "h265"])
        self.combo_codec.setCurrentText(config.VIDEO_CODEC)
        qf.addRow("编码器:", self.combo_codec)
        self.btn_apply_quality = QPushButton("应用画质(需重连)")
        self.btn_apply_quality.clicked.connect(self._apply_quality)
        qf.addRow(self.btn_apply_quality)
        self.tabs.addTab(qt, "画质")

        layout.addWidget(self.tabs)
        self.btn_apply = QPushButton("应用所有设置")
        self.btn_apply.clicked.connect(self._apply_settings)
        layout.addWidget(self.btn_apply)
        return panel

    def _with_pick_btn(self, spin, hint):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(spin)
        btn = QPushButton("瞄准")
        btn.setFixedWidth(36)
        btn.clicked.connect(lambda _, sp=spin, hh=hint: self._start_pick(sp, hh))
        self.video_label.coordinate_picked.connect(
            lambda x, y, sp=spin: self._set_spin_from_pick(sp, x, y))
        h.addWidget(btn)
        return w

    def _start_pick(self, spin, hint):
        """进入拾取模式；若映射激活则先暂停（否则鼠标被 grab，点不到画面）"""
        self._active_pick_spin = spin   # 记录本次拾取目标，广播信号只对它生效
        self._active_pick_hint = hint
        if self.mapper and self.mapper.is_capturing:
            self._pick_was_capturing = True
            self.mapper.deactivate_capture()
            self.signals.log.emit(f"拾取模式({hint})：映射已暂停，点击画面选择位置")
        self.video_label.enter_pick_mode(hint)

    def _set_spin_from_pick(self, spin, x, y):
        # 信号是广播的：只处理"当前拾取目标"的回调，防止摇杆/视角互相污染
        if spin is not getattr(self, "_active_pick_spin", None):
            return
        if spin is self.spin_js_x:
            self.spin_js_x.setValue(x)
            self.spin_js_y.setValue(y)
            config.JOYSTICK_CENTER_X = x
            config.JOYSTICK_CENTER_Y = y
        elif spin is self.spin_look_x:
            self.spin_look_x.setValue(x)
            self.spin_look_y.setValue(y)
            config.LOOK_CENTER_X = x
            config.LOOK_CENTER_Y = y
        self._active_pick_spin = None  # 拾取完成
        self.video_label.update()
        self._do_autosave()
        self.signals.log.emit(f"{self._active_pick_hint} 已拾取: ({x}, {y})")
        # 拾取前映射是激活状态 → 自动恢复
        if getattr(self, "_pick_was_capturing", False):
            self._pick_was_capturing = False
            if self.rb_game.isChecked() and self.mapper and self.scrcpy:
                self.mapper.activate_capture()

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        w.setLayout(layout)
        return w

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #2b2b2b; color: #ddd; }
            QGroupBox { border: 1px solid #555; border-radius: 4px;
                        margin-top: 8px; padding-top: 8px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QPushButton { background-color: #3c3f41; border: 1px solid #555;
                          border-radius: 3px; padding: 0 10px; min-height: 24px; }
            QPushButton:hover { background-color: #4c5052; }
            QPushButton:disabled { color: #777; background-color: #333; }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QListWidget,
            QTextEdit, QTableWidget {
                background-color: #1e1e1e; border: 1px solid #555; border-radius: 2px;
                padding: 0 4px; min-height: 22px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView { padding: 4px; background: #1e1e1e;
                                          color: #ddd; }
            QComboBox::item { min-height: 24px; padding: 0 6px; }
            QTabWidget::pane { border: 1px solid #555; }
            QTabBar::tab { background: #3c3f41; padding: 0 12px; min-height: 26px; }
            QTabBar::tab:selected { background: #505355; }
            QSlider::groove:horizontal { height: 6px; background: #555; }
            QSlider::handle:horizontal { background: #4fc3f7; width: 14px; margin: -5px 0; }
            QStatusBar { background-color: #1e1e1e; }
            QHeaderView::section { background-color: #3c3f41; padding: 4px; border: 1px solid #555; }
            QRadioButton { padding: 0; }
            QRadioButton::indicator { width: 14px; height: 14px; }
            QCheckBox { padding: 0; }
            QCheckBox::indicator { width: 14px; height: 14px; }
        """)

    # ============ 自动保存/加载 ============
    def _load_autosave(self):
        """启动时自动加载上次的配置"""
        data = profile_mgr.load_autosave()
        if data is None:
            return
        try:
            # 模式
            mode = data.get("mode", "game")
            if mode == "normal":
                self.rb_normal.setChecked(True)
            else:
                self.rb_game.setChecked(True)
            # 摇杆
            js = data.get("joystick", {})
            self.spin_js_x.setValue(js.get("center_x", config.JOYSTICK_CENTER_X))
            self.spin_js_y.setValue(js.get("center_y", config.JOYSTICK_CENTER_Y))
            self.spin_js_r.setValue(js.get("radius", config.JOYSTICK_RADIUS))
            config.JOYSTICK_CENTER_X = js.get("center_x", config.JOYSTICK_CENTER_X)
            config.JOYSTICK_CENTER_Y = js.get("center_y", config.JOYSTICK_CENTER_Y)
            config.JOYSTICK_RADIUS = js.get("radius", config.JOYSTICK_RADIUS)
            # 视角
            lk = data.get("look", {})
            self.spin_look_x.setValue(lk.get("center_x", config.LOOK_CENTER_X))
            self.spin_look_y.setValue(lk.get("center_y", config.LOOK_CENTER_Y))
            self.slider_sens.setValue(int(lk.get("sensitivity", config.MOUSE_SENSITIVITY) * 100))
            config.LOOK_CENTER_X = lk.get("center_x", config.LOOK_CENTER_X)
            config.LOOK_CENTER_Y = lk.get("center_y", config.LOOK_CENTER_Y)
            config.MOUSE_SENSITIVITY = lk.get("sensitivity", config.MOUSE_SENSITIVITY)
            # 连点器
            ac = data.get("auto_click", {})
            self.chk_autoclick.setChecked(ac.get("enabled", False))
            self.combo_ac_btn.setCurrentText(ac.get("button", "left"))
            self.spin_ac_interval.setValue(ac.get("interval", 0.05))
            self.spin_ac_hold.setValue(ac.get("hold_time", 0.02))
            config.AUTO_CLICK_ENABLED = ac.get("enabled", config.AUTO_CLICK_ENABLED)
            config.AUTO_CLICK_BUTTON = ac.get("button", config.AUTO_CLICK_BUTTON)
            config.AUTO_CLICK_INTERVAL = ac.get("interval", config.AUTO_CLICK_INTERVAL)
            config.AUTO_CLICK_HOLD_TIME = ac.get("hold_time", config.AUTO_CLICK_HOLD_TIME)
            # 压枪
            rc = data.get("recoil", {})
            self.chk_recoil.setChecked(rc.get("enabled", False))
            self.spin_recoil_str.setValue(rc.get("strength", 3.0))
            self.spin_recoil_int.setValue(rc.get("interval", 0.03))
            config.RECOIL_CONTROL_ENABLED = rc.get("enabled", config.RECOIL_CONTROL_ENABLED)
            config.RECOIL_STRENGTH = rc.get("strength", config.RECOIL_STRENGTH)
            config.RECOIL_INTERVAL = rc.get("interval", config.RECOIL_INTERVAL)
            # 悬浮按键
            if self.mapper:
                self.mapper.key_map.clear()
                for key, info in data.get("keymap", {}).items():
                    pos = info.get("pos", [0, 0])
                    self.mapper.key_map[key] = tuple(pos)
                # 宏
                self.mapper.macros = dict(data.get("macros", {}))
                self.mapper.macro_keys = dict(data.get("macro_keys", {}))
                QTimer.singleShot(200, self._refresh_floating_buttons_from_mapper)
            self.signals.log.emit("已自动加载上次配置")
        except Exception as e:
            self.signals.log.emit(f"加载自动配置失败: {e}")

    def _do_autosave(self):
        """自动保存当前配置"""
        try:
            data = self._collect_profile_data()
            profile_mgr.save_autosave(data)
        except Exception:
            pass

    # ============ 模式切换 ============
    def _exit_game_mode(self):
        """Esc 触发：彻底退出游戏模式，回到普通模式"""
        self.rb_normal.setChecked(True)

    def _on_mode_changed(self):
        # 防重复触发：模式没真正变化就跳过（两个 radio 的 toggled 都连了本槽）
        mode = "normal" if self.rb_normal.isChecked() else "game"
        if getattr(self, "_current_mode", None) == mode:
            return
        self._current_mode = mode
        if self.rb_normal.isChecked():
            self.video_label.operation_mode = "normal"
            config.OPERATION_MODE = "normal"
            # 普通模式：隐藏全部游戏叠加层（悬浮按键+摇杆轮盘+视角）
            self.video_label.set_game_overlay_visible(False)
            # 取消游戏模式映射
            if self.mapper and self.mapper.is_capturing:
                self.mapper.deactivate_capture()
                self.btn_capture.setText("▶ 激活映射 (F9)")
                self.btn_capture.setStyleSheet(
                    "background-color: #2e7d32; color: white; font-weight: bold; padding: 8px;")
                self.capture_status.setText("状态: 未激活")
            # 退出全屏 + 恢复两侧设置栏
            if self.isFullScreen():
                self.showNormal()
            self.left_panel.show()
            self.right_panel.show()
            self.signals.log.emit("切换到普通模式 - 点击/拖动画面直接触控")
        else:
            self.video_label.operation_mode = "game"
            config.OPERATION_MODE = "game"
            # 进入游戏模式：收起两侧设置栏 + 自动全屏
            self.left_panel.hide()
            self.right_panel.hide()
            self.showFullScreen()
            # 自动激活映射（叠加层由 _on_capture_state_changed 统一控制显示）
            if self.mapper and not self.mapper.is_capturing and self.scrcpy:
                self.mapper.activate_capture()
            self.signals.log.emit("切换到游戏模式 - 键鼠映射已自动激活")
        self._sync_capture_ui()
        self.video_label.update()
        self._do_autosave()

    def _sync_capture_ui(self):
        """根据当前映射状态统一刷新：按钮文字/状态栏/叠加层可见性"""
        active = bool(self.mapper and self.mapper.is_capturing)
        if active:
            self.btn_capture.setText("■ 取消映射 (F9)")
            self.btn_capture.setStyleSheet(
                "background-color: #c62828; color: white; font-weight: bold; padding: 8px;")
            self.capture_status.setText("状态: 已激活")
        else:
            self.btn_capture.setText("▶ 激活映射 (F9)")
            self.btn_capture.setStyleSheet(
                "background-color: #2e7d32; color: white; font-weight: bold; padding: 8px;")
            self.capture_status.setText("状态: 未激活")
        # 叠加层可见性由"编辑模式"开关统一控制（与游戏/普通模式无关）
        self.video_label.set_game_overlay_visible(self.chk_edit_keys.isChecked())

    def _on_capture_state_changed(self, active):
        """F9/Esc 等直接激活/取消映射时的回调"""
        self._sync_capture_ui()

    def _on_edit_keys_toggled(self, state):
        editing = state == Qt.Checked
        for btn in self.video_label.get_floating_buttons():
            btn.set_editing(editing)
        # 编辑模式 = 叠加层总开关：开→显示轮盘/触点/悬浮按键，关→全部隐藏
        self.video_label.set_game_overlay_visible(editing)

    # ============ 悬浮按键 ============
    def _add_floating_key(self):
        key_name = self.fkey_combo.currentText().strip().lower()
        if not key_name:
            key_name = "k"
        # 找一个空位
        x = config.SCREEN_WIDTH // 2
        y = config.SCREEN_HEIGHT // 2
        btn = FloatingKeyButton(key_name, x, y)
        btn.position_changed.connect(self._on_fkey_moved)
        self.video_label.add_floating_button(btn)
        if self.mapper:
            self.mapper.key_map[key_name] = (x, y)
        self.fkey_combo.addItem(key_name)
        self.fkey_combo.setCurrentText(key_name)
        self.signals.log.emit(f"添加悬浮按键: {key_name}")
        self._do_autosave()

    def _del_floating_key(self):
        key_name = self.fkey_combo.currentText().strip().lower()
        buttons = self.video_label.get_floating_buttons()
        for btn in buttons:
            if btn.key_name == key_name:
                self.video_label.remove_floating_button(btn)
                if self.mapper and key_name in self.mapper.key_map:
                    del self.mapper.key_map[key_name]
                self.signals.log.emit(f"删除悬浮按键: {key_name}")
                self._do_autosave()
                return
        QMessageBox.information(self, "提示", f"未找到按键: {key_name}")

    def _on_fkey_moved(self, key_name, x, y):
        if self.mapper:
            self.mapper.key_map[key_name] = (x, y)
        self.video_label.update()
        self._do_autosave()

    def _refresh_floating_buttons_from_mapper(self):
        """从 mapper 的 key_map 刷新悬浮按钮"""
        self.video_label.clear_floating_buttons()
        self.fkey_combo.clear()
        if not self.mapper:
            return
        for key_name, (x, y) in self.mapper.key_map.items():
            btn = FloatingKeyButton(key_name, x, y)
            btn.position_changed.connect(self._on_fkey_moved)
            btn.set_editing(self.chk_edit_keys.isChecked())
            self.video_label.add_floating_button(btn)
            self.fkey_combo.addItem(key_name)

    # ============ 设备 ============
    def showEvent(self, event):
        """窗口显示后注册 Raw Input（此时 winId() 有效）"""
        super().showEvent(event)
        if not self._raw_registered:
            self._register_raw_input()

    def _register_raw_input(self):
        """注册 Raw Input — 同时注册鼠标和键盘（绕过被拦截的全局 hook）"""
        try:
            hwnd = int(self.winId())
            if hwnd == 0:
                print("[RAW] winId() returned 0, skip register")
                return

            class RAWINPUTDEVICE(ctypes.Structure):
                _fields_ = [
                    ("usUsagePage", wintypes.USHORT),
                    ("usUsage", wintypes.USHORT),
                    ("dwFlags", wintypes.DWORD),
                    ("hwndTarget", wintypes.HWND),
                ]

            # Register BOTH mouse (0x01/0x02) AND keyboard (0x01/0x06)
            rids = (RAWINPUTDEVICE * 2)()
            rids[0].usUsagePage = 0x01   # HID generic desktop
            rids[0].usUsage     = 0x02   # Mouse
            rids[0].dwFlags     = 0x00000100  # RIDEV_INPUTSINK
            rids[0].hwndTarget  = hwnd

            rids[1].usUsagePage = 0x01   # HID generic desktop
            rids[1].usUsage     = 0x06   # Keyboard
            rids[1].dwFlags     = 0x00000100  # RIDEV_INPUTSINK
            rids[1].hwndTarget  = hwnd

            ok = ctypes.windll.user32.RegisterRawInputDevices(
                ctypes.byref(rids), 2, ctypes.sizeof(RAWINPUTDEVICE))
            if ok:
                self._raw_registered = True
                print("[RAW] Mouse+Keyboard Raw Input registered OK!")
            else:
                print(f"[RAW] Register failed, error={ctypes.GetLastError()}")
        except Exception as e:
            print(f"[RAW] Register exception: {e}")

    def nativeEvent(self, eventType, message):
        """处理 WM_INPUT — 鼠标位移 + 鼠标按钮 + 键盘按键"""
        if not self._raw_registered:
            return super().nativeEvent(eventType, message)
        if eventType != b"windows_generic_MSG":
            return super().nativeEvent(eventType, message)
        try:
            msg = ctypes.cast(int(message),
                              ctypes.POINTER(wintypes.MSG)).contents
            if msg.message != 0x00FF:  # WM_INPUT
                return super().nativeEvent(eventType, message)

            hrawinput = msg.lParam
            size = wintypes.UINT(0)
            ctypes.windll.user32.GetRawInputData(
                hrawinput, 0x10000003, None, ctypes.byref(size),
                ctypes.sizeof(_RAWINPUTHEADER))
            if size.value == 0:
                return True, 0

            buf = ctypes.create_string_buffer(size.value)
            ret = ctypes.windll.user32.GetRawInputData(
                hrawinput, 0x10000003, buf, ctypes.byref(size),
                ctypes.sizeof(_RAWINPUTHEADER))
            if ret <= 0:
                return True, 0

            raw = ctypes.cast(buf, ctypes.POINTER(_RAWINPUT)).contents
            dwType = raw.header.dwType

            if dwType == 0:  # RIM_TYPEMOUSE — 跳过, 改用 Qt mouseMoveEvent
                pass

            elif dwType == 1:  # RIM_TYPEKEYBOARD
                kb = raw.data.keyboard
                vkey = kb.VKey
                scan = kb.MakeCode
                is_down = not (kb.Flags & RI_KEY_BREAK)  # 0=make(down), 1=break(up)
                is_repeat = bool(kb.Flags & RI_KEY_PREV_KEY_STATE)  # key held (auto-repeat)
                if self.mapper:
                    self.mapper.on_raw_key(vkey, scan, is_down, is_repeat)

            return True, 0  # Handled
        except Exception as e:
            print(f"[RAW] nativeEvent exception: {e}")
            return super().nativeEvent(eventType, message)

    def _init_adb(self):
        self.signals.log.emit("正在启动 ADB server...")
        self.signals.log.emit(f"使用 ADB: {self.adb.adb_path}")
        if self.adb.start_server():
            self.signals.log.emit("ADB server 已就绪")
            time.sleep(1)
            self._refresh_devices_async()
        else:
            self.signals.error.emit("ADB server 启动失败")

    def _refresh_devices(self):
        threading.Thread(target=self._refresh_devices_async, daemon=True).start()

    def _refresh_devices_async(self):
        devices = self.adb.get_devices()
        self.signals.log.emit(f"发现 {len(devices)} 台设备")
        self.signals.devices_list.emit(devices)

    def _update_device_combo(self, devices):
        self.device_combo.clear()
        if devices:
            for d in devices:
                self.device_combo.addItem(d)
            self.device_combo.setCurrentIndex(0)
        else:
            self.device_combo.addItem("未检测到设备")

    def _connect_device(self):
        try:
            devices = self.adb.get_devices()
        except Exception as e:
            QMessageBox.warning(self, "提示", f"获取设备失败: {e}")
            return
        if not devices:
            QMessageBox.warning(self, "提示", "请先连接安卓设备并开启 USB 调试")
            return
        serial = devices[0]
        self.btn_connect.setEnabled(False)
        self.statusBar().showMessage("正在连接...")
        self.signals.log.emit(f"正在连接设备: {serial}")
        threading.Thread(target=self._do_connect, args=(serial,), daemon=True).start()

    def _do_connect(self, serial):
        try:
            self.scrcpy = ScrcpyClient(self.adb, serial)
            self.scrcpy.on_frame = lambda f: self.signals.frame_ready.emit(f)
            self.scrcpy.on_error = lambda m: self.signals.error.emit(m)
            self.scrcpy.on_disconnected = self._handle_disconnect

            if self.scrcpy.connect(timeout=15):
                w, h = self.adb.get_screen_size(serial)
                if w and h:
                    self.scrcpy.screen_width = w
                    self.scrcpy.screen_height = h
                    config.SCREEN_WIDTH = w
                    config.SCREEN_HEIGHT = h
                    self.video_label.set_screen_size(w, h)
                # 保存旧 mapper 的键位配置，避免连接时丢失
                old_keymap = dict(self.mapper.key_map) if self.mapper else {}
                old_macros = dict(self.mapper.macros) if self.mapper else {}
                old_macro_keys = dict(self.mapper.macro_keys) if self.mapper else {}
                print(f"[DBG-CONNECT] old_keymap keys={len(old_keymap)}: {sorted(old_keymap.keys()) if old_keymap else 'EMPTY'}")

                self.mapper = InputMapper(self.scrcpy, self.video_label)
                self.mapper.on_status = lambda m: self.signals.log.emit(m)
                self.mapper.on_exit_game_mode = self._exit_game_mode
                self.mapper.on_capture_state = self._on_capture_state_changed
                # 恢复键位配置 - 仅在有旧配置时才覆盖, 否则保留默认
                if old_keymap:
                    self.mapper.key_map = old_keymap
                else:
                    # 没有 mapper 前选的配置 → 此时把激活配置的按键映射应用进去
                    self._apply_keymap_to_mapper()
                self.mapper.macros = old_macros
                self.mapper.macro_keys = old_macro_keys
                print(f"[DBG-CONNECT] final mapper key_map keys={len(self.mapper.key_map)}")
                self.mapper.start()
                QTimer.singleShot(100, self._refresh_floating_buttons_from_mapper)
                self.video_label.mapper = self.mapper
                self._reconnect_count = 0
                self.signals.connected.emit()
                self.signals.log.emit(f"设备已连接: {self.scrcpy.device_name} ({w}x{h})")
                self.signals.refresh_key_buttons.emit()
            else:
                detail = self.scrcpy.last_error if self.scrcpy.last_error else "未知原因"
                self.signals.error.emit(f"连接失败: {detail}")
                self.btn_connect.setEnabled(True)
                self.statusBar().showMessage("连接失败")
        except Exception as e:
            self.signals.error.emit(f"连接异常: {e}")
            self.btn_connect.setEnabled(True)
            self.statusBar().showMessage("连接异常")

    def _disconnect_device(self):
        if self.mapper:
            self.mapper.stop()
            self.mapper = None
        if self.scrcpy:
            self.scrcpy.disconnect()
            self.scrcpy = None
        self._reconnecting = False
        self.signals.disconnected.emit()
        self.signals.log.emit("设备已断开")

    def _handle_disconnect(self):
        if self._reconnecting:
            return
        if not self.chk_autoreconnect.isChecked():
            self.signals.disconnected.emit()
            self.signals.log.emit("设备断开（自动重连已关闭）")
            return
        self._reconnecting = True
        self.signals.log.emit("设备断开，准备自动重连...")
        self.signals.disconnected.emit()
        threading.Thread(target=self._auto_reconnect, daemon=True).start()

    def _auto_reconnect(self):
        serial = self.device_combo.currentText()
        if not serial or serial == "未检测到设备":
            self._reconnecting = False
            return
        max_retries = config.RECONNECT_MAX_RETRIES
        while self._reconnecting and (max_retries == 0 or self._reconnect_count < max_retries):
            self._reconnect_count += 1
            delay = self.spin_reconnect_delay.value()
            self.signals.log.emit(f"等待 {delay} 秒后重连 (第 {self._reconnect_count} 次)...")
            self.signals.reconnecting.emit(self._reconnect_count)
            time.sleep(delay)
            if self.scrcpy:
                self.scrcpy.disconnect()
                self.scrcpy = None
            self.signals.log.emit("正在重新连接...")
            try:
                self.scrcpy = ScrcpyClient(self.adb, serial)
                self.scrcpy.on_frame = lambda f: self.signals.frame_ready.emit(f)
                self.scrcpy.on_error = lambda m: self.signals.log.emit(f"[重连错误] {m}")
                self.scrcpy.on_disconnected = self._handle_disconnect
                if self.scrcpy.connect(timeout=10):
                    w, h = self.adb.get_screen_size(serial)
                    if w and h:
                        self.scrcpy.screen_width = w
                        self.scrcpy.screen_height = h
                        self.video_label.set_screen_size(w, h)
                    if self.mapper:
                        self.mapper.scrcpy = self.scrcpy
                    else:
                        self.mapper = InputMapper(self.scrcpy, self.video_label)
                        self.mapper.on_status = lambda m: self.signals.log.emit(m)
                        self.mapper.on_exit_game_mode = self._exit_game_mode
                        self.mapper.on_capture_state = self._on_capture_state_changed
                        self.mapper.start()
                    self.video_label.mapper = self.mapper
                    self._reconnecting = False
                    self._reconnect_count = 0
                    self.signals.connected.emit()
                    self.signals.log.emit("重连成功！")
                    return
            except Exception as e:
                self.signals.log.emit(f"重连失败: {e}")
        self._reconnecting = False
        self.signals.log.emit("重连次数已达上限，请手动连接")

    # ============ 映射 ============
    def _toggle_capture(self):
        if not self.mapper:
            return
        active = self.mapper.toggle_capture()
        self._sync_capture_ui()

    # ============ 普通模式触控 ============
    def _on_normal_touch(self, x, y, action):
        """普通模式：鼠标点击/拖动 → 触控事件
        action: 0=DOWN, 1=UP, 2=MOVE
        x, y 已经是物理屏幕坐标（由 VideoLabel 转换好）。
        """
        if not self.scrcpy:
            return
        self.scrcpy.inject_touch(action, x, y, pointer_id=0)

    def _on_game_mouse_delta(self, dx, dy):
        if not hasattr(self, '_dbg_delta_count'): self._dbg_delta_count = 0
        self._dbg_delta_count += 1
        if self._dbg_delta_count <= 5 or self._dbg_delta_count % 50 == 0:
            print(f"[DBG-HOOK] #{self._dbg_delta_count} dx={dx} dy={dy} sens={config.MOUSE_SENSITIVITY}")
        if self.mapper:
            self.mapper.on_raw_mouse_delta(dx, dy)

    def _on_game_mouse_button(self, btn_name, pressed):
        if self.mapper:
            self.mapper.on_raw_button(btn_name, pressed)

    # ============ 视频 ============
    def _on_frame(self, frame):
        self.video_label.update_frame(frame)

    def _poll_frame(self):
        if self.scrcpy:
            frame = self.scrcpy.get_latest_frame()
            if frame is not None:
                self.video_label.fps = self.scrcpy.fps
                self.video_label.update_frame(frame)

    # ============ 截图 ============
    def _take_screenshot(self):
        if not self.scrcpy:
            return
        path = self.scrcpy.take_screenshot()
        if path:
            self.signals.log.emit(f"截图已保存: {path}")
            self.statusBar().showMessage(f"截图: {os.path.basename(path)}")
        else:
            self.signals.log.emit("截图失败：无画面")

    # ============ 宏 ============
    def _add_macro(self):
        name, ok = QInputDialog.getText(self, "新建宏", "宏名称:")
        if ok and name:
            self.macro_list.addItem(name)
            if self.mapper:
                self.mapper.macros[name] = []

    def _del_macro(self):
        row = self.macro_list.currentRow()
        if row >= 0:
            name = self.macro_list.item(row).text()
            self.macro_list.takeItem(row)
            if self.mapper and name in self.mapper.macros:
                del self.mapper.macros[name]

    def _save_macro_steps(self):
        row = self.macro_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一个宏")
            return
        name = self.macro_list.item(row).text()
        trigger = self.macro_trigger.text().strip().lower()
        steps = []
        for line in self.macro_steps.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) == 2:
                key = parts[0].strip().lower()
                try:
                    duration = float(parts[1].strip())
                    steps.append((key, duration))
                except ValueError:
                    pass
        if self.mapper:
            self.mapper.macros[name] = steps
            if trigger:
                self.mapper.macro_keys[trigger] = name
        self.signals.log.emit(f"宏 [{name}] 已保存 ({len(steps)} 步)")

    # ============ 画质 ============
    def _apply_quality(self):
        config.MAX_SIZE = self.spin_max_size.value()
        config.BIT_RATE = self.spin_bitrate.value() * 1_000_000
        config.MAX_FPS = self.spin_fps_q.value()
        config.VIDEO_CODEC = self.combo_codec.currentText()
        self.signals.log.emit("画质设置已保存，重连后生效")
        if self.scrcpy:
            QMessageBox.information(self, "提示", "画质设置需重连设备后生效")

    # ============ 设置应用 ============
    def _apply_settings(self):
        config.JOYSTICK_CENTER_X = self.spin_js_x.value()
        config.JOYSTICK_CENTER_Y = self.spin_js_y.value()
        config.JOYSTICK_RADIUS = self.spin_js_r.value()
        config.LOOK_CENTER_X = self.spin_look_x.value()
        config.LOOK_CENTER_Y = self.spin_look_y.value()
        config.MOUSE_SENSITIVITY = self.slider_sens.value() / 100.0
        config.AUTO_CLICK_ENABLED = self.chk_autoclick.isChecked()
        config.AUTO_CLICK_BUTTON = self.combo_ac_btn.currentText()
        config.AUTO_CLICK_INTERVAL = self.spin_ac_interval.value()
        config.AUTO_CLICK_HOLD_TIME = self.spin_ac_hold.value()
        config.RECOIL_CONTROL_ENABLED = self.chk_recoil.isChecked()
        config.RECOIL_STRENGTH = self.spin_recoil_str.value()
        config.RECOIL_INTERVAL = self.spin_recoil_int.value()
        config.AUTO_RECONNECT = self.chk_autoreconnect.isChecked()
        config.RECONNECT_DELAY = self.spin_reconnect_delay.value()
        self.signals.log.emit("所有设置已应用")
        self._do_autosave()

    # ============ 配置 ============
    def _refresh_profile_list(self):
        """重新扫描 profiles 目录，保持当前选中项不变"""
        current = self.profile_combo.currentText()
        names = ["默认"] + profile_mgr.list_profiles()
        if [self.profile_combo.itemText(i) for i in range(self.profile_combo.count())] == names:
            return
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        idx = self.profile_combo.findText(current)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _on_profile_changed(self, index):
        """切换配置下拉框时自动加载"""
        name = self.profile_combo.currentText()
        if not name:
            return
        if name == "默认":
            data = dict(profile_mgr.DEFAULT_PROFILE)
        else:
            data = profile_mgr.load_profile(name)
        self._apply_profile_data(data)
        self.signals.log.emit(f"已加载配置: {name}")

    def _save_profile(self):
        name, ok = QInputDialog.getText(self, "保存配置", "配置名称:")
        if ok and name:
            data = self._collect_profile_data()
            profile_mgr.save_profile(name, data)
            if self.profile_combo.findText(name) < 0:
                self.profile_combo.addItem(name)
            self.signals.log.emit(f"配置已保存: {name}")

    def _collect_profile_data(self):
        data = dict(profile_mgr.DEFAULT_PROFILE)
        data["mode"] = "normal" if self.rb_normal.isChecked() else "game"
        data["joystick"] = {
            "center_x": self.spin_js_x.value(),
            "center_y": self.spin_js_y.value(),
            "radius": self.spin_js_r.value(),
        }
        data["look"] = {
            "center_x": self.spin_look_x.value(),
            "center_y": self.spin_look_y.value(),
            "sensitivity": self.slider_sens.value() / 100.0,
        }
        data["auto_click"] = {
            "enabled": self.chk_autoclick.isChecked(),
            "button": self.combo_ac_btn.currentText(),
            "interval": self.spin_ac_interval.value(),
            "hold_time": self.spin_ac_hold.value(),
        }
        data["recoil"] = {
            "enabled": self.chk_recoil.isChecked(),
            "strength": self.spin_recoil_str.value(),
            "interval": self.spin_recoil_int.value(),
        }
        keymap = {}
        for btn in self.video_label.get_floating_buttons():
            keymap[btn.key_name] = {"pos": [btn.screen_x, btn.screen_y]}
        data["keymap"] = keymap
        if self.mapper:
            data["macros"] = self.mapper.macros
            data["macro_keys"] = self.mapper.macro_keys
        return data

    def _apply_profile_data(self, data):
        """把配置数据应用到界面和 mapper"""
        # 记住当前激活的配置：断线重连新建 mapper 后要重新应用按键映射
        self._active_profile_data = data
        mode = data.get("mode", "game")
        if mode == "normal":
            self.rb_normal.setChecked(True)
        else:
            self.rb_game.setChecked(True)
        js = data.get("joystick", {})
        self.spin_js_x.setValue(js.get("center_x", config.JOYSTICK_CENTER_X))
        self.spin_js_y.setValue(js.get("center_y", config.JOYSTICK_CENTER_Y))
        self.spin_js_r.setValue(js.get("radius", config.JOYSTICK_RADIUS))
        config.JOYSTICK_CENTER_X = js.get("center_x", config.JOYSTICK_CENTER_X)
        config.JOYSTICK_CENTER_Y = js.get("center_y", config.JOYSTICK_CENTER_Y)
        config.JOYSTICK_RADIUS = js.get("radius", config.JOYSTICK_RADIUS)
        lk = data.get("look", {})
        self.spin_look_x.setValue(lk.get("center_x", config.LOOK_CENTER_X))
        self.spin_look_y.setValue(lk.get("center_y", config.LOOK_CENTER_Y))
        self.slider_sens.setValue(int(lk.get("sensitivity", config.MOUSE_SENSITIVITY) * 100))
        config.LOOK_CENTER_X = lk.get("center_x", config.LOOK_CENTER_X)
        config.LOOK_CENTER_Y = lk.get("center_y", config.LOOK_CENTER_Y)
        config.MOUSE_SENSITIVITY = lk.get("sensitivity", config.MOUSE_SENSITIVITY)
        ac = data.get("auto_click", {})
        self.chk_autoclick.setChecked(ac.get("enabled", False))
        self.combo_ac_btn.setCurrentText(ac.get("button", "left"))
        self.spin_ac_interval.setValue(ac.get("interval", 0.05))
        self.spin_ac_hold.setValue(ac.get("hold_time", 0.02))
        rc = data.get("recoil", {})
        self.chk_recoil.setChecked(rc.get("enabled", False))
        self.spin_recoil_str.setValue(rc.get("strength", 3.0))
        self.spin_recoil_int.setValue(rc.get("interval", 0.03))
        if self.mapper:
            self._apply_keymap_to_mapper()

    def _apply_keymap_to_mapper(self):
        """把当前激活配置的按键映射应用到 mapper 并刷新悬浮按键"""
        if not self.mapper:
            return
        data = getattr(self, "_active_profile_data", None) or {}
        self.mapper.key_map.clear()
        for key, info in data.get("keymap", {}).items():
            pos = info.get("pos", [0, 0])
            self.mapper.key_map[key] = tuple(pos)
        QTimer.singleShot(100, self._refresh_floating_buttons_from_mapper)

    def _load_profile(self):
        name = self.profile_combo.currentText()
        if name == "默认":
            data = dict(profile_mgr.DEFAULT_PROFILE)
        else:
            data = profile_mgr.load_profile(name)
        self._apply_profile_data(data)
        self.signals.log.emit(f"配置已加载: {name}")

    # ============ 信号槽 ============
    def _on_connected(self):
        self.btn_connect.setEnabled(False)
        self.btn_disconnect.setEnabled(True)
        self.btn_capture.setEnabled(True)
        self.btn_screenshot.setEnabled(True)
        self.statusBar().showMessage("已连接")
        # 同步物理分辨率到 VideoLabel
        if self.scrcpy:
            self.video_label._screen_w = self.scrcpy.screen_width
            self.video_label._screen_h = self.scrcpy.screen_height
            self.video_label._physical_size = (self.scrcpy.screen_width, self.scrcpy.screen_height)
        # 如果当前是游戏模式，自动全屏；不自动激活映射，
        # 避免重连循环中反复 grabMouse 抢占鼠标导致无法退出
        if self.rb_game.isChecked():
            self.showFullScreen()

    def _on_disconnected(self):
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.btn_capture.setEnabled(False)
        self.btn_screenshot.setEnabled(False)
        self.btn_capture.setText("▶ 激活映射 (F9)")
        self.capture_status.setText("状态: 未激活")
        self.video_label.clear_floating_buttons()
        self.statusBar().showMessage("已断开")

    def _on_reconnecting(self, count):
        self.statusBar().showMessage(f"正在重连... (第 {count} 次)")

    def _on_error(self, msg):
        self.signals.log.emit(f"[错误] {msg}")
        self.statusBar().showMessage(f"错误: {msg}")

    def _append_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")

    def _on_coordinate_picked(self, x, y):
        pass

    def closeEvent(self, event):
        try:
            # 自动保存当前配置
            self._do_autosave()
            self._reconnecting = False
            if self.mapper:
                self.mapper.stop()
            if self.scrcpy:
                self.scrcpy.disconnect()
        except Exception:
            pass
        event.accept()


def main():
    config.ensure_dirs()

    # ========== DPI 高缩放适配 ==========
    # 必须在 QApplication() 之前设置。
    # PassThrough：保留 1.25/1.5 等小数 DPR；默认 RoundPreferFloor 会把
    # 125%/150% 舍入成 1x 再被 Windows 位图拉伸（模糊/文字裁切）。
    os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

