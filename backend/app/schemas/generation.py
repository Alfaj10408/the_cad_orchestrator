"""Generation / analysis schemas."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from app.schemas.clarification import ClarificationQuestion


class Intent(str, Enum):
    concept_cad = "concept_cad"
    printable_model = "printable_model"
    robotics_urdf = "robotics_urdf"
    mechatronic = "mechatronic"
    print_package = "print_package"
    unknown = "unknown"


class AnalyzeRequest(BaseModel):
    prompt: str


class PromptAnalysis(BaseModel):
    prompt: str
    intent: Intent
    confidence: float
    signals: List[str] = []
    missing: List[str] = []


class Brief(BaseModel):
    project_id: str
    prompt: str
    intent: Intent
    summary: str
    parameters: dict = {}
    assumptions: List[str] = []
    user_answers: dict = {}
    ready_to_generate: bool = False
    created_at: str


class GenerateRequest(BaseModel):
    generation_mode: str = "deterministic"  # deterministic | llm


class GenerateResponse(BaseModel):
    project_id: str
    job_id: str
    status: str
    stage: str
    message: str


class GenerationStatus(BaseModel):
    project_id: str
    status: str  # "not_started" or a job status
    stage: Optional[str] = None
    job_id: Optional[str] = None
    created_at: Optional[str] = None


class AnalyzeResponse(BaseModel):
    project_id: str
    intent: Intent
    confidence: float
    ready_to_generate: bool
    questions: List[ClarificationQuestion] = []
    brief: Optional[Brief] = None
