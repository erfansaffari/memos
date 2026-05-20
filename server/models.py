from typing import Optional

from pydantic import BaseModel


class RecallRequest(BaseModel):
    query: str
    budget: str = "medium"    # "shallow" | "medium" | "deep"
    platform: str = "unknown"  # "claude" | "chatgpt" | "gemini"


class RecallResponse(BaseModel):
    context: str
    memory_count: int
    levels_used: list[int]
    budget: str


class RememberRequest(BaseModel):
    user_message: str
    assistant_response: str
    platform: str = "unknown"


class RememberResponse(BaseModel):
    memories_stored: int
    memories_skipped: int
    reasoning: str


class StatsResponse(BaseModel):
    total: int
    by_level: dict
    server_version: str = "1.0.0"
