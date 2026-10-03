"""Tests for pet manifest parsing and validation."""

import json
from pathlib import Path

import pytest

from ai_desktop.ui.pet_manifest import (
    ManifestError,
    load_manifest,
)


def _write_manifest(tmp_path: Path, data: dict) -> str:
    manifest_file = tmp_path / "pet.json"
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    return str(manifest_file)


def _minimal_manifest() -> dict:
    return {
        "schema_version": 1,
        "name": "test-pet",
        "spritesheet": {
            "file": "spritesheet.png",
            "frame_width": 512,
            "frame_height": 512,
            "columns": 4,
            "frame_count": 8,
        },
        "anchor": {"x": 0.5, "y": 0.95},
        "defaults": {"state": "idle", "fps": 8},
        "states": {
            "idle": {"base": "idle_base"},
        },
        "animations": {
            "idle_base": {"frames": [0, 1, 2, 3], "fps": 5, "loop": True},
        },
    }


class TestLoadManifest:
    def test_loads_valid_manifest(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_manifest())
        manifest = load_manifest(path)

        assert manifest.schema_version == 1
        assert manifest.name == "test-pet"
        assert manifest.spritesheet.file == "spritesheet.png"
        assert manifest.spritesheet.frame_width == 512
        assert manifest.spritesheet.frame_height == 512
        assert manifest.spritesheet.columns == 4
        assert manifest.spritesheet.frame_count == 8
        assert manifest.anchor.x == 0.5
        assert manifest.anchor.y == 0.95
        assert manifest.default_state == "idle"
        assert manifest.default_fps == 8
        assert "idle" in manifest.states
        assert manifest.states["idle"].base == "idle_base"
        assert "idle_base" in manifest.animations
        assert manifest.animations["idle_base"].frames == [0, 1, 2, 3]
        assert manifest.animations["idle_base"].fps == 5
        assert manifest.animations["idle_base"].loop is True

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(ManifestError, match="not found"):
            load_manifest(tmp_path / "nonexistent.json")

    def test_rejects_invalid_schema_version(self, tmp_path):
        data = _minimal_manifest()
        data["schema_version"] = 2
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="unsupported schema_version"):
            load_manifest(path)

    def test_rejects_missing_name(self, tmp_path):
        data = _minimal_manifest()
        del data["name"]
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="non-empty 'name'"):
            load_manifest(path)

    def test_rejects_invalid_spritesheet_fields(self, tmp_path):
        data = _minimal_manifest()
        data["spritesheet"]["frame_width"] = -1
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="positive integer"):
            load_manifest(path)

    def test_rejects_anchor_out_of_range(self, tmp_path):
        data = _minimal_manifest()
        data["anchor"]["x"] = 1.5
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="anchor.x must be between"):
            load_manifest(path)

    def test_rejects_unknown_default_state(self, tmp_path):
        data = _minimal_manifest()
        data["defaults"]["state"] = "nonexistent"
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="not defined in states"):
            load_manifest(path)

    def test_rejects_unknown_base_animation(self, tmp_path):
        data = _minimal_manifest()
        data["states"]["idle"]["base"] = "nonexistent_anim"
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="base animation.*not found"):
            load_manifest(path)

    def test_rejects_unknown_ambient_animation(self, tmp_path):
        data = _minimal_manifest()
        data["states"]["idle"]["ambient"] = ["nonexistent_ambient"]
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="ambient.*not found"):
            load_manifest(path)

    def test_rejects_unknown_next_state(self, tmp_path):
        data = _minimal_manifest()
        data["animations"]["idle_base"]["next_state"] = "nonexistent"
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="next_state.*not in states"):
            load_manifest(path)

    def test_parses_animation_with_next_state(self, tmp_path):
        data = _minimal_manifest()
        data["states"]["success"] = {"base": "success_anim"}
        data["animations"]["success_anim"] = {
            "frames": [4, 5, 6],
            "fps": 10,
            "loop": False,
            "next_state": "idle",
        }
        path = _write_manifest(tmp_path, data)
        manifest = load_manifest(path)

        assert manifest.animations["success_anim"].next_state == "idle"
        assert manifest.animations["success_anim"].loop is False

    def test_parses_state_with_ambient(self, tmp_path):
        data = _minimal_manifest()
        data["animations"]["blink"] = {
            "frames": [4, 5],
            "fps": 10,
            "loop": False,
        }
        data["states"]["idle"]["ambient"] = ["blink"]
        path = _write_manifest(tmp_path, data)
        manifest = load_manifest(path)

        assert manifest.states["idle"].ambient == ["blink"]

    def test_uses_default_anchor_when_missing(self, tmp_path):
        data = _minimal_manifest()
        del data["anchor"]
        path = _write_manifest(tmp_path, data)
        manifest = load_manifest(path)

        assert manifest.anchor.x == 0.5
        assert manifest.anchor.y == 1.0

    def test_rejects_empty_frames_list(self, tmp_path):
        data = _minimal_manifest()
        data["animations"]["idle_base"]["frames"] = []
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="non-empty 'frames'"):
            load_manifest(path)

    def test_rejects_negative_frame_index(self, tmp_path):
        data = _minimal_manifest()
        data["animations"]["idle_base"]["frames"] = [0, -1, 2]
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="non-negative integers"):
            load_manifest(path)

    def test_rejects_zero_fps(self, tmp_path):
        data = _minimal_manifest()
        data["animations"]["idle_base"]["fps"] = 0
        path = _write_manifest(tmp_path, data)
        with pytest.raises(ManifestError, match="fps must be positive"):
            load_manifest(path)


@pytest.mark.parametrize("mutate", [
    lambda data: data["animations"]["idle_base"].update(frames=[8]),
    lambda data: data["animations"]["idle_base"].update(fps=float("nan")),
    lambda data: data["animations"]["idle_base"].update(fps=float("inf")),
    lambda data: data["animations"]["idle_base"].update(durations_ms=[100]),
    lambda data: data["animations"]["idle_base"].update(durations_ms=[100, 0, 100, 100]),
    lambda data: data["states"]["idle"].update(ambient=["idle_base"]),
    lambda data: data["spritesheet"].update(file="../external.png"),
])
def test_rejects_unsafe_or_unplayable_sequences(tmp_path, mutate):
    data = _minimal_manifest()
    mutate(data)
    with pytest.raises(ManifestError):
        load_manifest(_write_manifest(tmp_path, data))


def test_preserves_fractional_fps_and_per_frame_duration(tmp_path):
    data = _minimal_manifest()
    data["animations"]["idle_base"].update(fps=0.5, durations_ms=[280, 40, 100, 320])
    animation = load_manifest(_write_manifest(tmp_path, data)).animations["idle_base"]
    assert animation.fps == .5
    assert animation.duration(1) == .04
