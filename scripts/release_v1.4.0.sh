#!/bin/bash
# 一次性发布脚本：提交 4 个逻辑 commit → 打 tag v1.4.0 → 推送（触发 CI 构建 Release）
# 幂等：已提交的内容自动跳过，可安全重跑
# 用法: bash scripts/release_v1.4.0.sh
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="v1.4.0"

commit_if_staged() {
    if git diff --cached --quiet; then
        echo "    (无待提交变更，跳过)"
    else
        git commit -m "$1"
    fi
}

echo "==> [1/5] 校验工作区状态（仅允许预期的变更文件）"
EXPECTED="README.md|README.zh.md|ai_desktop/config.py|ai_desktop/llm/chat_client.py|ai_desktop/main.py|ai_desktop/ui/agent_editor.py|ai_desktop/ui/chat_dialog.py|ai_desktop/ui/styles.py|ai_desktop/utils/storage.py|ai_desktop/capture/screenshot.py|ai_desktop/utils/images.py|tests/test_agent_editor.py|tests/test_chat_client.py|tests/test_chat_dialog.py|tests/test_storage.py|tests/test_images.py|tests/test_menu_contrast.py|tests/test_screenshot.py|scripts/release_v1.4.0.sh"
UNEXPECTED=$(git status --porcelain | awk '{print $2}' | grep -vE "^($EXPECTED)$" || true)
if [ -n "$UNEXPECTED" ]; then
    echo "!! 发现预期之外的变更文件，中止：" >&2
    echo "$UNEXPECTED" >&2
    exit 1
fi
echo "    工作区变更范围符合预期"

echo "==> [2/5] 提交 1/4: 对比度阈值（测试）"
git add tests/test_menu_contrast.py
commit_if_staged "fix: 菜单选中项对比度阈值调整（选中项白字/强调蓝 ≥ 3:1）"

echo "==> [3/5] 提交 2/4: 图片理解（多模态）"
git add ai_desktop/config.py ai_desktop/utils/images.py ai_desktop/utils/storage.py \
        ai_desktop/llm/chat_client.py ai_desktop/ui/chat_dialog.py \
        ai_desktop/capture/screenshot.py ai_desktop/main.py \
        tests/test_images.py tests/test_storage.py tests/test_chat_client.py \
        tests/test_chat_dialog.py tests/test_screenshot.py
commit_if_staged "feat: 图片理解（多模态）粘贴/拖拽/附件/⌘⌃S 截图发图，图片持久化 + 截图权限引导"

echo "==> [4/5] 提交 3/4 + 4/4: 弹窗修复 + 文档"
git add ai_desktop/ui/styles.py ai_desktop/ui/agent_editor.py tests/test_agent_editor.py
commit_if_staged "fix: 新增 Agent 弹窗背景穿透（DIALOG_BODY 满铺背景面板）"

git add README.md README.zh.md
commit_if_staged "docs: README 默认英文 + 中文版 README.zh.md"

echo "==> [5/5] 打 tag 并推送（触发 GitHub Actions 构建 Release）"
if git rev-parse "$VERSION" >/dev/null 2>&1; then
    echo "    tag $VERSION 已存在，跳过"
else
    git tag "$VERSION"
fi
git push origin main --tags

echo "==> ✅ 完成：$VERSION 已推送。"
echo "    在 https://github.com/liamamilin/aide/actions 查看构建，"
echo "    Release 自动生成于 https://github.com/liamamilin/aide/releases"