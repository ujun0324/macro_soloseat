import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

IS_WINDOWS = sys.platform == "win32"

# Windows: 화면 배율(125%, 150% 등)에서도 클릭/캡처 좌표가 일치하도록 DPI 인식 설정
# (pyautogui / pynput import 전에 실행해야 함)
if IS_WINDOWS:
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import pyautogui
from PIL import Image
from pynput import keyboard, mouse

if IS_WINDOWS:
    # Windows에서 Alt 단독 입력은 앱 메뉴바를 활성화하고 한/영 키와도 얽히므로 F8 사용
    TOGGLE_KEYS = {keyboard.Key.f8}
    TOGGLE_LABEL = "F8"
else:
    TOGGLE_KEYS = {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r}
    TOGGLE_LABEL = "Option"


pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03


class ScreenColorAutoClicker:
    def __init__(self, root):
        self.root = root
        self.root.title("Screen Color Auto Clicker")
        scale = self.root.winfo_fpixels("1i") / 96 if IS_WINDOWS else 1.0
        self.root.geometry(f"{int(560 * scale)}x{int(520 * scale)}")
        self.root.resizable(False, False)

        self.region = None
        self.target_color = None
        self.button2_pos = None
        self.running = False
        self.worker = None
        self.keyboard_listener = None
        self.capture_listener = None
        self.capture_cancel = None
        self.capturing = False
        self.option_pressed = False

        self.status_var = tk.StringVar(value="1. 감지 영역을 먼저 지정하세요.")
        self.region_var = tk.StringVar(value="미지정")
        self.color_var = tk.StringVar(value="미지정")
        self.button2_var = tk.StringVar(value="미지정")
        self.threshold_var = tk.IntVar(value=20)
        self.interval_var = tk.DoubleVar(value=0.0)
        self.cooldown_var = tk.DoubleVar(value=0.5)

        self.build_ui()
        self.start_keyboard_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        outer = tk.Frame(self.root, padx=18, pady=16)
        outer.pack(fill="both", expand=True)

        title = tk.Label(
            outer,
            text="화면 색상 감지 후 버튼 1 -> 버튼 2 자동 클릭",
            font=("Arial", 16, "bold"),
            anchor="w",
        )
        title.pack(fill="x", pady=(0, 10))

        help_text = (
            "설정 순서: 감지 영역 지정 -> 기준 색상 추출 -> 버튼 2 위치 지정 -> 시작\n"
            f"{TOGGLE_LABEL} 키: 시작/중지 토글, 안전 중지: ESC 키 또는 마우스를 화면 왼쪽 위 모서리로 이동"
        )
        tk.Label(outer, text=help_text, justify="left", anchor="w").pack(fill="x", pady=(0, 14))

        self.add_info_row(outer, "감지 영역", self.region_var, self.select_region)
        self.add_info_row(outer, "기준 색상", self.color_var, self.pick_color, preview=True)
        self.add_info_row(outer, "버튼 2 위치", self.button2_var, self.select_button2)

        settings = tk.LabelFrame(outer, text="설정", padx=12, pady=12)
        settings.pack(fill="x", pady=(10, 12))

        tk.Label(settings, text="색상 오차 허용값").grid(row=0, column=0, sticky="w")
        threshold = tk.Scale(
            settings,
            from_=0,
            to=120,
            orient="horizontal",
            variable=self.threshold_var,
            length=320,
        )
        threshold.grid(row=0, column=1, sticky="ew", padx=(12, 0))

        tk.Label(settings, text="감지 간격(초)").grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Spinbox(
            settings,
            from_=0.05,
            to=5.0,
            increment=0.05,
            textvariable=self.interval_var,
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(8, 0))

        tk.Label(settings, text="클릭 후 대기(초)").grid(row=2, column=0, sticky="w", pady=(8, 0))
        tk.Spinbox(
            settings,
            from_=0.05,
            to=10.0,
            increment=0.05,
            textvariable=self.cooldown_var,
            width=8,
        ).grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))

        buttons = tk.Frame(outer)
        buttons.pack(fill="x", pady=(4, 10))

        self.start_btn = tk.Button(buttons, text="시작", command=self.start, width=16, height=2)
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            buttons,
            text="중지",
            command=self.stop,
            width=16,
            height=2,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

        status_box = tk.LabelFrame(outer, text="상태", padx=12, pady=12)
        status_box.pack(fill="both", expand=True)
        tk.Label(status_box, textvariable=self.status_var, justify="left", anchor="nw").pack(
            fill="both", expand=True
        )

    def add_info_row(self, parent, label, value_var, command, preview=False):
        row = tk.Frame(parent)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, width=13, anchor="w").pack(side="left")
        tk.Label(row, textvariable=value_var, anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(row, text="지정", command=command, width=10).pack(side="right")
        if preview:
            self.color_preview = tk.Canvas(row, width=48, height=28, highlightthickness=1)
            self.color_preview.pack(side="right", padx=(0, 8))
            self.update_color_preview()

    def start_keyboard_listener(self):
        def on_press(key):
            if key == keyboard.Key.esc:
                if self.capturing and self.capture_cancel:
                    self.root.after(0, self.capture_cancel)
                else:
                    self.root.after(0, self.stop)
            elif key in TOGGLE_KEYS:
                if not self.option_pressed:
                    self.option_pressed = True
                    if not self.capturing:
                        self.root.after(0, self.toggle_running)

        def on_release(key):
            if key in TOGGLE_KEYS:
                self.option_pressed = False

        self.keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()

    def log(self, message):
        try:
            print(message, flush=True)
        except Exception:
            pass  # 콘솔 없는 exe(--windowed)에서는 출력 대상이 없을 수 있음

    def select_region(self):
        if self.running:
            return
        self.status_var.set("감지 영역의 왼쪽 위를 클릭한 뒤, 오른쪽 아래를 클릭하세요.")
        self.capture_clicks(
            "감지 영역 지정\n왼쪽 위 클릭 -> 오른쪽 아래 클릭",
            2,
            self.on_region_selected,
        )

    def pick_color(self):
        if self.running:
            return
        self.status_var.set("스포이드: 기준 색상으로 사용할 화면 위치를 클릭하세요.")
        self.capture_clicks("기준 색상 추출\n색상을 가져올 위치를 클릭하세요.", 1, self.on_color_picked)

    def select_button2(self):
        if self.running:
            return
        self.status_var.set("버튼 2로 사용할 화면 위치를 클릭하세요.")
        self.capture_clicks("버튼 2 위치 지정\n버튼 2 위치를 클릭하세요.", 1, self.on_button2_selected)

    def capture_clicks(self, instruction, count, callback):
        if self.capture_listener:
            self.capture_listener.stop()

        guide = tk.Toplevel(self.root)
        guide.title("위치 지정")
        guide.attributes("-topmost", True)
        guide.geometry("+40+40")
        guide.resizable(False, False)

        label = tk.Label(
            guide,
            text=instruction + "\n화면을 보면서 원하는 위치를 클릭하세요.\n취소: ESC",
            justify="left",
            padx=18,
            pady=14,
            font=("Arial", 15, "bold"),
        )
        label.pack()

        points = []

        def finish_capture():
            self.capturing = False
            self.capture_cancel = None
            if self.capture_listener:
                self.capture_listener.stop()
                self.capture_listener = None
            if guide.winfo_exists():
                guide.destroy()

        def cancel():
            finish_capture()
            self.status_var.set("지정이 취소되었습니다.")
            self.log("지정이 취소됨")

        def on_click(x, y, _button, pressed):
            if not pressed or not self.capturing:
                return

            points.append((int(x), int(y)))
            if len(points) < count:
                self.root.after(
                    0,
                    lambda: label.configure(
                        text="오른쪽 아래를 클릭하세요.\n"
                        "화면을 보면서 원하는 위치를 클릭하세요.\n"
                        "취소: ESC"
                    ),
                )
                return

            self.root.after(0, finish_capture)
            self.root.after(160, lambda: callback(points))
            return False

        self.capturing = True
        self.capture_cancel = cancel
        self.capture_listener = mouse.Listener(on_click=on_click)
        self.capture_listener.daemon = True
        self.capture_listener.start()

        guide.protocol("WM_DELETE_WINDOW", cancel)
        guide.bind("<Escape>", lambda _event: cancel())
        guide.focus_force()

    def on_region_selected(self, points):
        (x1, y1), (x2, y2) = points
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        width = right - left
        height = bottom - top

        if width < 2 or height < 2:
            messagebox.showwarning("영역 오류", "감지 영역이 너무 작습니다. 다시 지정하세요.")
            self.status_var.set("감지 영역이 너무 작습니다.")
            return

        self.region = (left, top, width, height)
        self.region_var.set(f"x={left}, y={top}, w={width}, h={height}")
        self.status_var.set("감지 영역이 지정되었습니다. 다음으로 기준 색상을 추출하세요.")
        self.log(f"범위가 지정됨: x={left}, y={top}, w={width}, h={height}")

    def on_color_picked(self, points):
        x, y = points[0]
        try:
            img = pyautogui.screenshot(region=(x, y, 1, 1)).convert("RGB")
            self.target_color = img.getpixel((0, 0))
        except Exception as exc:
            messagebox.showerror("색상 추출 실패", f"화면 색상을 가져오지 못했습니다.\n{exc}")
            self.status_var.set("색상 추출에 실패했습니다.")
            return

        r, g, b = self.target_color
        self.color_var.set(f"RGB({r}, {g}, {b}) at ({x}, {y})")
        self.update_color_preview()
        self.status_var.set("기준 색상이 지정되었습니다. 다음으로 버튼 2 위치를 지정하세요.")
        self.log(f"색상이 지정됨: RGB({r}, {g}, {b}) at ({x}, {y})")

    def on_button2_selected(self, points):
        self.button2_pos = points[0]
        x, y = self.button2_pos
        self.button2_var.set(f"x={x}, y={y}")
        self.status_var.set("버튼 2 위치가 지정되었습니다. 시작할 수 있습니다.")
        self.log(f"버튼 2가 지정됨: x={x}, y={y}")

    def update_color_preview(self):
        self.color_preview.delete("all")
        color = "#eeeeee"
        if self.target_color:
            color = "#{:02x}{:02x}{:02x}".format(*self.target_color)
        self.color_preview.create_rectangle(0, 0, 48, 28, fill=color, outline="#777777")

    def toggle_running(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.running:
            return
        if not self.region or not self.target_color or not self.button2_pos:
            messagebox.showwarning("설정 필요", "감지 영역, 기준 색상, 버튼 2 위치를 모두 지정하세요.")
            self.log("시작 실패: 감지 영역, 기준 색상, 버튼 2 위치를 모두 지정해야 합니다.")
            return

        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set(f"감지 중입니다. {TOGGLE_LABEL} 또는 ESC를 누르면 중지합니다.")
        self.log("자동 감지 시작됨")
        self.worker = threading.Thread(target=self.detect_loop, daemon=True)
        self.worker.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("중지되었습니다.")
        self.log("자동 감지 중지됨")

    def detect_loop(self):
        while self.running:
            try:
                found = self.find_target_color()
                if found:
                    x, y = found
                    self.root.after(
                        0,
                        lambda px=x, py=y: self.status_var.set(
                            f"기준 색상 감지: 버튼 1=({px}, {py}) 클릭 후 버튼 2 클릭"
                        ),
                    )
                    self.log(f"버튼 1 감지 및 클릭됨: x={x}, y={y}")
                    pyautogui.click(x, y)
                    time.sleep(0.08)
                    pyautogui.click(*self.button2_pos)
                    self.log(f"버튼 2 클릭됨: x={self.button2_pos[0]}, y={self.button2_pos[1]}")
                    time.sleep(max(0.05, float(self.cooldown_var.get())))
                else:
                    time.sleep(max(0.05, float(self.interval_var.get())))
            except pyautogui.FailSafeException:
                self.root.after(0, self.stop)
                self.root.after(0, lambda: self.status_var.set("마우스 모서리 안전 중지로 멈췄습니다."))
                break
            except Exception as exc:
                self.running = False
                self.root.after(0, self.stop)
                self.root.after(
                    0,
                    lambda error=exc: messagebox.showerror(
                        "감지 오류",
                        "화면 캡처 또는 클릭 중 오류가 발생했습니다.\n"
                        + (
                            "클릭할 프로그램이 관리자 권한으로 실행 중이면 이 프로그램도 관리자 권한으로 실행하세요.\n\n"
                            if IS_WINDOWS
                            else "macOS라면 터미널/파이썬에 화면 기록 및 손쉬운 사용 권한을 허용하세요.\n\n"
                        )
                        + f"{error}",
                    ),
                )
                break

    def find_target_color(self):
        left, top, width, height = self.region
        img = pyautogui.screenshot(region=self.region)
        if not isinstance(img, Image.Image):
            img = Image.frombytes("RGB", img.size, img.tobytes())
        img = img.convert("RGB")

        tr, tg, tb = self.target_color
        threshold = int(self.threshold_var.get())
        threshold_sq = threshold * threshold

        pixels = img.load()

        for y in range(height):
            for x in range(width):
                r, g, b = pixels[x, y]
                distance_sq = (r - tr) ** 2 + (g - tg) ** 2 + (b - tb) ** 2
                if distance_sq <= threshold_sq:
                    return left + x, top + y

        return None

    def on_close(self):
        self.running = False
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.capture_listener:
            self.capture_listener.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ScreenColorAutoClicker(root)
    root.bind("<Escape>", lambda _event: app.stop())
    root.mainloop()


if __name__ == "__main__":
    main()
