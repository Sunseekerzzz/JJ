# -*- coding: utf-8 -*-
"""v10 冒烟测试:启动 exe → 确认存活 → 正常关闭(WM_CLOSE) →
验证 crash_marker 被清理、watchdog 不重启、进程全部退出。
运行:系统 Python 3.12 python smoke_v10.py <exe路径>
"""
import ctypes
import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[1]))
MARKER = os.path.join(APP_DIR, "crash_marker.txt")
CRASH_LOG = os.path.join(APP_DIR, "crash.log")

exe = os.path.abspath(sys.argv[1])
# 记录 crash.log 基线(冒烟期间不应新增内容)
crash_before = 0
try:
    crash_before = os.path.getsize(CRASH_LOG)
except OSError:
    pass
# 清理可能残留的标记
try:
    if os.path.isfile(MARKER):
        os.remove(MARKER)
except OSError:
    pass

p = subprocess.Popen([exe])
fail = []

def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fail.append(name)

def tasklist_procs():
    """tasklist 输出在中文 Windows 是 GBK,需容错解码"""
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq LosslessVideoEditor.exe"],
                         capture_output=True,
                         encoding="gbk", errors="replace").stdout
    procs = []
    for line in (out or "").splitlines():
        if "LosslessVideoEditor.exe" in line and "Image" not in line:
            parts = line.split()
            if len(parts) >= 2:
                procs.append(parts[1])
    return procs


# 1. 启动 12 秒(解压 ffmpeg + 拉起 watchdog)后主进程应存活
time.sleep(12)
check("主进程启动后存活", p.poll() is None)

# 2. watchdog 子进程存在(exe 同名进程共 2 个:主进程 + watchdog)
time.sleep(2)
alive = set(tasklist_procs())
check("watchdog 守护进程已拉起", len(alive) >= 2,
      "进程 PIDs: %s" % sorted(alive))

# 3. 崩溃标记已写入
check("崩溃标记已写入", os.path.isfile(MARKER))

# 4. 主窗口已创建(枚举窗口找标题"无损视频剪辑")
found = []

def _enum(hwnd, _):
    if ctypes.windll.user32.IsWindowVisible(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        if "无损视频剪辑" in buf.value:
            found.append(hwnd)
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
ctypes.windll.user32.EnumWindows(WNDENUMPROC(_enum), 0)
check("主窗口已显示(标题: 无损视频剪辑)", len(found) >= 1)

# 5. 正常关闭:向主窗口发 WM_CLOSE(0x0010) → 应删除标记并退出
if found:
    ctypes.windll.user32.PostMessageW(found[0], 0x0010, 0, 0)
# 轮询等待主进程退出。PyInstaller onefile windowed 的 bootloader 父进程
# 在 App 退出后还需清理临时目录,实测最长约 16s(最终 exit 0 无残留),
# 因此等待窗口放宽到 30s。
exit_wait = 0
while p.poll() is None and exit_wait < 30:
    time.sleep(1)
    exit_wait += 1
check("正常关闭后主进程退出", p.poll() is not None,
      "等待 %ds" % exit_wait)
check("正常关闭后崩溃标记已清理", not os.path.isfile(MARKER))

# 6. watchdog 应随之退出(标记已删, 主进程消失 → 不重启, 自身退出)
time.sleep(4)
left = tasklist_procs()
check("watchdog 正常退出(无残留进程)", len(left) == 0, "残留: %s" % left)

# 7. 冒烟期间 crash.log 不应新增
try:
    crash_after = os.path.getsize(CRASH_LOG)
except OSError:
    crash_after = 0
check("crash.log 无新增异常", crash_after == crash_before,
      "before=%d after=%d" % (crash_before, crash_after))

print("\n冒烟 %s: %d 失败" % ("通过" if not fail else "未通过", len(fail)))
sys.exit(1 if fail else 0)
