#!/usr/bin/env python3
"""Generate a neutral app icon (system-colour squircle with the sparkle glyph the menu bar uses).

Usage: make_native_icon.py OUT.icns
Brand artwork is never generated or stored in the repo: local builds may pass their own icon through
``AIDE_ICON`` (see build_native.sh); this default exists so public builds are not iconless.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSGradient,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakeRect,
    NSPNGFileType,
)


def render(size: int) -> bytes:
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()
    inset = size * 0.0977          # macOS icon grid: 824/1024 artwork inside 1024
    body = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, size * 0.185, size * 0.185)
    gradient = NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.systemIndigoColor(), NSColor.systemBlueColor())
    gradient.drawInBezierPath_angle_(path, -90)
    symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_("sparkle", None)
    size_config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size * 0.42, 5)
    color_config = NSImageSymbolConfiguration.configurationWithPaletteColors_([NSColor.whiteColor()])
    symbol = symbol.imageWithSymbolConfiguration_(size_config.configurationByApplyingConfiguration_(color_config))
    w, h = symbol.size()
    symbol.drawInRect_fromRect_operation_fraction_(
        NSMakeRect((size - w) / 2, (size - h) / 2, w, h), NSMakeRect(0, 0, w, h), 2, 1.0)
    image.unlockFocus()
    rep = NSBitmapImageRep.alloc().initWithData_(image.TIFFRepresentation())
    return bytes(rep.representationUsingType_properties_(NSPNGFileType, None))


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="aide-icon."))
    try:
        iconset = work / "AppIcon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            (iconset / f"icon_{base}x{base}.png").write_bytes(render(base))
            (iconset / f"icon_{base}x{base}@2x.png").write_bytes(render(base * 2))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
