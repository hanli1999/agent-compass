"""Feedback subsystem."""
from .events import record_feedback
from ..tasks.service import FeedbackService

__all__ = ["record_feedback", "FeedbackService"]
