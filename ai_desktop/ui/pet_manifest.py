"""Parse and validate pet.json manifests for the spritesheet pet engine."""

import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SpritesheetInfo:
    file: str
    frame_width: int
    frame_height: int
    columns: int
    frame_count: int


@dataclass(frozen=True)
class Anchor:
    x: float
    y: float


@dataclass(frozen=True)
class AnimationDef:
    name: str
    frames: list[int]
    fps: float
    loop: bool
    next_state: str | None = None
    durations_ms: list[int] = field(default_factory=list)

    def duration(self, position: int) -> float:
        return self.durations_ms[position] / 1000 if self.durations_ms else 1 / self.fps


@dataclass(frozen=True)
class StateDef:
    base: str
    ambient: list[str] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RenderingOptions:
    pixel_art: bool = False
    scale: float = 1.0
    baseline: float | None = None
    remove_magenta_matte: bool = False


@dataclass(frozen=True)
class PetManifest:
    schema_version: int
    name: str
    spritesheet: SpritesheetInfo
    anchor: Anchor
    default_state: str
    default_fps: float
    states: dict[str, StateDef]
    animations: dict[str, AnimationDef]
    rendering: RenderingOptions = field(default_factory=RenderingOptions)
    profile_id: str = ""

    @property
    def spritesheet_path(self) -> str:
        return self.spritesheet.file


class ManifestError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _parse_animation(name: str, data: dict) -> AnimationDef:
    _require(isinstance(data, dict), f"animation '{name}' must be an object")
    frames = data.get("frames")
    _require(isinstance(frames, list) and len(frames) > 0,
             f"animation '{name}' requires a non-empty 'frames' list")
    _require(all(type(f) is int and f >= 0 for f in frames),
             f"animation '{name}' frames must be non-negative integers")
    fps = data.get("fps", 8)
    _require(type(fps) in (int, float) and math.isfinite(fps) and fps > 0,
             f"animation '{name}' fps must be positive")
    loop = data.get("loop", True)
    _require(isinstance(loop, bool), f"animation '{name}' loop must be boolean")
    next_state = data.get("next_state")
    durations = data.get("durations_ms", [])
    _require(isinstance(durations, list) and (not durations or len(durations) == len(frames))
             and all(type(d) is int and d > 0 for d in durations),
             f"animation '{name}' durations_ms must match frames with positive integers")
    if next_state is not None:
        _require(isinstance(next_state, str),
                 f"animation '{name}' next_state must be a string")
    return AnimationDef(
        name=name,
        frames=list(frames),
        fps=float(fps),
        loop=bool(loop),
        next_state=next_state,
        durations_ms=list(durations),
    )


def _parse_state(name: str, data: dict) -> StateDef:
    _require(isinstance(data, dict), f"state '{name}' must be an object")
    base = data.get("base")
    _require(isinstance(base, str) and base,
             f"state '{name}' requires a non-empty 'base' animation name")
    ambient = data.get("ambient", [])
    _require(isinstance(ambient, list),
             f"state '{name}' ambient must be a list")
    _require(all(isinstance(a, str) and a for a in ambient),
             f"state '{name}' ambient entries must be non-empty strings")
    reactions = data.get("reactions", [])
    _require(isinstance(reactions, list) and all(isinstance(a, str) and a for a in reactions),
             f"state '{name}' reactions must be a list of animation names")
    return StateDef(base=base, ambient=list(ambient), reactions=list(reactions))


def load_manifest(path: str | Path) -> PetManifest:
    """Load and validate a pet.json manifest file."""
    manifest_path = Path(path)
    _require(manifest_path.exists(), f"manifest not found: {manifest_path}")
    try:
        data = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc

    try:
        import json
        root = json.loads(data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ManifestError(f"invalid JSON: {exc}") from exc

    _require(isinstance(root, dict), "manifest root must be an object")

    schema_version = root.get("schema_version")
    _require(type(schema_version) is int and schema_version == 1,
             f"unsupported schema_version: {schema_version}")

    name = root.get("name")
    _require(isinstance(name, str) and name, "manifest requires a non-empty 'name'")

    ss_data = root.get("spritesheet")
    _require(isinstance(ss_data, dict), "manifest requires a 'spritesheet' object")
    ss_file = ss_data.get("file")
    _require(isinstance(ss_file, str) and ss_file,
             "spritesheet requires a non-empty 'file'")
    _require(not Path(ss_file).is_absolute() and ".." not in Path(ss_file).parts,
             "spritesheet file must stay inside the pet directory")
    ss_fw = ss_data.get("frame_width")
    ss_fh = ss_data.get("frame_height")
    ss_cols = ss_data.get("columns")
    ss_count = ss_data.get("frame_count")
    for key, val, label in [
        ("frame_width", ss_fw, "spritesheet"),
        ("frame_height", ss_fh, "spritesheet"),
        ("columns", ss_cols, "spritesheet"),
        ("frame_count", ss_count, "spritesheet"),
    ]:
        _require(type(val) is int and val > 0,
                 f"{label} requires a positive integer '{key}'")
    _require(ss_count <= 256 and ss_fw * ss_fh * ss_count <= 16_000_000,
             "spritesheet exceeds the frame/pixel budget")
    spritesheet = SpritesheetInfo(
        file=ss_file,
        frame_width=ss_fw,
        frame_height=ss_fh,
        columns=ss_cols,
        frame_count=ss_count,
    )

    anchor_data = root.get("anchor", {"x": 0.5, "y": 1.0})
    _require(isinstance(anchor_data, dict), "anchor must be an object")
    anchor_x = anchor_data.get("x", 0.5)
    anchor_y = anchor_data.get("y", 1.0)
    _require(isinstance(anchor_x, (int, float)) and 0.0 <= anchor_x <= 1.0,
             "anchor.x must be between 0 and 1")
    _require(isinstance(anchor_y, (int, float)) and 0.0 <= anchor_y <= 1.0,
             "anchor.y must be between 0 and 1")
    anchor = Anchor(x=float(anchor_x), y=float(anchor_y))

    rendering_data = root.get("rendering", {})
    _require(isinstance(rendering_data, dict), "rendering must be an object")
    pixel_art = rendering_data.get("pixel_art", False)
    remove_matte = rendering_data.get("remove_magenta_matte", False)
    render_scale = rendering_data.get("scale", 1.0)
    baseline = rendering_data.get("baseline")
    _require(type(pixel_art) is bool, "rendering.pixel_art must be boolean")
    _require(type(remove_matte) is bool, "rendering.remove_magenta_matte must be boolean")
    _require(type(render_scale) in (int, float) and 0.5 <= render_scale <= 1.0,
             "rendering.scale must be between 0.5 and 1")
    _require(baseline is None or (type(baseline) in (int, float) and 0.5 <= baseline <= 1.0),
             "rendering.baseline must be between 0.5 and 1")

    defaults = root.get("defaults", {})
    _require(isinstance(defaults, dict), "defaults must be an object")
    default_state = defaults.get("state", "idle")
    _require(isinstance(default_state, str) and default_state,
             "defaults.state must be a non-empty string")
    default_fps = defaults.get("fps", 8)
    _require(type(default_fps) in (int, float) and math.isfinite(default_fps) and default_fps > 0,
             "defaults.fps must be positive")

    states_data = root.get("states", {})
    _require(isinstance(states_data, dict), "states must be an object")
    states: dict[str, StateDef] = {}
    for state_name, state_raw in states_data.items():
        states[state_name] = _parse_state(state_name, state_raw)

    anims_data = root.get("animations", {})
    _require(isinstance(anims_data, dict), "animations must be an object")
    animations: dict[str, AnimationDef] = {}
    for anim_name, anim_raw in anims_data.items():
        animations[anim_name] = _parse_animation(anim_name, anim_raw)

    _require(default_state in states,
             f"defaults.state '{default_state}' not defined in states")
    for state_name, state_def in states.items():
        _require(state_def.base in animations,
                 f"state '{state_name}' base animation '{state_def.base}' not found")
        for ambient_name in state_def.ambient + state_def.reactions:
            _require(ambient_name in animations,
                 f"state '{state_name}' ambient '{ambient_name}' not found")
            _require(not animations[ambient_name].loop,
                     f"state '{state_name}' one-shot '{ambient_name}' cannot loop")
    for anim_def in animations.values():
        _require(all(f < ss_count for f in anim_def.frames),
                 f"animation '{anim_def.name}' frame exceeds spritesheet frame_count")
        if anim_def.next_state is not None:
            _require(anim_def.next_state in states,
                     f"animation '{anim_def.name}' next_state '{anim_def.next_state}' not in states")

    return PetManifest(
        schema_version=1,
        name=name,
        spritesheet=spritesheet,
        anchor=anchor,
        default_state=default_state,
        default_fps=float(default_fps),
        states=states,
        animations=animations,
        rendering=RenderingOptions(pixel_art, float(render_scale), baseline, remove_matte),
    )
