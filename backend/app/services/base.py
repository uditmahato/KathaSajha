"""Generation provider contract shared by Gemini and mock implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    """What a provider call actually consumed.

    Units, not money. Prices change and vary by model, so cost is derived later
    from configured rates; the units are the durable record. A provider that
    reports nothing leaves these at zero, which reads as "free" and is correct
    for the mock.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            images=self.images + other.images,
        )


@dataclass
class StoryDraft:
    title: str
    paragraphs: list[str]
    image_prompts: list[str]  # one per paragraph, ready to feed the image model
    moral: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class GeneratedImage:
    data: bytes | None = None
    mime: str = "image/png"
    error: str = ""
    usage: Usage = field(default_factory=Usage)

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.error


@dataclass
class StoryRequest:
    prompt: str
    language: str = "en"  # en | ne
    hero_name: str = ""
    max_paragraphs: int = 5
    # The story's frozen cast, as stored on the row. Passed as JSON so the
    # provider boundary stays free of ORM types.
    cast_json: str = ""
    # Band code only — an exact age never crosses this boundary.
    reading_band: str = ""


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
