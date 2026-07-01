"""Update checker tests — mock GitHub API responses."""
from unittest.mock import patch

from ai_desktop.utils.update_checker import _compare_versions, check_for_update


class TestCompareVersions:
    def test_equal(self):
        assert _compare_versions("1.0.0", "1.0.0") == 0

    def test_newer(self):
        assert _compare_versions("1.1.0", "1.0.0") == 1

    def test_older(self):
        assert _compare_versions("1.0.0", "1.1.0") == -1

    def test_with_prefix(self):
        assert _compare_versions("2.0.0", "1.9.0") == 1

    def test_prerelease(self):
        assert _compare_versions("1.0.0-alpha", "1.0.0") == 0  # same prefix, pre-release treated as equal


class TestCheckForUpdate:
    def test_no_update_when_latest(self):
        with patch("ai_desktop.utils.update_checker.get_setting", return_value="0"), \
                patch("ai_desktop.utils.update_checker.requests.get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "tag_name": "v1.0.0",
                "html_url": "https://github.com/test/repo/releases",
                "body": "No changes",
            }
            with patch("ai_desktop.utils.update_checker.save_setting"):
                with patch("ai_desktop.utils.update_checker.__version__", "1.0.0"):
                    result = check_for_update()
        assert result is None

    def test_update_available(self):
        with patch("ai_desktop.utils.update_checker.get_setting", return_value="0"), \
                patch("ai_desktop.utils.update_checker.requests.get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "tag_name": "v1.2.0",
                "html_url": "https://github.com/test/repo/releases/v1.2.0",
                "body": "Bug fixes",
            }
            with patch("ai_desktop.utils.update_checker.save_setting"):
                with patch("ai_desktop.utils.update_checker.__version__", "1.0.0"):
                    result = check_for_update()
        assert result is not None
        assert result.version == "1.2.0"
        assert "v1.2.0" in result.url

    def test_http_error_returns_none(self):
        with patch("ai_desktop.utils.update_checker.get_setting", return_value="0"), \
                patch("ai_desktop.utils.update_checker.requests.get") as mock_get:
            mock_resp = mock_get.return_value
            mock_resp.status_code = 404
            with patch("ai_desktop.utils.update_checker.save_setting"):
                result = check_for_update()
        assert result is None

    def test_rate_limit_skips_check(self):
        with patch("ai_desktop.utils.update_checker.get_setting", return_value="9999999999"):
            result = check_for_update()
        assert result is None
