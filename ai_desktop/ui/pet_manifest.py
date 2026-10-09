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
    family: str = ""
    weight: float = 1.0
    cooldown_ms: int = 0
    tempo_variation: float = 0.0

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
class PatchFrame:
    cell: int | None
    source_frame: int | None
    fallback_frame: int


@dataclass(frozen=True)
class FramePatches:
    image_path: Path
    columns: int
    frame_count: int
    base_frame: int
    mask: tuple[tuple[float, float], ...]
    frames: list[PatchFrame]
    sha256: str = ""


@dataclass(frozen=True)
class SecondaryPart:
    name: str
    source_mask: tuple[tuple[float, float], ...]
    cutout_mask: tuple[tuple[float, float], ...]
    bounds: tuple[float, float, float, float]
    pivot: tuple[float, float]
    angle_deg: float
    delay_ms: int


@dataclass(frozen=True)
class SecondaryMotion:
    frames: tuple[int, ...]
    parts: tuple[SecondaryPart, ...]
    triggers: dict[str, float]
    duration_ms: int
    fps: int


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
    # Target base animation -> compatible one-shot bridges. Frame matching
    # also lets an interrupted bridge resume from its actual visible pose.
    transitions: dict[str, list[str]] = field(default_factory=dict)
    interactions: dict[str, str] = field(default_factory=dict)
    frame_patches: FramePatches | None = None
    secondary_motion: SecondaryMotion | None = None

    @property
    def spritesheet_path(self) -> str:
        return self.spritesheet.file

    @property
    def total_frame_count(self) -> int:
        return self.spritesheet.frame_count + (len(self.frame_patches.frames) if self.frame_patches else 0)


class ManifestError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _parse_frame_patches(data, directory: Path, sheet: SpritesheetInfo) -> FramePatches | None:
    if data is None:
        return None
    _require(isinstance(data, dict), "frame_patches must be an object")
    file = data.get("file")
    _require(isinstance(file, str) and file and not Path(file).is_absolute() and ".." not in Path(file).parts,
             "frame_patches file must stay inside the manifest directory")
    path = (directory / file).resolve()
    _require(path.is_relative_to(directory.resolve()), "frame_patches file resolves outside the manifest directory")
    columns, count = data.get("columns"), data.get("frame_count")
    _require(type(columns) is int and type(count) is int and 0 < columns <= count <= 256,
             "frame_patches requires valid columns and frame_count")
    _require(sheet.frame_width * sheet.frame_height * columns * math.ceil(count / columns) <= 16_000_000,
             "frame_patches image exceeds pixel budget")
    base = data.get("base_frame")
    _require(type(base) is int and 0 <= base < sheet.frame_count, "frame_patches base_frame exceeds source atlas")
    mask = data.get("mask")
    _require(isinstance(mask, list) and 3 <= len(mask) <= 32, "frame_patches requires a polygon mask")
    for point in mask:
        _require(isinstance(point, list) and len(point) == 2
                 and all(type(v) in (int, float) and math.isfinite(v) for v in point)
                 and 0 <= point[0] <= sheet.frame_width and 0 <= point[1] <= sheet.frame_height,
                 "frame_patches mask must stay inside a frame")
    frames = data.get("frames")
    _require(isinstance(frames, list) and frames, "frame_patches requires frames")
    _require(sheet.frame_count + len(frames) <= 256
             and sheet.frame_width * sheet.frame_height * (sheet.frame_count + len(frames)) <= 16_000_000,
             "frame_patches exceeds total frame/pixel budget")
    parsed = []
    for item in frames:
        _require(isinstance(item, dict) and (("cell" in item) != ("source_frame" in item)),
                 "frame_patches frame requires exactly one cell or source_frame")
        cell, source = item.get("cell"), item.get("source_frame")
        _require((type(cell) is int and 0 <= cell < count) if "cell" in item
                 else (type(source) is int and 0 <= source < sheet.frame_count),
                 "frame_patches source index exceeds atlas")
        fallback = item.get("fallback_frame", base)
        _require(type(fallback) is int and 0 <= fallback < sheet.frame_count,
                 "frame_patches fallback_frame exceeds source atlas")
        parsed.append(PatchFrame(cell, source, fallback))
    digest = data.get("sha256", "")
    _require(isinstance(digest, str) and (not digest or (len(digest) == 64
             and all(char in "0123456789abcdef" for char in digest))), "frame_patches invalid sha256")
    return FramePatches(path, columns, count, base, tuple(tuple(p) for p in mask), parsed, digest)


def _parse_secondary_motion(data, sheet: SpritesheetInfo, count: int) -> SecondaryMotion | None:
    if data is None:
        return None
    prefix = "secondary_motion"
    _require(isinstance(data, dict), f"{prefix} must be an object")
    _require(sheet.frame_width * sheet.frame_height <= 1_000_000, f"{prefix} exceeds frame pixel budget")
    frames = data.get("frames")
    _require(isinstance(frames, list) and 0 < len(frames) <= 256
             and all(type(index) is int and 0 <= index < count for index in frames)
             and len(set(frames)) == len(frames), f"{prefix} requires unique valid frames")
    duration, fps = data.get("duration_ms"), data.get("fps")
    _require(type(duration) is int and 200 <= duration <= 1200,
             f"{prefix} duration_ms must be between 200 and 1200")
    _require(type(fps) is int and 12 <= fps <= 30, f"{prefix} fps must be between 12 and 30")
    triggers = data.get("triggers")
    _require(isinstance(triggers, dict) and 0 < len(triggers) <= 32
             and all(isinstance(name, str) and name and type(value) in (int, float)
                     and math.isfinite(value) and 0 < value <= 1 for name, value in triggers.items()),
             f"{prefix} requires bounded trigger strengths")
    raw_parts = data.get("parts")
    _require(isinstance(raw_parts, list) and 0 < len(raw_parts) <= 3, f"{prefix} requires 1–3 parts")
    parts, names = [], set()
    for part in raw_parts:
        _require(isinstance(part, dict), f"{prefix} part must be an object")
        name = part.get("name")
        _require(isinstance(name, str) and name and name not in names, f"{prefix} part requires a unique name")
        names.add(name)
        bounds = part.get("bounds")
        _require(isinstance(bounds, list) and len(bounds) == 4
                 and all(type(v) in (int, float) and math.isfinite(v) for v in bounds),
                 f"{prefix} bounds require four finite numbers")
        x, y, width, height = bounds
        _require(x >= 0 and y >= 0 and width > 0 and height > 0
                 and x + width <= sheet.frame_width and y + height <= sheet.frame_height,
                 f"{prefix} bounds must stay inside a frame")
        polygons = []
        for key in ("source_mask", "cutout_mask"):
            polygon = part.get(key)
            _require(isinstance(polygon, list) and 3 <= len(polygon) <= 32,
                     f"{prefix} {key} requires a polygon")
            for point in polygon:
                _require(isinstance(point, list) and len(point) == 2
                         and all(type(v) in (int, float) and math.isfinite(v) for v in point)
                         and x <= point[0] <= x + width and y <= point[1] <= y + height,
                         f"{prefix} {key} must stay inside part bounds")
            polygons.append(tuple(tuple(point) for point in polygon))
        pivot = part.get("pivot")
        _require(isinstance(pivot, list) and len(pivot) == 2
                 and all(type(v) in (int, float) and math.isfinite(v) for v in pivot)
                 and x <= pivot[0] <= x + width and y <= pivot[1] <= y + height,
                 f"{prefix} pivot must stay inside part bounds")
        angle, delay = part.get("angle_deg"), part.get("delay_ms", 0)
        _require(type(angle) in (int, float) and math.isfinite(angle) and 0 < abs(angle) <= 12,
                 f"{prefix} angle_deg must be nonzero and at most 12 degrees")
        _require(type(delay) is int and 0 <= delay <= 300, f"{prefix} delay_ms must be between 0 and 300")
        parts.append(SecondaryPart(name, *polygons, tuple(bounds), tuple(pivot), float(angle), delay))
    return SecondaryMotion(tuple(frames), tuple(parts), dict(triggers), duration, fps)


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
    family = data.get("family", "")
    weight = data.get("weight", 1.0)
    cooldown = data.get("cooldown_ms", 0)
    variation = data.get("tempo_variation", 0.0)
    _require(isinstance(family, str), f"animation '{name}' family must be a string")
    _require(type(weight) in (int, float) and math.isfinite(weight) and 0 < weight <= 100,
             f"animation '{name}' weight must be between 0 and 100")
    _require(type(cooldown) is int and 0 <= cooldown <= 3_600_000,
             f"animation '{name}' cooldown_ms must be between 0 and 3600000")
    _require(type(variation) in (int, float) and math.isfinite(variation) and 0 <= variation <= .15,
             f"animation '{name}' tempo_variation must be between 0 and 0.15")
    return AnimationDef(
        name=name,
        frames=list(frames),
        fps=float(fps),
        loop=bool(loop),
        next_state=next_state,
        durations_ms=list(durations),
        family=family, weight=float(weight), cooldown_ms=cooldown,
        tempo_variation=float(variation),
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
    frame_patches = _parse_frame_patches(root.get("frame_patches"), manifest_path.parent, spritesheet)
    total_frame_count = ss_count + (len(frame_patches.frames) if frame_patches else 0)

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
        _require(all(f < total_frame_count for f in anim_def.frames),
                 f"animation '{anim_def.name}' frame exceeds spritesheet frame_count")
        if anim_def.next_state is not None:
            _require(anim_def.next_state in states,
                     f"animation '{anim_def.name}' next_state '{anim_def.next_state}' not in states")

    transitions = root.get("transitions", {})
    interactions = root.get("interactions", {})
    _require(isinstance(transitions, dict), "transitions must be an object")
    _require(isinstance(interactions, dict), "interactions must be an object")
    bases = {state.base for state in states.values()}
    for target, bridges in transitions.items():
        _require(target in bases, f"transition target '{target}' must be a base animation")
        _require(isinstance(bridges, list) and bridges and all(isinstance(b, str) for b in bridges),
                 f"transition '{target}' requires a list of bridge names")
        for bridge in bridges:
            _require(bridge in animations, f"transition '{bridge}' animation not found")
            anim = animations[bridge]
            _require(not anim.loop and anim.next_state is None,
                     f"transition '{bridge}' must be a one-shot without next_state")
            _require(anim.frames[-1] == animations[target].frames[0],
                     f"transition '{bridge}' must end at target '{target}' first frame")
            _require(sum(anim.duration(i) for i in range(len(anim.frames))) <= .6 + 1e-9,
                     f"transition '{bridge}' exceeds 600 ms")
    for action, animation in interactions.items():
        _require(isinstance(animation, str) and animation in animations,
                 f"interaction '{action}' animation not found")
        _require(not animations[animation].loop and animations[animation].next_state is None,
                 f"interaction '{action}' must be a one-shot without next_state")

    secondary = _parse_secondary_motion(root.get("secondary_motion"), spritesheet, total_frame_count)
    if secondary:
        names = set(animations) | {animation.family for animation in animations.values() if animation.family}
        _require(set(secondary.triggers) <= names, "secondary_motion trigger must name an animation or family")
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
        transitions={target: list(bridges) for target, bridges in transitions.items()},
        interactions=dict(interactions),
        frame_patches=frame_patches,
        secondary_motion=secondary,
    )
