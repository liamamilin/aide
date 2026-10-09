"""Finite part motion preserves body pixels, scheduling and bounded cache use."""

import copy
import json
import random
from pathlib import Path

import pytest
from PyQt5.QtCore import QPoint, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPixmap

from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import ManifestError, load_manifest
from ai_desktop.ui.pet_secondary import SecondaryPlayer, compose_secondary
from ai_desktop.ui.spritesheet import Spritesheet

ROOT = Path(__file__).resolve().parents[1]
PATHS = [ROOT / "ai_desktop/pets/owl-v2/pet.json", ROOT / "ai_desktop/pets/petdex-profiles/boba.json"]


@pytest.fixture(params=PATHS, ids=["owl", "boba"])
def manifest(request):
    return load_manifest(request.param)


def test_delayed_motion_is_bounded_finite_and_not_restarted(manifest):
    motion = manifest.secondary_motion
    player = SecondaryPlayer(motion)
    player.trigger("release")
    assert player.active and not any(player.angles(motion.frames[0]))
    player.advance(min(part.delay_ms for part in motion.parts) / 1000 - .001)
    assert not any(player.angles(motion.frames[0]))
    samples = []
    for _ in range(50):
        player.advance(.03)
        angles = player.angles(motion.frames[0])
        for part, angle in zip(motion.parts, angles):
            assert abs(angle) <= abs(part.angle_deg)
        samples.append(angles)
        before = player.angles(motion.frames[0])
        if player.active:
            player.trigger("release")
            assert player.angles(motion.frames[0]) == before
    assert any(any(pose) for pose in samples)
    assert any(pose and pose[0] < 0 for pose in samples)
    assert any(pose and pose[0] > 0 for pose in samples)
    assert not player.active and player.next_wake_seconds is None
    assert player.angles(motion.frames[0]) == ()


def test_follow_through_can_outlast_primary_release_then_goes_to_sleep(manifest):
    animator = PetAnimator(manifest, rng=random.Random(5))
    animator.set_state("hover", animate=False)
    assert animator.gesture("grab", hold=True)
    assert animator.next_wake_seconds is None
    assert animator.gesture("release")
    animator.tick(.6)
    assert animator.layer == "base"
    assert animator._secondary.active and animator.next_wake_seconds <= 1 / 24
    assert not animator.react()
    animator.tick(.6)
    assert animator.next_wake_seconds is None
    assert animator.snapshot().secondary_angles == ()


@pytest.mark.parametrize("action", ["task", "reduced", "held"])
def test_tasks_reduced_motion_and_holding_clear_part_motion_immediately(manifest, action):
    animator = PetAnimator(manifest)
    assert animator.gesture("signature")
    animator.tick(.25)
    assert any(animator.snapshot().secondary_angles)
    if action == "task":
        animator.set_state("working")
    elif action == "reduced":
        animator.set_state("idle", restart=True, animate=False)
    else:
        assert animator.gesture("press", hold=True)
    assert animator.snapshot().secondary_angles == ()
    assert not animator._secondary.active


def test_unsupported_frames_and_old_manifests_remain_static(manifest):
    player = SecondaryPlayer(manifest.secondary_motion)
    player.trigger("release")
    player.advance(.25)
    assert player.angles(-1) == ()
    player = SecondaryPlayer(None)
    player.trigger("release")
    player.advance(20)
    assert player.angles(0) == () and player.next_wake_seconds is None


def test_sleep_expires_old_part_motion_without_advancing_body(manifest):
    animator = PetAnimator(manifest)
    assert animator.gesture("signature")
    animator.tick(.25)
    before = animator.snapshot()
    assert any(before.secondary_angles)
    animator.tick(0, elapsed_seconds=120)
    assert animator.snapshot().frame_index == before.frame_index
    assert animator.snapshot().animation_name == before.animation_name
    assert animator.snapshot().secondary_angles == ()


@pytest.fixture
def source(manifest, qapp):
    info = manifest.spritesheet
    if manifest.name == "owl":
        sheet = Spritesheet(ROOT / "ai_desktop/pets/owl-v2" / info.file,
                            info.frame_width, info.frame_height, info.columns, info.frame_count)
        assert sheet.load()
        return sheet.frame(0)
    # CI uses the real Boba calibration with a connected synthetic pixel pet.
    pixmap = QPixmap(info.frame_width, info.frame_height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#956b44"))
    painter.drawRect(40, 40, 104, 155)
    path = QPainterPath()
    path.moveTo(136, 190)
    path.lineTo(169, 155)
    path.lineTo(173, 170)
    path.lineTo(151, 190)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return pixmap


@pytest.mark.parametrize("factor", [-1, -.5, .5, 1])
def test_composition_changes_only_owned_part_areas(manifest, source, factor):
    motion = manifest.secondary_motion
    angles = tuple(part.angle_deg * factor for part in motion.parts)
    result = compose_secondary(source, motion, angles, pixel_art=manifest.rendering.pixel_art).toImage()
    original = source.toImage()
    changed = 0
    for y in range(original.height()):
        for x in range(original.width()):
            if result.pixelColor(x, y) != original.pixelColor(x, y):
                changed += 1
                assert any(QRectF(*part.bounds).contains(QPoint(x, y)) for part in motion.parts)
    assert changed > 20
    # Faces, cups/laptop and feet are outside the calibrated ownership areas.
    assert compose_secondary(source, motion, (0,) * len(motion.parts)) is source


def test_pose_cache_is_bounded_and_reload_clears_it(manifest, source, tmp_path):
    path = tmp_path / "source.png"
    source.save(str(path))
    sheet = Spritesheet(path, source.width(), source.height(), 1, 1)
    assert sheet.load()
    motion = manifest.secondary_motion
    for i in range(41):
        angles = tuple(round(part.angle_deg * (i - 20) / 20 * 2) / 2 for part in motion.parts)
        result = sheet.frame(0, secondary=motion, angles=angles, pixel_art=manifest.rendering.pixel_art)
        assert not result.isNull()
    assert 0 < len(sheet._secondary_cache) <= sheet._secondary_cache_limit <= 24
    assert sheet.frame(0).toImage() == source.toImage()
    assert sheet.load() and not sheet._secondary_cache


@pytest.mark.parametrize("change", [
    {"frames": []}, {"frames": [True]}, {"frames": [256]}, {"frames": [0, 0]},
    {"duration_ms": 199}, {"duration_ms": 1201}, {"fps": True}, {"fps": 31},
    {"triggers": {}}, {"triggers": {"missing": .5}}, {"triggers": {"release": float("nan")}},
    {"triggers": {"release": 1.1}}, {"parts": []},
])
def test_invalid_motion_metadata_is_rejected(tmp_path, change):
    data = json.loads(PATHS[0].read_text())
    data["secondary_motion"].update(change)
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="secondary_motion"):
        load_manifest(path)


@pytest.mark.parametrize("change", [
    {"angle_deg": 0}, {"angle_deg": 13}, {"angle_deg": True}, {"angle_deg": float("inf")},
    {"delay_ms": 301}, {"delay_ms": -1}, {"pivot": [float("nan"), 45]},
    {"pivot": [0, 0]}, {"bounds": [-1, 0, 10, 10]}, {"bounds": [0, 0, 500, 500]},
    {"source_mask": [[1, 1], [2, 2]]}, {"cutout_mask": [[0, 0], [1, 0], [1, 1]]},
])
def test_invalid_part_geometry_is_rejected(tmp_path, change):
    data = json.loads(PATHS[0].read_text())
    data["secondary_motion"]["parts"][0].update(copy.deepcopy(change))
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="secondary_motion"):
        load_manifest(path)
