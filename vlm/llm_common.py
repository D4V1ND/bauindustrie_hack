"""Shared helpers: OpenAI client from vlm/.env and a JSON chat call."""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")


def client_and_model() -> tuple[OpenAI, str]:
    key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_MODEL")
    if not key or not model:
        sys.exit("Fehlt: OPENAI_API_KEY und/oder OPENAI_MODEL in vlm/.env")
    return OpenAI(api_key=key), model


def chat_json(client: OpenAI, model: str, content: list[dict]) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)
