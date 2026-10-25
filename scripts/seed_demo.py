"""Seed an isolated demo workspace. Set KNOWGRAPH_HOME/KNOWGRAPH_VAULT first."""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from knowgraph.adapters import RawCandidate  # noqa: E402
from knowgraph.app import Application  # noqa: E402

app = Application()
app.profile.update(
    {
        "name": "Demo User",
        "headline": "Backend engineer exploring distributed systems",
        "location": "Demo City",
        "links": {},
    }
)
for name, level in [("Java", 3), ("Redis", 2), ("Kubernetes", 1), ("C++", 2)]:
    app.profile.add_skill(name, level=level)
app.profile.add_goal("short", "完成幂等支付回调的并发测试", "验证超时重试下的业务不变式", "active", "2026-11-15", 1)
app.profile.add_goal("long", "成为分布式系统方向的后端架构师", "关注一致性、可观测性与容量规划", "active", None, 2)
app.sources.add("arxiv", "arXiv 探索", {"categories": ["cs.AI"]}, interval_minutes=1440)
items = [
    RawCandidate(
        source_key="demo:redis",
        source_type="article",
        title="Redis Lua 脚本与事务边界",
        url="https://example.org/redis-lua",
        summary="梳理 MULTI/EXEC 与 Lua 脚本的原子性边界，并讨论超时重试时的幂等键设计。",
        content=(
            "Redis 的 MULTI/EXEC 提供事务队列，但不会自动回滚运行时错误。"
            "Lua 脚本在服务端原子执行，适合把校验和写入合并。"
            "客户端超时后重试时，需要幂等键避免重复扣款。C++ 客户端还应设置合理超时与连接池上限。"
        ),
        content_scope="excerpt",
        tags=["Redis", "Lua", "C++"],
        author="示例作者",
    ),
    RawCandidate(
        source_key="demo:k8s",
        source_type="repository",
        title="kubernetes/kubernetes",
        url="https://github.com/kubernetes/kubernetes",
        summary="生产级容器编排系统，近期关注调度器与控制器一致性。",
        content="Kubernetes 调度器通过 informer 缓存和调度队列工作。",
        content_scope="metadata",
        tags=["Kubernetes", "Go"],
        author="kubernetes",
    ),
    RawCandidate(
        source_key="demo:arxiv",
        source_type="paper",
        title="Consistency Models for Distributed Transactions",
        url="https://arxiv.org/abs/0000.00000",
        summary="讨论分布式事务的一致性模型与补偿策略。",
        content="论文比较了两阶段提交、Saga 与幂等补偿在失败恢复中的取舍。",
        content_scope="excerpt",
        tags=["分布式事务", "一致性"],
        author="A. Researcher",
        published_at="2026-10-10T00:00:00Z",
    ),
]
ids = [app.ingest.ingest(candidate, enrich=False)["id"] for candidate in items]
app.ingest.feedback(ids[0], "saved")
app.progress.add("完成 Redis 幂等练习", skill="Redis", status="done", minutes=45, notes="用重复回调测试幂等键")
app.progress.add("阅读调度器设计文档", skill="Kubernetes", status="partial", minutes=25)
app.interests.record_interaction("search", "Redis Lua 原子性")
app.interests.record_interaction("ask", "C++ 客户端超时重试如何保证幂等")
app.reviews.generate("week", offset=-1, force=True)
print({"counts": app.status()["counts"], "interests": [item["slug"] for item in app.interests.list()]})
