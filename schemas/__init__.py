"""Pydantic models shared across the API layer."""

from typing import Dict

from pydantic import BaseModel


class VoiceRecordRequest(BaseModel):
    duration_seconds: int = 5


class VoiceResponse(BaseModel):
    transcription: str
    response: str
    state: str
    conversation_finished: bool
    conversation_negotiation_cancel: bool
    audio_generated: bool


class SystemStatus(BaseModel):
    status: str
    active_sessions: int
    ollama_available: bool
    stt_loaded: bool
    system_info: Dict[str, str]
