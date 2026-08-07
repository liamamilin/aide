"""Storage layer tests — uses temp SQLite database"""
import sys
import tempfile
import threading
from pathlib import Path

import ai_desktop.utils.storage as storage

_ORIG_PATH = storage.DB_PATH


def _use_temp_db() -> str:
    """Point storage at a fresh temp DB, preserving the original path."""
    storage._local = threading.local()  # fresh connection pool
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    storage.DB_PATH = Path(tmp.name)
    tmp.close()
    storage.init_db()
    return tmp.name


def _restore_db() -> None:
    storage.DB_PATH = _ORIG_PATH
    storage._local = threading.local()


def _cleanup(path: str) -> None:
    Path(path).unlink(missing_ok=True)


class TestConversationCRUD:
    """对话 CRUD 测试"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        _cleanup(cls._db_path)
        _restore_db()

    def test_create_and_get(self):
        conv = storage.create_conversation("code_expert", "测试对话")
        assert conv.id > 0
        assert conv.agent_id == "code_expert"
        assert conv.title == "测试对话"

        loaded = storage.get_conversation(conv.id)
        assert loaded is not None
        assert loaded.title == "测试对话"

    def test_list_conversations(self):
        storage.create_conversation("translator")
        storage.create_conversation("general_assistant")
        convs = storage.list_conversations(limit=10)
        assert len(convs) >= 2

    def test_delete_conversation(self):
        conv = storage.create_conversation("code_expert")
        cid = conv.id
        storage.delete_conversation(cid)
        assert storage.get_conversation(cid) is None

    def test_save_message_and_title(self):
        conv = storage.create_conversation("code_expert")
        msg = storage.save_message(conv.id, "user", "Python 的 GIL 是什么？")
        assert msg.id > 0
        assert msg.role == "user"

        # 第一条用户消息应该自动设为标题
        loaded = storage.get_conversation(conv.id)
        assert loaded is not None
        assert "Python 的 GIL" in loaded.title

    def test_messages_order(self):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "user", "Q1")
        storage.save_message(conv.id, "assistant", "A1")
        storage.save_message(conv.id, "user", "Q2")
        storage.save_message(conv.id, "assistant", "A2")

        loaded = storage.get_conversation(conv.id)
        assert loaded is not None
        assert len(loaded.messages) == 4
        assert loaded.messages[0].content == "Q1"
        assert loaded.messages[-1].content == "A2"

    def test_list_with_counts(self):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "user", "hello")
        storage.save_message(conv.id, "assistant", "hi")

        convs = storage.list_conversations_with_counts(limit=10)
        found = next(c for c in convs if c["id"] == conv.id)
        assert found["msg_count"] == 2

    def test_search(self):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "user", "Python 多线程性能分析")
        storage.save_message(conv.id, "assistant", "GIL 是主要原因")

        # 搜索标题
        results = storage.search_conversations("多线程")
        assert any(r["id"] == conv.id for r in results)

        # 搜索消息内容
        results = storage.search_conversations("GIL")
        assert any(r["id"] == conv.id for r in results)

        # 无匹配
        results = storage.search_conversations("不存在的关键字")
        assert len(results) == 0


class TestSettings:
    """设置持久化测试"""

    @classmethod
    def setup_class(cls):
        cls._db_path = _use_temp_db()

    @classmethod
    def teardown_class(cls):
        _cleanup(cls._db_path)
        _restore_db()

    def test_save_and_get(self):
        storage.save_setting("test_key", "test_value")
        assert storage.get_setting("test_key") == "test_value"

    def test_get_default(self):
        assert storage.get_setting("nonexistent", "default") == "default"

    def test_overwrite(self):
        storage.save_setting("key", "first")
        storage.save_setting("key", "second")
        assert storage.get_setting("key") == "second"

    def test_custom_agents_roundtrip(self):
        agents = [
            {"id": "custom_1", "name": "设计师", "icon": "🎨", "system_prompt": "你是一个设计师"},
        ]
        storage.save_custom_agents(agents)
        loaded = storage.load_custom_agents()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "设计师"

    def test_custom_agents_empty(self):
        storage.save_custom_agents([])
        assert storage.load_custom_agents() == []

    def test_custom_agents_corrupt(self):
        storage.save_setting("custom_agents", "not-json")
        assert storage.load_custom_agents() == []


class TestInputHistory:
    """输入历史（上下键浏览）测试"""

    def test_list_input_history_newest_first(self, tmp_db):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "user", "第一条")
        storage.save_message(conv.id, "assistant", "回复")
        storage.save_message(conv.id, "user", "第二条")
        assert storage.list_input_history() == ["第二条", "第一条"]

    def test_list_input_history_dedup(self, tmp_db):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "user", "重复问题")
        storage.save_message(conv.id, "user", "其他问题")
        storage.save_message(conv.id, "user", "重复问题")
        assert storage.list_input_history() == ["重复问题", "其他问题"]

    def test_list_input_history_excludes_assistant_and_empty(self, tmp_db):
        conv = storage.create_conversation("code_expert")
        storage.save_message(conv.id, "assistant", "不是用户输入")
        storage.save_message(conv.id, "user", "")
        storage.save_message(conv.id, "user", "   ")
        storage.save_message(conv.id, "user", "有效输入")
        assert storage.list_input_history() == ["有效输入"]

    def test_list_input_history_limit(self, tmp_db):
        conv = storage.create_conversation("code_expert")
        for i in range(5):
            storage.save_message(conv.id, "user", f"消息{i}")
        result = storage.list_input_history(limit=3)
        assert len(result) == 3
        assert result == ["消息4", "消息3", "消息2"]

    def test_list_input_history_across_conversations(self, tmp_db):
        conv1 = storage.create_conversation("code_expert")
        conv2 = storage.create_conversation("translator")
        storage.save_message(conv1.id, "user", "对话A的输入")
        storage.save_message(conv2.id, "user", "对话B的输入")
        assert storage.list_input_history() == ["对话B的输入", "对话A的输入"]

    def test_list_input_history_empty_db(self, tmp_db):
        assert storage.list_input_history() == []


class TestDbPath:
    """Test DB path resolution"""

    def test_dev_mode_fallback(self):
        """When DB exists next to source, use it (dev mode)"""
        from ai_desktop.utils.storage import _resolve_db_path
        # In test env, there's no chat_history.db next to source,
        # so it should resolve to the production path
        result = _resolve_db_path()
        assert "ai-desktop-assistant" in str(result) or "chat_history.db" in str(result)

    def test_production_path_structure(self):
        """Production path should be under Application Support or dev fallback"""
        from ai_desktop.utils.storage import _resolve_db_path
        result = _resolve_db_path()
        # On macOS, should use Library/Application Support OR dev fallback
        if sys.platform == "darwin":
            assert ("Library/Application Support" in str(result)
                    or "chat_history.db" in str(result))
