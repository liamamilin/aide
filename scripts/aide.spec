# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI 桌面助手 — macOS .app bundle"""
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))

mlx_datas, mlx_binaries, mlx_hiddenimports = collect_all('mlx')
mlx_audio_datas, mlx_audio_binaries, mlx_audio_hiddenimports = collect_all('mlx_audio')
qwen_tts_hiddenimports = collect_submodules('mlx_audio.tts.models.qwen3_tts')

a = Analysis(
    [os.path.join(ROOT, "ai_desktop", "main.py")],
    pathex=[ROOT],
    binaries=mlx_binaries + mlx_audio_binaries,
    datas=[
        (os.path.join(ROOT, "ai_desktop", "图标.icns"), "ai_desktop"),
        (os.path.join(ROOT, "ai_desktop", "图标.png"), "ai_desktop"),
    ] + mlx_datas + mlx_audio_datas,
    hiddenimports=[
        'pynput.keyboard._darwin',
        'pynput.mouse._darwin',
        'pynput.keyboard._base',
        'pynput.mouse._base',
        # PyObjC — NSEvent 全局监听 + 权限请求所需
        'AppKit',
        'Foundation',
        'CoreFoundation',
        'HIServices',
        'objc',
        'Quartz',
        'PyObjCTools',
        'sounddevice',
    ] + qwen_tts_hiddenimports + mlx_hiddenimports + mlx_audio_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5.QtWebEngine*',
        'PyQt5.QtWebSockets',
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PyQt5.QtBluetooth',
        'PyQt5.QtNfc',
        'PyQt5.QtPositioning',
        'PyQt5.QtSensors',
        'PyQt5.QtQuick*',
        'PyQt5.QtQml*',
        'PyQt5.Qt3D*',
        'PyQt5.QtSql',
        'PyQt5.QtTest',
        'PyQt5.QtDBus',
        'PyQt5.QtXmlPatterns',
        'tkinter',
        'matplotlib',
        'pytest',
        '_pytest',
        'PIL',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AI桌面助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch='arm64',
    entitlements_file=None,
    icon=[os.path.join(ROOT, "ai_desktop", "图标.icns")],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AI桌面助手',
)
app = BUNDLE(
    coll,
    name='AI桌面助手.app',
    icon=os.path.join(ROOT, "ai_desktop", "图标.icns"),
    bundle_identifier='com.milin.ai-desktop-assistant',
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'CFBundleName': 'AI 桌面助手',
        'CFBundleDisplayName': 'AI 桌面助手',
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        # 隐私权限声明（macOS 10.14+ 需要用途说明）
        'NSAppleEventsUsageDescription': '用于读取选中文字和模拟快捷键。',
        'NSAccessibilityUsageDescription': '全局快捷键 ⌘⌃L 需要辅助功能权限来监听键盘事件。',
    },
)
