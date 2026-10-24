"""
输入映射引擎 - AWSD+++
"""
import math, threading, time, ctypes
from ctypes import wintypes
from typing import Dict, Tuple, Optional, Callable
from pynput import keyboard, mouse
import config
from scrcpy_client import ScrcpyClient, ACTION_DOWN, ACTION_UP, ACTION_MOVE


class InputMapper:
    __doc__ = "将键鼠输入映射为触屏事件"

    def __init__(self, scrcpy, video_widget=None):
        self.scrcpy = scrcpy
        self._video_widget = video_widget
        self.enabled = False
        self._active = False
        self._cursor_visible = True
        self._joystick_active = False
        self._joystick_dx = 0.0
        self._joystick_dy = 0.0
        self._joystick_thread = None
        self._joystick_stop = False
        self._pressed_keys = set()
        self._look_active = False
        self._look_x = float(config.LOOK_CENTER_X)
        self._look_y = float(config.LOOK_CENTER_Y)
        self._key_states = {}
        self._key_down_times = {}
        self._auto_click_thread = None
        self._auto_click_stop = False
        self._fire_pressed = False
        self._recoil_thread = None
        self._recoil_stop = False
        self._macro_thread = None
        self._macro_stop = False
        self.macros = {}
        self.macro_keys = {}
        self._kb_listener = None
        self._mouse_listener = None
        self.on_status = None
        self.on_exit_game_mode = None
        self.on_capture_state = None
        self.key_map = {}
        self._init_default_keymap()



    def _init_default_keymap(self):
        """初始化默认键位映射 — 键盘按键/鼠标按钮 -> 触屏坐标"""
        import config as cfg
        self.key_map = {
            # 鼠标按钮
            'left':   tuple(cfg.FIRE_POS),
            'right':  tuple(cfg.SCOPE_POS),
            'middle': tuple(cfg.INTERACT_POS),
            # 键盘按键 (WASD 不走 keymap, 走摇杆)
            'space':  tuple(cfg.JUMP_POS),
            'c':      tuple(cfg.CROUCH_POS),
            'z':      tuple(cfg.PRONE_POS),
            'r':      tuple(cfg.RELOAD_POS),
            'f':      tuple(cfg.INTERACT_POS),
            'g':      tuple(cfg.SWITCH_WEAPON_POS),
            'm':      tuple(cfg.MAP_POS),
            'tab':    tuple(cfg.BACKPACK_POS),
            't':      tuple(cfg.VOICE_POS),
        }






    def start(self):
        """启动输入监听 — 现在完全由 main.py 的 Raw Input 驱动, 
        pynput 在部分机器上被安全软件拦截所以不再依赖"""
        self.enabled = True
        self._status("输入监听已启动（按 F9 切换映射捕获）")
        print(f"[DBG-START] InputMapper.start() called, enabled=True, keymap_keys={len(self.key_map)}")



    def stop(self):
        self.enabled = False
        self.deactivate_capture()
        print("[DBG-STOP] InputMapper.stop() called")



    def activate_capture(self):
        # 1. 先设置 look 中心, 再激活 (防止竞态)
        self._joystick_dx = 0.0
        self._joystick_dy = 0.0
        self._joystick_active = False   # 复位：否则下次按 WASD 跳过 DOWN 事件
        self._joystick_stop = False     # 复位：否则快速重连后轮盘线程直接退出
        self._pressed_keys.clear()
        self._look_x = float(config.LOOK_CENTER_X)
        self._look_y = float(config.LOOK_CENTER_Y)

        # 2. 打印运行时屏幕/帧尺寸
        sw = self.scrcpy.screen_width
        sh = self.scrcpy.screen_height
        fw = self.scrcpy.frame_width
        fh = self.scrcpy.frame_height
        aspect_auto = ((fw/sw)/(fh/sh) if sh>0 and sw>0 and fh>0 else 2.22)
        print("=" * 50)
        print(f"[DIMS] screen={sw}x{sh}  frame={fw}x{fh}")
        print(f"[DIMS] aspect_fix config={config.ASPECT_FIX_XY}, auto={aspect_auto:.2f}")
        print("=" * 50)

        # 3. 激活后才开始接受鼠标位移
        self._active = True
        self._start_joystick_loop()

        # 4. QtScrcpy 方式: 在 video_widget 上隐藏光标 + grabMouse
        try:
            if self._video_widget is not None:
                from PyQt5.QtCore import Qt
                self._video_widget._game_capturing = True
                self._video_widget.setCursor(Qt.BlankCursor)
                self._video_widget.grabMouse()
                # 复位到 widget 中心
                c = self._video_widget.rect().center()
                from PyQt5.QtGui import QCursor
                gp = self._video_widget.mapToGlobal(c)
                cx, cy = int(gp.x()), int(gp.y())
                QCursor.setPos(gp)
                self._center_x = cx
                self._center_y = cy
                print(f"[CURSOR] grabMouse + BlankCursor, center=({cx},{cy})")
            else:
                import ctypes
                while ctypes.windll.user32.ShowCursor(False) >= 0:
                    pass
                self._cursor_visible = False
        except Exception as e:
            print(f"[CURSOR] failed: {e}")

        self._status("映射已激活 - 键鼠输入已接管")
        if self.on_capture_state:
            try:
                self.on_capture_state(True)
            except Exception:
                pass

    def deactivate_capture(self):
        self._active = False
        self._auto_click_stop = True
        self._recoil_stop = True
        self._macro_stop = True
        self._joystick_stop = True
        self._recenter_stop = True

        # Release cursor clip FIRST
        try:
            ctypes.windll.user32.ClipCursor(None)
        except Exception:
            pass

        # Release look pointer
        if self._look_active:
            self.scrcpy.inject_touch(ACTION_UP, self._look_x, self._look_y,
                                     pointer_id=config.LOOK_POINTER_ID)
            self._look_active = False

        # Release mouse buttons
        if hasattr(self, "_mouse_pressed"):
            for btn_name in list(self._mouse_pressed.keys()):
                if btn_name in self.key_map:
                    bx, by = self.key_map[btn_name]
                    ptr_id = abs(hash(btn_name)) % 98 + 3
                    self.scrcpy.inject_touch(ACTION_UP, bx, by, pointer_id=ptr_id)
            self._mouse_pressed.clear()

        # Release keyboard keys
        for key_name, was_down in list(self._key_states.items()):
            if was_down and key_name in self.key_map:
                x, y = self.key_map[key_name]
                ptr_id = abs(hash(key_name)) % 98 + 3
                self.scrcpy.inject_touch(ACTION_UP, x, y, pointer_id=ptr_id)
        self._key_states.clear()
        self._key_down_times.clear()

        # Restore cursor (QtScrcpy: releaseMouse + ArrowCursor)
        try:
            if self._video_widget is not None:
                from PyQt5.QtCore import Qt
                self._video_widget._game_capturing = False
                self._video_widget.releaseMouse()
                self._video_widget.setCursor(Qt.ArrowCursor)
            while ctypes.windll.user32.ShowCursor(True) < 0:
                pass
            self._cursor_visible = True
        except Exception:
            pass

        self._status("映射已取消 - 键鼠输入已释放")
        if self.on_capture_state:
            try:
                self.on_capture_state(False)
            except Exception:
                pass


    def toggle_capture(self):
        if self._active:
            self.deactivate_capture()
        else:
            self.activate_capture()
        return self._active



    def _on_key_press(self, key):
        if not self.enabled:
            return
        try:
            key_name = self._key_name(key)
        except Exception:
            return
        # F9 切换映射
        if key_name == "f9":
            self.toggle_capture()
            return
        # F2 切换光标显示
        if key_name == "f2":
            try:
                if self._cursor_visible:
                    while ctypes.windll.user32.ShowCursor(False) >= 0:
                        pass
                    self._cursor_visible = False
                    self._status("鼠标光标已隐藏")
                else:
                    while ctypes.windll.user32.ShowCursor(True) < 0:
                        pass
                    self._cursor_visible = True
                    self._status("鼠标光标已显示")
            except Exception:
                pass
            return
        # 以下处理需要 active 状态
        if not self._active:
            return
        if config.OPERATION_MODE != "game":
            return
        # 去抖
        if self._key_states.get(key_name, False):
            return
        self._key_states[key_name] = True
        # WASD -> 摇杆
        if key_name in ('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'):
            self._update_joystick(key_name, True)
            return
        # 宏
        if key_name in self.macro_keys:
            self.play_macro(self.macro_keys[key_name])
            return
        # 普通按键 -> keymap 触控
        if key_name in self.key_map:
            x, y = self.key_map[key_name]
            ptr_id = abs(hash(key_name)) % 98 + 3
            self.scrcpy.inject_touch(ACTION_DOWN, x, y, pointer_id=ptr_id)
            threading.Thread(target=self._release_tap, args=(x, y, ptr_id),
                             daemon=True).start()



    def _on_key_release(self, key):
        if not self.enabled:
            return
        try:
            key_name = self._key_name(key)
        except Exception:
            return
        else:
            self._key_states[key_name] = False
            if not self._active:
                return
            if key_name in ('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'):
                self._update_joystick(key_name, False)



    def _release_tap(self, x, y, pointer_id):
        time.sleep(0.02)
        self.scrcpy.inject_touch(ACTION_UP, x, y, pointer_id=pointer_id)



    def _key_name(self, key):
        if hasattr(key, "char"):
            if key.char:
                return key.char.lower()
        if hasattr(key, "name"):
            name = key.name.lower()
            special = {
                'space': 'space', 'enter': 'enter', 'esc': 'esc',
                'tab': 'tab', 'shift': 'shift', 'ctrl': 'ctrl',
                'alt': 'alt', 'cmd': 'cmd', 'up': 'up', 'down': 'down',
                'left': 'left', 'right': 'right'}
            return special.get(name, name)
        return str(key).lower()



    def _update_joystick(self, key, pressed):
        if pressed:
            self._pressed_keys.add(key)
        else:
            self._pressed_keys.discard(key)
        dx, dy = (0.0, 0.0)
        if "w" in self._pressed_keys or "up" in self._pressed_keys:
            dy -= 1.0
        if "s" in self._pressed_keys or "down" in self._pressed_keys:
            dy += 1.0
        if "a" in self._pressed_keys or "left" in self._pressed_keys:
            dx -= 1.0
        if "d" in self._pressed_keys or "right" in self._pressed_keys:
            dx += 1.0
        length = math.hypot(dx, dy)
        if length > 1.0:
            dx /= length
            dy /= length
        self._joystick_dx = dx
        self._joystick_dy = dy



    def _start_joystick_loop(self):
        if self._joystick_thread:
            if self._joystick_thread.is_alive():
                return
        self._joystick_stop = False
        self._joystick_thread = threading.Thread(target=(self._joystick_loop), daemon=True)
        self._joystick_thread.start()



    def _joystick_loop(self):
        was_active = False
        interval = 0.016
        while not self._joystick_stop:
            if self._active:
                dx = self._joystick_dx
                dy = self._joystick_dy
                moving = dx != 0.0 or dy != 0.0
                if moving:
                    x = config.JOYSTICK_CENTER_X + dx * config.JOYSTICK_RADIUS
                    y = config.JOYSTICK_CENTER_Y + dy * config.JOYSTICK_RADIUS_Y
                    if not self._joystick_active:
                        self.scrcpy.inject_touch(ACTION_DOWN, (config.JOYSTICK_CENTER_X),
                          (config.JOYSTICK_CENTER_Y),
                          pointer_id=(config.JOYSTICK_POINTER_ID))
                        time.sleep(0.02)
                        self.scrcpy.inject_touch(ACTION_MOVE, x, y, pointer_id=(config.JOYSTICK_POINTER_ID))
                        self._joystick_active = True
                        was_active = True
                    else:
                        self.scrcpy.inject_touch(ACTION_MOVE, x, y, pointer_id=(config.JOYSTICK_POINTER_ID))
                elif was_active:
                    self.scrcpy.inject_touch(ACTION_MOVE, (config.JOYSTICK_CENTER_X),
                      (config.JOYSTICK_CENTER_Y),
                      pointer_id=(config.JOYSTICK_POINTER_ID))
                    time.sleep(0.02)
                    self.scrcpy.inject_touch(ACTION_UP, (config.JOYSTICK_CENTER_X),
                      (config.JOYSTICK_CENTER_Y),
                      pointer_id=(config.JOYSTICK_POINTER_ID))
                    self._joystick_active = False
                    was_active = False
                time.sleep(interval)

        if was_active:
            self.scrcpy.inject_touch(ACTION_UP, (config.JOYSTICK_CENTER_X),
              (config.JOYSTICK_CENTER_Y),
              pointer_id=(config.JOYSTICK_POINTER_ID))
            self._joystick_active = False



    _first_move = True
    def on_raw_mouse_delta(self, dx, dy):
        """接收 Raw Input 原始鼠标位移"""
        if not self.enabled or not self._active:
            return
        if config.OPERATION_MODE != "game":
            return
        if abs(dx) < config.MOUSE_DEADZONE and abs(dy) < config.MOUSE_DEADZONE:
            return

        fw = self.scrcpy.frame_width if self.scrcpy.frame_width > 0 else config.SCREEN_WIDTH
        fh = self.scrcpy.frame_height if self.scrcpy.frame_height > 0 else config.SCREEN_HEIGHT
        sw = self.scrcpy.screen_width if self.scrcpy.screen_width > 0 else config.SCREEN_WIDTH
        sh = self.scrcpy.screen_height if self.scrcpy.screen_height > 0 else config.SCREEN_HEIGHT

        aspect_fix = getattr(config, "ASPECT_FIX_XY", 0.0)
        if aspect_fix <= 0:
            aspect_fix = (fw / sw) / (fh / sh) if fh > 0 and sw > 0 else 2.22

        screen_dx = dx * config.MOUSE_SENSITIVITY
        y_boost = getattr(config, "LOOK_Y_BOOST", 1.0)
        screen_dy = dy * config.MOUSE_SENSITIVITY * aspect_fix * y_boost
        max_x = config.LOOK_MAX_STEP
        max_y = config.LOOK_MAX_STEP * aspect_fix
        screen_dx = max(-max_x, min(max_x, screen_dx))
        screen_dy = max(-max_y, min(max_y, screen_dy))

        self._look_x += screen_dx
        self._look_y += screen_dy
        BUF = 30
        self._look_x = max(BUF, min(sw - BUF, self._look_x))
        self._look_y = max(BUF, min(sh - BUF, self._look_y))

        if not self._look_active:
            self.scrcpy.inject_touch(ACTION_DOWN, self._look_x, self._look_y, pointer_id=config.LOOK_POINTER_ID)
            print(f"[DBG-LOOK] DOWN px=({self._look_x:.0f},{self._look_y:.0f}) dx={dx} dy={dy}")
            self._look_active = True
        else:
            self.scrcpy.inject_touch(ACTION_MOVE, self._look_x, self._look_y, pointer_id=config.LOOK_POINTER_ID)


    def _recenter_loop(self):
        # No longer needed with Qt mouseMoveEvent (QtScrcpy way)
        pass


    def _on_mouse_move(self, x, y):
        return



    def _on_mouse_click(self, x, y, button, pressed):
        if not self.enabled or not self._active:
            return
        if config.OPERATION_MODE != "game":
            return
        btn_name = ("left" if button == mouse.Button.left
                    else "right" if button == mouse.Button.right
                    else "middle")
        if btn_name not in self.key_map:
            return
        bx, by = self.key_map[btn_name]
        ptr_id = abs(hash(btn_name)) % 98 + 3
        if pressed:
            self.scrcpy.inject_touch(ACTION_DOWN, bx, by, pointer_id=ptr_id)
            if not hasattr(self, '_mouse_pressed'):
                self._mouse_pressed = {}
            self._mouse_pressed[btn_name] = True
        else:
            self.scrcpy.inject_touch(ACTION_UP, bx, by, pointer_id=ptr_id)
            if hasattr(self, '_mouse_pressed'):
                self._mouse_pressed.pop(btn_name, None)



    def _start_auto_click(self):
        if self._auto_click_thread:
            if self._auto_click_thread.is_alive():
                return
        self._auto_click_stop = False
        self._auto_click_thread = threading.Thread(target=(self._auto_click_loop), daemon=True)
        self._auto_click_thread.start()



    def _stop_auto_click(self):
        self._auto_click_stop = True



    def _auto_click_loop(self):
        pos = config.FIRE_POS if config.AUTO_CLICK_BUTTON == "left" else config.SCOPE_POS
        ptr_id = 3 if config.AUTO_CLICK_BUTTON == "left" else 4
        while not self._auto_click_stop:
            if self._active:
                self.scrcpy.inject_touch(ACTION_DOWN, (pos[0]), (pos[1]), pointer_id=ptr_id)
                time.sleep(config.AUTO_CLICK_HOLD_TIME)
                self.scrcpy.inject_touch(ACTION_UP, (pos[0]), (pos[1]), pointer_id=ptr_id)
                time.sleep(config.AUTO_CLICK_INTERVAL)



    def _start_recoil(self):
        if self._recoil_thread:
            if self._recoil_thread.is_alive():
                return
        self._recoil_stop = False
        self._recoil_thread = threading.Thread(target=(self._recoil_loop), daemon=True)
        self._recoil_thread.start()



    def _stop_recoil(self):
        self._recoil_stop = True



    def _recoil_loop(self):
        while not self._recoil_stop:
            if self._active:
                if self._fire_pressed:
                    self._look_y += config.RECOIL_STRENGTH
                    self._look_y = min(self.scrcpy.screen_height, self._look_y)
                    if self._look_active:
                        self.scrcpy.inject_touch(ACTION_MOVE, (self._look_x), (self._look_y), pointer_id=(config.LOOK_POINTER_ID))
                    time.sleep(config.RECOIL_INTERVAL)



    def add_macro(self, name, trigger_key, steps):
        self.macros[name] = steps
        self.macro_keys[trigger_key.lower()] = name



    def play_macro(self, name):
        if name not in self.macros:
            return
        if self._macro_thread:
            if self._macro_thread.is_alive():
                return
        self._macro_stop = False
        self._macro_thread = threading.Thread(target=(self._macro_loop), args=(name,), daemon=True)
        self._macro_thread.start()



    def _macro_loop(self, name):
        steps = self.macros.get(name, [])
        for key_name, hold_time in steps:
            if not self._macro_stop:
                if not self._active:
                    break
                if key_name in self.key_map:
                    x, y = self.key_map[key_name]
                    ptr_id = abs(hash(key_name)) % 98 + 3
                    self.scrcpy.inject_touch(ACTION_DOWN, x, y, pointer_id=ptr_id)
                    time.sleep(hold_time)
                    self.scrcpy.inject_touch(ACTION_UP, x, y, pointer_id=ptr_id)
                time.sleep(0.02)



    def _on_mouse_scroll(self, x, y, dx, dy):
        return self.enabled and self._active or None
        if dy > 0:
            pass
        else:
            return



    def _status(self, msg):
        print(f"[MAPPER] {msg}")
        if self.on_status:
            self.on_status(msg)



    @property
    def is_capturing(self):
        return self._active


    # ========== Raw Input 驱动的新事件入口 (替代 pynput) ==========

    def on_raw_key(self, vkey: int, scan_code: int, is_down: bool, is_repeat: bool = False):
        """
        来自 main.py nativeEvent 的原始键盘事件.
        vkey: Windows 虚拟键码 (VK_*)
        scan_code: HID 扫描码
        is_down: True=按下, False=释放
        """
        if not self.enabled:
            return
        key_name = self._vk_to_name(vkey, scan_code)
        if key_name is None:
            return

        # Auto-repeat DOWN: skip entirely BEFORE logging
        if is_down and is_repeat:
            return

        print(f"[DBG-RAWKEY] {'DOWN' if is_down else 'UP'} key={key_name} vk={vkey} scan={scan_code} active={self._active} op={config.OPERATION_MODE} in_km={key_name in self.key_map}")
        
        # F9 = 取消映射
        if key_name == "f9":
            if is_down and self._active:
                self.deactivate_capture()
            return

        # Esc = 取消映射并彻底退出游戏模式（返回普通模式）
        if key_name == "esc":
            if is_down:
                if self._active:
                    self.deactivate_capture()
                if self.on_exit_game_mode:
                    try:
                        self.on_exit_game_mode()
                    except Exception:
                        pass
            return

        # Everything below requires active
        if not self._active:
            return
        if config.OPERATION_MODE != "game":
            return
        
        now = time.time()
        debounce_window = 0.05  # 50ms \u2014 忽略此窗口内的假 UP

        if is_down:
            # Debounce: already down -> auto-repeat, ignore
            if self._key_states.get(key_name, False):
                return
            self._key_states[key_name] = True
            self._key_down_times[key_name] = now

            # WASD -> joystick
            if key_name in ('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'):
                self._update_joystick(key_name, True)
                return

            # Macros
            if key_name in self.macro_keys:
                self.play_macro(self.macro_keys[key_name])
                return

            # Normal key -> keymap touch
            if key_name in self.key_map:
                x, y = self.key_map[key_name]
                ptr_id = abs(hash(key_name)) % 98 + 3
                print(f"[DBG-RAWKEY -> TOUCH] {key_name} -> DOWN ({x},{y})")
                self.scrcpy.inject_touch(ACTION_DOWN, x, y, pointer_id=ptr_id)
        else:
            # Time-based debounce: 如果 UP 离 DOWN 不到 50ms, 视为假 UP (auto-repeat)
            last_down = self._key_down_times.get(key_name, 0)
            if now - last_down < debounce_window:
                # 假 UP: 不发 touch UP, 不重置状态
                return
            self._key_states[key_name] = False
            if key_name in ('w', 'a', 's', 'd', 'up', 'down', 'left', 'right'):
                self._update_joystick(key_name, False)
            elif key_name in self.key_map:
                x, y = self.key_map[key_name]
                ptr_id = abs(hash(key_name)) % 98 + 3
                self.scrcpy.inject_touch(ACTION_UP, x, y, pointer_id=ptr_id)

    def on_raw_button(self, btn_name: str, is_down: bool):
        """
        来自 main.py nativeEvent 的 Raw Input 鼠标按钮事件.
        btn_name: 'left' | 'right' | 'middle'
        is_down: True=按下, False=释放
        """
        if not self.enabled or not self._active:
            return
        if config.OPERATION_MODE != "game":
            return
        if btn_name not in self.key_map:
            return
        # Init _mouse_pressed if missing
        if not hasattr(self, '_mouse_pressed'):
            self._mouse_pressed = {}

        # Debounce: already pressed + DOWN → skip; not pressed + UP → skip
        already_down = self._mouse_pressed.get(btn_name, False)
        if is_down and already_down:
            return
        if not is_down and not already_down:
            return

        bx, by = self.key_map[btn_name]
        ptr_id = abs(hash(btn_name)) % 98 + 3
        print(f"[DBG-RAWBUTTON] {'DOWN' if is_down else 'UP'} btn={btn_name} -> ({bx},{by})")
        if is_down:
            self.scrcpy.inject_touch(ACTION_DOWN, bx, by, pointer_id=ptr_id)
            self._mouse_pressed[btn_name] = True
        else:
            self.scrcpy.inject_touch(ACTION_UP, bx, by, pointer_id=ptr_id)
            self._mouse_pressed.pop(btn_name, None)

    def _vk_to_name(self, vkey: int, scan_code: int) -> str:
        """把 Windows VK 码转换为内部 key_name 字符串"""
        # 常用 VK 码映射
        VK_MAP = {
            0x41: 'a', 0x42: 'b', 0x43: 'c', 0x44: 'd',
            0x45: 'e', 0x46: 'f', 0x47: 'g', 0x48: 'h',
            0x49: 'i', 0x4A: 'j', 0x4B: 'k', 0x4C: 'l',
            0x4D: 'm', 0x4E: 'n', 0x4F: 'o', 0x50: 'p',
            0x51: 'q', 0x52: 'r', 0x53: 's', 0x54: 't',
            0x55: 'u', 0x56: 'v', 0x57: 'w', 0x58: 'x',
            0x59: 'y', 0x5A: 'z',
            0x30: '0', 0x31: '1', 0x32: '2', 0x33: '3', 0x34: '4',
            0x35: '5', 0x36: '6', 0x37: '7', 0x38: '8', 0x39: '9',
            0x09: 'tab', 0x0D: 'enter', 0x1B: 'esc', 0x20: 'space',
            0x14: 'caps_lock', 0x90: 'num_lock', 0x91: 'scroll_lock',
            0x10: 'shift', 0x11: 'ctrl', 0x12: 'alt',
            0x70: 'f1', 0x71: 'f2', 0x72: 'f3', 0x73: 'f4',
            0x74: 'f5', 0x75: 'f6', 0x76: 'f7', 0x77: 'f8',
            0x78: 'f9', 0x79: 'f10', 0x7A: 'f11', 0x7B: 'f12',
            0x26: 'up', 0x28: 'down', 0x25: 'left', 0x27: 'right',
            0x2D: 'insert', 0x2E: 'delete', 0x24: 'home', 0x23: 'end',
            0x21: 'page_up', 0x22: 'page_down',
            0x5B: 'cmd', 0x5C: 'cmd',
        }
        name = VK_MAP.get(vkey)
        if name is not None:
            return name
        return None



