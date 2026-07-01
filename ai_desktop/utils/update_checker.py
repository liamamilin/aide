import logging
from typing import NamedTuple

import requests

from ai_desktop import config
from ai_desktop.__init__ import __version__
from ai_desktop.utils.storage import get_setting, save_setting

logger = logging.getLogger(__name__)


class UpdateInfo(NamedTuple):
    version: str
    url: str
    body: str


def check_for_update() -> UpdateInfo | None:
    """检查 GitHub Releases 是否有新版本

    返回 UpdateInfo（有新版）或 None（已最新或检查失败）。
    """
    # 频率限制：上次检查距今超过间隔才执行
    last_check = get_setting("last_update_check", "0")
    try:
        elapsed = __import__("time").time() - float(last_check)
    except (ValueError, TypeError):
        elapsed = float("inf")
    if elapsed < config.UPDATE_CHECK_INTERVAL:
        return None

    save_setting("last_update_check", str(__import__("time").time()))

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest",
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("Update check returned %d", resp.status_code)
            return None
        data = resp.json()
        latest = data.get("tag_name", "").lstrip("v")
        current = __version__.lstrip("v")
        if _compare_versions(latest, current) <= 0:
            return None
        return UpdateInfo(
            version=latest,
            url=data.get("html_url", ""),
            body=data.get("body", ""),
        )
    except Exception:
        logger.debug("Update check failed", exc_info=True)
        return None


def _compare_versions(a: str, b: str) -> int:
    """比较两个语义化版本号，返回 -1/0/1"""
    try:
        parts_a = [int(x) for x in a.replace("-", ".").split(".")]
        parts_b = [int(x) for x in b.replace("-", ".").split(".")]
        for pa, pb in zip(parts_a, parts_b):
            if pa != pb:
                return -1 if pa < pb else 1
        # 前缀相同，较长者更新
        return -1 if len(parts_a) < len(parts_b) else (1 if len(parts_a) > len(parts_b) else 0)
    except (ValueError, AttributeError):
        return 0
