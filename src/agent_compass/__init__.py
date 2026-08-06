"""Agent Compass: local-first behavioral and task state layer."""
from .config import CompassConfig
from .models import DecisionContext, Decision, Task, MemoryCandidate, FeedbackEvent
from .policy.engine import PolicyEngine
from .privacy.boundary import PrivacyBoundary
from .storage.sqlite import SQLiteStore
from .tasks.state_machine import TaskStateMachine, Checkpoint, IdempotencyRegistry

__version__ = "0.1.0"


class TaskService:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self.machine = TaskStateMachine()

    def create(self, goal: str, **metadata) -> Task:
        task = Task(goal=goal, metadata=metadata)
        self.store.save_task(task)
        return task

    def get(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)


class MemoryService:
    def __init__(self, boundary: PrivacyBoundary):
        self.boundary = boundary

    def propose(self, content: str, **kwargs) -> MemoryCandidate:
        inspection = self.boundary.inspect(content)
        if inspection.blocked:
            raise ValueError(f"secret content cannot become a memory: {', '.join(inspection.matches)}")
        privacy = kwargs.pop("privacy", "local_only")
        return MemoryCandidate(content=content, privacy=privacy, **kwargs)


class Compass:
    def __init__(self, config: CompassConfig | None = None):
        self.config = config or CompassConfig.from_env()
        self.config.ensure()
        self.store = SQLiteStore(self.config.database_path)
        self.policy = PolicyEngine(self.config)
        self.privacy = PrivacyBoundary()
        self.tasks = TaskService(self.store)
        self.memory = MemoryService(self.privacy)
        self.idempotency = IdempotencyRegistry()

    @classmethod
    def from_config(cls, _path: str | None = None) -> "Compass":
        # YAML loading is intentionally optional in the offline MVP; environment
        # configuration keeps the core dependency-free.
        return cls(CompassConfig.from_env())

    def decide(self, context: DecisionContext) -> Decision:
        return self.policy.decide(context)


__all__ = ["Compass", "CompassConfig", "DecisionContext", "Decision", "Task", "MemoryCandidate", "FeedbackEvent", "__version__"]
