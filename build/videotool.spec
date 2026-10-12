# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 单文件夹 + 内嵌 ffmpeg/ffprobe。

用法：
    pyinstaller --noconfirm --clean build/videotool.spec

产物：dist/VideoToolbox/VideoToolbox.exe，ffmpeg 位于 dist/VideoToolbox/_internal/ffmpeg/
（runtime 的 paths.ffmpeg_dir() 正是按 sys._MEIPASS/"ffmpeg" 定位，二者对应）
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent          # build/ 的上一级，即项目根目录
FFMPEG_DIR = ROOT / "assets" / "ffmpeg"
ENTRY = ROOT / "src" / "videotool" / "main.py"
ICON = ROOT / "build" / "icon.ico"

# 用不到的 Qt 模块与标准库，剔除以缩小体积
EXCLUDES = [
    "tkinter",
    "pydoc_data",
    "test",
    "lib2to3",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    # 注意：不要排除 QtMultimedia / QtMultimediaWidgets —— 「选段裁切」的预览播放器要用。
    # 它的 FFmpeg 后端依赖 PySide6 自带的 avcodec/avformat 等运行库，
    # 这些 DLL 也必须一起打包（PyInstaller 的 PySide6 hook 会收集）。
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSpatialAudio",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtLocation",
    "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer",
    "PySide6.QtNetworkAuth",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtStateMachine",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=[
        (str(FFMPEG_DIR / "ffmpeg.exe"), "ffmpeg"),
        (str(FFMPEG_DIR / "ffprobe.exe"), "ffmpeg"),
    ],
    datas=[],
    hiddenimports=[
        "PIL._tkinter_finder",  # Pillow 的常规 hook 依赖，缺失会告警
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoToolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩会破坏部分 Qt DLL，自用软件不差这点体积
    console=False,      # 窗口程序，不弹黑色控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoToolbox",
)