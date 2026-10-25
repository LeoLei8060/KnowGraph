from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("KNOWGRAPH_DISABLE_KEYRING", "1")

from knowgraph.adapters import RawCandidate
from knowgraph.api import create_app
from knowgraph.app import Application
from knowgraph.config import Paths


@pytest.fixture()
def app(tmp_path: Path) -> Application:
    paths = Paths(
        home=tmp_path / "data",
        vault=tmp_path / "vault",
        db_path=tmp_path / "data" / "knowgraph.sqlite3",
        resume_dir=tmp_path / "data" / "resume",
    )
    application = Application(paths)
    application.settings.set("llm_enabled", "0")
    application.settings.set("track_interactions", "1")
    return application


@pytest.fixture()
def client(app: Application):
    with TestClient(create_app(app)) as test_client:
        yield test_client


@pytest.fixture()
def candidate() -> RawCandidate:
    return RawCandidate(
        source_key="test:redis",
        source_type="article",
        title="Redis 事务与 C++ 幂等实践",
        url="https://example.org/redis-transaction",
        summary="Redis Lua 脚本保证原子性；C++ 客户端需要处理超时重试与幂等。",
        content=(
            "Redis 事务通过 MULTI/EXEC 或 Lua 脚本执行。Lua 脚本在服务端原子执行。"
            "C++ 客户端在超时后重试时，必须使用幂等键避免重复扣款。"
            "async/await 不是 Redis 事务的保证。"
        ),
        content_scope="excerpt",
        tags=["Redis", "C++"],
    )
