"""Clarification schemas."""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel


class ClarificationQuestion(BaseModel):
    id: str           # field key the answer fills, e.g. "units"
    question: str
    options: List[str] = []   # suggested choices; free text also allowed
    required: bool = True


class ClarificationSubmit(BaseModel):
    # Free-form map of question id -> answer (string or list of strings).
    answers: Dict[str, Any] = {}
