from pathlib import Path

from setuptools import find_packages, setup


def _get_version() -> str:
    namespace: dict[str, str] = {}
    version_file = Path(__file__).parent / "ai_desktop" / "version.py"
    exec(version_file.read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


with open("requirements.txt", encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

setup(
    name="ai-desktop-assistant",
    version=_get_version(),
    description="macOS 桌面 AI 助手 —— 选中文字即问，悬浮窗即答",
    long_description=Path("README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="milin",
    python_requires=">=3.10",
    packages=find_packages(),
    package_data={"ai_desktop": ["*.png", "*.icns"]},
    include_package_data=True,
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "aide = ai_desktop.__main__:main",
        ],
    },
    classifiers=[
        "Operating System :: MacOS",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
