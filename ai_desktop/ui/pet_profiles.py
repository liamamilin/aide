"""Curated playback for known Petdex atlases, without changing user assets.

An exact atlas fingerprint and grid match protect custom/regenerated pets from
receiving another character's frame mapping. Unknown pets retain their manifest.
"""

import hashlib
import json
import logging
from dataclasses import replace
from pathlib import Path

from ai_desktop.ui.pet_manifest import ManifestError, PetManifest, load_manifest
from ai_desktop.utils.paths import resource_path

_logger = logging.getLogger(__name__)
_KNOWN_PETS = {"astra", "boba", "shinchan"}


def apply_petdex_profile(manifest: PetManifest, image_path: Path,
                         *, profile_dir: Path | None = None) -> PetManifest:
    name = manifest.name.casefold()
    if name not in _KNOWN_PETS:
        return manifest
    directory = profile_dir or Path(resource_path("ai_desktop", "pets", "petdex-profiles"))
    path = directory / f"{name}.json"
    try:
        profile = load_manifest(path)
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if (profile.spritesheet != manifest.spritesheet
                or metadata.get("atlas_sha256") != hashlib.sha256(image_path.read_bytes()).hexdigest()):
            _logger.info("Petdex profile skipped: atlas differs for %s", name)
            return manifest
        return replace(profile, name=manifest.name, profile_id=f"petdex-{name}-v1")
    except (OSError, ManifestError, ValueError) as exc:
        _logger.warning("Petdex profile unavailable for %s: %s", name, exc)
        return manifest
