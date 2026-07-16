from __future__ import annotations

from dataclasses import dataclass, field

from src.rbac import UserContext, UserRole


@dataclass(frozen=True)
class TextToSQLRequest:
    question: str
    user: UserContext

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("Question must not be empty")
        if not isinstance(self.user, UserContext):
            raise ValueError("User must be a valid UserContext")


@dataclass(frozen=True)
class TextToSQLResponse:
    success: bool
    question: str
    provider: str
    model: str
    role: UserRole
    generated_sql: str
    authorized_sql: str
    explanation: str
    rows: list[dict[str, object]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    row_level_security_applied: bool = False
    guardrail_checks: list[str] = field(default_factory=list)
    rbac_checks: list[str] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.success:
            if not self.generated_sql.strip():
                raise ValueError("Successful responses must include generated_sql")
            if not self.authorized_sql.strip():
                raise ValueError("Successful responses must include authorized_sql")
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("Successful responses must not include errors")
        else:
            object.__setattr__(self, "rows", [])
            object.__setattr__(self, "row_count", 0)
            object.__setattr__(self, "truncated", False)
            object.__setattr__(self, "row_level_security_applied", False)
