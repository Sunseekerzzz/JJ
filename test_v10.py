# -*- coding: utf-8 -*-
"""v10 全量测试:8 项新功能 + UI 几何无遮挡 + v9/v8 回归。
运行:系统 Python 3.12(带 tkinter) python test_v10.py
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


class V10Tests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # 清理测试可能遗留的崩溃标记
        for f in (m.CRASH_MARKER, m.CRASH_COUNT):
            try:
                if os.path.isfile(f):
                    os.remove(f)
            except OSError:
                pass

    # ================= ① 拖拽文件到窗口直接打开 =================
    def test_drop(self):
        print("\n== 拖拽文件到窗口 ==")
        fd1, f1 = tempfile.mkstemp(suffix=".mp4")
        fd2, f2 = tempfile.mkstemp(suffix=".mp4")
        os.close(fd1)
        os.close(fd2)
        try:
            called = []
            stub = SimpleNamespace(
                _pending_open=None, log=lambda *a: None,
                set_status=lambda s: called.append(s),
                _try_pending=lambda: None)
            m.App._on_file_dropped(stub, [f1, f2])
            check("drop", stub._pending_open == f1,
                  "_on_file_dropped 打开第一个视频")
            # 第二次拖入:第一个仍有效 → 覆盖为新的第一个
            m.App._on_file_dropped(stub, [f2])
            check("drop", stub._pending_open == f2,
                  "再次拖入时更新待打开文件")
            # 拖入不存在的内容:不覆盖、给提示
            m.App._on_file_dropped(stub, ["C:/videos/不存在的.mp4"])
            check("drop", stub._pending_open == f2,
                  "无有效文件时不覆盖待打开文件")
            check("drop", len(called) >= 1 and "没有可用文件" in called[-1],
                  "无有效文件时给出提示")
            # enable_file_drop:无窗口句柄时安全降级(不崩溃)
            r1 = m.enable_file_drop(None, lambda p: None)
            check("drop", r1 is False, "无窗口句柄时安全降级")
        finally:
            for f in (f1, f2):
                try:
                    os.remove(f)
                except OSError:
                    pass

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

    # ================= ② 播放头拖动预览联动 =================
    def test_playhead_live(self):
        print("\n== 播放头拖动预览联动 ==")
        calls = {"refresh": 0, "schedule": 0}
        stub = SimpleNamespace(
            _pv_live_last=0.0,
            refresh_preview_now=lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
            schedule_preview=lambda: calls.__setitem__("schedule", calls["schedule"] + 1))
        # 第一次:距上次 >120ms → 立即抽帧
        stub._pv_live_last = _time.time() - 1.0
        m.App.on_playhead_drag(stub)
        check("live", calls["refresh"] == 1, "拖动节流:超过间隔立即抽帧")
        # 立即第二次:间隔 <120ms → 合并到防抖
        stub._pv_live_last = _time.time()
        m.App.on_playhead_drag(stub)
        check("live", calls["refresh"] == 1 and calls["schedule"] == 1,
              "高频拖动:合并到防抖刷新,不堆积 ffmpeg")
        # 拖动播放头调用链:Timeline.on_drag → on_playhead_drag
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
        # on_press 点击空白处(跳转播放头)→ 立即联动;点在手柄上只进入拖拽态
        m.Timeline.on_press(tl, SimpleNamespace(x=25, y=0))
        check("live", drags["n"] == 2 and tl.app.playhead_t == 25,
              "点击时间轴空白处跳转播放头并联动预览")
        m.Timeline.on_press(tl, SimpleNamespace(x=50, y=0))
        check("live", tl.drag_mode == "play", "点击播放头进入拖拽态(联动由拖动触发)")

    # ================= ③ 片段列表右键菜单 =================
    def test_seg_menu(self):
        print("\n== 片段列表右键菜单 ==")
        # 构造真实 App(静默 ffmpeg 检测与配置写入)
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        app.segments = [{"file": "a.mp4", "start": 0, "end": 1,
                         "label": "0:00:00 → 0:00:01 (时长 0:00:01)  a.mp4"}]
        app.refresh_segment_list()
        menu = app._build_seg_menu(0)
        labels = []
        for i in range(menu.index("end") + 1):
            try:
                labels.append(menu.entrycget(i, "label"))
            except Exception:
                pass  # 分隔符条目没有 label
        for want in ("▶ 定位到该片段", "✎ 重命名", "↑ 上移", "↓ 下移",
                     "－ 删除片段", "清空列表"):
            check("menu", want in labels, "菜单包含「%s」" % want)
        # 重命名逻辑
        app._rename_segment(0)  # 打开对话框(不操作,直接销毁)
        for w in app.winfo_children():
            pass
        app.destroy()
        # 纯逻辑:del_segment 删除指定索引
        app2 = SimpleNamespace(segments=[{"label": "x"}, {"label": "y"}],
                               refresh_segment_list=lambda: None)
        m.App._del_segment_idx(app2, 0)
        check("menu", len(app2.segments) == 1 and app2.segments[0]["label"] == "y",
              "_del_segment_idx 删除正确索引")

    # ================= ④ 导出 GIF =================
    def test_gif(self):
        print("\n== 导出 GIF ==")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"))
        cmd = m.App._build_gif_cmd(stub, "in.mp4", 1.5, 9.5, "out.gif")
        s = " ".join(cmd)
        check("gif", "-ss" in s and "1.500" in s and "-to" in s and "9.500" in s,
              "GIF 命令含起止时间")
        check("gif", "palettegen" in s and "paletteuse" in s,
              "GIF 使用 palettegen/paletteuse 两遍法")
        check("gif", "-loop" in s and "out.gif" in s, "GIF 循环播放 + .gif 输出")
        check("gif", "scale=640" in s and "fps=15" in s, "GIF 默认 15fps 宽 640")

    # ================= ⑤ 导出纯音频 MP3 =================
    def test_audio(self):
        print("\n== 导出纯音频 MP3 ===")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                               _mp3_enc=None)
        orig = m.run_capture
        m.run_capture = lambda cmd, timeout=60: (0, b"libmp3lame  A       libmp3lame MP3 (MPEG audio layer 3) (codec mp3)", b"")
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
        # 绑定真实方法:命中 _mp3_enc 缓存即返回,不实际跑 ffmpeg
        stub3._ensure_mp3_encoder = m.App._ensure_mp3_encoder.__get__(stub3)
        cmd = m.App._build_audio_cmd(stub3, "in.mkv", 0, 10, "out.mp3")
        s = " ".join(cmd)
        check("audio", "-vn" in s, "音频导出丢弃视频流(-vn)")
        check("audio", "-c:a" in s and "libmp3lame" in s, "音频导出使用 MP3 编码器")
        check("audio", "-q:a" in s and "out.mp3" in s, "音频导出 VBR q2 + .mp3 输出")

    # ================= ⑥ 导出后清理抽帧缓存 =================
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
        # 抽帧失败路径也删 png
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

    # ================= ⑦ 播放速度选择 =================
    def test_speed(self):
        print("\n== 播放速度选择 ==")
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
            stub = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=5.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("2x"), log=lambda *a: None)
            m.App.play_selection(stub)
            s = " ".join(launched[-1])
            check("speed", "-speed" in s and "2.00" in s, "2x 速度传入 ffplay")
            stub2 = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=5.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("0.5x"), log=lambda *a: None)
            m.App.play_selection(stub2)
            s2 = " ".join(launched[-1])
            check("speed", "-speed" in s2 and "0.50" in s2, "0.5x 速度传入 ffplay")
            stub3 = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=5.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("1x"), log=lambda *a: None)
            m.App.play_selection(stub3)
            s3 = " ".join(launched[-1])
            check("speed", "-speed" not in s3, "1x 时不加 -speed(默认)")

            # 播放起点取播放头与选区起点的较大者(回归 v7 修复)
            stub4 = SimpleNamespace(
                ffapi=SimpleNamespace(has_ffplay=True, ffplay="ffplay.exe"),
                video="in.mp4", playhead_t=1.0, start_t=2.0, end_t=10.0,
                speed_var=FakeSpeed("1x"), log=lambda *a: None)
            m.App.play_selection(stub4)
            check("speed", "-ss" in " ".join(launched[-1]) and "2.000" in " ".join(launched[-1]),
                  "播放头在选区前时从选区起点播(回归)")
        finally:
            m.subprocess.Popen = orig

    # ================= ⑧ 崩溃自动重启 =================
    def test_watchdog(self):
        print("\n== 崩溃自动重启 ==")
        td = tempfile.mkdtemp(prefix="wb_wd_")
        marker = os.path.join(td, "crash_marker.txt")
        countf = os.path.join(td, "crash_count.txt")
        real_marker = os.path.join(m.APP_DIR, "crash_marker.txt")
        real_count = os.path.join(m.APP_DIR, "crash_count.txt")

        class FakeTime:
            """_watchdog_main 需要 sleep(阻塞测试)、_start_watchdog 需要 time()"""

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

            # 崩溃:标记存在且匹配 → 自动重启
            with open(marker, "w", encoding="utf-8") as f:
                f.write("12345")
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "主进程异常退出后自动重启")
            check("watchdog", not os.path.isfile(marker), "重启后清除崩溃标记")

            # 正常关闭:标记已删 → 不重启
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "正常关闭(无标记)不重启")

            # 旧标记(pid 不匹配)→ 不处理
            with open(marker, "w", encoding="utf-8") as f:
                f.write("99999")
            m._watchdog_main(12345)
            check("watchdog", len(launched) == 1, "不属于本 watchdog 的旧标记不重启")
            try:
                os.remove(marker)
            except OSError:
                pass

            # 防无限重启:1 分钟内连续 4 次崩溃后放弃
            base = len(launched)
            m._start_watchdog()
            m._start_watchdog()
            m._start_watchdog()
            check("watchdog", len(launched) == base + 3,
                  "连续前 3 次崩溃均重启 watchdog")
            m._start_watchdog()
            check("watchdog", len(launched) == base + 3,
                  "第 4 次连续崩溃放弃自动重启(防死循环)")
            check("watchdog", os.path.isfile(marker), "放弃前仍保留崩溃标记")
            # 时间超过 1 分钟后计数重置,可再次自动重启
            m.time.t += 61
            m._start_watchdog()
            check("watchdog", len(launched) == base + 4,
                  "超 1 分钟后计数重置,恢复自动重启")
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

    # ================= ⑨ UI 几何:新增控件不遮挡 =================
    def test_ui_geometry(self):
        print("\n== UI 几何无遮挡 ==")
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None
        app = m.App()
        app.update_idletasks()
        app.update()
        try:
            # 控制行(时间轴下方):起点/终点/勾选/速度标签/速度下拉 两两不重叠
            ctrl_widgets = [app.btn_set_s, app.btn_set_e, app.chk_precise,
                            app.lbl_speed, app.speed_box]
            bad = []
            for i in range(len(ctrl_widgets)):
                for j in range(i + 1, len(ctrl_widgets)):
                    if overlap(rect(ctrl_widgets[i]), rect(ctrl_widgets[j])):
                        bad.append((i, j))
            check("ui", not bad, "控制行 5 个控件两两不重叠%s" % (bad or ""))

            # 右侧面板:合并/批量/GIF/音频 按钮纵向不重叠
            for a, b in ((app.btn_merge, app.btn_batch),
                         (app.btn_batch, app.btn_gif),
                         (app.btn_gif, app.btn_audio)):
                check("ui", not overlap(rect(a), rect(b)),
                      "%s 与 %s 不重叠" % (a.cget("text").strip(), b.cget("text").strip()))

            # 纵向布局:预览区在上、信息条、时间轴、控制行依次向下不重叠
            seq = [(app.cv, "预览区"), (app.info_bar, "信息条"),
                   (app.timeline, "时间轴"), (app.lbl_speed, "控制行")]
            for i in range(len(seq) - 1):
                _, name1 = seq[i]
                w1, name2 = seq[i + 1]
                check("ui", not overlap(rect(seq[i][0]), rect(w1)),
                      "%s 与 %s 不重叠" % (name1, name2))

            # 新增导出按钮初始为禁用(未加载视频时)
            check("ui", str(app.btn_gif["state"]) == "disabled",
                  "GIF 按钮初始禁用")
            check("ui", str(app.btn_audio["state"]) == "disabled",
                  "音频按钮初始禁用")
            # 速度下拉默认 1x
            check("ui", app.speed_var.get() == "1x", "速度默认 1x")
            # v9:预览缩放倍率档位保留
            check("ui", app.ZOOM_LEVELS == (0.25, 0.5, 1, 2, 3, 4),
                  "预览缩放档位 ZOOM_LEVELS 保留")
            check("ui", app.preview_zoom == 1.0, "预览默认缩放 100%")

            # 最小窗口尺寸下也能容纳控制行(不溢出)
            app.geometry("1040x720")
            app.update_idletasks()
            sb_r = app.speed_box.winfo_rootx() + app.speed_box.winfo_width()
            win_r = app.winfo_rootx() + app.winfo_width()
            check("ui", sb_r <= win_r - 10,
                  "控制行在最小宽度下不超出窗口(右缘 %d ≤ %d)" % (sb_r, win_r))
            # 纵向:预览区 → 信息条 → 时间轴 → 控制行 依次排布,不互相压盖
            def below(a, b):
                return a.winfo_rooty() + a.winfo_height() <= b.winfo_rooty()

            seq = [(app.cv, "预览区"), (app.info_bar, "信息条"),
                   (app.timeline, "时间轴"), (app.lbl_speed, "控制行")]
            for i in range(len(seq) - 1):
                check("ui", below(seq[i][0], seq[i + 1][0]),
                      "%s 在 %s 上方" % (seq[i][1], seq[i + 1][1]))
        finally:
            app.destroy()

    # ================= ⑩ 回归:保留 v9/v8 能力,无 v8 缩放残留 =================
    def test_regression(self):
        print("\n== 回归 ==")
        # v9:预览平移保留(ZOOM_LEVELS 是实例属性,在 UI 测试中验证)
        for name in ("_clamp_pv_center", "_on_pv_press", "_on_pv_drag",
                     "_on_pv_release", "_on_preview_wheel",
                     "_on_preview_double"):
            check("reg", hasattr(m.App, name) or hasattr(m, name),
                  "%s 保留" % name)
        # v8:批量导出保留
        check("reg", hasattr(m.App, "export_all_segments"), "批量导出保留")
        check("reg", hasattr(m.App, "_batch_poll"), "_batch_poll 保留")
        # v8 时间轴缩放/气泡已彻底移除
        for name in ("_set_zoom", "zoom_in", "zoom_out", "zoom_reset",
                     "_pan_view", "ensure_visible", "_show_bubble",
                     "_on_ctrl_wheel", "_hit_zoom_btn"):
            check("reg", not hasattr(m.Timeline, name), "时间轴缩放残留已移除: %s" % name)
        check("reg", not hasattr(m.App, "ensure_visible"), "App 无视口跟随残留")
        # 时间轴刻度恢复整秒(v7 形式)
        st = m.Timeline._tick_step
        check("reg", st(None, 30) == 5, "30s 视频主刻度 5s")
        check("reg", st(None, 3600) == 300, "1h 视频主刻度 300s")
        check("reg", st(None, 10) == 1, "10s 视频主刻度 1s")
        # v10 新功能符号齐全
        for name in ("enable_file_drop", "_watchdog_main", "_start_watchdog",
                     "on_playhead_drag", "export_gif", "export_audio",
                     "_ensure_mp3_encoder", "_clean_preview_cache",
                     "_build_seg_menu", "_argv_video_path"):
            check("reg", hasattr(m, name) or hasattr(m.App, name), "v10 符号 %s 存在" % name)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(V10Tests)
    runner = unittest.TextTestRunner(verbosity=0)
    runner.run(suite)
    print("\n通过 %d 项,失败 %d 项" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
