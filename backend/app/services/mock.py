"""Deterministic mock provider — full pipeline works with no API key.

Stories are templated from the prompt; illustrations are generated locally with
Pillow (soft gradient + simple shapes + caption), so the end-to-end flow
(queue, progress, storage, frontend, PDF) is fully testable offline.
"""

import asyncio
import hashlib
import random
from io import BytesIO

from PIL import Image, ImageDraw

from .base import GeneratedImage, GenerationProvider, StoryDraft, StoryRequest

_PALETTES = [
    ((255, 214, 165), (255, 111, 97)),  # sunrise
    ((181, 234, 215), (0, 129, 167)),  # lagoon
    ((255, 239, 213), (214, 45, 32)),  # nepali red
    ((230, 220, 255), (108, 92, 231)),  # twilight
    ((255, 250, 205), (255, 190, 11)),  # gold
]

_OPENINGS_EN = [
    "Once upon a time, in a valley wrapped in morning mist,",
    "Long ago, beneath the tallest mountain in the world,",
    "In a small village where prayer flags danced in the wind,",
]
_OPENINGS_NE = [
    "एकादेशमा, हिमालको फेदमा बसेको सानो गाउँमा,",
    "धेरै वर्ष पहिले, कुहिरोले ढाकेको उपत्यकामा,",
]


class MockProvider(GenerationProvider):
    name = "mock"

    async def write_story(self, req: StoryRequest) -> StoryDraft:
        await asyncio.sleep(0.8)  # simulate model latency so progress UI is visible
        rng = random.Random(hashlib.sha256(req.prompt.encode()).hexdigest())
        hero = req.hero_name or "our young hero"
        n = req.max_paragraphs

        if req.language == "ne":
            opening = rng.choice(_OPENINGS_NE)
            title = f"कथा: {req.prompt[:40]}"
            beats = [
                f"{opening} {hero} बस्थे। {req.prompt} भन्ने कुराले उनको मन तानिरह्यो।",
                f"एक बिहान {hero} ले साहस बटुलेर यात्रा सुरु गरे। बाटोमा नयाँ साथीहरू भेटिए।",
                "बाटो सजिलो थिएन। ठूलो चुनौती आइपुग्यो, तर हार मान्ने कुरै थिएन।",
                f"साथीहरूको मद्दत र आफ्नै जुक्तिले {hero} ले समस्या समाधान गरे।",
                "त्यस दिनदेखि गाउँभरि खुसी छायो। साहस र मित्रताले जित्यो।",
            ]
        else:
            opening = rng.choice(_OPENINGS_EN)
            title = f"The Tale of {req.prompt.title()[:50]}"
            beats = [
                f"{opening} there lived {hero}, whose heart was set on one thing: {req.prompt}.",
                f"One bright morning, {hero} packed a small bag, took a deep breath, and set off. Along the winding path, unexpected friends joined the journey, each with a talent of their own.",
                f"But the way was not easy. A great challenge rose before them, and for a moment even the bravest heart trembled. {hero} remembered what grandmother always said: courage is being scared and trying anyway.",
                f"Working together, using every clever idea they had, {hero} and the new friends faced the challenge head-on. And slowly, wonderfully, things began to change.",
                f"When they returned home, the whole village celebrated. {hero} had learned that kindness and courage, shared with friends, can move mountains.",
            ]
        paragraphs = beats[:n]
        image_prompts = [
            f"Colorful children's storybook illustration, scene {i + 1} of '{title}': {p[:140]}"
            for i, p in enumerate(paragraphs)
        ]
        return StoryDraft(
            title=title,
            paragraphs=paragraphs,
            image_prompts=image_prompts,
            moral="Courage and kindness, shared with friends, can move mountains.",
        )

    async def illustrate(self, image_prompt: str, *, title: str, position: int) -> GeneratedImage:
        await asyncio.sleep(0.5 + (position % 3) * 0.3)  # staggered latency for realistic progress
        seed = int(hashlib.sha256(f"{title}:{position}".encode()).hexdigest(), 16)
        rng = random.Random(seed)
        top, bottom = _PALETTES[seed % len(_PALETTES)]

        w, h = 768, 512
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        for y in range(h):  # vertical gradient
            t = y / h
            draw.line(
                [(0, y), (w, y)],
                fill=tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)),
            )
        # hills
        hill = tuple(max(0, c - 60) for c in bottom)
        draw.polygon(
            [(0, h), (0, h - 90), (w * 0.35, h - 190), (w * 0.7, h - 80), (w, h - 150), (w, h)], fill=hill
        )
        # sun/moon
        cx, cy, r = rng.randint(100, w - 100), rng.randint(70, 160), rng.randint(35, 55)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 250, 235))
        # sparkles
        for _ in range(24):
            x, y = rng.randint(0, w), rng.randint(0, h - 200)
            s = rng.randint(1, 3)
            draw.ellipse([x, y, x + s, y + s], fill=(255, 255, 255))
        # simple character: circle body + head
        bx, by = w // 2 + rng.randint(-120, 120), h - 130
        body = tuple(min(255, c + 40) for c in top)
        draw.ellipse([bx - 38, by - 30, bx + 38, by + 60], fill=body, outline=(60, 40, 30), width=3)
        draw.ellipse(
            [bx - 24, by - 78, bx + 24, by - 30], fill=(250, 224, 196), outline=(60, 40, 30), width=3
        )
        draw.ellipse([bx - 12, by - 62, bx - 6, by - 56], fill=(40, 30, 30))
        draw.ellipse([bx + 6, by - 62, bx + 12, by - 56], fill=(40, 30, 30))
        draw.arc([bx - 10, by - 56, bx + 10, by - 42], 20, 160, fill=(40, 30, 30), width=2)
        # scene number badge
        draw.ellipse([w - 64, 16, w - 16, 64], fill=(255, 255, 255))
        draw.text((w - 46, 28), str(position + 1), fill=(60, 40, 30))
        draw.text((20, h - 30), "KathaSajha mock illustration", fill=(255, 255, 255))

        buf = BytesIO()
        img.save(buf, format="PNG")
        return GeneratedImage(data=buf.getvalue(), mime="image/png")
