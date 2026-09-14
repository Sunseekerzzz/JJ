@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ============================================================
rem  无损视频剪辑工具 启动器
rem  自动查找带 tkinter 的 Python 并启动主程序
rem  注意:括号块内避免使用半角括号,否则会破坏 cmd 块解析
rem ============================================================

set "PYEXE="

rem 1) py 启动器(官方 Python 安装版自带,优先使用)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import tkinter" >nul 2>nul
    if !errorlevel!==0 set "PYEXE=py -3"
)

rem 2) 常见官方 Python 安装路径
if not defined PYEXE (
    for %%P in (
        "%LocalAppData%\Programs\Python\Python313\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "%ProgramFiles%\Python311\python.exe"
    ) do (
        if not defined PYEXE (
            if exist %%P (
                %%P -c "import tkinter" >nul 2>nul
                if !errorlevel!==0 set "PYEXE=%%P"
            )
        )
    )
)

rem 3) PATH 里的 python,逐个验证是否带 tkinter
if not defined PYEXE (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PYEXE (
            "%%i" -c "import tkinter" >nul 2>nul
            if !errorlevel!==0 set "PYEXE="%%i""
        )
    )
)

if defined PYEXE goto run

echo.
echo [错误] 未找到可用的 Python 环境。
echo 本工具需要 Python 3.8+ 且带 tkinter,官方安装版默认自带。
echo.
echo 请到 https://www.python.org/downloads/windows/ 下载安装,
echo 安装时务必勾选 Add python.exe to PATH,完成后重新双击本文件。
echo.
pause
exit /b 1

:run
echo 正在启动无损视频剪辑工具...
%PYEXE% lossless_editor.py
echo.
echo 工具已退出。
pause
