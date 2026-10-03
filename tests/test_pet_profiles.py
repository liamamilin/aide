"""Profile safety and semantic playback, independent of installed Petdex files."""

import hashlib
import json
import random
from pathlib import Path

import pytest

from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import ManifestError, load_manifest
from ai_desktop.ui.pet_profiles import apply_petdex_profile

PROFILES = Path(__file__).resolve().parents[1] / "ai_desktop/pets/petdex-profiles"


@pytest.mark.parametrize("name", ["astra", "boba", "shinchan"])
def test_profile_actions_stay_still_and_return_home(name):
    manifest = load_manifest(PROFILES / f"{name}.json")
    rest = manifest.animations[manifest.states["idle"].base].frames[0]
    assert len(manifest.animations[manifest.states["idle"].base].frames) == 1
    assert len(manifest.states["idle"].ambient) == 5
    assert len(manifest.states["hover"].reactions) == 5
    for gesture in manifest.states["idle"].ambient + manifest.states["hover"].reactions:
        animation = manifest.animations[gesture]
        assert animation.frames[0] == animation.frames[-1] == rest
        assert sum(animation.durations_ms) <= 1500
    # Known run/jump rows are not suitable for a stationary assistant.
    assert not any(8 <= index < 24 or 32 <= index < 40
                   for animation in manifest.animations.values() for index in animation.frames)
    animator = PetAnimator(manifest, rng=random.Random(5))
    animator.set_state("hover")
    assert animator.react()
    animator.settle_to("idle")
    animator.tick(2)
    assert animator.current_state == "idle"
    assert animator.snapshot().frame_index == rest
    assert animator.layer == "base"
    animator.set_state("working")
    assert animator.next_wake_seconds is None
    animator.tick(20)
    assert animator.snapshot().frame_index == manifest.animations["focus"].frames[0]


def _matched_profile(tmp_path, name="boba"):
    raw = json.loads((PROFILES / f"{name}.json").read_text())
    atlas = tmp_path / "spritesheet.webp"
    atlas.write_bytes(b"synthetic atlas")
    raw["atlas_sha256"] = hashlib.sha256(atlas.read_bytes()).hexdigest()
    (tmp_path / f"{name}.json").write_text(json.dumps(raw))
    original_path = tmp_path / "pet.json"
    original_path.write_text(json.dumps(raw))
    return load_manifest(original_path), atlas, original_path


def test_profile_overlay_leaves_original_files_untouched(tmp_path):
    original, atlas, source = _matched_profile(tmp_path)
    before = source.read_bytes(), atlas.read_bytes()
    profiled = apply_petdex_profile(original, atlas, profile_dir=tmp_path)
    assert profiled.profile_id == "petdex-boba-v1"
    assert (source.read_bytes(), atlas.read_bytes()) == before
    assert original.profile_id == ""


def test_regenerated_atlas_does_not_get_an_old_frame_mapping(tmp_path):
    original, atlas, _ = _matched_profile(tmp_path)
    atlas.write_bytes(b"regenerated different character")
    assert apply_petdex_profile(original, atlas, profile_dir=tmp_path) is original


def test_grid_mismatch_preserves_custom_manifest(tmp_path):
    original, atlas, _ = _matched_profile(tmp_path)
    raw = json.loads((tmp_path / "boba.json").read_text())
    raw["spritesheet"]["columns"] = 4
    (tmp_path / "boba.json").write_text(json.dumps(raw))
    assert apply_petdex_profile(original, atlas, profile_dir=tmp_path) is original


def test_missing_or_broken_profile_preserves_custom_manifest(tmp_path):
    original, atlas, _ = _matched_profile(tmp_path)
    profile = tmp_path / "boba.json"
    profile.write_text("{")
    assert apply_petdex_profile(original, atlas, profile_dir=tmp_path) is original
    profile.unlink()
    assert apply_petdex_profile(original, atlas, profile_dir=tmp_path) is original


@pytest.mark.parametrize("options", [
    {"pixel_art": "true"}, {"scale": 1.1}, {"scale": float("nan")},
    {"scale": True}, {"baseline": 0.3}, {"baseline": float("inf")},
    {"remove_magenta_matte": 1},
])
def test_invalid_rendering_options_are_rejected(tmp_path, options):
    raw = json.loads((PROFILES / "boba.json").read_text())
    raw["rendering"] = options
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ManifestError, match="rendering"):
        load_manifest(path)


def test_profile_reaches_widget_and_task_states_without_mutating_pet(qtbot, tmp_path, monkeypatch):
    from PyQt5.QtCore import QEvent
    from PyQt5.QtGui import QColor, QImage

    from ai_desktop import config
    from ai_desktop.ui.float_button import FloatButton

    _, atlas, source = _matched_profile(tmp_path)
    image = QImage(1536, 2288, QImage.Format_ARGB32)
    image.fill(QColor("#734924"))
    assert image.save(str(atlas), "PNG")
    raw = json.loads((tmp_path / "boba.json").read_text())
    raw["atlas_sha256"] = hashlib.sha256(atlas.read_bytes()).hexdigest()
    (tmp_path / "boba.json").write_text(json.dumps(raw))
    before = source.read_bytes(), atlas.read_bytes()
    monkeypatch.setattr(config, "PET_SOURCE", "petdex")
    monkeypatch.setattr(config, "PET_NAME", "boba")
    monkeypatch.setattr("ai_desktop.ui.pet_profiles.resource_path", lambda *args: str(tmp_path))
    monkeypatch.setattr("ai_desktop.ui.float_button._get_pet_manifest_path", lambda *args: str(source))
    button = FloatButton()
    qtbot.addWidget(button)
    button.show()
    assert button._animator.manifest.profile_id == "petdex-boba-v1"
    assert button._animator.manifest.rendering.pixel_art
    button.enterEvent(QEvent(QEvent.Enter))
    assert button._animator.layer == "reaction"
    button.set_responding(True)
    assert button._animator.current_state == "working"
    assert button._animator.snapshot().frame_index == 65
    button.set_reduce_motion(True)
    assert not button._animation_timer.isActive()
    assert not button._idle_wake_timer.isActive()
    button.set_responding(False)
    button.set_speaking(True)
    assert button._animator.current_state == "speaking"
    assert button._animator.snapshot().frame_index == 0
    assert (source.read_bytes(), atlas.read_bytes()) == before
