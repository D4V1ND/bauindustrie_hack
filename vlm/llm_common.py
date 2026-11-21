"""Shared helpers: the chat client (OpenAI or Claude) from vlm/.env and a JSON chat call.

OpenAI when OPENAI_API_KEY and OPENAI_MODEL are set, otherwise Claude with ANTHROPIC_API_KEY
(model: ANTHROPIC_PLAN_MODEL, default PLAN_MODEL). Callers pass OpenAI-style content blocks;
chat_json translates them for Claude."""
import base64
import io
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PLAN_MODEL = "claude-opus-5-5"  # reads drawings: the strongest vision model
MAX_TOKENS = 16000
MAX_IMAGE_BYTES = 4_500_000  # Claude takes at most 5 MB per image (base64 counted)
MAX_SIDE = 7000  # and at most 8000 px per side


def client_and_model() -> tuple:
    key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL")
    if key and model:
        from openai import OpenAI
        return OpenAI(api_key=key), model
    akey = os.getenv("ANTHROPIC_API_KEY")
    if akey:
        import anthropic
        return anthropic.Anthropic(api_key=akey), os.getenv("ANTHROPIC_PLAN_MODEL") or PLAN_MODEL
    sys.exit("Fehlt: ANTHROPIC_API_KEY (oder OPENAI_API_KEY und OPENAI_MODEL) in .env")


def chat_json(client, model: str, content: list[dict]) -> dict:
    if type(client).__module__.startswith("anthropic"):
        return _claude_json(client, model, content)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def _claude_json(client, model: str, content: list[dict]) -> dict:
    blocks = []
    for block in content:
        if block.get("type") == "image_url":
            media, data = _claude_image(block["image_url"]["url"])
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        else:
            blocks.append({"type": "text", "text": block["text"]})
    blocks.append({"type": "text", "text": "Antworte nur mit dem JSON-Objekt, ohne Text davor oder danach."})
    with client.messages.stream(model=model, max_tokens=MAX_TOKENS,
                                messages=[{"role": "user", "content": blocks}]) as stream:
        msg = stream.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text")
    return _parse_json(text)


def _claude_image(url: str) -> tuple[str, str]:
    """A data URL as (media type, base64), shrunk to Claude's limits when needed."""
    head, b64 = url.split(",", 1)
    media = head[5:].split(";")[0] or "image/png"
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    media = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}.get(img.format, media)
    if img.format not in ("PNG", "JPEG", "GIF", "WEBP"):
        b64 = ""  # other formats are re-encoded below
    if b64 and len(b64) <= MAX_IMAGE_BYTES and max(img.size) <= MAX_SIDE:
        return media, b64
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    img = img.convert("RGB")
    out = b64
    for quality in (90, 80, 70, 60):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        out = base64.b64encode(buf.getvalue()).decode()
        if len(out) <= MAX_IMAGE_BYTES:
            break
        img.thumbnail((int(img.width * 0.8), int(img.height * 0.8)))
    return "image/jpeg", out


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])
