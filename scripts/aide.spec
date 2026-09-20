# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI 桌面助手 — macOS .app bundle"""
import os
import runpy

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(SPEC)), ".."))
VERSION = runpy.run_path(os.path.join(ROOT, "ai_desktop", "version.py"))["__version__"]

a = Analysis(
    [os.path.join(ROOT, "ai_desktop", "main.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "ai_desktop", "图标.icns"), "ai_desktop"),
        (os.path.join(ROOT, "ai_desktop", "图标.png"), "ai_desktop"),
        (os.path.join(ROOT, "ai_desktop", "桌面宠物.png"), "ai_desktop"),
        (os.path.join(ROOT, "ai_desktop", "pet_frames", "idle.png"), "ai_desktop/pet_frames"),
        (os.path.join(ROOT, "ai_desktop", "pet_frames", "hover.png"), "ai_desktop/pet_frames"),
        # Kokoro/Misaki uses language-tags JSON data at runtime.  PyInstaller
        # does not collect this package data automatically.
        *collect_data_files("language_tags"),
        # espeakng_loader supplies the pronunciation engine used by Misaki.
        # Its espeak-ng-data directory is required at runtime by the frozen app.
        *collect_data_files("espeakng_loader"),
        # spaCy's English tokenizer/model is loaded lazily by Kokoro/Misaki.
        *collect_data_files("en_core_web_sm"),
    ],
    binaries=[
        *collect_dynamic_libs("espeakng_loader"),
    ],
    hiddenimports=[
        'PyQt5.QtNetwork',
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
        # F02 local OCR (Vision pulls CoreML as its native model bridge)
        'CoreML',
        'Vision',
        'PyObjCTools',
        'espeakng_loader',
        'en_core_web_sm',
        *collect_submodules("en_core_web_sm"),
    ],
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
        'scipy',
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
    target_arch=None,
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
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'CFBundleName': 'AI 桌面助手',
        'CFBundleDisplayName': 'AI 桌面助手',
        'NSHighResolutionCapable': True,
        'NSSupportsAutomaticGraphicsSwitching': True,
        # 隐私权限声明（macOS 10.14+ 需要用途说明）
        'NSAppleEventsUsageDescription': '用于读取选中文字和模拟快捷键。',
        'NSAccessibilityUsageDescription': '全局快捷键 ⌘⌃L 需要辅助功能权限来监听键盘事件。',
    },
)
