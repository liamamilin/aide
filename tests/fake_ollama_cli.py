"""Run FakeOllama as a process for the Swift tests: prints its URL, then serves canned replies.

Usage: python tests/fake_ollama_cli.py [stream|hold|error]  (one mode for every request)
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fake_ollama import FakeOllama  # noqa: E402


def _frame(content="", done=False, **extra):
    return {"message": {"role": "assistant", "content": content}, "done": done, **extra}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "stream"
    fake = FakeOllama()
    for _ in range(50):
        if mode == "stream":
            fake.enqueue(_frame("Hel"), _frame("lo"), _frame("", done=True, done_reason="stop"))
        elif mode == "hold":
            fake.enqueue(_frame("partial"), hold_open=True)
        else:
            fake.enqueue(status=500, chunks=[json.dumps({"error": "boom"}).encode()])
    print(fake.url, flush=True)
    try:
        sys.stdin.read()  # exits when the test closes our stdin
    finally:
        fake.close()
        time.sleep(0)


if __name__ == "__main__":
    main()
