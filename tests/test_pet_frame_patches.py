"""Authored limb patches preserve identity and fail back without losing the pet."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage

from ai_desktop.ui.pet_manifest import ManifestError, load_manifest
from ai_desktop.ui.spritesheet import Spritesheet


@pytest.fixture
def assets(qtbot, tmp_path):
    original = QImage(64, 16, QImage.Format_RGBA8888)
    for y in range(16):
        for x in range(64):
            original.setPixelColor(x, y, QColor(20 + x // 16 * 30, 80, 120, 255))
    original.save(str(tmp_path / "original.png"))
    patch = QImage(32, 16, QImage.Format_RGBA8888)
    patch.fill(Qt.transparent)
    for y in range(16):
        for x in range(16):
            patch.setPixelColor(x, y, QColor(220, 40, 20))
    patch.setPixelColor(16 + 10, 7, QColor(30, 240, 50))
    patch.save(str(tmp_path / "patch.png"))
    data = {
        "schema_version": 1, "name": "patch fixture",
        "spritesheet": {"file": "original.png", "frame_width": 16, "frame_height": 16,
                        "columns": 4, "frame_count": 4},
        "states": {"idle": {"base": "rest"}},
        "animations": {"rest": {"frames": [1], "loop": True},
                       "gesture": {"frames": [1, 4, 5, 6, 1], "loop": False}},
        "frame_patches": {
            "file": "patch.png", "columns": 2, "frame_count": 2, "base_frame": 1,
            "mask": [[8, 4], [14, 4], [14, 12], [8, 12]],
            "frames": [{"cell": 0}, {"cell": 1}, {"source_frame": 2}],
            "sha256": hashlib.sha256((tmp_path / "patch.png").read_bytes()).hexdigest(),
        },
    }
    return tmp_path, data


def load(assets, data=None):
    directory, original = assets
    path = directory / "pet.json"
    path.write_text(json.dumps(data or original))
    manifest = load_manifest(path)
    sheet = Spritesheet.from_manifest(directory, "original.png", 16, 16, 4, 4,
                                     frame_patches=manifest.frame_patches)
    assert sheet.load()
    return manifest, sheet


def test_append_keeps_source_grid_and_all_pixels_outside_limb(assets):
    manifest, sheet = load(assets)
    assert manifest.spritesheet.frame_count == 4
    assert manifest.total_frame_count == sheet.frame_count == 7
    assert sheet.is_loaded and sheet.patches_loaded
    base = sheet.frame(1).toImage()
    for frame in (4, 5, 6):
        image = sheet.frame(frame).toImage()
        for y in range(16):
            for x in range(16):
                if not (8 <= x < 14 and 4 <= y < 12):
                    assert image.pixelColor(x, y) == base.pixelColor(x, y)
    assert sheet.frame(4).toImage().pixelColor(10, 7) == QColor(220, 40, 20)
    assert sheet.frame(5).toImage().pixelColor(10, 7) == QColor(30, 240, 50)
    assert sheet.frame(5).toImage().pixelColor(9, 7).alpha() == 0  # Erase the old limb, no ghosting.
    assert sheet.frame(6).toImage().pixelColor(10, 7) == sheet.frame(2).toImage().pixelColor(10, 7)
    assert sheet.load() and sheet.frame_count == 7  # Reload cannot append twice.
    assert sheet.frame(7).isNull()


@pytest.mark.parametrize("failure", ["missing", "corrupt", "wrong_size", "wrong_fingerprint"])
def test_unavailable_patch_falls_back_to_original_and_keeps_normalized_endpoint(assets, failure, caplog):
    directory, data = assets
    path = directory / "patch.png"
    if failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"invalid png")
    elif failure == "wrong_size":
        image = QImage(8, 8, QImage.Format_RGBA8888)
        image.fill(Qt.red)
        image.save(str(path))
    else:
        data["frame_patches"]["sha256"] = "0" * 64
    _, sheet = load(assets)
    assert sheet.is_loaded and not sheet.patches_loaded
    assert sheet.frame_count == 7
    for frame in (4, 5):
        assert sheet.frame(frame).toImage() == sheet.frame(1).toImage()
    assert sheet.frame(6).toImage().pixelColor(10, 7) == sheet.frame(2).toImage().pixelColor(10, 7)
    assert "using original poses" in caplog.text


@pytest.mark.parametrize("change", [
    {"file": "../patch.png"}, {"file": "/private/tmp/patch.png"},
    {"columns": 0}, {"columns": True}, {"frame_count": 257}, {"base_frame": 4},
    {"mask": [[0, 0], [2, 2]]}, {"mask": [[0, 0], [17, 4], [5, 5]]},
    {"mask": [[0, 0], [2, float("nan")], [5, 5]]},
    {"frames": []}, {"frames": [{"cell": 2}]}, {"frames": [{"source_frame": 4}]},
    {"frames": [{"cell": 0, "source_frame": 0}]}, {"frames": [{"cell": 0, "fallback_frame": -1}]},
    {"frames": [{"cell": 0}] * 253}, {"sha256": "wrong"},
])
def test_invalid_patch_metadata_is_rejected(assets, change):
    directory, original = assets
    data = copy.deepcopy(original)
    data["frame_patches"].update(change)
    path = directory / "pet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="frame_patches"):
        load_manifest(path)


def test_patch_symlink_cannot_escape_manifest_directory(assets, tmp_path):
    directory, data = assets
    nested = directory / "nested"
    nested.mkdir()
    (nested / "escape.png").symlink_to(directory / "patch.png")
    data["frame_patches"]["file"] = "escape.png"
    path = nested / "pet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="resolves outside"):
        load_manifest(path)


@pytest.mark.parametrize("name", ["astra", "boba"])
def test_curated_patches_and_all_interruption_bridges_stay_consistent(qtbot, tmp_path, name):
    from ai_desktop.ui.pet_animator import PetAnimator

    manifest = load_manifest(Path(__file__).resolve().parents[1] / f"ai_desktop/pets/petdex-profiles/{name}.json")
    info = manifest.spritesheet
    image = QImage(info.columns * info.frame_width,
                   ((info.frame_count + info.columns - 1) // info.columns) * info.frame_height,
                   QImage.Format_RGBA8888)
    image.fill(QColor(120, 90, 60))
    path = tmp_path / "original.png"
    image.save(str(path))
    sheet = Spritesheet(path, info.frame_width, info.frame_height, info.columns, info.frame_count,
                        frame_patches=manifest.frame_patches)
    assert sheet.load() and sheet.patches_loaded
    assert manifest.total_frame_count == sheet.frame_count == info.frame_count + 4
    for frame in range(info.frame_count, sheet.frame_count):
        animator = PetAnimator(manifest)
        # Every added pose is present in a real signature and has a bridge to
        # both rest and focus, so work never waits for the wave to finish.
        assert frame in manifest.animations[manifest.interactions["signature"]].frames
        animator._start_animation("limb_to_rest", "transition")
        animator._frame_pos = manifest.animations["limb_to_rest"].frames.index(frame)
        animator.set_state("working")
        assert animator.current_state == "working"
        assert animator.snapshot().frame_index == frame
        animator.tick(.6)
        assert animator.layer == "base"
        assert animator.next_wake_seconds is None
