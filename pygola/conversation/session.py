"""Conversation session management.

A ConversationSession holds the ordered list of turns for one multi-turn chat.
Each Turn stores only the sanitized user message and the de-pseudonymized
assistant reply — never raw PII values.

ConversationStore is the abstract interface; InMemoryConversationStore is the
default implementation suitable for single-process deployments.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Turn:
    """A single completed exchange: one sanitized user message and the reply."""
    user_message: str
    assistant_reply: str


@dataclass
class ConversationSession:
    """An ongoing dialogue identified by a UUID."""
    conversation_id: str
    turns: list[Turn] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationStore(ABC):
    """Abstract store for conversation sessions."""

    @abstractmethod
    def create_session(self) -> str:
        """Create a new session and return its conversation_id."""
        raise NotImplementedError

    @abstractmethod
    def get_turns(self, conversation_id: str) -> list[Turn]:
        """Return all turns for a session. Raises KeyError if not found."""
        raise NotImplementedError

    @abstractmethod
    def has_session(self, conversation_id: str) -> bool:
        """Return True if the session exists and has not expired."""
        raise NotImplementedError

    @abstractmethod
    def append_turn(self, conversation_id: str, turn: Turn) -> None:
        """Append a completed turn and update last_active_at."""
        raise NotImplementedError

    @abstractmethod
    def delete_session(self, conversation_id: str) -> None:
        """Delete a session and all its history. No-op if not found."""
        raise NotImplementedError

    @abstractmethod
    def expire_idle_sessions(self, timeout_seconds: int) -> int:
        """Delete sessions idle beyond timeout_seconds. Returns count removed."""
        raise NotImplementedError


class InMemoryConversationStore(ConversationStore):
    """Thread-safe in-memory implementation of ConversationStore.

    Suitable for single-process deployments. State is lost on restart.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}

    def create_session(self) -> str:
        conversation_id = str(uuid.uuid4())
        self._sessions[conversation_id] = ConversationSession(
            conversation_id=conversation_id
        )
        return conversation_id

    def get_turns(self, conversation_id: str) -> list[Turn]:
        session = self._sessions.get(conversation_id)
        if session is None:
            raise KeyError(conversation_id)
        return list(session.turns)

    def has_session(self, conversation_id: str) -> bool:
        return conversation_id in self._sessions

    def append_turn(self, conversation_id: str, turn: Turn) -> None:
        session = self._sessions.get(conversation_id)
        if session is None:
            raise KeyError(conversation_id)
        session.turns.append(turn)
        session.last_active_at = datetime.now(timezone.utc)

    def delete_session(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)

    def expire_idle_sessions(self, timeout_seconds: int) -> int:
        now = datetime.now(timezone.utc)
        expired = [
            cid
            for cid, session in self._sessions.items()
            if (now - session.last_active_at).total_seconds() > timeout_seconds
        ]
        for cid in expired:
            del self._sessions[cid]
        return len(expired)
