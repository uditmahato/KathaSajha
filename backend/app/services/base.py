"""Generation provider contract shared by Gemini and mock implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StoryDraft:
    title: str
    paragraphs: list[str]
    image_prompts: list[str]  # one per paragraph, ready to feed the image model
    moral: str = ""


@dataclass
class GeneratedImage:
    data: bytes | None = None
    mime: str = "image/png"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.error


@dataclass
class StoryRequest:
    prompt: str
    language: str = "en"  # en | ne
    hero_name: str = ""
    max_paragraphs: int = 5


class GenerationProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def write_story(self, req: StoryRequest) -> StoryDraft:
        """Generate title + paragraphs + per-paragraph illustration prompts in ONE call."""

    @abstractmethod
    async def illustrate(self, image_prompt: str, *, title: str, position: int) -> GeneratedImage:
        """Generate a single illustration. Must not raise; return .error instead."""


class GenerationError(Exception):
    """Raised when story text generation fails (images fail soft, stories fail hard)."""

    def __init__(self, message: str, *, user_message: str = ""):
        super().__init__(message)
        self.user_message = user_message or "Story generation failed. Please try a different idea."
