# -*- coding: utf-8 -*-
"""
无损视频剪辑工具 (Lossless Video Editor)
========================================
基于 ffmpeg stream copy 的轻量裁剪 / 合并工具,像素级无损、速度极快。
界面采用 OBS Studio 风格深色主题。

特性:
  * 打开视频 -> 自动抽帧预览,可拖动时间轴选择起止区间
  * 裁剪导出:ffmpeg -c copy,画质零损失
  * 合并导出:concat demuxer + copy,多个片段拼成一段
  * 未安装 ffmpeg 时自动下载(仅首次,约 90MB,下载到 exe 所在目录)
  * 零第三方依赖,仅用 Python 标准库

用法:双击 LosslessVideoEditor.exe(或 start.bat)启动。
"""

import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from tkinter import (
    Button, Canvas, Checkbutton, Entry, Frame, Label, Listbox, Menu,
    Scrollbar, BooleanVar, StringVar, Text, Tk, Toplevel, filedialog, messagebox,
)
from tkinter import font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------------
# 路径定位:兼容「源码运行」与「PyInstaller 单文件打包」
# 打包后 __file__ 指向临时解压目录,必须用 exe 所在目录保存 ffmpeg
# --------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_DIR = os.path.join(APP_DIR, "ffmpeg")

# 子进程无控制台窗口(打包为 windowed 后避免 ffmpeg 闪黑窗)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 内置 ffmpeg 压缩包:PyInstaller 打包时通过 --add-data 放进
# sys._MEIPASS/ffmpeg_bundle/ffmpeg-bundle.zip;源码运行时放在 exe 目录旁
def bundle_zip():
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = os.path.join(meipass, "ffmpeg_bundle", "ffmpeg-bundle.zip")
        if os.path.isfile(p):
            return p
    p = os.path.join(APP_DIR, "ffmpeg-bundle.zip")
    return p if os.path.isfile(p) else None


FFMPEG_ZIP_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_MANUAL_URL = "https://www.gyan.dev/ffmpeg/builds/"

# --------------------------------------------------------------------------
# OBS Studio 深色主题色板
# --------------------------------------------------------------------------
COL_BG = "#1e1e1e"          # 窗口主背景
COL_PANEL = "#2b2b2b"       # 面板 / 工具栏 / 状态栏
COL_WIDGET = "#3d3d3d"      # 控件按钮
COL_WIDGET_HOVER = "#4a4a4a"
COL_WIDGET_DISABLED = "#282828"
COL_ENTRY = "#1a1a1a"       # 输入框 / 列表 / 日志
COL_BORDER = "#404040"      # 分隔线 / 边框
COL_TEXT = "#e0e0e0"        # 主文字
COL_TEXT_DIM = "#9a9a9a"    # 次要文字
COL_TEXT_FAINT = "#6b7280"  # 占位提示
COL_ACCENT = "#1ea3ff"      # OBS 强调蓝
COL_ACCENT_HOVER = "#3db0ff"
COL_ACCENT_SEL = "#1a5f8f"  # 时间轴选区填充(深蓝)
COL_START = "#f0922d"       # 起点手柄 橙
COL_END = "#19a974"         # 终点手柄 青
COL_PLAY = "#e03131"        # 播放头 红
COL_PREVIEW_BG = "#000000"  # 预览画布
COL_STATUS_BG = "#26262b"   # 状态栏

PREVIEW_MIN_W = 360
CACHE_LIMIT = 60


def fmt_time(sec):
    """00:00:00.000 格式"""
    if sec is None or sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec - h * 3600 - m * 60
    return "%02d:%02d:%06.3f" % (h, m, s)


def fmt_short(sec):
    """00:00:01 格式(片段列表用)"""
    if sec is None or sec < 0:
        sec = 0
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    return "%02d:%02d:%02d" % (h, m, s)


def _exe_ok(d):
    """校验目录里的 ffmpeg/ffprobe 存在、体积正常且可运行。
    只检查文件是否存在会误判——空文件/损坏/被杀软清空的 exe
    会导致 probe 失败并误报『文件损坏』。"""
    fm = os.path.join(d, "ffmpeg.exe")
    fp = os.path.join(d, "ffprobe.exe")
    if not (os.path.isfile(fm) and os.path.isfile(fp)):
        return False
    # 真实 ffmpeg 静态编译版通常 > 60MB,占位/空文件快速排除
    if os.path.getsize(fm) < 1_000_000 or os.path.getsize(fp) < 1_000_000:
        return False
    rc, _, _ = run_capture([fm, "-version"], timeout=15)
    return rc == 0


def find_ffmpeg_dir():
    """在 PATH 与程序目录下查找可运行的 ffmpeg/ffprobe,返回目录或 None"""
    candidates = []
    for exe in ("ffmpeg",):
        w = shutil.which(exe)
        if w:
            candidates.append(os.path.dirname(w))
    for d in (FFMPEG_DIR, os.path.join(FFMPEG_DIR, "bin"), APP_DIR):
        if os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            candidates.append(d)
    for d in candidates:
        if _exe_ok(d):
            return d
    return None


def ff_join(d, name):
    return os.path.join(d, name + ".exe") if sys.platform == "win32" else os.path.join(d, name)


def run_capture(cmd, timeout=60):
    """运行命令并返回 (rc, stdout, stderr),子进程不弹控制台窗口"""
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, b"", b"timeout"
    except OSError as e:
        return -1, b"", str(e).encode("utf-8", "replace")


def _safe_crash(stage, exc):
    """线程安全的崩溃日志(writer 线程/worker 线程的异常主线程钩子看不到)"""
    try:
        import traceback as _tb
        with open(os.path.join(APP_DIR, "crash.log"), "a",
                  encoding="utf-8") as f:
            f.write("[%s] %s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), stage,
                "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))))
    except Exception:
        pass


# --------------------------------------------------------------------------
# 崩溃自动重启:主程序启动时写崩溃标记 + 拉起一个 --watchdog 守护子进程。
# 主进程异常退出(崩溃/被杀,未走正常关闭流程)时,标记文件仍在,
# watchdog 检测到主 pid 消失后自动重新拉起程序;正常关闭会删除标记,不重启。
# --------------------------------------------------------------------------
CRASH_MARKER = os.path.join(APP_DIR, "crash_marker.txt")
CRASH_COUNT = os.path.join(APP_DIR, "crash_count.txt")


def _pid_alive(pid):
    """Windows 下用 tasklist 查询进程是否存活"""
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=CREATE_NO_WINDOW)
            return str(pid) in r.stdout.decode("utf-8", "replace")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _launch_self(extra=None):
    """重启自身:打包版直接跑 exe;源码版用 python 跑本脚本"""
    args = [sys.executable]
    if not getattr(sys, "frozen", False):
        args.append(os.path.abspath(sys.argv[0]))
    if extra:
        args.extend(extra)
    subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)


def _watchdog_main(pid):
    """守护子进程主逻辑:每 2 秒检查主进程;主进程消失且崩溃标记仍在则重启"""
    time.sleep(3)  # 等主进程完全启动(避免误判)
    while True:
        time.sleep(2)
        if _pid_alive(pid):
            continue
        # 主进程已退出
        if os.path.isfile(CRASH_MARKER):
            try:
                with open(CRASH_MARKER, "r", encoding="utf-8") as f:
                    if f.read().strip() != str(pid):
                        return  # 旧标记(不属于本 watchdog 监控的进程),不处理
            except Exception:
                pass
            try:
                os.remove(CRASH_MARKER)
            except OSError:
                pass
            # 异常退出 → 自动重启
            try:
                _launch_self()
            except Exception:
                pass
        return


def _start_watchdog():
    """主进程启动时调用:写崩溃标记 + 拉起 watchdog。
    1 分钟内连续崩溃 >= 4 次则放弃自动重启(防无限重启死循环)。"""
    try:
        now = time.time()
        n, ts = 0, 0.0
        if os.path.isfile(CRASH_COUNT):
            with open(CRASH_COUNT, "r", encoding="utf-8") as f:
                parts = f.read().split()
                n = int(parts[0]) if parts else 0
                ts = float(parts[1]) if len(parts) > 1 else 0.0
        n = n + 1 if (now - ts) < 60 else 1
        with open(CRASH_COUNT, "w", encoding="utf-8") as f:
            f.write("%d %.3f" % (n, now))
        if n >= 4:
            return  # 连续崩溃太频繁,放弃自动重启
    except Exception:
        pass
    try:
        with open(CRASH_MARKER, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        return  # 目录不可写,不启用 watchdog
    try:
        _launch_self(["--watchdog", str(os.getpid())])
    except Exception:
        pass


def _clear_crash_marker():
    """正常关闭时清除崩溃标记(watchdog 看到后不会重启)"""
    try:
        if os.path.isfile(CRASH_MARKER):
            os.remove(CRASH_MARKER)
    except OSError:
        pass


# --------------------------------------------------------------------------
# v10 曾实现窗口拖放(WndProc / 透明子窗口接收器),但 Tk 8.6 的窗口过程链
# 会消费 WM_DROPFILES,ctypes 替换/子窗口方案在真实环境中不稳定(闪退)。
# v11 移除拖放到窗口功能,保留"拖到 exe 图标启动"(命令行参数路径)。
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 配置管理:记住上次目录 / 窗口位置(写在 exe 目录旁,删除即恢复默认)
# --------------------------------------------------------------------------
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CFG = {
    "last_open_dir": "",     # 上次打开视频的目录
    "last_export_dir": "",   # 上次导出目录
    "window_geometry": "",   # 窗口位置与大小
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = dict(DEFAULT_CFG)
        cfg.update({k: v for k, v in data.items() if k in DEFAULT_CFG})
        return cfg
    except Exception:
        return dict(DEFAULT_CFG)


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 配置写失败(如目录只读)不影响主流程


def fps_value(fps_str):
    """'30000/1001' -> 29.97; '25' -> 25.0; 解析失败返回 None"""
    if not fps_str:
        return None
    try:
        if "/" in fps_str:
            a, b = fps_str.split("/", 1)
            v = float(a) / float(b)
        else:
            v = float(fps_str)
        return v if 0 < v < 240 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def humanize_ffmpeg_error(tail):
    """把 ffmpeg 原始报错翻译成普通人能看懂的中文提示;找不到匹配返回 None"""
    t = tail or ""
    table = [
        (r"moov atom not found",
         "输出文件不完整(MP4 索引缺失),通常是磁盘已满或程序被中途打断。"),
        (r"No space left on device|Cannot allocate memory",
         "磁盘空间不足或内存不足,请清理磁盘后重试。"),
        (r"Permission denied",
         "没有写入权限,请更换输出位置(避免系统保护目录)。"),
        (r"Invalid data found when processing input",
         "输入文件损坏或格式不受支持,无法读取。"),
        (r"Unknown encoder|Encoder not found",
         "缺少所需编码器,当前 ffmpeg 版本不支持该输出格式。"),
        (r"Non-monotonous DTS|Packet corrupt|decoding for stream",
         "视频流数据异常,多为片段编码参数不一致或源文件有损坏;\n"
         "可尝试把每个片段分别裁剪后再合并,或改用重编码导出。"),
        (r"does not match any|matches no streams",
         "视频/音频流信息不匹配,片段之间的编码参数不一致。"),
        (r"Protocol not found|Unable to open",
         "文件路径或格式有问题,请检查路径中是否含有特殊字符。"),
        (r"concat.*(codec|parameter|differ)|Stream specifier.*matches no streams",
         "片段编码参数不一致,无法直接无损拼接。"),
    ]
    for pat, msg in table:
        if re.search(pat, t, re.IGNORECASE):
            return "%s\n\n(ffmpeg 原始输出节选)\n%s" % (msg, t[:600])
    return None


class FFmpegAPI:
    """封装 ffmpeg / ffprobe / ffplay 的调用"""

    def __init__(self, bindir):
        self.bindir = bindir
        self.ffmpeg = ff_join(bindir, "ffmpeg")
        self.ffprobe = ff_join(bindir, "ffprobe")
        self.ffplay = ff_join(bindir, "ffplay")
        self.has_ffplay = os.path.isfile(self.ffplay)
        self.last_err = ""  # 最近一次 probe 失败的具体原因

    def probe(self, path):
        """解析视频信息,返回 dict;失败返回 None 并记录 self.last_err"""
        self.last_err = ""
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
        ]
        rc, out, err = run_capture(cmd, timeout=60)
        if rc != 0:
            raw = (err or out or b"").decode("utf-8", "replace").strip()
            self.last_err = raw or "ffprobe 退出码 %d(可能 exe 无效或文件无法访问)" % rc
            return None
        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except Exception as e:
            self.last_err = "ffprobe 输出解析失败: %s" % e
            return None
        video = None
        audio = None
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and video is None:
                fps = s.get("avg_frame_rate") or s.get("r_frame_rate") or ""
                video = {
                    "codec": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                    "pix_fmt": s.get("pix_fmt"),
                    "fps": fps,
                }
            elif s.get("codec_type") == "audio" and audio is None:
                audio = {
                    "codec": s.get("codec_name"),
                    "sample_rate": s.get("sample_rate"),
                    "channels": s.get("channels"),
                }
        duration = None
        try:
            duration = float(data.get("format", {}).get("duration") or 0)
        except (TypeError, ValueError):
            duration = None
        if duration is None or duration <= 0:
            # 部分封装格式 format.duration 缺失,用 show_entries 兜底
            # 注意:输出在 stdout(不是 stderr)
            rc2, out2, _err2 = run_capture(
                [self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path], timeout=30)
            if rc2 == 0:
                try:
                    duration = float(out2.decode("utf-8", "replace").strip())
                except ValueError:
                    duration = None
        return {
            "path": path,
            "duration": duration or 0,
            "video": video,
            "audio": audio,
            "has_video": video is not None,
            "filename": os.path.basename(path),
        }

    def grab_frame(self, path, t, width, out_png, timeout=15):
        """用快速 seek 抽一帧为 PNG(scale 到指定宽度)"""
        cmd = [
            self.ffmpeg, "-y", "-loglevel", "error",
            "-ss", "%.3f" % max(0.0, t),
            "-i", path,
            "-frames:v", "1",
            "-vf", "scale=%d:-2" % width,
            "-f", "image2",
            out_png,
        ]
        rc, _o, _e = run_capture(cmd, timeout=timeout)
        return rc == 0 and os.path.isfile(out_png)

    def segments_compatible(self, segs):
        """检查多个片段编码参数是否一致(无损合并的前提)"""
        params = []
        for seg in segs:
            info = self.probe(seg["file"])
            if info is None:
                return False, "无法解析片段: %s" % seg["file"]
            v = info.get("video") or {}
            a = info.get("audio") or {}
            params.append({
                "v": (v.get("codec"), v.get("width"), v.get("height"),
                      v.get("pix_fmt"), v.get("fps")),
                "a": (a.get("codec"), a.get("sample_rate"), a.get("channels")),
            })
        first = params[0]
        for i, pa in enumerate(params[1:], start=2):
            if pa["v"] != first["v"] or pa["a"] != first["a"]:
                return False, (
                    "第 1 个片段与第 %d 个片段的编码参数不一致\n"
                    "(分辨率/编码器/码率/采样率需完全相同才能无损拼接)。" % i
                )
        return True, ""


class Timeline(Canvas):
    """可拖动的时间轴:两个裁剪手柄 + 播放头(OBS 深色风格)"""

    PAD = 12
    TRACK_H = 46
    HANDLE_W = 11
    HANDLE_H = 20

    def __init__(self, master, app, **kw):
        kw.setdefault("height", 112)
        kw.setdefault("bg", COL_PANEL)
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, **kw)
        self.app = app
        self.drag_mode = None  # "start" | "end" | "play" | None
        self.drag_off = 0.0
        self.bind("<Button-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Double-Button-1>", self.on_double)
        self.bind("<Configure>", lambda e: self.draw())

    # ---- 坐标换算 ----
    def t_to_x(self, t):
        dur = self.app.duration or 1
        w = max(self.winfo_width() - 2 * self.PAD, 1)
        return self.PAD + w * (max(0.0, min(dur, t)) / dur)

    def x_to_t(self, x):
        dur = self.app.duration or 1
        w = max(self.winfo_width() - 2 * self.PAD, 1)
        return max(0.0, min(dur, (x - self.PAD) / w * dur))

    # ---- 绘制 ----
    def draw(self):
        self.delete("all")
        app = self.app
        dur = app.duration
        if not app.video or dur <= 0:
            self.create_text(
                self.winfo_width() // 2, 52,
                text="打开视频后,可在此拖动选择裁剪区间",
                fill=COL_TEXT_FAINT, font=app.font_small,
            )
            return
        w = self.winfo_width()
        x0, x1 = self.t_to_x(app.start_t), self.t_to_x(app.end_t)
        y0 = 28
        y1 = y0 + self.TRACK_H

        # 轨道
        self.create_rectangle(self.PAD, y0, w - self.PAD, y1,
                              fill=COL_WIDGET, outline=COL_BORDER)
        # 选区(OBS 蓝)
        if x1 - x0 > 1:
            self.create_rectangle(x0, y0, x1, y1,
                                  fill=COL_ACCENT_SEL, outline="")
            self.create_rectangle(x0, y0, x1, y1,
                                  fill="", outline=COL_ACCENT, width=1)
        # 刻度:主刻度(长线+标签) + 次刻度(短线)
        self._draw_ticks(self._tick_step(dur), dur)
        # 起始/结束时间标签
        self.create_text(max(x0, self.PAD + 34), y0 - 12,
                         text=fmt_time(app.start_t),
                         fill=COL_START, font=app.font_small, anchor="w")
        self.create_text(min(x1, w - self.PAD - 34), y0 - 12,
                         text=fmt_time(app.end_t),
                         fill=COL_END, font=app.font_small, anchor="e")
        # 播放头
        px = self.t_to_x(app.playhead_t)
        self.create_line(px, y0 - 14, px, y1, fill=COL_PLAY, width=2)
        self.create_oval(px - 5, y0 - 18, px + 5, y0 - 8,
                         fill=COL_PLAY, outline="")
        # 手柄
        self._draw_handle(x0, COL_START)
        self._draw_handle(x1, COL_END)

    def _draw_handle(self, x, color):
        y0 = 28
        y1 = y0 + self.TRACK_H
        self.create_rectangle(x - 1, y0, x + 1, y1, fill=color, outline="")
        self.create_rectangle(
            x - self.HANDLE_W // 2, y0 - self.HANDLE_H, x + self.HANDLE_W // 2, y0,
            fill=color, outline="#ffffff", width=1,
        )

    def _tick_step(self, dur):
        for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600):
            if dur / s <= 12:
                return s
        return 3600 * 4

    def _tick_label(self, t, step):
        """刻度标签:步长 >=1s 显示 mm:ss / h:mm:ss,<1s 显示 秒.毫秒"""
        if step >= 60:
            return "%d:%02d:%02d" % (int(t // 3600), int(t % 3600 // 60), int(t % 60))
        if step >= 1:
            return "%02d:%02d" % (int(t // 60), int(t % 60))
        return "%.3f" % t

    def _draw_ticks(self, step, dur):
        """主刻度(每 4 格,长线+时间标签) + 次刻度(短线)"""
        y0, y1 = 28, 28 + self.TRACK_H
        t = 0.0
        n = 0
        while t <= dur + 1e-6:
            x = self.t_to_x(t)
            if n % 4 == 0:
                self.create_line(x, y1, x, y1 + 8, fill=COL_TEXT_DIM)
                self.create_text(x, y1 + 16, text=self._tick_label(t, step),
                                 fill=COL_TEXT_DIM, font=self.app.font_tiny,
                                 anchor="n")
            else:
                self.create_line(x, y1, x, y1 + 4, fill=COL_TEXT_FAINT)
            t += step / 4
            n += 1

    # ---- 交互 ----
    def on_press(self, e):
        app = self.app
        if not app.video or app.duration <= 0:
            return
        x0, x1 = self.t_to_x(app.start_t), self.t_to_x(app.end_t)
        px = self.t_to_x(app.playhead_t)
        for name, hx in (("start", x0), ("end", x1)):
            if abs(e.x - hx) <= 10:
                self.drag_mode = name
                self.drag_off = e.x - hx
                return
        if abs(e.x - px) <= 8:
            self.drag_mode = "play"
            self.drag_off = e.x - px
            return
        app.playhead_t = self.x_to_t(e.x)
        self.draw()
        app.on_playhead_drag()

    def on_drag(self, e):
        if not self.drag_mode:
            return
        app = self.app
        t = self.x_to_t(e.x - self.drag_off)
        if self.drag_mode == "start":
            app.start_t = min(t, app.end_t)
        elif self.drag_mode == "end":
            app.end_t = max(t, app.start_t)
        else:
            app.playhead_t = t
            app.on_playhead_drag()  # v10:拖动播放头时预览帧实时联动
        self.draw()

    def on_release(self, _e):
        if self.drag_mode:
            self.drag_mode = None
            self.app.refresh_preview_now()

    def on_double(self, _e):
        if self.app.video and self.app.duration > 0:
            self.app.open_precise_dialog()


class App(Tk):
    def __init__(self):
        super().__init__()
        self.title("无损视频剪辑 · Lossless Video Editor")
        self.geometry("1240x860")
        self.minsize(1040, 720)
        self.configure(bg=COL_BG)

        self.base_font = tkfont.nametofont("TkDefaultFont")
        self.base_font.configure(family="Microsoft YaHei UI", size=10)
        self.font_small = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.font_tiny = tkfont.Font(family="Consolas", size=8)
        self.font_info = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        self.font_mono = tkfont.Font(family="Consolas", size=9)

        # 状态
        self.cfg = load_config()
        self.ffapi = None
        self.ffdir = None
        self.video = None
        self.info = None
        self.duration = 0.0
        self.start_t = 0.0
        self.end_t = 0.0
        self.playhead_t = 0.0
        self._preview_after = None
        self._preview_gen = 0
        # 预览缩放(⑥:滚轮缩放 + 1:1 查看;缩放级数)
        self.preview_zoom = 1.0
        self._pv_img = None
        self._pv_center = None
        self._pv_drag = None
        self.ZOOM_LEVELS = (0.25, 0.5, 1, 2, 3, 4)
        self._load_gen = 0  # 视频加载代际:连续打开时旧任务作废
        self.frame_cache = {}
        self.segments = []
        self.busy = False
        self._btns = []
        self._video_btns = []
        self._job = None
        self._job_total = 0.0
        self._proc = None
        self._cancel_flag = False
        # v10:预览联动最新帧序号(丢弃过期抽帧) / 播放速度 / 待打开文件(命令行)
        self._preview_seq = 0
        self._pv_live_last = 0.0
        self.speed_var = StringVar(value="1x")
        self._pending_open = None
        self._mp3_enc = None  # MP3 编码器探测结果缓存(libmp3lame / mp3)

        self.build_ui()
        self._restore_geometry()
        self.after(200, self.ensure_ffmpeg)
        self.after(300, self._try_pending)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _try_pending(self):
        """ffmpeg 就绪后自动打开待加载的视频(命令行传入 / 拖到 exe 图标启动)"""
        p = getattr(self, "_pending_open", None)
        if not p:
            return
        if not self.ffapi:
            self.after(500, self._try_pending)
            return
        self._pending_open = None
        self.set_status("正在打开拖入的视频…")
        self.load_video(p)

    def _restore_geometry(self):
        """恢复上次窗口大小与位置(防越界到屏幕外/恢复到已拔掉的外接屏)"""
        geo = self.cfg.get("window_geometry") or ""
        if not geo:
            return
        try:
            m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo)
            if not m:
                return
            w, h = int(m.group(1)), int(m.group(2))
            x, y = int(m.group(3)), int(m.group(4))
            if w < self.minsize()[0] or h < self.minsize()[1]:
                return  # 太小,保持默认
            # 位置校验:窗口必须仍与当前屏幕有交集,否则恢复到默认位置
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            if x >= sw - 80 or y >= sh - 40 or x + w <= 0 or y + h <= 0:
                return
            self.geometry(geo)
        except Exception:
            pass

    def _on_close(self):
        # 导出进行中直接关窗口 → 先中止后台 ffmpeg,避免进程残留占用输出文件
        p = getattr(self, "_proc", None)
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
            self._cancel_flag = True
        try:
            self.cfg["window_geometry"] = self.geometry()
            save_config(self.cfg)
        except Exception:
            pass
        _clear_crash_marker()  # 正常关闭 → watchdog 不会重启
        self.destroy()
        # 打包版兜底:windowed 模式下 Tk 主窗口销毁后 mainloop 偶发不返回,
        # 走正常清理路径结束进程,保证"点关闭即退出"(bootloader 父进程同步退出)
        if getattr(sys, "frozen", False):
            try:
                sys.exit(0)
            except SystemExit:
                raise
            except Exception:
                pass

    # ================= 控件工厂 =================
    def _btn(self, parent, text, cmd, accent=False, width=None, register=True):
        """OBS 风格扁平按钮。
        register=False 的按钮(如临时对话框里的)不登记到 _btns,
        避免对话框关闭后已销毁的 widget 残留引用导致导出时崩溃。"""
        bg = COL_ACCENT if accent else COL_WIDGET
        fg = "#ffffff" if accent else COL_TEXT
        ab = COL_ACCENT_HOVER if accent else COL_WIDGET_HOVER
        b = Button(
            parent, text=text, command=cmd,
            bg=bg, fg=fg,
            activebackground=ab, activeforeground=fg,
            relief="flat", bd=0,
            padx=12, pady=5,
            font=self.font_small, cursor="hand2",
            highlightthickness=0,
        )
        if width:
            b.config(width=width)
        b._nb, b._nf = bg, fg
        if register:
            self._btns.append(b)
        return b

    def _set_btn_enabled(self, b, en):
        try:
            if not b.winfo_exists():
                return  # widget 已销毁,静默跳过
        except Exception:
            return
        b.config(
            state="normal" if en else "disabled",
            bg=b._nb if en else COL_WIDGET_DISABLED,
            fg=b._nf if en else COL_TEXT_FAINT,
        )

    def _label(self, parent, text="", fg=COL_TEXT, font=None, anchor="w"):
        return Label(parent, text=text, bg=COL_PANEL, fg=fg,
                     font=font or self.font_small, anchor=anchor,
                     highlightthickness=0, bd=0)

    # ================= UI =================
    def build_ui(self):
        # ---------- 顶部工具栏(OBS 风格) ----------
        self.topbar = Frame(self, bg=COL_PANEL, height=50)
        self.topbar.pack(fill="x", side="top")
        self.topbar.pack_propagate(False)
        sep = Frame(self, bg=COL_BG, height=1)
        sep.pack(fill="x", side="top")

        # Logo 区
        logo_badge = Frame(self.topbar, bg=COL_PANEL)
        logo_badge.pack(side="left", padx=(12, 0))
        badge = Label(logo_badge, text="WB", width=4, bg=COL_ACCENT, fg="#ffffff",
                      font=tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold"),
                      highlightthickness=0, bd=0)
        badge.pack(side="left", pady=10)
        logo_text = Frame(logo_badge, bg=COL_PANEL)
        logo_text.pack(side="left", padx=(8, 0), pady=6)
        Label(logo_text, text="无损视频剪辑", bg=COL_PANEL, fg="#ffffff",
              font=tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold"),
              anchor="w").pack(anchor="w")
        Label(logo_text, text="LOSS VIDEO · FFMPEG STREAM COPY", bg=COL_PANEL,
              fg=COL_TEXT_FAINT,
              font=tkfont.Font(family="Consolas", size=7), anchor="w").pack(anchor="w")

        # 主操作按钮(右侧)
        self.btn_cut = self._btn(self.topbar, "  ✂  裁剪导出(无损)  ", self.export_cut,
                                 accent=True)
        self.btn_cut.pack(side="right", padx=(8, 12), pady=7)
        self.btn_precise = self._btn(self.topbar, "精确设置", self.open_precise_dialog)
        self.btn_precise.pack(side="right", padx=(8, 0), pady=7)
        self.btn_play = self._btn(self.topbar, "▶ 播放选区", self.play_selection)
        self.btn_play.pack(side="right", padx=(8, 0), pady=7)
        self.btn_fit = self._btn(self.topbar, "重置预览", self.reset_view)
        self.btn_fit.pack(side="right", padx=(8, 0), pady=7)
        self.btn_open = self._btn(self.topbar, "打开视频…", self.open_video)
        self.btn_open.pack(side="right", pady=7)

        # ---------- 主体:左预览 / 右控制面板 ----------
        main = Frame(self, bg=COL_BG)
        main.pack(fill="both", expand=True)

        left = Frame(main, bg=COL_BG)
        left.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)

        # 预览区(黑底 + 边框)
        self.cv = Canvas(left, bg=COL_PREVIEW_BG, highlightthickness=1,
                         highlightbackground=COL_BORDER)
        self.cv.pack(fill="both", expand=True)
        self.cv.create_text(
            400, 220, text="打开一个视频文件后,这里会显示预览帧\n\n"
                           "拖动下方时间轴即可选择要保留的区间",
            fill="#4d5661", font=self.font_info, justify="center", tags="hint")
        # ⑥:预览缩放交互 —— 滚轮缩放(以鼠标为中心) / 双击恢复 100% / 放大后按住拖动平移
        self.cv.bind("<MouseWheel>", self._on_preview_wheel)
        self.cv.bind("<Double-Button-1>", self._on_preview_double)
        self.cv.bind("<ButtonPress-1>", self._on_pv_press)
        self.cv.bind("<B1-Motion>", self._on_pv_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_pv_release)

        # 视频信息条(①:分辨率/编码/帧率/音频/大小/时长)
        self.info_bar = Frame(left, bg=COL_PANEL)
        self.info_bar.pack(fill="x", pady=(6, 0))
        self.lbl_vname = self._label(self.info_bar, "未加载视频",
                                     fg=COL_TEXT_DIM, font=self.font_info)
        self.lbl_vname.pack(fill="x", padx=10, pady=(6, 0))
        self.lbl_vmeta = self._label(self.info_bar, "", fg=COL_TEXT_FAINT)
        self.lbl_vmeta.pack(fill="x", padx=10, pady=(0, 6))

        # 时间轴
        self.timeline = Timeline(left, self)
        self.timeline.pack(fill="x", pady=(6, 0))

        # 控制行(时间轴下方)
        ctrl = Frame(left, bg=COL_BG)
        ctrl.pack(fill="x", pady=(6, 0))
        self.btn_set_s = self._btn(ctrl, "⏮ 起点 = 播放头", self.set_start)
        self.btn_set_s.pack(side="left")
        self.btn_set_e = self._btn(ctrl, "终点 = 播放头 ⏭", self.set_end)
        self.btn_set_e.pack(side="left", padx=(6, 0))
        self.precise_var = BooleanVar(value=False)
        self.chk_precise = Checkbutton(
            ctrl, text="精确到帧(慢,不重编码)", variable=self.precise_var,
            bg=COL_BG, fg=COL_TEXT, activebackground=COL_BG,
            activeforeground=COL_TEXT, selectcolor=COL_ENTRY,
            highlightthickness=0, bd=0, font=self.font_small)
        self.chk_precise.pack(side="left", padx=(12, 0))
        # v11:播放速度选择(自定义深色风格下拉,0.25x~2x 六档)
        self.SPEED_VALUES = ("0.25x", "0.5x", "0.75x", "1x", "1.5x", "2x")
        self.lbl_speed = self._label(ctrl, "倍速", fg=COL_TEXT_DIM)
        self.lbl_speed.pack(side="left", padx=(16, 4))
        self.speed_box = Button(
            ctrl, text="1x \u25be", command=self._speed_popup,
            bg=COL_WIDGET, fg=COL_TEXT, activebackground=COL_WIDGET_HOVER,
            activeforeground=COL_TEXT, relief="flat", bd=0,
            padx=10, pady=3, font=self.font_small, cursor="hand2",
            highlightthickness=0, highlightbackground=COL_BORDER,
            highlightcolor=COL_ACCENT, width=6)
        self.speed_box.pack(side="left")
        self.speed_box._nb, self.speed_box._nf = COL_WIDGET, COL_TEXT

        # ---------- 右侧控制面板 ----------
        panel = Frame(main, bg=COL_PANEL, width=300)
        panel.pack(side="right", fill="y", padx=(4, 8), pady=8)
        panel.pack_propagate(False)

        # 片段列表
        Label(panel, text="合并片段列表", bg=COL_PANEL, fg="#ffffff",
              font=self.font_info, anchor="w").pack(fill="x", padx=10, pady=(10, 4))
        box = Frame(panel, bg=COL_PANEL)
        box.pack(fill="x", padx=10)
        self.lst = Listbox(box, height=9, font=self.font_mono, activestyle="none",
                           bg=COL_ENTRY, fg="#d0d0d0", selectbackground=COL_ACCENT,
                           selectforeground="#ffffff", highlightthickness=1,
                           highlightbackground=COL_BORDER, bd=0, relief="flat",
                           selectmode="extended")  # v12:多选(Shift范围/Ctrl逐个)
        self.lst.pack(side="left", fill="both", expand=True)
        sb = Scrollbar(box, command=self.lst.yview, bg=COL_WIDGET,
                       troughcolor=COL_ENTRY, activebackground=COL_WIDGET_HOVER,
                       bd=0, relief="flat")
        sb.pack(side="right", fill="y")
        self.lst.config(yscrollcommand=sb.set)
        self.lst.bind("<Double-Button-1>", self.on_seg_double)
        self.lst.bind("<Button-3>", self._on_seg_menu)  # v10:右键菜单
        # v11:拖拽排序
        self._drag_start_idx = None
        self._drag_pending = False
        self.lst.bind("<Button-1>", self._on_drag_start, add="+")
        self.lst.bind("<B1-Motion>", self._on_drag_motion, add="+")
        self.lst.bind("<ButtonRelease-1>", self._on_drag_release, add="+")

        seg_btns = Frame(panel, bg=COL_PANEL)
        seg_btns.pack(fill="x", padx=10, pady=(8, 0))
        self.btn_add = self._btn(seg_btns, "＋ 添加当前选区", self.add_segment)
        self.btn_add.pack(side="left", fill="x", expand=True)
        self.btn_del = self._btn(seg_btns, "－", self.del_segment)
        self.btn_del.pack(side="left", padx=(6, 0))
        seg_btns2 = Frame(panel, bg=COL_PANEL)
        seg_btns2.pack(fill="x", padx=10, pady=(6, 0))
        self.btn_up = self._btn(seg_btns2, "↑ 上移", self.move_segment(-1))
        self.btn_up.pack(side="left", fill="x", expand=True)
        self.btn_down = self._btn(seg_btns2, "↓ 下移", self.move_segment(1))
        self.btn_down.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.btn_clear = self._btn(seg_btns2, "清空", self.clear_segments)
        self.btn_clear.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.btn_merge = self._btn(panel, "  ⧉  合并导出(无损)  ", self.export_merge,
                                   accent=True)
        self.btn_merge.pack(fill="x", padx=10, pady=(10, 0))
        # ②:批量导出 —— 把片段列表每个片段导出为独立文件
        self.btn_batch = self._btn(panel, " ⧉  批量导出全部片段  ",
                                   self.export_all_segments)
        self.btn_batch.pack(fill="x", padx=10, pady=(6, 0))
        # v10:GIF / 纯音频 MP3 导出(重编码,单行并排,不挤压日志区)
        export_row = Frame(panel, bg=COL_PANEL)
        export_row.pack(fill="x", padx=10, pady=(6, 0))
        self.btn_gif = self._btn(export_row, "⦿ 导出 GIF", self.export_gif)
        self.btn_gif.pack(side="left", fill="x", expand=True)
        self.btn_audio = self._btn(export_row, "♪ 导出音频 MP3",
                                   self.export_audio)
        self.btn_audio.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # 日志(点击标题可折叠/展开)
        self.log_header = Frame(panel, bg=COL_PANEL)
        self.log_header.pack(fill="x", padx=10, pady=(12, 4))
        self.lbl_log_title = Label(self.log_header, text="▾ 处理日志(点击折叠)",
                                   bg=COL_PANEL, fg=COL_TEXT_DIM,
                                   font=self.font_small, anchor="w", cursor="hand2")
        self.lbl_log_title.pack(side="left")
        self.lbl_log_title.bind("<Button-1>", lambda e: self.toggle_log())
        self.log_text = Text(panel, height=9, font=self.font_mono, bg=COL_ENTRY,
                             fg="#c8d6c8", wrap="word", state="disabled",
                             insertbackground=COL_TEXT, highlightthickness=1,
                             highlightbackground=COL_BORDER, bd=0, relief="flat")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._log_visible = True

        # ---------- 状态栏 ----------
        status_bar = Frame(self, bg=COL_STATUS_BG, height=30)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        Frame(status_bar, bg=COL_BORDER, height=1).pack(fill="x", side="top")
        self.lbl_range = Label(status_bar, text="区间: --:--:--.--- → --:--:--.--- "
                                                "(全长 --:--:--)",
                               bg=COL_STATUS_BG, fg=COL_TEXT_DIM,
                               font=self.font_small, anchor="w")
        self.lbl_range.pack(side="left", padx=10, pady=6)
        self.status = Label(status_bar, text="正在检测 ffmpeg…", anchor="e",
                            bg=COL_STATUS_BG, fg="#cfd6de", font=self.font_small)
        self.status.pack(side="right", padx=10, pady=6)

        # ---------- 导出进度条(④:平时隐藏,导出时显示到状态栏上方) ----------
        self.prog_bar_frame = Frame(self, bg=COL_PANEL, height=36)
        self.prog_bar_frame.pack_propagate(False)
        self.prog_bar = ttk_progressbar(self.prog_bar_frame, length=340, maximum=100)
        self.prog_bar.pack(side="left", padx=12, pady=10)
        self.btn_cancel = self._btn(self.prog_bar_frame, "✕ 取消导出",
                                    self.cancel_job, register=False)
        self.btn_cancel.pack(side="left", padx=(8, 0), pady=7)
        self.prog_bar_frame.pack_forget()  # 默认隐藏

        # 初始按钮状态
        for b in (self.btn_play, self.btn_fit, self.btn_precise, self.btn_set_s,
                  self.btn_set_e, self.btn_cut, self.btn_add,
                  self.btn_gif, self.btn_audio):
            self._set_btn_enabled(b, False)
            self._video_btns.append(b)

        # ---------- 快捷键 ----------
        # ←/→ 单步微调(精确到帧) · Shift+←/→ 快速微调 · 空格播放选区(见 _on_key)
        self.bind_all("<Key>", self._on_key)

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def set_status(self, msg):
        self.status.config(text=msg)

    def toggle_log(self):
        """点击日志标题折叠/展开日志面板"""
        if self._log_visible:
            self.log_text.pack_forget()
            self.lbl_log_title.config(text="▸ 处理日志(点击展开)")
            self._log_visible = False
        else:
            self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            self.lbl_log_title.config(text="▾ 处理日志(点击折叠)")
            self._log_visible = True

    # ================= 快捷键与播放头微调 =================
    def _focus_typing(self):
        """焦点是否在输入/列表类控件(此时不劫持 ←/→/空格)"""
        w = self.focus_get()
        if w is None:
            return False
        if isinstance(w, (Entry, Text)):
            return True
        try:
            return w.winfo_class() in ("Entry", "Text", "Listbox", "TEntry")
        except Exception:
            return False

    def _nudge_step_sec(self):
        """←/→ 单步步长(秒):按视频帧率精确到帧,未知帧率按 25fps 兜底"""
        fps = None
        if self.info:
            fps = fps_value((self.info.get("video") or {}).get("fps"))
        if fps and fps > 0:
            return 1.0 / fps
        return 0.04  # 未知帧率按 25fps 兜底

    def _fast_nudge_sec(self):
        """Shift+←/→ 快速步长:固定 1 秒"""
        return 1.0

    def _on_key(self, e):
        """←/→ 逐帧微调播放头,Shift+←/→ 快速微调(1秒),空格播放选区"""
        key = e.keysym
        if key not in ("Left", "Right", "KP_Left", "KP_Right", "space"):
            return
        if self._focus_typing():
            return  # 输入/列表控件里不劫持,让默认行为生效
        if self.busy or not self.video or self.duration <= 0:
            return
        shift = (e.state & 0x0001) != 0
        step = self._fast_nudge_sec() if shift else self._nudge_step_sec()
        if key in ("Left", "KP_Left"):
            self._nudge_playhead(-step)
        elif key in ("Right", "KP_Right"):
            self._nudge_playhead(step)
        else:
            self.play_selection()
        return "break"

    def _nudge_playhead(self, delta):
        self.playhead_t = max(0.0, min(self.duration, self.playhead_t + delta))
        self.timeline.draw()
        # 防抖刷新:按住方向键连发时避免堆积 ffmpeg 抽帧进程
        self.schedule_preview()

    # ================= v12:速度下拉(原生 Menu,无 grab 死锁) =================
    def _speed_popup(self):
        """点击速度按钮 → 弹出原生菜单下拉列表(无 grab / 无 wait_window,不会卡死)"""
        try:
            m = Menu(self, tearoff=0, bg=COL_ENTRY, fg=COL_TEXT,
                     activebackground=COL_ACCENT, activeforeground="#ffffff",
                     relief="flat", bd=0, font=self.font_small)
            cur = self.speed_var.get() or "1x"
            for v in self.SPEED_VALUES:
                label = ("● " + v) if v == cur else ("  " + v)
                m.add_command(label=label, command=lambda val=v: self._set_speed(val))
            m.tk_popup(self.speed_box.winfo_rootx(),
                       self.speed_box.winfo_rooty() + self.speed_box.winfo_height())
        except Exception:
            pass  # 应用关闭中 / 窗口已销毁,静默忽略
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _set_speed(self, val):
        """设置播放速度并更新按钮显示"""
        self.speed_var.set(val)
        self.speed_box.config(text=val + " \u25be")

    # ================= ffmpeg 检测/内置解压/下载 =================
    def ensure_ffmpeg(self):
        d = find_ffmpeg_dir()
        if d:
            self.ffdir = d
            self.ffapi = FFmpegAPI(d)
            self.set_status("ffmpeg 就绪: %s" % os.path.join(d, "ffmpeg.exe"))
            return
        # 优先尝试内置 ffmpeg(打包进 exe,免下载、免联网)
        if bundle_zip():
            self.set_status("首次启动,正在解压内置 ffmpeg…")
            self.extract_bundle()
            return
        self.set_status("未检测到 ffmpeg")
        if messagebox.askyesno(
            "需要 ffmpeg",
            "本工具需要 ffmpeg 才能处理视频,当前未检测到。\n\n"
            "是否现在自动下载?(约 90MB,仅首次需要,\n"
            "下载的是官方精简版 ffmpeg-release-essentials)\n\n"
            "选择“否”可稍后自行安装: %s" % FFMPEG_MANUAL_URL,
        ):
            self.start_download()
        else:
            messagebox.showinfo(
                "手动安装指引",
                "两种方式任选其一:\n\n"
                "1. 把 ffmpeg.exe / ffprobe.exe / ffplay.exe 放入:\n"
                "   %s\n\n"
                "2. 安装 ffmpeg 并加入系统 PATH,重启工具即可。\n"
                "   下载地址: %s" % (os.path.join(FFMPEG_DIR, "bin"), FFMPEG_MANUAL_URL),
            )

    def extract_bundle(self):
        """从内置 zip 提取 ffmpeg/ffprobe/ffplay 到 exe 目录旁(带进度窗口)。
        解压目标不可写时回退到临时目录。"""
        bundle = bundle_zip()
        dst = os.path.join(FFMPEG_DIR, "bin")
        try:
            os.makedirs(dst, exist_ok=True)
            os.path.isfile(os.path.join(dst, "ffmpeg.exe"))  # 探测可写性
        except OSError:
            dst = os.path.join(tempfile.gettempdir(), "wb_ffmpeg", "bin")
            os.makedirs(dst, exist_ok=True)

        win = Toplevel(self)
        win.title("正在准备 ffmpeg…")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=COL_PANEL)
        win.lift()
        win.focus_force()
        win.grab_set()
        f = Frame(win, bg=COL_PANEL, padx=20, pady=14)
        f.pack()
        Label(f, text="首次启动,正在解压内置 ffmpeg(约 440MB)…",
              font=self.font_small, bg=COL_PANEL, fg=COL_TEXT).pack()
        bar = ttk_progressbar(f, length=420, maximum=100)
        bar.pack(pady=(10, 4))
        lbl = Label(f, text="0%", font=self.font_small, bg=COL_PANEL, fg=COL_TEXT_DIM)
        lbl.pack()
        q = queue.Queue()

        def worker():
            try:
                z = zipfile.ZipFile(bundle)
                wanted = {}
                total_bytes = 0
                for n in z.namelist():
                    base = os.path.basename(n.replace("\\", "/"))
                    if base in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
                        wanted[base] = n
                        total_bytes += z.getinfo(n).file_size
                if not wanted:
                    raise RuntimeError("内置包中未找到 ffmpeg 组件")
                done = 0
                for base, entry in wanted.items():
                    info = z.getinfo(entry)
                    target = os.path.join(dst, base)
                    tmp = target + ".part"
                    with z.open(entry) as src, open(tmp, "wb") as fh:
                        while True:
                            chunk = src.read(1 << 20)
                            if not chunk:
                                break
                            fh.write(chunk)
                            done += len(chunk)
                            q.put(("prog", done / total_bytes * 100))
                    if os.path.exists(target):
                        os.remove(target)
                    os.replace(tmp, target)
                z.close()
                q.put(("done", None))
            except Exception as e:
                q.put(("done", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    kind, val = q.get_nowait()
                    if kind == "prog":
                        bar["value"] = val
                        lbl.config(text="%.1f%%" % val)
                    elif kind == "done":
                        if val is None:
                            win.destroy()
                            self.ffdir = dst
                            self.ffapi = FFmpegAPI(dst)
                            self.set_status("ffmpeg 就绪 ✓ (内置,已解压)")
                            self.log("内置 ffmpeg 已解压到 %s" % dst)
                        else:
                            win.destroy()
                            self.set_status("内置 ffmpeg 解压失败")
                            self.log("内置 ffmpeg 解压失败: %s" % val)
                            messagebox.showerror(
                                "解压失败", "内置 ffmpeg 解压失败:\n%s\n\n"
                                "可手动下载后放入:\n%s" % (val, os.path.join(FFMPEG_DIR, "bin")))
                        return
            except queue.Empty:
                pass
            self.after(80, poll)

        poll()

    def start_download(self):
        win = Toplevel(self)
        win.title("下载 ffmpeg…")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=COL_PANEL)
        win.lift()
        win.focus_force()
        win.grab_set()
        f = Frame(win, bg=COL_PANEL, padx=20, pady=14)
        f.pack()
        Label(f, text="正在下载 ffmpeg(约 90MB,首次只需一次)…",
              font=self.font_small, bg=COL_PANEL, fg=COL_TEXT).pack()
        bar = ttk_progressbar(f, length=420, maximum=100)
        bar.pack(pady=(10, 4))
        lbl = Label(f, text="0%", font=self.font_small, bg=COL_PANEL, fg=COL_TEXT_DIM)
        lbl.pack()
        q = queue.Queue()
        err = {}

        def worker():
            try:
                tmp_zip = os.path.join(tempfile.gettempdir(), "wb_ffmpeg_dl.zip")
                if os.path.isfile(tmp_zip):
                    os.remove(tmp_zip)
                start = time.time()

                def hook(block_num, block_sz, total):
                    if total > 0:
                        q.put(("prog", block_num * block_sz / total * 100))
                    else:
                        q.put(("prog", 0))
                req = urllib.request.Request(FFMPEG_ZIP_URL, headers={
                    "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_zip, "wb") as fh:
                    total = int(resp.headers.get("Content-Length") or 0)
                    got = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        if total:
                            q.put(("prog", got / total * 100))
                    q.put(("prog", 100))
                # 解压
                q.put(("msg", "解压中…"))
                outdir = os.path.join(FFMPEG_DIR, "_tmp")
                if os.path.isdir(outdir):
                    shutil.rmtree(outdir, ignore_errors=True)
                os.makedirs(outdir, exist_ok=True)
                with zipfile.ZipFile(tmp_zip) as z:
                    z.extractall(outdir)
                binds = glob.glob(os.path.join(outdir, "**", "bin"), recursive=True)
                src = None
                for b in binds:
                    if os.path.isfile(os.path.join(b, "ffmpeg.exe")):
                        src = b
                        break
                if not src:
                    raise RuntimeError("解压后未找到 ffmpeg.exe")
                dst = os.path.join(FFMPEG_DIR, "bin")
                os.makedirs(dst, exist_ok=True)
                for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
                    shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
                shutil.rmtree(outdir, ignore_errors=True)
                q.put(("done", None))
            except Exception as e:
                q.put(("done", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    kind, val = q.get_nowait()
                    if kind == "prog":
                        bar["value"] = val
                        lbl.config(text="%.1f%%" % val)
                    elif kind == "msg":
                        lbl.config(text=val)
                    elif kind == "done":
                        if val is None:
                            win.destroy()
                            self.ffdir = os.path.join(FFMPEG_DIR, "bin")
                            self.ffapi = FFmpegAPI(self.ffdir)
                            self.set_status("ffmpeg 下载并安装成功 ✓")
                            self.log("ffmpeg 已安装到 %s" % self.ffdir)
                            messagebox.showinfo("完成", "ffmpeg 已就绪,可以开始使用了。")
                        else:
                            win.destroy()
                            self.set_status("ffmpeg 下载失败")
                            self.log("下载失败: %s" % val)
                            messagebox.showerror(
                                "下载失败", "自动下载失败:\n%s\n\n可手动下载后放入:\n%s"
                                % (val, os.path.join(FFMPEG_DIR, "bin")))
                        return
            except queue.Empty:
                pass
            self.after(80, poll)

        poll()

    # ================= 视频加载 =================
    def open_video(self):
        d0 = self.cfg.get("last_open_dir") or ""
        path = filedialog.askopenfilename(
            title="选择视频文件",
            initialdir=d0 if os.path.isdir(d0) else None,
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.ts *.m2ts "
                            "*.webm *.m4v *.mpg *.mpeg *.rmvb"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.load_video(path)

    def load_video(self, path, keep_segments=False, done_cb=None):
        """打开视频。ffprobe 在后台线程解析,界面不卡;完成回调可传 done_cb。"""
        if not self.ffapi:
            messagebox.showwarning("提示", "ffmpeg 尚未就绪,请先完成安装。")
            return
        self._load_gen += 1
        gen = self._load_gen
        self.set_status("正在解析视频信息…")
        self.cv.delete("hint")
        self.cv.create_text(
            max(self.cv.winfo_width(), PREVIEW_MIN_W) // 2,
            max(self.cv.winfo_height(), 200) // 2,
            text="正在解析视频信息…", fill=COL_TEXT_FAINT,
            font=self.font_info, anchor="center")
        self.update_idletasks()
        ff = self.ffapi
        q = queue.Queue()

        def worker():
            try:
                q.put(("ok", ff.probe(path)))
            except Exception as e:
                q.put(("err", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            if gen != self._load_gen:
                return  # 期间又打开了别的视频,本次作废
            try:
                kind, info = q.get_nowait()
            except queue.Empty:
                self.after(50, poll)
                return
            if kind == "err":
                self.set_status("")
                messagebox.showerror("无法打开视频", "解析进程异常:\n%s" % info)
                return
            self._finish_load(path, info, keep_segments, done_cb)

        self.after(50, poll)

    def _finish_load(self, path, info, keep_segments, done_cb=None):
        if info is None:
            self.set_status("")
            why = (self.ffapi.last_err or "").strip() or "未知原因"
            messagebox.showerror(
                "无法打开视频",
                "视频解析失败(视频文件本身大概率是好的,问题出在 ffprobe):\n\n%s\n\n"
                "ffprobe 返回:\n%s\n\n"
                "提示:若 ffmpeg 是手动放置的,请确认 ffmpeg.exe/ffprobe.exe 完整有效;\n"
                "重新打开本工具会自动修复。" % (path, why[:600]))
            return
        self.video = path
        self.info = info
        self.duration = info["duration"]
        self.start_t = 0.0
        self.end_t = info["duration"]
        self.playhead_t = 0.0
        self.frame_cache = {}
        if not keep_segments:
            self.segments = []
            self.refresh_segment_list()
        self._preview_gen += 1
        # 切换视频:重置预览缩放状态
        self.preview_zoom = 1.0
        self._pv_img = None
        self._pv_center = None
        if self._preview_after:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        # 标题
        self.title("无损视频剪辑 · %s" % info["filename"])
        self._update_info_bar()
        # 按钮
        for b in self._video_btns:
            self._set_btn_enabled(b, True)
        self.cv.delete("hint")
        self.timeline.draw()
        self.update_range_label()
        self.set_status("%s ✓" % info["filename"])
        self.log("已打开视频: %s (%.3f 秒)" % (info["filename"], self.duration))
        if not info.get("has_video"):
            messagebox.showinfo("提示", "该文件没有视频流(纯音频),只能进行区间裁剪/合并,无画面预览。")
        # 记忆目录
        self.cfg["last_open_dir"] = os.path.dirname(path)
        save_config(self.cfg)
        self.refresh_preview_now()
        if done_cb:
            done_cb()

    def _update_info_bar(self):
        """①:在信息条显示 分辨率/编码/帧率/像素格式/音频/大小"""
        info = self.info
        if not info:
            self.lbl_vname.config(text="未加载视频", fg=COL_TEXT_DIM)
            self.lbl_vmeta.config(text="")
            return
        v = info.get("video") or {}
        a = info.get("audio") or {}
        size_mb = 0.0
        try:
            size_mb = os.path.getsize(info["path"]) / 1048576.0
        except OSError:
            pass
        fps = fps_value(v.get("fps"))
        fps_txt = ("%.3f fps" % fps) if fps else "帧率未知"
        self.lbl_vname.config(
            text="%s    ·    大小 %.1f MB    ·    时长 %s"
                 % (info["filename"], size_mb, fmt_short(self.duration)),
            fg=COL_TEXT)
        parts = []
        if v:
            parts.append("%d×%d" % (v.get("width") or 0, v.get("height") or 0))
            parts.append(v.get("codec") or "?")
            parts.append(fps_txt)
            if v.get("pix_fmt"):
                parts.append(v.get("pix_fmt"))
        if a:
            parts.append("音频 %s %sHz %s声道"
                         % (a.get("codec"), a.get("sample_rate"), a.get("channels")))
        if not parts:
            parts.append("纯音频文件,无视频流")
        self.lbl_vmeta.config(text="  ·  ".join(parts), fg=COL_TEXT_DIM)

    def reset_view(self):
        if self.duration > 0:
            self.start_t = 0.0
            self.end_t = self.duration
            self.timeline.draw()
            self.update_range_label()

    def set_start(self):
        self.start_t = min(self.playhead_t, self.end_t)
        self.timeline.draw()
        self.update_range_label()
        self.refresh_preview_now()

    def set_end(self):
        self.end_t = max(self.playhead_t, self.start_t)
        self.timeline.draw()
        self.update_range_label()
        self.refresh_preview_now()

    def update_range_label(self):
        self.lbl_range.config(
            text="区间: %s → %s (全长 %s)"
            % (fmt_time(self.start_t), fmt_time(self.end_t), fmt_short(self.duration)))

    # ================= 预览 =================
    def schedule_preview(self):
        if self._preview_after:
            self.after_cancel(self._preview_after)
        self._preview_after = self.after(160, self.refresh_preview_now)

    def on_playhead_drag(self):
        """v10:拖动播放头时预览实时联动 —— 节流 120ms 立即抽帧,
        其余高频事件合并到防抖刷新,避免拖动时堆积 ffmpeg 进程"""
        now = time.time()
        if now - self._pv_live_last > 0.12:
            self._pv_live_last = now
            self.refresh_preview_now()
        else:
            self.schedule_preview()

    def refresh_preview_now(self):
        if self._preview_after:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        if not self.video or not self.ffapi or not self.info or not self.info.get("has_video"):
            return
        gen = self._preview_gen
        # v10:最新帧序号 —— 快速拖动时并发抽帧,只显示最新请求的结果
        self._preview_seq += 1
        seq = self._preview_seq
        t = self.playhead_t
        w = max(PREVIEW_MIN_W, self.cv.winfo_width() or PREVIEW_MIN_W)

        def done(img):
            if gen != self._preview_gen or seq != self._preview_seq:
                return  # 已切视频 / 已发起更新的抽帧 → 丢弃过期帧
            self._show_img(img)

        self.grab_async(t, w, done)

    def grab_async(self, t, width, callback):
        """后台抽帧,带缓存。
        跨线程安全:worker 只写队列,主线程 after 轮询再更新 UI。"""
        key = (int(t * 5), width)
        if key in self.frame_cache:
            callback(self.frame_cache[key])
            return
        # 唯一后缀:快速拖动时多个抽帧线程并发,固定文件名会互相覆盖导致错帧
        png = os.path.join(tempfile.gettempdir(),
                           "wb_preview_%d_%d.png"
                           % (os.getpid(), int(time.time() * 1000) % 10**9))
        ff = self.ffapi
        path, t0 = self.video, t
        q = queue.Queue()

        def worker():
            try:
                ok = ff.grab_frame(path, t0, width, png)
            except Exception:
                ok = False
            q.put((ok, png))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                ok, png2 = q.get_nowait()
            except queue.Empty:
                self.after(50, poll)
                return
            self._on_frame(ok, png2, key, callback)

        self.after(50, poll)

    def _on_frame(self, ok, png, key, callback):
        if not ok:
            # v10:抽帧失败时清理残留 png(此前只清理成功路径)
            try:
                os.remove(png)
            except OSError:
                pass
            return
        try:
            from tkinter import PhotoImage
            img = PhotoImage(file=png)
        except Exception:
            img = None
        finally:
            try:
                os.remove(png)
            except OSError:
                pass
        if img is None:
            return
        if len(self.frame_cache) >= CACHE_LIMIT:
            self.frame_cache.pop(next(iter(self.frame_cache)))
        self.frame_cache[key] = img
        callback(img)

    def _show_img(self, img):
        """显示预览帧(原始图),并按当前缩放倍率重绘"""
        self._pv_img = img
        if self._pv_center is None:
            cw = max(self.cv.winfo_width(), PREVIEW_MIN_W)
            ch = max(self.cv.winfo_height(), 200)
            self._pv_center = (cw // 2, ch // 2)
        self._pv_draw()

    def _clamp_pv_center(self, cx, cy, iw, ih):
        """限制画面中心位置:画面比画布小时强制居中;画面大时允许平移但边缘不露空"""
        cw = max(self.cv.winfo_width(), PREVIEW_MIN_W)
        ch = max(self.cv.winfo_height(), 200)
        mx = max(0, iw // 2 - cw // 2)
        my = max(0, ih // 2 - ch // 2)
        return (min(max(cx, cw // 2 - mx), cw // 2 + mx),
                min(max(cy, ch // 2 - my), ch // 2 + my))

    def _pv_draw(self):
        """按 preview_zoom 缩放绘制预览图(中心锚点,信息条显示缩放比例)"""
        img = self._pv_img
        if img is None:
            return
        z = self.preview_zoom
        try:
            if z > 1:
                disp = img.zoom(int(round(z)))
            elif z < 1:
                disp = img.subsample(int(round(1 / z)))
            else:
                disp = img
        except Exception:
            disp = img  # 缩放失败退回原图
        iw, ih = disp.width(), disp.height()
        cx, cy = self._clamp_pv_center(self._pv_center[0], self._pv_center[1],
                                       iw, ih)
        self._pv_center = (cx, cy)
        try:
            # 放大后光标变手形,提示可拖拽平移
            self.cv.config(cursor="fleur" if z > 1 else "")
        except Exception:
            pass
        cw = max(self.cv.winfo_width(), PREVIEW_MIN_W)
        ch = max(self.cv.winfo_height(), 200)
        self.cv.delete("all")
        self.cv.create_image(cx, cy, image=disp, anchor="center")
        self.cv.image = disp  # 保持引用
        # 底部信息条
        bar_h = 32
        self.cv.create_rectangle(0, ch - bar_h, cw, ch, fill=COL_STATUS_BG, outline="")
        zt = "%.0f%%" % (z * 100)
        hint = "滚轮缩放,双击还原,按住拖动平移" if z > 1 else "滚轮缩放,双击还原"
        self.cv.create_text(
            10, ch - bar_h // 2, anchor="w", fill="#cfd6de", font=self.font_small,
            text="播放头 %s   |   区间 %s → %s   |   缩放 %s (%s)"
                 % (fmt_time(self.playhead_t), fmt_time(self.start_t),
                    fmt_time(self.end_t), zt, hint),
        )

    def _on_preview_wheel(self, e):
        """⑥:预览区滚轮缩放,以鼠标位置为中心(保持鼠标下的画面点不动)"""
        if not self._pv_img:
            return
        old = self.preview_zoom
        try:
            idx = self.ZOOM_LEVELS.index(old)
        except ValueError:
            idx = self.ZOOM_LEVELS.index(1.0)
        idx = max(0, min(len(self.ZOOM_LEVELS) - 1,
                         idx + (1 if e.delta > 0 else -1)))
        new = self.ZOOM_LEVELS[idx]
        if new == old:
            return
        cx, cy = self._pv_center or (0, 0)
        cx = e.x - (e.x - cx) * (new / old)
        cy = e.y - (e.y - cy) * (new / old)
        self.preview_zoom = new
        self._pv_center = (cx, cy)
        self._pv_draw()

    def _on_preview_double(self, _e):
        """⑥:双击预览区恢复 100% 缩放并居中"""
        if not self._pv_img:
            return
        self.preview_zoom = 1.0
        cw = max(self.cv.winfo_width(), PREVIEW_MIN_W)
        ch = max(self.cv.winfo_height(), 200)
        self._pv_center = (cw // 2, ch // 2)
        self._pv_draw()

    def _on_pv_press(self, e):
        """放大后按住左键开始拖拽平移预览画面"""
        if self.preview_zoom <= 1 or not self._pv_img:
            return
        self._pv_drag = (e.x, e.y)

    def _on_pv_drag(self, e):
        if getattr(self, "_pv_drag", None) is None or not self._pv_img:
            return
        cx, cy = self._pv_center
        dx, dy = e.x - self._pv_drag[0], e.y - self._pv_drag[1]
        z = self.preview_zoom
        iw = int(self._pv_img.width() * (z if z >= 1 else 1))
        ih = int(self._pv_img.height() * (z if z >= 1 else 1))
        cx, cy = self._clamp_pv_center(cx + dx, cy + dy, iw, ih)
        self._pv_center = (cx, cy)
        self._pv_drag = (e.x, e.y)
        self._pv_draw()

    def _on_pv_release(self, _e):
        self._pv_drag = None

    # ================= 播放预览 =================
    def play_selection(self):
        if not self.ffapi or not self.ffapi.has_ffplay:
            messagebox.showwarning("提示", "当前 ffmpeg 版本未附带 ffplay 播放器,\n无法播放预览。")
            return
        if not self.video:
            return
        # 播放起点取「播放头」与「选区起点」的较大者,并限制不超过选区终点:
        # 播放头在选区内 → 从播放头播;在选区外(更早) → 从选区起点播
        start = max(self.playhead_t, self.start_t)
        start = min(start, self.end_t)
        if self.end_t - start < 0.001:
            return  # 起点已在选区末尾,无内容可播
        cmd = [
            self.ffapi.ffplay,
            "-autoexit", "-window_title", "无损预览",
            "-ss", "%.3f" % start,
            "-t", "%.3f" % (self.end_t - start),
            "-i", self.video,
        ]
        # v10:播放速度(0.5x / 1x / 2x,ffplay 的 -speed 参数)
        try:
            speed = float((self.speed_var.get() or "1x").rstrip("x"))
            if speed > 0 and speed != 1.0:
                cmd = cmd[:1] + ["-speed", "%.2f" % speed] + cmd[1:]
        except (ValueError, AttributeError):
            pass
        subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
        self.log("播放预览: %s → %s (从播放头起, %s)"
                 % (fmt_time(start), fmt_time(self.end_t), self.speed_var.get() or "1x"))

    # ================= 精确设置 =================
    def open_precise_dialog(self):
        dlg = Toplevel(self)
        dlg.title("精确设置起止时间")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.configure(bg=COL_PANEL)
        # v12:确保在最前 + grab 顺序正确
        dlg.lift()
        dlg.focus_force()
        dlg.grab_set()
        f = Frame(dlg, bg=COL_PANEL, padx=16, pady=12)
        f.pack()

        def parse(s):
            s = s.strip().replace("，", ",")
            parts = [p for p in re.split(r"[:,.]", s) if p != ""]
            try:
                if len(parts) == 1:
                    return float(parts[0])
                if len(parts) == 2:
                    return int(parts[0]) * 60 + float(parts[1])
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                if len(parts) == 4:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + float(parts[3]) / 1000
            except ValueError:
                return None
            return None

        Label(f, text="格式: 小时:分钟:秒.毫秒 或 秒 (如 00:01:05.500 / 65.5)",
              font=self.font_small, bg=COL_PANEL, fg=COL_TEXT_DIM).pack(anchor="w")
        rows = Frame(f, bg=COL_PANEL)
        rows.pack(pady=(8, 0))
        Label(rows, text="起点:", bg=COL_PANEL, fg=COL_TEXT,
              font=self.font_small).grid(row=0, column=0, sticky="e", pady=3)
        e_s = Entry(rows, width=16, bg=COL_ENTRY, fg=COL_TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=COL_BORDER,
                    insertbackground=COL_TEXT, font=self.font_mono)
        e_s.grid(row=0, column=1, padx=6, pady=3)
        e_s.insert(0, fmt_time(self.start_t))
        Label(rows, text="终点:", bg=COL_PANEL, fg=COL_TEXT,
              font=self.font_small).grid(row=1, column=0, sticky="e", pady=3)
        e_e = Entry(rows, width=16, bg=COL_ENTRY, fg=COL_TEXT, relief="flat",
                    highlightthickness=1, highlightbackground=COL_BORDER,
                    insertbackground=COL_TEXT, font=self.font_mono)
        e_e.grid(row=1, column=1, padx=6, pady=3)
        e_e.insert(0, fmt_time(self.end_t))
        err = Label(f, text="", bg=COL_PANEL, fg="#ff6b6b", font=self.font_small)
        err.pack(anchor="w")

        def ok():
            s = parse(e_s.get())
            e = parse(e_e.get())
            if s is None or e is None or s < 0 or e <= s or e > self.duration + 0.001:
                err.config(text="时间格式错误或超出范围 (0 ~ %s)"
                           % fmt_time(self.duration))
                return
            self.start_t = s
            self.end_t = e
            self.playhead_t = s
            dlg.destroy()
            self.timeline.draw()
            self.update_range_label()
            self.refresh_preview_now()

        btns = Frame(f, bg=COL_PANEL)
        btns.pack(pady=(8, 0))
        self._btn(btns, "确定", ok, accent=True, register=False).pack(side="left")
        self._btn(btns, "取消", dlg.destroy, register=False).pack(side="left", padx=(8, 0))
        e_s.focus_set()
        e_s.select_range(0, "end")

    # ================= 片段列表 =================
    def add_segment(self):
        if not self.video or self.end_t - self.start_t < 0.001:
            return
        dur = self.end_t - self.start_t
        self.segments.append({
            "file": self.video,
            "start": self.start_t,
            "end": self.end_t,
            "label": "%s → %s (时长 %s)  %s" % (
                fmt_short(self.start_t), fmt_short(self.end_t),
                fmt_short(dur), os.path.basename(self.video)),
        })
        self.refresh_segment_list()
        self.log("已添加片段: %s → %s" % (fmt_time(self.start_t), fmt_time(self.end_t)))

    def del_segment(self):
        """v12:支持多选批量删除(从后往前删,避免索引偏移)"""
        sel = self.lst.curselection()
        if not sel:
            return
        for idx in sorted(sel, reverse=True):
            if 0 <= idx < len(self.segments):
                del self.segments[idx]
        self.refresh_segment_list()
        if sel:
            self.log("删除 %d 个片段" % len(sel))

    def move_segment(self, d):
        """v12:支持多选块移动(保持选中项的相对顺序)"""
        def _do():
            sel = self.lst.curselection()
            if not sel:
                return
            sel = sorted(sel)
            # 块移动:整个选中块向上/下移一位
            if d < 0:
                # 上移:检查块首是否已在顶部
                if sel[0] == 0:
                    return
                # 交换块首前一个与整个块
                for i in sel:
                    j = i + d
                    if 0 <= j < len(self.segments):
                        self.segments[i], self.segments[j] = self.segments[j], self.segments[i]
                new_sel = [i + d for i in sel]
            else:
                # 下移:检查块尾是否已在底部
                if sel[-1] >= len(self.segments) - 1:
                    return
                # 从后往前交换
                for i in reversed(sel):
                    j = i + d
                    if 0 <= j < len(self.segments):
                        self.segments[i], self.segments[j] = self.segments[j], self.segments[i]
                new_sel = [i + d for i in sel]
            self.refresh_segment_list()
            # 重新选中移动后的块
            for i in new_sel:
                self.lst.selection_set(i)
        return _do

    # ---- v11:片段列表拖拽排序(v12:支持多选拖拽) ----
    def _on_drag_start(self, e):
        """鼠标按下:记录起始索引,标记待判定(移动超过阈值才算拖拽)"""
        idx = self.lst.nearest(e.y)
        if idx < 0 or idx >= len(self.segments):
            self._drag_start_idx = None
            self._drag_pending = False
            return
        self._drag_start_idx = idx
        self._drag_pending = True

    def _on_drag_motion(self, e):
        """鼠标移动:超过 4px 才判定为拖拽(区分单击选中);拖拽时高亮目标位置"""
        if not self._drag_pending or self._drag_start_idx is None:
            return
        target = self.lst.nearest(e.y)
        if target < 0 or target >= len(self.segments):
            return
        # 移动时高亮目标行(视觉反馈)
        if target != self._drag_start_idx:
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(target)
            self.lst.activate(target)

    def _on_drag_release(self, e):
        """鼠标释放:若判定为拖拽则重排 segments 列表(v12:支持多选块拖拽)"""
        if not self._drag_pending or self._drag_start_idx is None:
            return
        target = self.lst.nearest(e.y)
        self._drag_pending = False
        src = self._drag_start_idx
        self._drag_start_idx = None
        if target < 0 or target >= len(self.segments) or target == src:
            return
        # 取出源片段,插入到目标位置
        seg = self.segments.pop(src)
        self.segments.insert(target, seg)
        self.refresh_segment_list(target)
        self.log("片段排序调整: #%d → #%d" % (src + 1, target + 1))

    def clear_segments(self):
        self.segments = []
        self.refresh_segment_list()

    def refresh_segment_list(self, select=None):
        self.lst.delete(0, "end")
        for i, seg in enumerate(self.segments):
            txt = "#%02d  %s" % (i + 1, seg["label"])
            # 长文件名截断,避免撑破右侧面板
            if len(txt) > 58:
                txt = txt[:57] + "…"
            self.lst.insert("end", txt)
        if select is not None:
            self.lst.selection_set(select)
        # 提示双击跳转
        if self.lst.size() > 0:
            self.lst.config(fg="#d0d0d0")
        else:
            self.lst.config(fg=COL_TEXT_FAINT)

    def on_seg_double(self, _e):
        """③:双击片段条目 → 自动加载该片段源视频并定位到区间,便于微调"""
        sel = self.lst.curselection()
        if not sel:
            return
        seg = self.segments[sel[0]]
        f = seg["file"]
        if not os.path.isfile(f):
            messagebox.showwarning("提示", "片段源文件已不存在:\n%s" % f)
            return
        if self.video != f:
            # 异步加载:加载完成后自动定位(保留片段列表)
            self.load_video(f, keep_segments=True,
                            done_cb=lambda: self._goto_seg(seg, sel[0]))
        else:
            self._goto_seg(seg, sel[0])

    # ---- v10:片段列表右键菜单 ----
    def _on_seg_menu(self, e):
        """右键片段:弹出 删除/重命名/上移/下移/定位 菜单"""
        sel = self.lst.nearest(e.y)
        if not self.segments or sel < 0 or sel >= len(self.segments):
            return
        # 让菜单落在被右键的条目上
        if sel not in self.lst.curselection():
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(sel)
        menu = self._build_seg_menu(sel)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _build_seg_menu(self, sel):
        """构造片段右键菜单(v12:多选时显示批量操作)"""
        menu = Menu(self.lst, tearoff=False, bg=COL_PANEL, fg=COL_TEXT,
                    activebackground=COL_ACCENT, activeforeground="#ffffff",
                    relief="flat", bd=0, font=self.font_small)
        cur_sel = self.lst.curselection()
        multi = len(cur_sel) > 1
        if not multi:
            menu.add_command(label="▶ 定位到该片段", command=lambda: self.on_seg_double(None))
            menu.add_command(label="✎ 重命名", command=lambda: self._rename_segment(sel))
            menu.add_separator()
        menu.add_command(label="↑ 上移", command=lambda: self.move_segment(-1)())
        menu.add_command(label="↓ 下移", command=lambda: self.move_segment(1)())
        if multi:
            menu.add_command(label="－ 删除选中 (%d)" % len(cur_sel),
                             command=self.del_segment)
        else:
            menu.add_command(label="－ 删除片段", command=lambda: self._del_segment_idx(sel))
        menu.add_separator()
        menu.add_command(label="清空列表", command=self.clear_segments)
        return menu

    def _del_segment_idx(self, idx):
        if 0 <= idx < len(self.segments):
            del self.segments[idx]
            self.refresh_segment_list()

    def _rename_segment(self, idx):
        """重命名片段(改显示名称,不影响导出内容)"""
        if not (0 <= idx < len(self.segments)):
            return
        dlg = Toplevel(self)
        dlg.title("重命名片段")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.configure(bg=COL_PANEL)
        dlg.lift()
        dlg.focus_force()
        dlg.grab_set()
        f = Frame(dlg, bg=COL_PANEL, padx=16, pady=12)
        f.pack()
        Label(f, text="输入片段显示名称(仅列表显示用,不影响导出):",
              font=self.font_small, bg=COL_PANEL, fg=COL_TEXT_DIM).pack(anchor="w")
        e = Entry(f, width=40, bg=COL_ENTRY, fg=COL_TEXT, relief="flat",
                  highlightthickness=1, highlightbackground=COL_BORDER,
                  insertbackground=COL_TEXT, font=self.font_small)
        e.pack(pady=(8, 0))
        cur = self.segments[idx].get("name", "")
        e.insert(0, cur or self.segments[idx]["label"])

        def ok():
            v = e.get().strip()
            if v:
                self.segments[idx]["name"] = v
                self.segments[idx]["label"] = "%s → %s (时长 %s)  %s" % (
                    fmt_short(self.segments[idx]["start"]),
                    fmt_short(self.segments[idx]["end"]),
                    fmt_short(self.segments[idx]["end"] - self.segments[idx]["start"]),
                    v)
                self.refresh_segment_list(idx)
                self.log("已重命名片段 #%02d: %s" % (idx + 1, v))
            dlg.destroy()

        btns = Frame(f, bg=COL_PANEL)
        btns.pack(pady=(10, 0))
        self._btn(btns, "确定", ok, accent=True, register=False).pack(side="left")
        self._btn(btns, "取消", dlg.destroy, register=False).pack(
            side="left", padx=(8, 0))
        e.focus_set()
        e.select_range(0, "end")

    def _goto_seg(self, seg, idx):
        """把播放头/区间定位到某个片段(视频已加载完成后调用)"""
        self.start_t = seg["start"]
        self.end_t = seg["end"]
        self.playhead_t = seg["start"]
        self.timeline.draw()
        self.update_range_label()
        self.refresh_preview_now()
        self.log("已定位到片段 #%02d: %s → %s (%s)"
                 % (idx + 1, fmt_time(seg["start"]), fmt_time(seg["end"]),
                    os.path.basename(seg["file"])))

    # ================= 导出 =================
    def _source_ext(self):
        """导出默认扩展名:跟源文件,避免 mkv/avi 等源(PCM/ass/vp9)强转 mp4 失败"""
        src = self.video
        if not src and self.segments:
            src = self.segments[0]["file"]
        ext = os.path.splitext(src or "")[1].lower()
        ok = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm",
              ".ts", ".flv", ".mpg", ".mpeg", ".wmv", ".m2ts")
        return ext if ext in ok else ".mp4"

    def _default_name(self, suffix):
        base = os.path.splitext(os.path.basename(self.video or "output"))[0]
        ts = time.strftime("%Y%m%d_%H%M%S")
        return "%s_%s_%s%s" % (base, suffix, ts, self._source_ext())

    def _ask_output(self, title, init):
        d0 = self.cfg.get("last_export_dir") or ""
        ext = self._source_ext()
        return filedialog.asksaveasfilename(
            title=title,
            initialdir=d0 if os.path.isdir(d0) else None,
            initialfile=init,
            defaultextension=ext,
            filetypes=[("%s 视频" % ext.upper().lstrip("."), "*%s" % ext),
                       ("MP4 视频", "*.mp4"), ("所有文件", "*.*")],
        )

    def _build_cut_cmd(self, src, s, e, out):
        if self.precise_var.get():
            # 精确到帧:-ss 放在 -i 之后 → 慢 seek(先解码到目标帧),帧级精确
            return [
                self.ffapi.ffmpeg, "-y", "-loglevel", "error", "-stats",
                "-i", src,
                "-ss", "%.3f" % s, "-to", "%.3f" % e,
                "-map", "0", "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                out,
            ]
        # 快速模式:-ss 放在 -i 之前 → 快 seek(定位到最近关键帧,毫秒级误差,通常视觉无感)
        return [
            self.ffapi.ffmpeg, "-y", "-loglevel", "error", "-stats",
            "-ss", "%.3f" % s, "-to", "%.3f" % e,
            "-i", src,
            "-map", "0", "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            out,
        ]

    def export_cut(self):
        if not self.video:
            return
        if self.busy:
            return  # v12:防重复点击
        if self.end_t - self.start_t < 0.001:
            messagebox.showwarning("提示", "起点与终点之间没有内容。")
            return
        out = self._ask_output("导出裁剪结果", self._default_name("cut"))
        if not out:
            return
        cmd = self._build_cut_cmd(self.video, self.start_t, self.end_t, out)
        self._run_job(cmd, "裁剪导出", out, "裁剪完成",
                      total=self.end_t - self.start_t)

    def export_merge(self):
        if self.busy:
            return  # v12:防重复点击
        if len(self.segments) < 2:
            messagebox.showwarning("提示", "请先添加至少 2 个片段到列表。")
            return
        files = set(s["file"] for s in self.segments)
        if len(files) > 1:
            ok, why = self.ffapi.segments_compatible(self.segments)
            if not ok:
                if not messagebox.askyesno("参数不一致", "%s\n\n仍要尝试无损拼接吗?\n"
                                           "(可能失败;失败时可改为重编码合并)" % why):
                    return
        out = self._ask_output("导出合并结果", self._default_name("merge"))
        if not out:
            return
        # 生成 concat list
        tmp = tempfile.mkdtemp(prefix="wb_concat_")
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for s in self.segments:
                p = s["file"].replace("\\", "/").replace("'", "'\\''")
                fh.write("file '%s'\n" % p)
                fh.write("inpoint %.3f\noutpoint %.3f\n" % (s["start"], s["end"]))
        cmd = [
            self.ffapi.ffmpeg, "-y", "-loglevel", "error", "-stats",
            "-f", "concat", "-safe", "0",
            "-i", lst,
            "-map", "0", "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            out,
        ]
        self._run_job(cmd, "合并导出", out, "合并完成", cleanup=tmp,
                      total=sum(s["end"] - s["start"] for s in self.segments))

    # ================= v10:GIF / 纯音频 MP3 导出(重编码) =================
    # v11:导出前弹窗选择质量档位(低/中/高)
    GIF_PRESETS = [
        # (label, fps, width)
        ("低 (10fps · 480px)",  10, 480),
        ("中 (15fps · 640px)",  15, 640),
        ("高 (24fps · 800px)",  24, 800),
    ]
    MP3_PRESETS = [
        # (label, -q:a value)  VBR: q6≈128k  q4≈190k  q2≈220k  q0≈245k
        ("低 (VBR q6 ≈128kbps)", 6),
        ("中 (VBR q4 ≈190kbps)", 4),
        ("高 (VBR q2 ≈220kbps)", 2),
    ]

    def _quality_dialog(self, title, presets, cfg_key=None):
        """通用质量选择弹窗:返回选中项的索引(0-based),取消返回 -1。
        presets = [(label, *params), ...]
        cfg_key: 若提供,从 self.cfg 读取/保存上次选择(导出参数记忆)"""
        # v12:导出参数记忆 — 从 config 读取上次选择
        default_idx = 1  # 默认"中"
        if cfg_key and cfg_key in self.cfg:
            try:
                default_idx = int(self.cfg[cfg_key])
                if not (0 <= default_idx < len(presets)):
                    default_idx = 1
            except (ValueError, TypeError):
                default_idx = 1
        result = [-1]
        win = Toplevel(self)
        win.title(title)
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=COL_PANEL)
        # v12:确保窗口在最前(防止被主窗口遮挡导致"假卡死")
        win.lift()
        win.focus_force()
        Label(win, text=title, font=self.font_info, bg=COL_PANEL,
              fg="#ffffff", anchor="w").pack(fill="x", padx=20, pady=(16, 8))
        sep = Frame(win, bg=COL_BORDER, height=1)
        sep.pack(fill="x", padx=16)
        f = Frame(win, bg=COL_PANEL, padx=20, pady=12)
        f.pack()

        def choose(idx):
            result[0] = idx
            # v12:保存到 config(导出参数记忆)
            if cfg_key:
                self.cfg[cfg_key] = idx
                save_config(self.cfg)
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        for i, (label, *_) in enumerate(presets):
            # 当前选中项前加 ● 标记
            display = ("● " + label) if i == default_idx else ("  " + label)
            b = self._btn(f, display, lambda n=i: choose(n), width=24,
                          register=False)
            b.pack(fill="x", pady=3)
        cancel = self._btn(f, "取消", lambda: (win.grab_release(), win.destroy()),
                           register=False, width=24)
        cancel.pack(fill="x", pady=(8, 0))
        win.grab_set()
        # v12:超时保底(5 分钟未操作自动关闭,防止 grab 永久占用)
        win.after(300000, lambda: (win.grab_release(), win.destroy())
                  if win.winfo_exists() else None)
        self.wait_window(win)
        return result[0] if result[0] >= 0 else default_idx

    def _build_gif_cmd(self, src, s, e, out, width=640, fps=15):
        """palette 两遍法导出 GIF:先统计调色板再用,质量好、体积可控"""
        vf = ("fps=%d,scale=%d:-1:flags=lanczos,"
              "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
              % (fps, width))
        return [
            self.ffapi.ffmpeg, "-y", "-loglevel", "error", "-stats",
            "-ss", "%.3f" % s, "-to", "%.3f" % e,
            "-i", src,
            "-vf", vf,
            "-loop", "0",
            out,
        ]

    def export_gif(self):
        if not self.video:
            return
        if self.busy:
            return  # v12:防重复点击
        if self.end_t - self.start_t < 0.001:
            messagebox.showwarning("提示", "起点与终点之间没有内容。")
            return
        # v11:先选质量档位,再选保存路径;v12:记忆上次选择
        idx = self._quality_dialog("GIF 导出质量", self.GIF_PRESETS,
                                   cfg_key="gif_quality")
        if idx < 0:
            return
        _, fps, width = self.GIF_PRESETS[idx]
        d0 = self.cfg.get("last_export_dir") or ""
        base = os.path.splitext(os.path.basename(self.video))[0]
        init = "%s_gif_%s.gif" % (base, time.strftime("%Y%m%d_%H%M%S"))
        out = filedialog.asksaveasfilename(
            title="导出 GIF 动图",
            initialdir=d0 if os.path.isdir(d0) else None,
            initialfile=init, defaultextension=".gif",
            filetypes=[("GIF 动图", "*.gif"), ("所有文件", "*.*")])
        if not out:
            return
        cmd = self._build_gif_cmd(self.video, self.start_t, self.end_t, out,
                                  width=width, fps=fps)
        self._run_job(cmd, "GIF 导出", out, "GIF 导出完成",
                      total=self.end_t - self.start_t)

    def _ensure_mp3_encoder(self):
        """探测可用的 MP3 编码器:libmp3lame 优先,没有则回退原生 mp3"""
        if self._mp3_enc:
            return self._mp3_enc
        enc = "libmp3lame"
        try:
            rc, out, err = run_capture(
                [self.ffapi.ffmpeg, "-hide_banner", "-encoders"], timeout=15)
            text = (out or err or b"").decode("utf-8", "replace")
            if "libmp3lame" not in text:
                enc = "mp3"
        except Exception:
            enc = "mp3"
        self._mp3_enc = enc
        return enc

    def _build_audio_cmd(self, src, s, e, out, q_a=2):
        """提取当前区间音频为 MP3(-vn 丢弃视频流,VBR 质量 q_a:0 最好 9 最差)"""
        return [
            self.ffapi.ffmpeg, "-y", "-loglevel", "error", "-stats",
            "-ss", "%.3f" % s, "-to", "%.3f" % e,
            "-i", src,
            "-vn", "-c:a", self._ensure_mp3_encoder(), "-q:a", str(q_a),
            out,
        ]

    def export_audio(self):
        if not self.video:
            return
        if self.busy:
            return  # v12:防重复点击
        if self.end_t - self.start_t < 0.001:
            messagebox.showwarning("提示", "起点与终点之间没有内容。")
            return
        # v11:先选质量档位,再选保存路径;v12:记忆上次选择
        idx = self._quality_dialog("MP3 导出质量", self.MP3_PRESETS,
                                   cfg_key="mp3_quality")
        if idx < 0:
            return
        _, q_a = self.MP3_PRESETS[idx]
        d0 = self.cfg.get("last_export_dir") or ""
        base = os.path.splitext(os.path.basename(self.video))[0]
        init = "%s_audio_%s.mp3" % (base, time.strftime("%Y%m%d_%H%M%S"))
        out = filedialog.asksaveasfilename(
            title="导出纯音频 MP3",
            initialdir=d0 if os.path.isdir(d0) else None,
            initialfile=init, defaultextension=".mp3",
            filetypes=[("MP3 音频", "*.mp3"), ("所有文件", "*.*")])
        if not out:
            return
        cmd = self._build_audio_cmd(self.video, self.start_t, self.end_t, out,
                                    q_a=q_a)
        self._run_job(cmd, "音频导出", out, "音频导出完成",
                      total=self.end_t - self.start_t)

    # ================= 批量导出(②:全部片段分别导出) =================
    def export_all_segments(self):
        """把片段列表里每个片段导出为独立文件(自动命名 xxx_cut_01.mp4…),
        逐个串行执行,支持进度显示与取消。"""
        if self.busy:
            return  # v12:防重复点击
        if not self.segments:
            messagebox.showwarning("提示", "请先添加至少 1 个片段到列表。")
            return
        if not self.ffapi:
            messagebox.showwarning("提示", "ffmpeg 尚未就绪。")
            return
        d0 = self.cfg.get("last_export_dir") or ""
        d = filedialog.askdirectory(
            title="选择批量导出保存目录",
            initialdir=d0 if os.path.isdir(d0) else None)
        if not d:
            return
        base = os.path.splitext(os.path.basename(self.segments[0]["file"]))[0]
        ext = self._source_ext()
        total_dur = sum(max(0.0, s["end"] - s["start"]) for s in self.segments)
        self.busy = True
        self._job_total = total_dur or 1
        self._cancel_flag = False
        self._proc = None
        try:
            self._set_export_enabled(False)
        except Exception:
            pass  # 按钮恢复异常不阻断导出
        self._show_progress(True)
        self.set_status("批量导出中… %d 个片段" % len(self.segments))
        q = queue.Queue()
        self._job = (q, "批量导出", d, "批量导出完成", None)

        def worker():
            done = 0
            fail = []
            base_t = 0.0
            for i, seg in enumerate(self.segments, 1):
                if self._cancel_flag:
                    q.put(("done", done, fail))
                    return
                out = os.path.join(d, "%s_cut_%02d%s" % (base, i, ext))
                cmd = self._build_cut_cmd(seg["file"], seg["start"],
                                          seg["end"], out)
                q.put(("seg", i, len(self.segments), os.path.basename(out)))
                seg_dur = max(0.0, seg["end"] - seg["start"])
                try:
                    p = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=CREATE_NO_WINDOW)
                    self._proc = p
                except Exception as e:
                    _safe_crash("批量导出启动失败", e)
                    fail.append((i, "无法启动 ffmpeg: %s" % e))
                    continue
                tail = []
                last_upd = 0.0
                try:
                    for line in p.stderr:
                        tail.append(line)
                        if len(tail) > 60:
                            tail.pop(0)
                        m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                        if m and time.time() - last_upd > 0.3:
                            last_upd = time.time()
                            sec = (int(m.group(1)) * 3600
                                   + int(m.group(2)) * 60 + float(m.group(3)))
                            q.put(("progress", base_t + min(sec, seg_dur)))
                    rc = p.wait()
                    try:
                        p.stderr.close()
                    except Exception:
                        pass
                except Exception as e:
                    _safe_crash("批量导出过程异常", e)
                    fail.append((i, str(e)))
                    continue
                if rc == 0 and os.path.isfile(out):
                    done += 1
                else:
                    fail.append((i, ("".join(tail[-10:]).strip()
                                     or "ffmpeg 返回码 %d" % rc)[:200]))
                base_t += seg_dur
            q.put(("done", done, fail))

        threading.Thread(target=worker, daemon=True).start()
        self._batch_poll()

    def _batch_poll(self):
        """主线程轮询批量导出队列:更新进度/日志,完成后恢复界面"""
        job = getattr(self, "_job", None)
        if job is None:
            return
        q, label, out, done_msg, cleanup = job
        try:
            while True:
                kind, *vals = q.get_nowait()
                if kind == "progress":
                    pct = min(100.0, vals[0] / max(self._job_total, 0.001) * 100)
                    try:
                        self.prog_bar["value"] = pct
                    except Exception:
                        pass
                    self.set_status("批量导出中… (%.1f%%)" % pct)
                elif kind == "seg":
                    i, n, name = vals
                    self.log("⧉ 片段 %d/%d: %s" % (i, n, name))
                elif kind == "done":
                    self._job = None
                    done, fail = vals
                    self.busy = False
                    self._proc = None
                    self._show_progress(False)
                    self._clean_preview_cache()  # v10:批量导出完成也清理缓存
                    try:
                        self._set_export_enabled(True)
                    except Exception:
                        pass
                    if self._cancel_flag:
                        self.set_status("已取消批量导出(成功 %d 个)" % done)
                        self.log("✕ 已取消批量导出(成功 %d 个)" % done)
                        return
                    msg = "成功导出 %d/%d 个片段" % (done, len(self.segments))
                    self.set_status("✓ %s" % msg)
                    if fail:
                        for i, why in fail[:6]:
                            self.log("  ✗ #%02d: %s" % (i, why))
                            msg += "\n  #%02d: %s" % (i, why[:80])
                        self.log("✓ %s" % msg)
                        messagebox.showwarning("批量导出完成", msg)
                        return
                    self.log("✓ %s → %s" % (msg, out))
                    self.cfg["last_export_dir"] = out
                    save_config(self.cfg)
                    if messagebox.askyesno("完成", "%s\n\n是否打开文件夹?" % msg):
                        self._open_folder(out)
                    return
        except queue.Empty:
            pass
        self.after(200, self._batch_poll)

    def _show_progress(self, show):
        """④:显示/隐藏导出进度条(含取消按钮)"""
        if show:
            self.prog_bar["value"] = 0
            self.prog_bar_frame.pack(fill="x", side="bottom")
            try:
                self.btn_cancel.config(state="normal")
            except Exception:
                pass
        else:
            self.prog_bar_frame.pack_forget()

    def cancel_job(self):
        """④:取消当前导出(kill ffmpeg 进程)"""
        self._cancel_flag = True
        p = getattr(self, "_proc", None)
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass
        try:
            self.btn_cancel.config(state="disabled")
        except Exception:
            pass
        self.set_status("正在取消导出…")

    def _run_job(self, cmd, label, out, done_msg, cleanup=None, total=None,
                 timeout=3600):
        self.busy = True
        self._job_total = total if total and total > 0 else (self.duration or 0)
        self._cancel_flag = False
        self._proc = None
        try:
            self._set_export_enabled(False)
        except Exception:
            pass  # 即使按钮状态异常也绝不阻断导出线程
        self._show_progress(True)
        self.set_status("%s中… %s" % (label, os.path.basename(out)))
        self.log("── %s ──\n命令: %s" % (label, " ".join(cmd)))
        # 跨线程安全:worker 线程绝不直接调用 Tk(会抛 RuntimeError),
        # 只写队列;主线程 _job_poll 轮询后统一更新 UI。
        q = queue.Queue()
        self._job = (q, label, out, done_msg, cleanup)

        def worker():
            try:
                p = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_NO_WINDOW)
                self._proc = p
            except Exception as e:
                # ffmpeg 启动失败(文件被占用/被安全软件拦截/路径失效)
                _safe_crash("导出启动失败", e)
                q.put(("done", -1, "无法启动 ffmpeg 进程:\n%s" % e))
                return
            deadline = time.time() + timeout
            tail = []
            last_update = 0.0
            try:
                for line in p.stderr:
                    if time.time() > deadline:
                        # ffmpeg 挂起(如被安全软件拦截)或输出路径无响应
                        try:
                            p.kill()
                        except Exception:
                            pass
                        q.put(("done", -2,
                               "导出超时(超过 %d 分钟),已强制中止。\n"
                               "ffmpeg 可能被安全软件挂起,或输出目录无响应。"
                               % (timeout // 60)))
                        return
                    tail.append(line)
                    if len(tail) > 60:
                        tail.pop(0)
                    m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                    if m and time.time() - last_update > 0.4:
                        last_update = time.time()
                        sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                        q.put(("progress", sec))
                rc = p.wait()
                try:
                    p.stderr.close()
                except Exception:
                    pass
            except Exception as e:
                _safe_crash("导出过程异常", e)
                q.put(("done", -1, "导出过程异常:\n%s" % e))
                return
            if self._cancel_flag:
                q.put(("done", -3, "用户已取消导出。"))
                return
            q.put(("done", rc, "".join(tail[-40:])))

        threading.Thread(target=worker, daemon=True).start()
        self._job_poll()

    def _job_poll(self):
        """主线程轮询导出队列:更新进度 / 完成后恢复界面(所有 Tk 调用都在主线程)"""
        job = getattr(self, "_job", None)
        if job is None:
            return
        q, label, out, done_msg, cleanup = job
        try:
            while True:
                kind, *vals = q.get_nowait()
                if kind == "progress":
                    sec = vals[0]
                    total = max(self._job_total, 0.001)
                    pct = min(100.0, sec / total * 100)
                    try:
                        self.prog_bar["value"] = pct
                    except Exception:
                        pass
                    self.set_status(
                        "%s中… %s (%.1f%%)"
                        % (label, os.path.basename(out), pct))
                elif kind == "done":
                    self._job = None
                    rc, tail = vals
                    self._job_done(rc, label, out, done_msg, tail, cleanup)
                    return
        except queue.Empty:
            pass
        self.after(200, self._job_poll)

    def _clean_preview_cache(self):
        """v10:导出完成后清理本进程的预览抽帧缓存(残留 png),避免长期积攒垃圾"""
        try:
            pat = os.path.join(tempfile.gettempdir(),
                               "wb_preview_%d_*.png" % os.getpid())
            for f in glob.glob(pat):
                try:
                    os.remove(f)
                except OSError:
                    pass
        except Exception:
            pass

    def _job_done(self, rc, label, out, done_msg, tail, cleanup):
        # 先恢复界面,再清理/弹窗:保证任何失败路径按钮都能恢复
        self.busy = False
        self._proc = None
        self._show_progress(False)
        self._clean_preview_cache()  # v10:导出完成自动清理预览抽帧缓存
        try:
            self._set_export_enabled(True)
        except Exception:
            pass
        if cleanup and os.path.isdir(cleanup):
            shutil.rmtree(cleanup, ignore_errors=True)
        if rc == 0 and os.path.isfile(out):
            # ⑤:完成后弹出「打开文件夹 / 播放预览 / 关闭」
            self.cfg["last_export_dir"] = os.path.dirname(out)
            save_config(self.cfg)
            self.set_status("✓ %s: %s" % (done_msg, os.path.basename(out)))
            self.log("✓ %s: %s (%d MB)"
                     % (done_msg, out, os.path.getsize(out) // 1048576))
            self._show_done_dialog(done_msg, out)
            return
        if rc == -3:
            self.set_status("已取消导出")
            self.log("已取消导出: %s" % os.path.basename(out))
            return
        # 失败:可读化翻译 + 原始输出节选
        self.set_status("✗ %s 失败" % label)
        self.log("✗ %s 失败:\n%s" % (label, tail))
        msg = humanize_ffmpeg_error(tail) or (
            "处理失败,详细原因见下方日志。\n\n%s" % tail[:1500])
        messagebox.showerror("%s失败" % label, msg)

    def _show_done_dialog(self, done_msg, out):
        """⑤:完成弹窗 —— 打开文件夹 / 播放预览 / 关闭 三选一"""
        dlg = Toplevel(self)
        dlg.title("完成")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.configure(bg=COL_PANEL)
        # v12:确保在最前 + grab 顺序正确
        dlg.lift()
        dlg.focus_force()
        dlg.grab_set()
        f = Frame(dlg, bg=COL_PANEL, padx=20, pady=16)
        f.pack()
        Label(f, text="%s ✓" % done_msg, bg=COL_PANEL, fg="#7ed97e",
              font=self.font_info).pack()
        Label(f, text=out, bg=COL_PANEL, fg=COL_TEXT_DIM, font=self.font_small,
              wraplength=460, justify="left").pack(pady=(10, 0))

        def open_folder():
            dlg.destroy()
            self._open_folder(out)

        def play_it():
            dlg.destroy()
            if self.ffapi and self.ffapi.has_ffplay:
                cmd = [self.ffapi.ffplay, "-autoexit", "-window_title",
                       "导出结果预览", "-i", out]
                try:
                    speed = float((self.speed_var.get() or "1x").rstrip("x"))
                    if speed > 0 and speed != 1.0:
                        cmd = cmd[:1] + ["-speed", "%.2f" % speed] + cmd[1:]
                except (ValueError, AttributeError):
                    pass
                subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
            else:
                messagebox.showwarning(
                    "提示", "当前 ffmpeg 版本未附带 ffplay 播放器,\n无法播放预览。")

        btns = Frame(f, bg=COL_PANEL)
        btns.pack(pady=(14, 0))
        self._btn(btns, "📁 打开文件夹", open_folder, accent=True,
                  register=False).pack(side="left")
        self._btn(btns, "▶ 播放预览", play_it, register=False).pack(
            side="left", padx=(8, 0))
        self._btn(btns, "关闭", dlg.destroy, register=False).pack(
            side="left", padx=(8, 0))

    def _open_folder(self, out):
        """在资源管理器中定位输出文件;失败时退回打开所在目录。"""
        try:
            # explorer 的 /select, 参数与路径之间不能有空格,必须拼接为单个参数,
            # 否则 explorer 会忽略选中指令并打开默认位置(表现为"路径打开错误")。
            subprocess.Popen(["explorer", "/select,%s" % out],
                             creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            _safe_crash("打开文件夹失败", e)
            d = os.path.dirname(out)
            if d and os.path.isdir(d):
                try:
                    os.startfile(d)
                except Exception:
                    pass

    def _set_export_enabled(self, en):
        alive = []
        for b in self._btns:
            try:
                if b.winfo_exists():
                    self._set_btn_enabled(b, en)
                    alive.append(b)
            except Exception:
                pass  # 已销毁的 widget:跳过并移出列表
        self._btns = alive


def ttk_progressbar(parent, length, maximum):
    """深色进度条(ttk 的默认样式在深色下也清晰,保持系统组件)"""
    from tkinter import ttk
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    return ttk.Progressbar(parent, length=length, maximum=maximum)


def _argv_video_path(argv):
    """从命令行参数提取第一个存在的视频文件路径(拖到 exe 图标 / 命令行传入)"""
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        if os.path.isfile(a):
            return os.path.abspath(a)
    return None


def main():
    # v10:崩溃自动重启 —— 若以 --watchdog <pid> 启动,则进入守护循环(无 UI)
    if len(sys.argv) >= 3 and sys.argv[1] == "--watchdog":
        try:
            _watchdog_main(int(sys.argv[2]))
        except (ValueError, IndexError):
            pass
        # 守护结束同样走正常清理退出,避免 bootloader 父进程残留
        try:
            sys.exit(0)
        except SystemExit:
            raise
        except Exception:
            pass
        return
    # Windows HiDPI 适配
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    # 打包版把未捕获异常写入 crash.log(便于定位问题)
    if getattr(sys, "frozen", False):
        try:
            import traceback as _tb

            def _hook(et, ev, etb):
                try:
                    with open(os.path.join(APP_DIR, "crash.log"), "a",
                              encoding="utf-8") as f:
                        f.write("".join(_tb.format_exception(et, ev, etb)))
                except Exception:
                    pass
                sys.__excepthook__(et, ev, etb)
            sys.excepthook = _hook
        except Exception:
            pass
    _start_watchdog()  # v10:拉起崩溃守护进程(异常退出自动重启)
    app = App()
    # 拖文件到 exe 图标上启动(命令行传入路径)→ 自动加载
    app._pending_open = _argv_video_path(sys.argv)
    # tkinter 回调异常也写日志
    def _report(exc, val, tb):
        try:
            import traceback as _tb2
            with open(os.path.join(APP_DIR, "crash.log"), "a",
                      encoding="utf-8") as f:
                f.write("".join(_tb2.format_exception(exc, val, tb)))
        except Exception:
            pass
    app.report_callback_exception = _report
    app.mainloop()
    # PyInstaller onefile 打包版在窗口关闭后偶发挂起(源码版无此问题)。
    # 用 sys.exit(0) 走正常清理路径退出,确保 bootloader 父进程同步退出,
    # 避免任务管理器残留进程。窗口与配置已在 _on_close 处理完毕。
    try:
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


if __name__ == "__main__":
    main()
