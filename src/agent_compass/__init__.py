"""Agent Compass: local-first behavioral and task state layer."""
from .config import CompassConfig
from .memory.service import MemoryService
from .models import (
    DecisionContext,
    Decision,
    Task,
    MemoryCandidate,
    MemoryStatus,
    FeedbackEvent,
    SessionState,
    TaskStatus,
)
from .policy.engine import PolicyEngine
from .privacy.boundary import PrivacyBoundary
from .retrieval import (
    CallableRetriever,
    LocalMemoryRetriever,
    RetrievalOrchestrator,
    RetrievalQuery,
    RetrievalResult,
    render_digest,
)
from .storage.sqlite import SQLiteStore
from .tasks.service import TaskService, FeedbackService
from .tasks.state_machine import TaskStateMachine, Checkpoint, IdempotencyRegistry

__version__ = "0.7.0"


class Compass:
    def __init__(self, config: CompassConfig | None = None):
        self.config = config or CompassConfig.from_env()
        self.config.ensure()
        self.store = SQLiteStore(self.config.database_path)
        self.policy = PolicyEngine(self.config)
        self.privacy = PrivacyBoundary()
        self.tasks = TaskService(self.store)
        self.memory = MemoryService(self.privacy, self.store)
        self.feedback = FeedbackService(self.store)
        self.retrieval = RetrievalOrchestrator([LocalMemoryRetriever(self.store)])
        self.idempotency = IdempotencyRegistry(self.store)

    @classmethod
    def from_config(cls, path: str | None = None) -> "Compass":
        return cls(CompassConfig.load(path))

    def decide(self, context: DecisionContext) -> Decision:
        return self.policy.decide(context)

    def recall(self, query: str | RetrievalQuery, **overrides) -> RetrievalResult:
        """Bounded memory recall: ranked summaries, never full bodies.

        Pass ``token_budget=`` to cap the size of what comes back. Use
        ``compass.memory.get(...)`` with a returned ``memory_id`` to pull a
        full body once you know you need it.
        """
        return self.retrieval.retrieve(query, **overrides)


__all__ = [
    "Compass",
    "CompassConfig",
    "DecisionContext",
    "Decision",
    "Task",
    "MemoryCandidate",
    "MemoryStatus",
    "FeedbackEvent",
    "SessionState",
    "TaskStatus",
    "TaskService",
    "TaskStateMachine",
    "MemoryService",
    "FeedbackService",
    "PrivacyBoundary",
    "SQLiteStore",
    "PolicyEngine",
    "RetrievalOrchestrator",
    "RetrievalQuery",
    "RetrievalResult",
    "LocalMemoryRetriever",
    "CallableRetriever",
    "render_digest",
    "Checkpoint",
    "IdempotencyRegistry",
    "__version__",
]
