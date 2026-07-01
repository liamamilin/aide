import subprocess

from setuptools import setup, find_packages


def _get_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--dirty=-dirty"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return "1.0.0"


with open("requirements.txt") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="ai-desktop-assistant",
    version=_get_version(),
    description="macOS 桌面 AI 助手 —— 选中文字即问，悬浮窗即答",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="milin",
    python_requires=">=3.10",
    packages=find_packages(),
    package_data={"ai_desktop": ["*.png", "*.icns"]},
    include_package_data=True,
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "aide = ai_desktop.main:main",
        ],
    },
    classifiers=[
        "Operating System :: MacOS",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
