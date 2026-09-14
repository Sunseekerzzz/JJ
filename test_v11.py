# -*- coding: utf-8 -*-
"""v11 全量测试:拖拽移除 + GIF/MP3 参数可调 + 速度多档位 + 自定义下拉 + 拖拽排序 + UI 回归。
运行:系统 Python 3.12(带 tkinter) python test_v11.py
"""
import os
import sys
import tempfile
import time as _time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lossless_editor as m

PASS = 0
FAIL = 0


def check(group, cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % msg)
    else:
        FAIL += 1
        print("  FAIL  %s" % msg)


def rect(w):
    return (w.winfo_x(), w.winfo_y(), w.winfo_x() + w.winfo_width(),
            w.winfo_y() + w.winfo_height())


def overlap(r1, r2):
    return not (r1[2] <= r2[0] or r2[2] <= r1[0] or r1[3] <= r2[1] or r2[3] <= r1[1])


class V11Tests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        for f in (m.CRASH_MARKER, m.CRASH_COUNT):
            try:
                if os.path.isfile(f):
                    os.remove(f)
            except OSError:
                pass

    # ================= ① 拖拽功能已移除 =================
    def test_drop_removed(self):
        print("\n== 拖拽功能移除验证 ==")
        check("drop", not hasattr(m, "enable_file_drop"), "enable_file_drop 已移除")
        check("drop", not hasattr(m.App, "_on_file_dropped"), "_on_file_dropped 已移除")
        check("drop", not hasattr(m, "_drop_sinks"), "_drop_sinks 已移除")
        check("drop", not hasattr(m, "_drop_sink_cls"), "_drop_sink_cls 已移除")
        # 命令行路径保留
        check("drop", hasattr(m.App, "_try_pending"), "_try_pending 保留(命令行路径)")
        check("drop", hasattr(m, "_argv_video_path"), "_argv_video_path 保留")

    def test_argv_video(self):
        print("\n== 拖到 exe 图标 / 命令行传参 ==")
        fd, tmp = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        try:
            p = m._argv_video_path(["prog", "--watchdog", "123", tmp])
            check("argv", p == os.path.abspath(tmp), "提取存在的视频路径")
            check("argv", m._argv_video_path(["prog"]) is None, "无参数返回 None")
            check("argv", m._argv_video_path(["prog", "-x", "nope.mp4"]) is None,
                  "跳过选项参数")
        finally:
            os.remove(tmp)

    # ================= ② GIF/MP3 参数可调 =================
    def test_gif_presets(self):
        print("\n== GIF 参数预设 ==")
        check("gif", len(m.App.GIF_PRESETS) == 3, "GIF 三档预设")
        labels = [p[0] for p in m.App.GIF_PRESETS]
        check("gif", "低" in labels[0] and "中" in labels[1] and "高" in labels[2],
              "GIF 预设标签: 低/中/高")
        _, fps_lo, w_lo = m.App.GIF_PRESETS[0]
        _, fps_hi, w_hi = m.App.GIF_PRESETS[2]
        check("gif", fps_lo < fps_hi, "低档 fps < 高档 fps")
        check("gif", w_lo < w_hi, "低档宽度 < 高档宽度")
        # _build_gif_cmd 支持自定义 width/fps
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"))
        cmd_lo = m.App._build_gif_cmd(stub, "in.mp4", 1, 5, "out.gif",
                                      width=480, fps=10)
        s_lo = " ".join(cmd_lo)
        check("gif", "fps=10" in s_lo and "scale=480" in s_lo,
              "GIF 低档命令含 fps=10 scale=480")
        cmd_hi = m.App._build_gif_cmd(stub, "in.mp4", 1, 5, "out.gif",
                                      width=800, fps=24)
        s_hi = " ".join(cmd_hi)
        check("gif", "fps=24" in s_hi and "scale=800" in s_hi,
              "GIF 高档命令含 fps=24 scale=800")
        # palette 两遍法保留
        check("gif", "palettegen" in s_lo and "paletteuse" in s_lo,
              "GIF 两遍法保留")

    def test_mp3_presets(self):
        print("\n== MP3 参数预设 ==")
        check("audio", len(m.App.MP3_PRESETS) == 3, "MP3 三档预设")
        labels = [p[0] for p in m.App.MP3_PRESETS]
        check("audio", "低" in labels[0] and "中" in labels[1] and "高" in labels[2],
              "MP3 预设标签: 低/中/高")
        _, q_lo = m.App.MP3_PRESETS[0]
        _, q_hi = m.App.MP3_PRESETS[2]
        check("audio", q_lo > q_hi, "低档 q_a > 高档 q_a (VBR: 数字越大质量越差)")
        # _build_audio_cmd 支持自定义 q_a
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                               _mp3_enc="libmp3lame")
        stub._ensure_mp3_encoder = m.App._ensure_mp3_encoder.__get__(stub)
        cmd_lo = m.App._build_audio_cmd(stub, "in.mp4", 0, 10, "out.mp3", q_a=6)
        s_lo = " ".join(cmd_lo)
        check("audio", "-q:a" in s_lo and "6" in s_lo, "MP3 低档含 -q:a 6")
        cmd_hi = m.App._build_audio_cmd(stub, "in.mp4", 0, 10, "out.mp3", q_a=2)
        s_hi = " ".join(cmd_hi)
        check("audio", "-q:a" in s_hi and "2" in s_hi, "MP3 高档含 -q:a 2")
        check("audio", "-vn" in s_hi and "libmp3lame" in s_hi,
              "MP3 保留 -vn 与编码器名")

    def test_quality_dialog(self):
        print("\n== 质量选择弹窗 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        try:
            check("ui", hasattr(app, "_quality_dialog"), "_quality_dialog 方法存在")
            check("ui", hasattr(app, "GIF_PRESETS"), "GIF_PRESETS 属性存在")
            check("ui", hasattr(app, "MP3_PRESETS"), "MP3_PRESETS 属性存在")
        finally:
            app.destroy()

    # ================= ③ 播放头拖动预览联动(v10 回归) =================
    def test_playhead_live(self):
        print("\n== 播放头拖动预览联动 ==")
        calls = {"refresh": 0, "schedule": 0}
        stub = SimpleNamespace(
            _pv_live_last=0.0,
            refresh_preview_now=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
            schedule_preview=lambda: calls.__setitem__("schedule", calls["schedule"] + 1))
        stub._pv_live_last = _time.time() - 1.0
        m.App.on_playhead_drag(stub)
        check("live", calls["refresh"] == 1, "拖动节流:超过间隔立即抽帧")
        stub._pv_live_last = _time.time()
        m.App.on_playhead_drag(stub)
        check("live", calls["refresh"] == 1 and calls["schedule"] == 1,
              "高频拖动:合并到防抖刷新,不堆积 ffmpeg")
        drags = {"n": 0}

        def _on_pd():
            drags["n"] += 1

        tl = SimpleNamespace(
            drag_mode="play", drag_off=0,
            app=SimpleNamespace(video="x.mp4", playhead_t=50.0,
                                start_t=0.0, end_t=100.0,
                                duration=100.0, on_playhead_drag=_on_pd),
            draw=lambda: None,
            t_to_x=lambda t: t, x_to_t=lambda x: x)
        m.Timeline.on_drag(tl, SimpleNamespace(x=100, x_root=0, y=0))
        check("live", drags["n"] == 1, "拖动播放头触发预览联动回调")
        m.Timeline.on_press(tl, SimpleNamespace(x=25, y=0))
        check("live", drags["n"] == 2 and tl.app.playhead_t == 25,
              "点击时间轴空白处跳转播放头并联动预览")

    # ================= ④ 片段列表右键菜单(v10 回归) =================
    def test_seg_menu(self):
        print("\n== 片段列表右键菜单 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        app.segments = [{"file": "a.mp4", "start": 0, "end": 1,
                         "label": "0:00:00 -> 0:00:01  a.mp4"}]
        app.refresh_segment_list()
        menu = app._build_seg_menu(0)
        labels = []
        for i in range(menu.index("end") + 1):
            try:
                labels.append(menu.entrycget(i, "label"))
            except Exception:
                pass
        for want in ("定位", "重命名", "上移", "下移", "删除", "清空"):
            check("menu", any(want in l for l in labels), "菜单包含「%s」" % want)
        # _del_segment_idx 逻辑
        app2 = SimpleNamespace(segments=[{"label": "x"}, {"label": "y"}],
                               refresh_segment_list=lambda: None)
        m.App._del_segment_idx(app2, 0)
        check("menu", len(app2.segments) == 1 and app2.segments[0]["label"] == "y",
              "_del_segment_idx 删除正确索引")
        app.destroy()

    # ================= ⑤ 导出 GIF/MP3 基础(v10 回归) =================
    def test_gif_basic(self):
        print("\n== 导出 GIF 基础 ==")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"))
        cmd = m.App._build_gif_cmd(stub, "in.mp4", 1.5, 9.5, "out.gif")
        s = " ".join(cmd)
        check("gif", "-ss" in s and "1.500" in s and "-to" in s and "9.500" in s,
              "GIF 命令含起止时间")
        check("gif", "palettegen" in s and "paletteuse" in s,
              "GIF 使用 palettegen/paletteuse 两遍法")
        check("gif", "-loop" in s and "out.gif" in s, "GIF 循环播放 + .gif 输出")
        check("gif", "scale=640" in s and "fps=15" in s, "GIF 默认 15fps 宽 640")

    def test_audio_basic(self):
        print("\n== 导出纯音频 MP3 基础 ==")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                               _mp3_enc=None)
        orig = m.run_capture
        m.run_capture = lambda cmd, timeout=60: (0, b"libmp3lame  A       libmp3lame MP3", b"")
        try:
            enc = m.App._ensure_mp3_encoder(stub)
            check("audio", enc == "libmp3lame", "探测到 libmp3lame 时优先使用")
        finally:
            m.run_capture = orig
        stub2 = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                                _mp3_enc=None)
        m.run_capture = lambda cmd, timeout=60: (0, b"no lame here", b"")
        try:
            enc2 = m.App._ensure_mp3_encoder(stub2)
            check("audio", enc2 == "mp3", "无 libmp3lame 时回退原生 mp3")
        finally:
            m.run_capture = orig
        stub3 = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                                _mp3_enc="libmp3lame")
        stub3._ensure_mp3_encoder = m.App._ensure_mp3_encoder.__get__(stub3)
        cmd = m.App._build_audio_cmd(stub3, "in.mkv", 0, 10, "out.mp3")
        s = " ".join(cmd)
        check("audio", "-vn" in s, "音频导出丢弃视频流(-vn)")
        check("audio", "-c:a" in s and "libmp3lame" in s, "音频导出使用 MP3 编码器")
        check("audio", "-q:a" in s and "out.mp3" in s, "音频导出 VBR + .mp3 输出")

    # ================= ⑥ 导出后清理抽帧缓存(v10 回归) =================
    def test_cache_clean(self):
        print("\n== 导出后自动清理抽帧缓存 ==")
        td = tempfile.gettempdir()
        mine = os.path.join(td, "wb_preview_%d_clean_test.png" % os.getpid())
        other = os.path.join(td, "wb_preview_99999999_clean_test.png")
        open(mine, "w").close()
        open(other, "w").close()
        try:
            m.App._clean_preview_cache(SimpleNamespace())
            check("cache", not os.path.isfile(mine), "本进程残留 png 被清理")
            check("cache", os.path.isfile(other), "其他进程的 png 不受影响")
        finally:
            for f in (mine, other):
                try:
                    os.remove(f)
                except OSError:
                    pass
        fake = os.path.join(td, "wb_preview_fail.png")
        open(fake, "w").close()
        try:
            m.App._on_frame(SimpleNamespace(), False, fake, None, None)
            check("cache", not os.path.isfile(fake), "抽帧失败时清理残留 png")
        finally:
            try:
                os.remove(fake)
            except OSError:
                pass

    # ================= ⑦ 播放速度多档位 + 自定义下拉 =================
    def test_speed(self):
        print("\n== 播放速度多档位 ==")
        class FakeSpeed:
            def __init__(self, v):
                self.v = v
            def get(self):
                return self.v

        launched = []
        def fake_popen(cmd, **kw):
            launched.append(cmd)

        orig = m.subprocess.Popen
        m.subprocess.Popen = fake_popen
        try:
            # v11 新增:0.25x / 0.75x / 1.5x
            for speed_str, expected in [("0.25x", "0.25"), ("0.5x", "0.50"),
                                        ("0.75x", "0.75"), ("1.5x", "1.50"),
                                        ("2x", "2.00")]:
                stub = SimpleNamespace(
                    ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                    video="in.mp4", playhead_t=5.0, start_t=2.0, end_t=10.0,
                    speed_var=FakeSpeed(speed_str), log=lambda *a: None)
                m.App.play_selection(stub)
                s = " ".join(launched[-1])
                check("speed", "-speed" in s and expected in s,
                      "%s 速度传入 ffplay" % speed_str)
            # 1x 不加 -speed
            stub1 = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=5.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("1x"), log=lambda *a: None)
            m.App.play_selection(stub1)
            check("speed", "-speed" not in " ".join(launched[-1]),
                  "1x 时不加 -speed(默认)")
            # 回归:播放头在选区前时从选区起点播
            stub4 = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=1.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("1x"), log=lambda *a: None)
            m.App.play_selection(stub4)
            check("speed", "2.000" in " ".join(launched[-1]),
                  "播放头在选区前时从选区起点播(回归)")
        finally:
            m.subprocess.Popen = orig

    def test_speed_dropdown(self):
        print("\n== 速度下拉框样式 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        try:
            # v11:速度下拉是自定义 Button(不是 ttk.Combobox)
            from tkinter import Button
            check("ui", isinstance(app.speed_box, Button),
                  "速度下拉是 Button(不是 Combobox)")
            check("ui", hasattr(app, "SPEED_VALUES"), "SPEED_VALUES 属性存在")
            check("ui", len(app.SPEED_VALUES) == 6, "速度档位 6 个")
            for v in ("0.25x", "0.5x", "0.75x", "1x", "1.5x", "2x"):
                check("ui", v in app.SPEED_VALUES, "速度档位含 %s" % v)
            check("ui", hasattr(app, "_speed_popup"), "_speed_popup 方法存在")
            # 按钮文字含下拉箭头
            txt = app.speed_box.cget("text")
            check("ui", "1x" in txt, "速度按钮文字含 1x")
            check("ui", app.speed_box.cget("bg") == m.COL_WIDGET,
                  "速度按钮背景 COL_WIDGET(深色风格)")
        finally:
            app.destroy()

    # ================= ⑧ 崩溃自动重启(v10 回归) =================
    def test_watchdog(self):
        print("\n== 崩溃自动重启 ==")
        td = tempfile.mkdtemp(prefix="wb_wd_")
        marker = os.path.join(td, "crash_marker.txt")
        countf = os.path.join(td, "crash_count.txt")
        real_marker = os.path.join(m.APP_DIR, "crash_marker.txt")
        real_count = os.path.join(m.APP_DIR, "crash_count.txt")

        class FakeTime:
            def __init__(self):
                self.t = 1000.0
            def sleep(self, s):
                self.t += s
            def time(self):
                return self.t

        orig_time = m.time
        orig_pid_alive = m._pid_alive
        orig_launch = m._launch_self
        try:
            m.time = FakeTime()
            m._pid_alive = lambda pid: False
            m.CRASH_MARKER = marker
            m.CRASH_COUNT = countf
            launched = []
            m._launch_self = lambda extra=None: launched.append(extra)

            with open(marker, "w", encoding="utf-8") as f:
                f.write("12345")
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "主进程异常退出后自动重启")
            check("watchdog", not os.path.isfile(marker), "重启后清除崩溃标记")
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "正常关闭(无标记)不重启")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("99999")
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "不属于本 watchdog 的旧标记不重启")
            try:
                os.remove(marker)
            except OSError:
                pass
            base = len(launched)
            m._start_watchdog()
            m._start_watchdog()
            m._start_watchdog()
            check("watchdog", len(launched) == base + 3, "连续前 3 次崩溃均重启")
            m._start_watchdog()
            check("watchdog", len(launched) == base + 3, "第 4 次放弃(防死循环)")
            m.time.t += 61
            m._start_watchdog()
            check("watchdog", len(launched) == base + 4, "超 1 分钟后恢复重启")
        finally:
            for f in (real_marker, real_count):
                try:
                    if os.path.isfile(f):
                        os.remove(f)
                except OSError:
                    pass
            m.time = orig_time
            m._pid_alive = orig_pid_alive
            m._launch_self = orig_launch
            m.CRASH_MARKER = real_marker
            m.CRASH_COUNT = real_count
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    # ================= ⑨ 片段列表拖拽排序(v11 新增) =================
    def test_drag_reorder(self):
        print("\n== 片段列表拖拽排序 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        try:
            # 构造 3 个片段
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
                {"file": "c.mp4", "start": 4, "end": 5, "label": "C"},
            ]
            app.refresh_segment_list()
            app.update_idletasks()
            app.update()

            check("drag", hasattr(app, "_on_drag_start"), "_on_drag_start 方法存在")
            check("drag", hasattr(app, "_on_drag_motion"), "_on_drag_motion 方法存在")
            check("drag", hasattr(app, "_on_drag_release"), "_on_drag_release 方法存在")
            check("drag", hasattr(app, "_drag_start_idx"), "_drag_start_idx 属性存在")
            check("drag", hasattr(app, "_drag_pending"), "_drag_pending 属性存在")

            # 模拟拖拽:从索引 0 拖到索引 2
            # 用 bbox 获取每行实际位置(避免字体高度差异)
            def row_y(idx):
                bbox = app.lst.bbox(idx)
                return bbox[1] + bbox[3] // 2 if bbox else idx * 20

            class FakeEvent:
                def __init__(self, y):
                    self.y = y
                    self.x_root = 0
                    self.y_root = 0

            # 按下:记录起始索引
            app._on_drag_start(FakeEvent(row_y(0)))
            check("drag", app._drag_start_idx == 0, "按下记录起始索引 0")
            check("drag", app._drag_pending is True, "按下标记待判定")

            # 移动到目标位置
            app._on_drag_motion(FakeEvent(row_y(2)))
            check("drag", app._drag_pending is True, "移动保持待判定")

            # 释放:从 0 拖到 2
            app._on_drag_release(FakeEvent(row_y(2)))
            check("drag", app.segments[0]["label"] == "B", "拖拽后索引 0 变为 B")
            check("drag", app.segments[1]["label"] == "C", "拖拽后索引 1 变为 C")
            check("drag", app.segments[2]["label"] == "A", "拖拽后索引 2 变为 A(被拖到末尾)")
            check("drag", app._drag_start_idx is None, "释放后清除起始索引")
            check("drag", app._drag_pending is False, "释放后清除待判定")

            # 拖到相同位置不移动
            app._on_drag_start(FakeEvent(row_y(0)))
            app._on_drag_release(FakeEvent(row_y(0)))
            check("drag", app.segments[0]["label"] == "B", "相同位置释放不移动")

            # 空列表不崩溃
            app.segments = []
            app.refresh_segment_list()
            app._on_drag_start(FakeEvent(0))
            app._on_drag_release(FakeEvent(0))
            check("drag", True, "空列表拖拽不崩溃")
        finally:
            app.destroy()

    # ================= ⑩ UI 几何:新增控件不遮挡 =================
    def test_ui_geometry(self):
        print("\n== UI 几何无遮挡 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        app.update_idletasks()
        app.update()
        try:
            # 控制行:5 个控件两两不重叠
            ctrl_widgets = [app.btn_set_s, app.btn_set_e, app.chk_precise,
                            app.lbl_speed, app.speed_box]
            bad = []
            for i in range(len(ctrl_widgets)):
                for j in range(i + 1, len(ctrl_widgets)):
                    if overlap(rect(ctrl_widgets[i]), rect(ctrl_widgets[j])):
                        bad.append((i, j))
            check("ui", not bad, "控制行 5 个控件两两不重叠%s" % (bad or ""))

            # 右侧面板按钮纵向不重叠
            for a, b in ((app.btn_merge, app.btn_batch),
                         (app.btn_batch, app.btn_gif),
                         (app.btn_gif, app.btn_audio)):
                check("ui", not overlap(rect(a), rect(b)),
                      "%s 与 %s 不重叠" % (a.cget("text").strip(), b.cget("text").strip()))

            # 纵向布局依次向下不重叠
            seq = [(app.cv, "预览区"), (app.info_bar, "信息条"),
                   (app.timeline, "时间轴"), (app.lbl_speed, "控制行")]
            for i in range(len(seq) - 1):
                w1, name1 = seq[i]
                w2, name2 = seq[i + 1]
                check("ui", not overlap(rect(w1), rect(w2)),
                      "%s 与 %s 不重叠" % (name1, name2))

            # 导出按钮初始禁用
            check("ui", str(app.btn_gif["state"]) == "disabled", "GIF 按钮初始禁用")
            check("ui", str(app.btn_audio["state"]) == "disabled", "音频按钮初始禁用")
            # 速度默认 1x
            check("ui", app.speed_var.get() == "1x", "速度默认 1x")
            # ZOOM_LEVELS 保留
            check("ui", app.ZOOM_LEVELS == (0.25, 0.5, 1, 2, 3, 4), "预览缩放档位保留")
            check("ui", app.preview_zoom == 1.0, "预览默认缩放 100%")

            # 最小窗口尺寸下控制行不溢出
            app.geometry("1040x720")
            app.update_idletasks()
            sb_r = app.speed_box.winfo_rootx() + app.speed_box.winfo_width()
            win_r = app.winfo_rootx() + app.winfo_width()
            check("ui", sb_r <= win_r - 10,
                  "控制行不超出窗口右缘(%d <= %d)" % (sb_r, win_r))

            # 纵向:预览 → 信息条 → 时间轴 → 控制行 依次排布
            def below(a, b):
                return a.winfo_rooty() + a.winfo_height() <= b.winfo_rooty()
            for i in range(len(seq) - 1):
                check("ui", below(seq[i][0], seq[i + 1][0]),
                      "%s 在 %s 上方" % (seq[i][1], seq[i + 1][1]))
        finally:
            app.destroy()

    # ================= ⑪ 回归:保留 v10/v9/v8 能力 =================
    def test_regression(self):
        print("\n== 回归 ==")
        # v9:预览平移保留
        for name in ("_clamp_pv_center", "_on_pv_press", "_on_pv_drag",
                     "_on_pv_release", "_on_preview_wheel", "_on_preview_double"):
            check("reg", hasattr(m.App, name) or hasattr(m, name), "%s 保留" % name)
        # v8:批量导出保留
        check("reg", hasattr(m.App, "export_all_segments"), "批量导出保留")
        check("reg", hasattr(m.App, "_batch_poll"), "_batch_poll 保留")
        # v8 时间轴缩放/气泡已移除
        for name in ("_set_zoom", "zoom_in", "zoom_out", "zoom_reset",
                     "_pan_view", "ensure_visible", "_show_bubble",
                     "_on_ctrl_wheel", "_hit_zoom_btn"):
            check("reg", not hasattr(m.Timeline, name), "缩放残留已移除: %s" % name)
        # 时间轴刻度
        st = m.Timeline._tick_step
        check("reg", st(None, 30) == 5, "30s 视频主刻度 5s")
        check("reg", st(None, 3600) == 300, "1h 视频主刻度 300s")
        # v10 功能符号(不含 enable_file_drop)
        for name in ("_watchdog_main", "_start_watchdog", "on_playhead_drag",
                     "export_gif", "export_audio", "_ensure_mp3_encoder",
                     "_clean_preview_cache", "_build_seg_menu", "_argv_video_path"):
            check("reg", hasattr(m, name) or hasattr(m.App, name), "v10 符号 %s 存在" % name)
        # v11 新功能符号(类级)
        for name in ("_quality_dialog", "_speed_popup", "_on_drag_start",
                     "_on_drag_motion", "_on_drag_release", "GIF_PRESETS",
                     "MP3_PRESETS"):
            check("reg", hasattr(m.App, name), "v11 符号 %s 存在" % name)
        # SPEED_VALUES 是实例属性(在 build_ui 中设置),在 test_speed_dropdown 已验证
        check("reg", not hasattr(m, "enable_file_drop"), "enable_file_drop 确认移除")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(V11Tests)
    runner = unittest.TextTestRunner(verbosity=0)
    runner.run(suite)
    print("\n通过 %d 项,失败 %d 项" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
