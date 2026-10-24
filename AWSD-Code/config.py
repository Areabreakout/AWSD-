# -*- coding: utf-8 -*-
"""
WASD+ 克隆 - 全局配置
所有可调参数集中在此，方便修改
"""
import os
import sys

# ============ 路径配置 ============
# 检测是否打包为 EXE（PyInstaller frozen 模式）
if getattr(sys, "frozen", False):
    # 运行时 EXE 所在目录（用于可写文件：配置、截图等）
    WORK_DIR = os.path.dirname(sys.executable)
    # 内嵌资源目录（adb.exe、scrcpy-server 等只读资源）
    _BUNDLE_DIR = os.path.join(sys._MEIPASS, "adb")
else:
    # 源码运行：使用项目自带的 adb 目录
    WORK_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adb")

# scrcpy/adb 资源路径
SCRCPY_DIR = _BUNDLE_DIR
ADB_PATH = os.path.join(_BUNDLE_DIR, "adb.exe")
SCRCPY_SERVER_PATH = os.path.join(_BUNDLE_DIR, "scrcpy-server")

# 设备上 scrcpy-server 的存放路径
REMOTE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"

# 配置目录：EXE 旁边可写，profiles 子目录
PROFILES_DIR = os.path.join(WORK_DIR, "profiles")

# ============ 网络配置 ============
# ADB forward 本地端口（scrcpy 视频+控制通道）
SCRCPY_PORT = 27183
# scrcpy 服务端版本号（必须与 scrcpy-server 版本一致）
SCRCPY_VERSION = "4.1"

# ============ 视频配置 ============
# 最大分辨率（长边，0 表示原始分辨率）
MAX_SIZE = 1280
# 码率 (bps)
BIT_RATE = 8_000_000
# 最大帧率
MAX_FPS = 60
# 视频编码 (h264 / h265)
VIDEO_CODEC = "h264"

# ============ 屏幕尺寸（用于坐标映射，可被实际设备尺寸覆盖）============
SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 2400

# ============ 虚拟摇杆配置（WASD 移动）============
# 摇杆中心坐标（屏幕坐标，左上为原点）
JOYSTICK_CENTER_X = 300
JOYSTICK_CENTER_Y = 1800
# 摇杆半径（像素）
JOYSTICK_RADIUS = 150
JOYSTICK_RADIUS_Y = 350
# 摇杆指针 ID（多点触控区分，需在 int64 范围内）
JOYSTICK_POINTER_ID = 1

# ============ 鼠标视角配置 ============
# 视角控制区域中心（右侧屏幕）
LOOK_CENTER_X = 540
LOOK_CENTER_Y = 1200
# 鼠标灵敏度（像素转屏幕坐标的比例）
MOUSE_SENSITIVITY = 0.15
# 视角指针 ID
LOOK_POINTER_ID = 2
# 视角控制最大单次偏移
LOOK_MAX_STEP = 60  # 横屏 frame 像素少, 上限减小
# ====== 关键: aspect_fix 完整补偿 ======
# 竖屏游戏里 Y 方向被 frame 缩放 (576/2400=0.24x) 缩了 4 倍
# 公式 = (frame_w/screen_w) / (frame_h/screen_h)
# 默认值 4.94, 如果上下还是慢 → 加大, 如果上下太快 → 减小
ASPECT_FIX_XY = 4.94
LOOK_Y_BOOST = 1.8

# ============ 按键配置（默认键位）============
# 开火键
FIRE_KEY = "left"          # 鼠标左键
# 开镜/右键
SCOPE_KEY = "right"        # 鼠标右键
# 跳跃
JUMP_KEY = "space"
# 下蹲
CROUCH_KEY = "c"
# 趴下
PRONE_KEY = "z"
# 换弹
RELOAD_KEY = "r"
# 互动/拾取
INTERACT_KEY = "f"
# 切换武器
SWITCH_WEAPON_KEY = "g"
# 地图
MAP_KEY = "m"
# 背包
BACKPACK_KEY = "tab"
# 语音
VOICE_KEY = "t"

# ============ 默认触屏点坐标（屏幕坐标）============
FIRE_POS = (960, 1600)
SCOPE_POS = (960, 1000)
JUMP_POS = (960, 2000)
CROUCH_POS = (150, 2000)
PRONE_POS = (300, 2100)
RELOAD_POS = (960, 1800)
INTERACT_POS = (540, 1200)
SWITCH_WEAPON_POS = (960, 1400)
MAP_POS = (100, 100)
BACKPACK_POS = (1000, 100)
VOICE_POS = (100, 2000)

# ============ 输入处理 ============
# 鼠标移动死区（小于此值的移动忽略）
MOUSE_DEADZONE = 2
# 视角控制最大单次偏移
# 按键去抖时间（秒）
KEY_DEBOUNCE = 0.01

# ============ 自动重连 ============
# 断连后自动重连
AUTO_RECONNECT = True
# 重连延迟（秒）
RECONNECT_DELAY = 1.5
# 最大重连次数（0 = 无限）
RECONNECT_MAX_RETRIES = 0

# ============ 连点器 ============
AUTO_CLICK_ENABLED = False
AUTO_CLICK_BUTTON = "left"   # left / right
AUTO_CLICK_INTERVAL = 0.05   # 连点间隔（秒），越小越快
AUTO_CLICK_HOLD_TIME = 0.02  # 按下持续时间

# ============ 压枪辅助 ============
RECOIL_CONTROL_ENABLED = False
RECOIL_STRENGTH = 3.0        # 每次下压的像素量
RECOIL_INTERVAL = 0.03       # 下压间隔（秒）

# ============ 宏 ============
# 宏列表：{名称: [ (按键, 按下时长秒), ... ]}
MACROS = {}

# ============ 显示选项 ============
SHOW_FPS = True
SHOW_OVERLAY = True          # 显示摇杆圈/视角中心/触控点
SHOW_TOUCH_FEEDBACK = True   # 显示触控点反馈

# ============ 截图 ============
SCREENSHOT_DIR = os.path.join(WORK_DIR, "screenshots")

# ============ 日志 ============
LOG_LEVEL = "info"  # debug / info / warn / error

# ============ 窗口 ============
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 800
WINDOW_TITLE = "AWSD+++"

# ============ 操作模式 ============
# "normal" = 普通模式：鼠标点击直接触控对应位置
# "game"   = 游戏模式：键盘控制摇杆/按键，鼠标滑动控制视角
OPERATION_MODE = "normal"  # default normal mode


def ensure_dirs():
    """确保必要目录存在"""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

