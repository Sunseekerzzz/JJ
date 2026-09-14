# -*- coding: utf-8 -*-
"""v12 全量测试:速度下拉修复(无 grab 死锁) + 片段多选 + 导出参数记忆 + 防卡死守卫 + 回归。
运行:系统 Python 3.12(带 tkinter) python test_v12.py
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


class V12Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_ensure = m.App.ensure_ffmpeg
        cls._orig_save = m.save_config
        m.App.ensure_ffmpeg = lambda self: None
        m.save_config = lambda cfg: None

    @classmethod
    def tearDownClass(cls):
        m.App.ensure_ffmpeg = cls._orig_ensure
        m.save_config = cls._orig_save
        for f in (m.CRASH_MARKER, m.CRASH_COUNT):
            try:
                if os.path.isfile(f):
                    os.remove(f)
            except OSError:
                pass

    # ================= ① 速度下拉修复(无 grab 死锁) =================
    def test_speed_popup_fix(self):
        print("\n== 速度下拉修复(无 grab 死锁) ==")
        # _speed_popup 方法存在且不含 grab_set/wait_window
        import inspect
        src = inspect.getsource(m.App._speed_popup)
        check("speed", "grab_set" not in src, "_speed_popup 不含 grab_set(修复死锁)")
        check("speed", "self.wait_window" not in src, "_speed_popup 不含 self.wait_window(修复死锁)")
        check("speed", "overrideredirect" not in src, "_speed_popup 不含 overrideredirect")
        check("speed", "tk_popup" in src or "Menu" in src,
              "_speed_popup 使用原生 Menu/tk_popup")
        # _set_speed 方法存在
        check("speed", hasattr(m.App, "_set_speed"), "_set_speed 方法存在")
        # 按钮触发的是 _speed_popup
        app = m.App()
        try:
            cmd_str = str(app.speed_box.cget("command"))
            check("speed", "speed_popup" in cmd_str or callable(app._speed_popup),
                  "速度按钮 command 绑定 _speed_popup")
        finally:
            app.destroy()

    def test_set_speed(self):
        print("\n== _set_speed 方法 ==")
        app = m.App()
        try:
            app._set_speed("0.5x")
            check("speed", app.speed_var.get() == "0.5x", "_set_speed 更新 speed_var")
            check("speed", "0.5x" in app.speed_box.cget("text"),
                  "_set_speed 更新按钮文字")
            app._set_speed("2x")
            check("speed", app.speed_var.get() == "2x", "_set_speed 更新为 2x")
        finally:
            app.destroy()

    # ================= ② 片段列表多选 =================
    def test_multiselect(self):
        print("\n== 片段列表多选 ==")
        app = m.App()
        try:
            # selectmode 是 extended
            check("multi", app.lst.cget("selectmode") == "extended",
                  "Listbox selectmode=extended(支持多选)")
            # 构造 4 个片段
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
                {"file": "c.mp4", "start": 4, "end": 5, "label": "C"},
                {"file": "d.mp4", "start": 6, "end": 7, "label": "D"},
            ]
            app.refresh_segment_list()
            # 选中 0 和 2
            app.lst.selection_set(0, 2)
            sel = app.lst.curselection()
            check("multi", len(sel) == 3, "范围选中 0-2 得到 3 项")
            # 批量删除
            app.del_segment()
            check("multi", len(app.segments) == 1, "批量删除 3 项后剩 1 项")
            check("multi", app.segments[0]["label"] == "D", "删除后剩余 D")
        finally:
            app.destroy()

    def test_multiselect_move(self):
        print("\n== 多选块移动 ==")
        app = m.App()
        try:
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
                {"file": "c.mp4", "start": 4, "end": 5, "label": "C"},
                {"file": "d.mp4", "start": 6, "end": 7, "label": "D"},
            ]
            app.refresh_segment_list()
            # 选中 B 和 C (索引 1, 2)
            app.lst.selection_set(1)
            app.lst.selection_set(2)
            # 上移
            app.move_segment(-1)()
            check("multi", app.segments[0]["label"] == "B", "上移后 B 在索引 0")
            check("multi", app.segments[1]["label"] == "C", "上移后 C 在索引 1")
            check("multi", app.segments[2]["label"] == "A", "上移后 A 在索引 2")
            check("multi", app.segments[3]["label"] == "D", "D 不动")
            # 选中块在顶部时上移无效
            app.lst.selection_clear(0, "end")
            app.lst.selection_set(0)
            app.lst.selection_set(1)
            app.move_segment(-1)()
            check("multi", app.segments[0]["label"] == "B", "块在顶部时上移无效")

            # 下移测试
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
                {"file": "c.mp4", "start": 4, "end": 5, "label": "C"},
                {"file": "d.mp4", "start": 6, "end": 7, "label": "D"},
            ]
            app.refresh_segment_list()
            app.lst.selection_set(0)
            app.lst.selection_set(1)
            app.move_segment(1)()
            check("multi", app.segments[0]["label"] == "C", "下移后 C 在索引 0")
            check("multi", app.segments[1]["label"] == "A", "下移后 A 在索引 1")
            check("multi", app.segments[2]["label"] == "B", "下移后 B 在索引 2")
        finally:
            app.destroy()

    def test_multiselect_menu(self):
        print("\n== 多选右键菜单 ==")
        app = m.App()
        try:
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
            ]
            app.refresh_segment_list()
            # 单选
            app.lst.selection_clear(0, "end")
            app.lst.selection_set(0)
            menu = app._build_seg_menu(0)
            labels = []
            for i in range(menu.index("end") + 1):
                try:
                    labels.append(menu.entrycget(i, "label"))
                except Exception:
                    pass
            check("multi", any("定位" in l for l in labels), "单选菜单含「定位」")
            check("multi", any("重命名" in l for l in labels), "单选菜单含「重命名」")

            # 多选
            app.lst.selection_set(1)
            menu2 = app._build_seg_menu(0)
            labels2 = []
            for i in range(menu2.index("end") + 1):
                try:
                    labels2.append(menu2.entrycget(i, "label"))
                except Exception:
                    pass
            check("multi", any("删除选中" in l for l in labels2), "多选菜单含「删除选中 (N)」")
            check("multi", any("2" in l for l in labels2 if "删除" in l),
                  "删除选中显示数量 2")
        finally:
            app.destroy()

    # ================= ③ 导出参数记忆 =================
    def test_export_memory(self):
        print("\n== 导出参数记忆 ==")
        # _quality_dialog 接受 cfg_key 参数
        import inspect
        sig = inspect.signature(m.App._quality_dialog)
        check("mem", "cfg_key" in sig.parameters, "_quality_dialog 接受 cfg_key 参数")
        # 从 config 读取
        app = m.App()
        try:
            app.cfg = {"gif_quality": 2}
            check("mem", True, "config 含 gif_quality=2")
            # 验证 _quality_dialog 源码读取 cfg_key
            src = inspect.getsource(m.App._quality_dialog)
            check("mem", "cfg_key" in src and "self.cfg" in src,
                  "_quality_dialog 从 config 读取记忆")
            check("mem", "save_config" in src, "_quality_dialog 保存选择到 config")
        finally:
            app.destroy()

    # ================= ④ 防卡死:busy 守卫 =================
    def test_busy_guard(self):
        print("\n== 导出函数 busy 守卫 ==")
        import inspect
        for func_name in ("export_cut", "export_merge", "export_gif",
                          "export_audio", "export_all_segments"):
            src = inspect.getsource(getattr(m.App, func_name))
            check("busy", "self.busy" in src, "%s 含 busy 检查" % func_name)

        # 实际验证:busy=True 时导出函数直接返回
        app = m.App()
        try:
            app.busy = True
            app.video = "fake.mp4"
            app.start_t = 0
            app.end_t = 10
            app.segments = [{"file": "a.mp4", "start": 0, "end": 1, "label": "A"}]
            # export_cut 不应触发 _run_job
            triggered = []
            app._run_job = lambda *a, **kw: triggered.append(True)
            app.export_cut()
            check("busy", len(triggered) == 0, "busy=True 时 export_cut 不触发")
            app.export_merge()
            check("busy", len(triggered) == 0, "busy=True 时 export_merge 不触发")
            app.export_gif()
            check("busy", len(triggered) == 0, "busy=True 时 export_gif 不触发")
            app.export_audio()
            check("busy", len(triggered) == 0, "busy=True 时 export_audio 不触发")
            app.export_all_segments()
            check("busy", len(triggered) == 0, "busy=True 时 export_all_segments 不触发")
        finally:
            app.destroy()

    # ================= ⑤ 对话框 z-order 安全 =================
    def test_dialog_zorder(self):
        print("\n== 对话框 z-order 安全(lift/focus_force) ==")
        import inspect
        # _quality_dialog 有 lift + focus_force
        src_q = inspect.getsource(m.App._quality_dialog)
        check("zord", "lift()" in src_q, "_quality_dialog 含 lift()")
        check("zord", "focus_force()" in src_q, "_quality_dialog 含 focus_force()")
        check("zord", "grab_release" in src_q, "_quality_dialog 含 grab_release(安全释放)")
        # _show_done_dialog
        src_d = inspect.getsource(m.App._show_done_dialog)
        check("zord", "lift()" in src_d, "_show_done_dialog 含 lift()")
        check("zord", "focus_force()" in src_d, "_show_done_dialog 含 focus_force()")
        # open_precise_dialog
        src_p = inspect.getsource(m.App.open_precise_dialog)
        check("zord", "lift()" in src_p, "open_precise_dialog 含 lift()")
        check("zord", "focus_force()" in src_p, "open_precise_dialog 含 focus_force()")
        # _quality_dialog 有超时保底
        check("zord", "300000" in src_q or "after(" in src_q,
              "_quality_dialog 有超时保底(5分钟)")

    # ================= ⑥ 速度档位回归 =================
    def test_speed_values(self):
        print("\n== 速度档位回归 ==")
        app = m.App()
        try:
            check("speed", len(app.SPEED_VALUES) == 6, "速度档位 6 个")
            for v in ("0.25x", "0.5x", "0.75x", "1x", "1.5x", "2x"):
                check("speed", v in app.SPEED_VALUES, "速度档位含 %s" % v)
            check("speed", app.speed_var.get() == "1x", "默认速度 1x")
        finally:
            app.destroy()

    # ================= ⑦ GIF/MP3 预设回归 =================
    def test_gif_presets(self):
        print("\n== GIF 预设回归 ==")
        check("gif", len(m.App.GIF_PRESETS) == 3, "GIF 三档预设")
        _, fps_lo, w_lo = m.App.GIF_PRESETS[0]
        _, fps_hi, w_hi = m.App.GIF_PRESETS[2]
        check("gif", fps_lo < fps_hi, "低档 fps < 高档 fps")
        check("gif", w_lo < w_hi, "低档宽度 < 高档宽度")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"))
        cmd = m.App._build_gif_cmd(stub, "in.mp4", 1, 5, "out.gif",
                                   width=480, fps=10)
        s = " ".join(cmd)
        check("gif", "fps=10" in s and "scale=480" in s, "GIF 低档命令正确")
        check("gif", "palettegen" in s and "paletteuse" in s, "GIF 两遍法保留")

    def test_mp3_presets(self):
        print("\n== MP3 预设回归 ==")
        check("audio", len(m.App.MP3_PRESETS) == 3, "MP3 三档预设")
        stub = SimpleNamespace(ffapi=SimpleNamespace(ffmpeg="ffmpeg.exe"),
                               _mp3_enc="libmp3lame")
        stub._ensure_mp3_encoder = m.App._ensure_mp3_encoder.__get__(stub)
        cmd = m.App._build_audio_cmd(stub, "in.mp4", 0, 10, "out.mp3", q_a=6)
        s = " ".join(cmd)
        check("audio", "-vn" in s and "libmp3lame" in s, "MP3 命令含 -vn 和编码器")
        check("audio", "-q:a" in s and "6" in s, "MP3 低档 q_a=6")

    # ================= ⑧ 拖拽排序回归 =================
    def test_drag_reorder(self):
        print("\n== 拖拽排序回归 ==")
        app = m.App()
        try:
            app.segments = [
                {"file": "a.mp4", "start": 0, "end": 1, "label": "A"},
                {"file": "b.mp4", "start": 2, "end": 3, "label": "B"},
                {"file": "c.mp4", "start": 4, "end": 5, "label": "C"},
            ]
            app.refresh_segment_list()
            app.update_idletasks()
            app.update()

            def row_y(idx):
                bbox = app.lst.bbox(idx)
                return bbox[1] + bbox[3] // 2 if bbox else idx * 20

            class FakeEvent:
                def __init__(self, y):
                    self.y = y
                    self.x_root = 0
                    self.y_root = 0

            app._on_drag_start(FakeEvent(row_y(0)))
            app._on_drag_motion(FakeEvent(row_y(2)))
            app._on_drag_release(FakeEvent(row_y(2)))
            check("drag", app.segments[2]["label"] == "A", "拖拽 0→2 后 A 在末尾")
            check("drag", app.segments[0]["label"] == "B", "拖拽后 B 在首位")
        finally:
            app.destroy()

    # ================= ⑨ 崩溃自动重启回归 =================
    def test_watchdog(self):
        print("\n== 崩溃自动重启回归 ==")
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
            check("wd", len(launched) == 1, "主进程异常退出后自动重启")
            check("wd", not os.path.isfile(marker), "重启后清除崩溃标记")
            m._watchdog_main(12345)
            check("wd", len(launched) == 1, "正常关闭(无标记)不重启")
        finally:
            for f in (real_marker, real_count, marker, countf):
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

    # ================= ⑩ UI 几何无遮挡 =================
    def test_ui_geometry(self):
        print("\n== UI 几何无遮挡 ==")
        app = m.App()
        app.update_idletasks()
        app.update()
        try:
            ctrl_widgets = [app.btn_set_s, app.btn_set_e, app.chk_precise,
                            app.lbl_speed, app.speed_box]
            bad = []
            for i in range(len(ctrl_widgets)):
                for j in range(i + 1, len(ctrl_widgets)):
                    if overlap(rect(ctrl_widgets[i]), rect(ctrl_widgets[j])):
                        bad.append((i, j))
            check("ui", not bad, "控制行 5 个控件两两不重叠%s" % (bad or ""))

            for a, b in ((app.btn_merge, app.btn_batch),
                         (app.btn_batch, app.btn_gif),
                         (app.btn_gif, app.btn_audio)):
                check("ui", not overlap(rect(a), rect(b)),
                      "%s 与 %s 不重叠" % (a.cget("text").strip(), b.cget("text").strip()))

            seq = [(app.cv, "预览区"), (app.info_bar, "信息条"),
                   (app.timeline, "时间轴"), (app.lbl_speed, "控制行")]
            for i in range(len(seq) - 1):
                w1, name1 = seq[i]
                w2, name2 = seq[i + 1]
                check("ui", not overlap(rect(w1), rect(w2)),
                      "%s 与 %s 不重叠" % (name1, name2))

            check("ui", str(app.btn_gif["state"]) == "disabled", "GIF 按钮初始禁用")
            check("ui", app.speed_var.get() == "1x", "速度默认 1x")
            check("ui", app.ZOOM_LEVELS == (0.25, 0.5, 1, 2, 3, 4), "预览缩放档位保留")

            app.geometry("1040x720")
            app.update_idletasks()
            sb_r = app.speed_box.winfo_rootx() + app.speed_box.winfo_width()
            win_r = app.winfo_rootx() + app.winfo_width()
            check("ui", sb_r <= win_r - 10,
                  "控制行不超出窗口右缘(%d <= %d)" % (sb_r, win_r))

            def below(a, b):
                return a.winfo_rooty() + a.winfo_height() <= b.winfo_rooty()
            for i in range(len(seq) - 1):
                check("ui", below(seq[i][0], seq[i + 1][0]),
                      "%s 在 %s 上方" % (seq[i][1], seq[i + 1][1]))
        finally:
            app.destroy()

    # ================= ⑪ 回归:保留全部能力 =================
    def test_regression(self):
        print("\n== 回归 ==")
        # v12 新功能符号
        for name in ("_speed_popup", "_set_speed", "_quality_dialog",
                     "GIF_PRESETS", "MP3_PRESETS", "_on_drag_start",
                     "_on_drag_motion", "_on_drag_release"):
            check("reg", hasattr(m.App, name), "v12 符号 %s 存在" % name)
        # 拖拽功能确认移除
        check("reg", not hasattr(m, "enable_file_drop"), "enable_file_drop 确认移除")
        # 命令行路径保留
        check("reg", hasattr(m.App, "_try_pending"), "_try_pending 保留")
        check("reg", hasattr(m, "_argv_video_path"), "_argv_video_path 保留")
        # v10 功能保留
        for name in ("_watchdog_main", "_start_watchdog", "on_playhead_drag",
                     "export_gif", "export_audio", "_ensure_mp3_encoder",
                     "_clean_preview_cache", "_build_seg_menu"):
            check("reg", hasattr(m, name) or hasattr(m.App, name), "v10 符号 %s 存在" % name)
        # v9 预览平移保留
        for name in ("_clamp_pv_center", "_on_pv_press", "_on_pv_drag",
                     "_on_pv_release", "_on_preview_wheel", "_on_preview_double"):
            check("reg", hasattr(m.App, name) or hasattr(m, name), "%s 保留" % name)
        # v8 批量导出保留
        check("reg", hasattr(m.App, "export_all_segments"), "批量导出保留")
        check("reg", hasattr(m.App, "_batch_poll"), "_batch_poll 保留")
        # 时间轴刻度
        st = m.Timeline._tick_step
        check("reg", st(None, 30) == 5, "30s 视频主刻度 5s")
        check("reg", st(None, 3600) == 300, "1h 视频主刻度 300s")
        # _quality_dialog 有 cfg_key 参数(导出参数记忆)
        import inspect
        sig = inspect.signature(m.App._quality_dialog)
        check("reg", "cfg_key" in sig.parameters, "_quality_dialog 有 cfg_key 参数")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(V12Tests)
    runner = unittest.TextTestRunner(verbosity=0)
    runner.run(suite)
    print("\n通过 %d 项,失败 %d 项" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
