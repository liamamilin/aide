"""D08 attachment lifecycle: validation, references, drafts, and cleanup."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt5.QtGui import QColor, QImage, QImageReader
from PyQt5.QtWidgets import QLabel

from ai_desktop.config import Agent
from ai_desktop.utils import images, storage


@pytest.fixture()
def attachment_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "app-data"
    data_dir.mkdir()
    monkeypatch.setenv("AIDE_DATA_DIR", str(data_dir))
    original_path = storage.DB_PATH
    storage.DB_PATH = data_dir / "chat_history.db"
    storage._local = threading.local()
    storage._active_attachment_uses = {}
    storage.init_db()
    yield data_dir
    connection = getattr(storage._local, "conn", None)
    if connection is not None:
        connection.close()
    storage.DB_PATH = original_path
    storage._local = threading.local()
    storage._active_attachment_uses = {}


@pytest.fixture()
def dialog(qtbot):
    from ai_desktop.ui.chat_dialog import ChatDialog

    agent = Agent(
        id="general_assistant",
        name="通用助手",
        icon="🤖",
        system_prompt="You are helpful.",
    )
    with patch("ai_desktop.ui.chat_dialog.pin_to_all_spaces"):
        widget = ChatDialog([agent], agent, models=["test"], active_model="test")
    qtbot.addWidget(widget)
    return widget


def _make_image(path: Path, width: int = 32, height: int = 24) -> Path:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#3b82f6"))
    assert image.save(str(path), "PNG")
    return path


def _stored_image(tmp_path: Path, name: str = "source.png", **dimensions) -> str:
    source = _make_image(tmp_path / name, **dimensions)
    return images.store_image(str(source))


def test_validation_rejects_corrupt_size_and_pixel_limits(
    attachment_env, tmp_path, monkeypatch,
):
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(images.AttachmentError, match="损坏|不受支持"):
        images.store_image(str(corrupt))

    source = _make_image(tmp_path / "valid.png", 10, 10)
    monkeypatch.setattr(images, "MAX_ATTACHMENT_BYTES", source.stat().st_size - 1)
    with pytest.raises(images.AttachmentError, match="20 MiB"):
        images.store_image(str(source))

    monkeypatch.setattr(images, "MAX_ATTACHMENT_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(images, "MAX_ATTACHMENT_PIXELS", 99)
    with pytest.raises(images.AttachmentError, match="4000 万像素"):
        images.store_image(str(source))


def test_managed_message_uses_relative_reference_and_deletes_last_copy(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path)
    original_source = tmp_path / "source.png"
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "看看", [stored])

    record = storage.list_attachments()[0]
    assert record["relative_path"].startswith("attachments/originals/")
    assert record["state"] == "referenced"
    assert not Path(record["relative_path"]).is_absolute()
    assert storage.get_conversation(conv.id).messages[0].images == [stored]

    storage.delete_conversation(conv.id)
    assert not Path(stored).exists()
    assert original_source.exists()
    assert storage.list_attachments() == []


def test_shared_reference_survives_until_final_message_is_deleted(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path)
    first = storage.create_conversation("vision")
    second = storage.create_conversation("vision")
    storage.save_message(first.id, "user", "一", [stored])
    storage.save_message(second.id, "user", "二", [stored])

    storage.delete_conversation(first.id)
    assert Path(stored).is_file()
    assert storage.list_attachments()[0]["state"] == "referenced"

    storage.delete_conversation(second.id)
    assert not Path(stored).exists()
    assert storage.list_attachments() == []


def test_external_files_are_classified_but_never_deleted(attachment_env, tmp_path):
    external = _make_image(tmp_path / "external.png")
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "外部", [str(external)])
    assert storage.list_attachments() == []
    assert storage.get_conversation(conv.id).messages[0].images == [str(external)]

    storage.delete_conversation(conv.id)
    assert external.is_file()


def test_missing_history_image_is_marked_without_blocking_message(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path)
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "仍可读", [stored])
    Path(stored).unlink()

    message = storage.get_conversation(conv.id).messages[0]
    assert message.content == "仍可读"
    assert message.images == []
    assert message.missing_images == [stored]


def test_rollback_preserves_draft_then_explicit_discard_removes_it(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path)
    conv = storage.create_conversation("vision")
    message = storage.save_message(conv.id, "user", "发送", [stored])

    storage.delete_message(message.id, preserve_attachments=True)
    assert Path(stored).is_file()
    assert storage.list_attachments()[0]["state"] == "staged"

    assert storage.discard_staged_attachment(stored)
    assert not Path(stored).exists()
    assert storage.list_attachments() == []


def test_failed_delete_is_retried_by_garbage_collection(
    attachment_env, tmp_path, monkeypatch,
):
    stored = _stored_image(tmp_path)
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "删除", [stored])
    real_delete = images.delete_managed_path
    monkeypatch.setattr(images, "delete_managed_path", lambda path: False)

    storage.delete_conversation(conv.id)
    assert Path(stored).exists()
    assert storage.list_attachments()[0]["state"] == "pending_gc"

    monkeypatch.setattr(images, "delete_managed_path", real_delete)
    assert storage.collect_attachment_garbage() == 1
    assert not Path(stored).exists()
    assert storage.list_attachments() == []


def test_active_request_pin_defers_collection_until_worker_releases_it(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path)
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "使用中", [stored])
    retained = storage.retain_attachment_paths([stored])

    storage.delete_conversation(conv.id)
    assert Path(stored).is_file()
    assert storage.list_attachments()[0]["state"] == "pending_gc"

    storage.release_attachment_paths(retained)
    assert not Path(stored).exists()
    assert storage.list_attachments() == []


def test_legacy_images_json_is_backfilled_without_moving_file(
    attachment_env,
):
    legacy_dir = attachment_env / "images"
    legacy_dir.mkdir()
    legacy = _make_image(legacy_dir / "legacy.png")
    conv = storage.create_conversation("vision")
    db = storage._conn()
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, images, created_at) "
        "VALUES (?, 'user', '旧消息', ?, ?)",
        (conv.id, json.dumps([str(legacy)]), time.time()),
    )
    db.commit()

    storage.init_db()
    record = storage.list_attachments()[0]
    assert record["relative_path"] == "images/legacy.png"
    assert legacy.is_file()
    assert storage.get_conversation(conv.id).messages[0].images == [str(legacy)]


def test_inference_copy_is_resized_and_original_is_preserved(
    attachment_env, tmp_path,
):
    stored = _stored_image(tmp_path, width=2200, height=40)
    conv = storage.create_conversation("vision")
    storage.save_message(conv.id, "user", "宽图", [stored])

    prepared = images.prepare_image_for_inference(stored)
    prepared_image = QImageReader(prepared).read()
    assert prepared != stored
    assert max(prepared_image.width(), prepared_image.height()) == 2048
    assert images.inspect_image(stored).width == 2200
    record = storage.list_attachments()[0]
    assert record["inference_path"].startswith("attachments/inference/")

    storage.delete_conversation(conv.id)
    assert not Path(stored).exists()
    assert not Path(prepared).exists()


def test_old_orphan_copy_is_collected(attachment_env, tmp_path):
    stored = Path(_stored_image(tmp_path))
    old = time.time() - 60
    os.utime(stored, (old, old))
    assert storage.collect_attachment_garbage(max_age_seconds=1) == 1
    assert not stored.exists()


def test_storage_rejects_more_than_four_images(attachment_env):
    conv = storage.create_conversation("vision")
    with pytest.raises(ValueError, match="最多添加 4 张"):
        storage.save_message(
            conv.id,
            "user",
            "太多",
            [f"/tmp/external-{index}.png" for index in range(5)],
        )


def test_dialog_removes_draft_file_and_renders_missing_notice(
    attachment_env, tmp_path, dialog,
):
    source = _make_image(tmp_path / "draft.png")
    with patch.object(dialog, "_show_attachment_errors"):
        dialog.attach_image_paths([str(source)])
    stored = dialog.get_pending_images()[0]
    assert Path(stored).is_file()

    dialog._remove_pending_image(0)
    assert not Path(stored).exists()

    missing = str(attachment_env / "attachments" / "originals" / "gone.png")
    dialog.add_user_message("历史仍显示", missing_images=[missing])
    notices = dialog.findChildren(QLabel, "missing_image_notice")
    assert len(notices) == 1
    assert "gone.png" in notices[0].text()


def test_send_detaches_draft_without_deleting_before_controller_handles_it(
    attachment_env, tmp_path, dialog, qtbot,
):
    source = _make_image(tmp_path / "send.png")
    with patch.object(dialog, "_show_attachment_errors"):
        dialog.attach_image_paths([str(source)])
    stored = dialog.get_pending_images()[0]
    dialog._input.setPlainText("发送")

    with qtbot.waitSignal(dialog.message_sent, timeout=1000) as signal:
        dialog._on_send()
    assert signal.args[1] == [stored]
    assert dialog.get_pending_images() == []
    assert Path(stored).is_file()
