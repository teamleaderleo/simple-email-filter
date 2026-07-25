"""Shared primitives for mailbox classification and retention."""

from .models import MailMessage, Policy, RetentionPlanItem
from .planner import build_retention_plan
from .policy import load_policies

__all__ = [
    "MailMessage",
    "Policy",
    "RetentionPlanItem",
    "build_retention_plan",
    "load_policies",
]
