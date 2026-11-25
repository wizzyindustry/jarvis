"""The single gate every tool call goes through, whether it came from the router or the AI model."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..safety.audit import Store
from ..safety.policy import Decision, Level
from .catalog import ESCALATE_WHEN_TAINTED
from .registry import Registry, ToolError, validate_args

log = logging.getLogger(__name__)

# approver(summary, click_only) -> True if approved
Approver = Callable[[str, bool], bool]


@dataclass
class Result:
    ok: bool
    text: str
    declined: bool = False


class Executor:
    def __init__(self, registry: Registry, store: Store, approver: Approver) -> None:
        self.registry = registry
        self.store = store
        self.approver = approver
        self.tainted = False  # reset per user request

    def new_request(self) -> None:
        self.tainted = False

    def run(self, name: str, args: Any, source: str) -> Result:
        tool = self.registry.get(name)
        if tool is None or (source == "model" and not tool.for_model):
            self.store.log_action(source, name, args if isinstance(args, dict) else {}, "NEVER", "denied", "unknown tool")
            return Result(False, f"There is no tool called {name}.")
        try:
            clean = validate_args(tool, args or {})
        except ToolError as e:
            self.store.log_action(source, name, args if isinstance(args, dict) else {}, "-", "invalid", str(e))
            return Result(False, f"Invalid arguments: {e}")

        decision = tool.decide(clean) if tool.decide else Decision(tool.level)
        level = decision.level
        if self.tainted and level == Level.FREE and name in ESCALATE_WHEN_TAINTED:
            level = Level.ASK

        if level == Level.NEVER:
            self.store.log_action(source, name, clean, level.name, "refused", decision.reason)
            return Result(False, f"I won't do that: {decision.reason}.", declined=True)

        if level in (Level.ASK, Level.CLICK):
            approved = False
            try:
                approved = self.approver(tool.summary(clean), level == Level.CLICK)
            except Exception:
                log.exception("approval failed")
            if not approved:
                self.store.log_action(source, name, clean, level.name, "declined", "")
                return Result(False, "Okay boss, I've left it alone.", declined=True)

        try:
            text = tool.func(**clean)
            ok = True
        except ToolError as e:
            text, ok = str(e), False
        except Exception as e:  # unexpected failure: report, don't crash
            log.exception("tool %s failed", name)
            text, ok = f"{name} failed: {e.__class__.__name__}", False
        if ok and tool.untrusted_output:
            self.tainted = True
        self.store.log_action(source, name, clean, level.name, "ran" if ok else "failed", str(text))
        return Result(ok, str(text))
