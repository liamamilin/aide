#!/usr/bin/env python3
"""Reproducible local M2 storage, UI, attachment, and request-preparation baseline."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _milliseconds(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "samples": len(ordered),
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[p95_index] * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
    }


def _measure(action, samples: int) -> dict[str, float | int]:
    action()
    durations = []
    for _ in range(samples):
        started = time.perf_counter()
        action()
        durations.append(time.perf_counter() - started)
    return _milliseconds(durations)


def _seed_storage(storage) -> None:
    db = storage._conn()
    conversations = [
        (f"历史 {index}", "general_assistant", float(index))
        for index in range(1000)
    ]
    db.executemany(
        "INSERT INTO conversations (title, agent_id, created_at) VALUES (?, ?, ?)",
        conversations,
    )
    rows = db.execute("SELECT id FROM conversations ORDER BY id").fetchall()
    messages = []
    for index, row in enumerate(rows):
        marker = " needle" if index % 10 == 0 else ""
        messages.extend(
            [
                (row["id"], "user", f"问题 {index}{marker}", "[]", float(index)),
                (row["id"], "assistant", "回答" * 40, "[]", float(index) + 0.1),
            ]
        )
    db.executemany(
        "INSERT INTO messages (conversation_id, role, content, images, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        messages,
    )
    cursor = db.execute(
        "INSERT INTO conversations (title, agent_id, created_at) VALUES (?, ?, ?)",
        ("长对话", "general_assistant", 2000.0),
    )
    long_messages = [
        (cursor.lastrowid, "user" if index % 2 == 0 else "assistant", "长正文" * 100, "[]", 2000.0 + index)
        for index in range(1000)
    ]
    db.executemany(
        "INSERT INTO messages (conversation_id, role, content, images, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        long_messages,
    )
    db.commit()


def run_benchmark() -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    with tempfile.TemporaryDirectory(prefix="aide-m2-benchmark-") as directory:
        os.environ["AIDE_DATA_DIR"] = directory
        from PyQt5.QtGui import QColor, QImage
        from PyQt5.QtWidgets import QApplication

        from ai_desktop.config import Agent
        from ai_desktop.llm.chat_client import ChatClient, _payload
        from ai_desktop.ui import chat_dialog as chat_dialog_module
        from ai_desktop.utils import images, storage
        from ai_desktop.utils.storage import Message

        storage.close_db()
        storage.DB_PATH = Path(directory) / "chat_history.db"
        storage.init_db()
        _seed_storage(storage)
        db = storage._conn()
        long_id = db.execute("SELECT id FROM conversations WHERE title='长对话'").fetchone()[0]

        metrics = {
            "history_first_page": _measure(
                lambda: storage.page_conversations(limit=50), 30
            ),
            "history_search": _measure(
                lambda: storage.page_conversations(limit=50, query="needle"), 30
            ),
            "long_conversation_load": _measure(
                lambda: storage.get_conversation(long_id), 20
            ),
        }

        source = Path(directory) / "benchmark-source.png"
        image = QImage(1600, 900, QImage.Format_ARGB32)
        image.fill(QColor("#3b82f6"))
        if not image.save(str(source), "PNG"):
            raise RuntimeError("Could not create benchmark image")

        def process_attachment() -> None:
            stored = images.store_image(str(source))
            storage.discard_staged_attachment(stored)

        metrics["attachment_validate_store_gc"] = _measure(process_attachment, 10)

        app = QApplication.instance() or QApplication([])
        chat_dialog_module.pin_to_all_spaces = lambda _widget: None
        agent = Agent("general_assistant", "通用助手", "🤖", "Help.")

        def construct_chat_window() -> None:
            dialog = chat_dialog_module.ChatDialog([agent], agent, ["model"], "model")
            dialog.show()
            app.processEvents()
            dialog.hide()
            dialog.deleteLater()
            app.processEvents()

        metrics["chat_window_construct_show"] = _measure(construct_chat_window, 10)

        request_messages = [
            Message("user" if index % 2 == 0 else "assistant", "请求正文" * 100, id=index)
            for index in range(200)
        ]
        client = ChatClient(base_url="http://127.0.0.1:1", model="benchmark")

        def prepare_request() -> None:
            request = client.create_request(request_messages, "系统提示")
            json.dumps(_payload(request, stream=True), ensure_ascii=False).encode("utf-8")

        gc.collect()
        tracemalloc.start()
        before_current, _ = tracemalloc.get_traced_memory()
        request_metric = _measure(prepare_request, 100)
        gc.collect()
        after_current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        request_metric.update(
            {
                "retained_kib": round((after_current - before_current) / 1024, 3),
                "peak_kib": round(peak / 1024, 3),
                "messages_per_request": len(request_messages),
            }
        )
        metrics["request_snapshot_and_json"] = request_metric
        storage.close_db()

    return {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        },
        "dataset": {
            "history_conversations": 1000,
            "history_messages": 2000,
            "long_conversation_messages": 1000,
            "attachment_pixels": 1600 * 900,
        },
        "targets_ms": {
            "history_first_page_p95": 300,
            "history_search_p95": 300,
            "chat_window_construct_show_p95": 300,
        },
        "metrics": metrics,
        "scope": (
            "Local offscreen baseline. Request metric covers immutable snapshot and JSON "
            "preparation without model inference or network latency."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args(argv)
    result = run_benchmark()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
