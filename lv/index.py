"""The assistant's search index: one card per line item, found by its short text first, its comments second and its
long text third (README.md, "Assistant").

A card has two halves. The search part is three texts, each with its own vector: `short` (section path and short
text), `comments` (the estimator's comments) and `long` (the start of the long text). The context part is what an
answer needs: project, item number, unit, quantity, price, prediction. A question is compared with all three vectors
and the scores are weighted by WEIGHTS; a card without comments shares their weight between the other two.

The vectors live in one cache file per model (EMBED_CACHE), keyed by the text, so a text is sent to Voyage only once
and a new comment embeds only that comment. The index file of a project (data/index/<project>.json) holds only the
cards. Without a Voyage key, or while a vector is missing, the search runs by words alone.
"""
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from lv import ai
from lv.match import clean, describe
from lv.model import LV, load

FIELDS = ("short", "comments", "long")
WEIGHTS = {"short": 0.5, "comments": 0.3, "long": 0.2}  # short text first, comments second, long text third
LONG_CHARS = 1500  # characters of the long text that are embedded: material, size and scope come first
RRF_K = 60  # reciprocal rank fusion of the word ranking and the meaning ranking, as in lv.match
DEPTH = 40  # each ranking offers its best 40 before they are merged
BEAT = 30  # seconds: a running build touches its .building marker this often
STALE = 120  # seconds: a marker not touched for this long was left by a build that died
# Voyage's limits without a payment method: 3 calls and 10,000 tokens a minute. Raise both in .env once a payment
# method is added (VOYAGE_RPM, VOYAGE_TPM), and indexing gets much faster.
CALL_TOKENS = 8000  # estimated tokens per Voyage call
CHARS_PER_TOKEN = 3  # a cautious estimate for German LV text
EMBED_CACHE = ai.EMBED_FILE.with_name(f"{ai.EMBED_MODEL}-rag.json")  # under LV_DATA, like the step 3 vectors
# Question words that say nothing about the item; without this "Preis pro m3" finds "Pumpe ... pro h".
STOP = set("""was wie wo wer welche welcher welches welchen kostet kosten gekostet kostete preis preise preisspanne
pro je fur für mit und oder in im von vom zu zum zur ist sind war waren wir haben hatten habe hat schon bisher
der die das den dem des ein eine einen einem einer teuer billig viel viele uns unser unsere bei auf aus als an
gibt es projekt projekten projekte""".split())

Embed = Callable[[list[str], str], list[list[float]]]  # texts, "document" or "query" -> one vector per text


class Card(BaseModel):
    id: str  # project + "/" + item number, like LineItem.id
    project: str
    source: Literal["library", "tender"]  # library: a past priced LV; tender: a new LV being priced
    oz: str
    section: list[str]
    short_text: str
    long_text: str
    unit: str
    quantity: float | None
    unit_price: float | None  # library: the past price; tender: empty
    kind: str
    item_type: str = ""
    trade: str = ""
    method: str | None = None  # tender: how step 3 found the price
    median: float | None = None  # tender: the found price
    confidence: int | None = None
    comments: list[str] = []
    texts: dict[str, str]  # field -> the text that is embedded; a missing field has no text


class Index(BaseModel):
    project: str
    source: Literal["library", "tender"]
    name: str
    model: str
    built_at: str
    source_mtime: float  # the result file's time when the cards were made: older than the file means stale
    cards: list[Card]


class Hit(BaseModel):
    card: Card
    score: float  # fused rank points, only for ordering
    meaning: dict[str, float] = {}  # cosine per field, for explaining a hit
    words: float = 0.0


# --- building ----------------------------------------------------------------------------------------------------

def cards(lv: LV, source: str) -> list[Card]:
    found = []
    for described in describe(lv):
        item = described.item
        notes = [c.text for c in item.comments if c.text]
        short = _plain(" | ".join([" > ".join(item.section[-2:]), f"{described.refers_to} {item.short_text}".strip()]))
        texts = {"short": short}
        if notes:
            texts["comments"] = _plain(" / ".join(notes))
        if item.long_text and _plain(item.long_text) != _plain(item.short_text):
            texts["long"] = _plain(item.long_text[:LONG_CHARS])
        prediction, risk = item.prediction, item.risk
        found.append(Card(
            id=item.id, project=lv.project, source=source, oz=item.oz, section=item.section,
            short_text=item.short_text, long_text=item.long_text, unit=item.unit, quantity=item.quantity,
            unit_price=item.unit_price, kind=item.kind, item_type=item.item_type, trade=item.trade,
            method=prediction.method if prediction else None, median=prediction.median if prediction else None,
            confidence=risk.confidence if risk else None, comments=notes, texts=texts))
    return found


def build(lv: LV, data: Path, source: str, embed: Embed | None = None, say: Callable[[str], None] = print) -> Index:
    """Makes the cards of one LV, embeds every text not yet in the cache and saves data/index/<project>.json."""
    folder = data / "index"
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / f"{lv.project}.building"
    marker.write_text(str(os.getpid()))
    done = threading.Event()

    def touch() -> None:  # keeps the marker fresh, so state() can tell a running build from a dead one
        while not done.wait(BEAT):
            os.utime(marker)
    beat = threading.Thread(target=touch, daemon=True)
    beat.start()
    try:
        made = cards(lv, source)
        if embed:
            texts = [text for card in made for text in card.texts.values()]
            missing = [text for text in dict.fromkeys(texts) if CACHE.get(text, "document") is None]
            say(f"assistant index: {len(made)} positions, {len(missing)} of {len(set(texts))} texts to embed "
                f"({ai.EMBED_MODEL})")
            if missing:
                embed(missing, "document")
        else:
            say(f"assistant index: {len(made)} positions, search by words only")
        result = data / ("library" if source == "library" else "out") / f"{lv.project}.json"
        index = Index(project=lv.project, source=source, name=lv.name, model=ai.EMBED_MODEL,
                      built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      source_mtime=result.stat().st_mtime if result.exists() else 0.0, cards=made)
        _write(folder / f"{lv.project}.json", index.model_dump_json())
        return index
    finally:
        done.set()
        beat.join()
        marker.unlink(missing_ok=True)


def build_file(path: Path, data: Path, source: str, say: Callable[[str], None] = print) -> Index:
    return build(load(path), data, source, voyage_embed if ai.voyage_key() else None, say)


def refresh(lv: LV, data: Path, source: str = "tender") -> None:
    """After a comment: the cards are made again at once (no network), the new comment texts are embedded in the
    background. Until they are, the search uses the other two fields of those cards."""
    build(lv, data, source, embed=None, say=lambda _: None)
    if ai.voyage_key():
        texts = [card.texts["comments"] for card in cards(lv, source) if "comments" in card.texts]
        missing = [text for text in texts if CACHE.get(text, "document") is None]
        if missing:
            threading.Thread(target=_quietly, args=(missing,), daemon=True).start()


def _quietly(texts: list[str]) -> None:
    try:
        voyage_embed(texts, "document")
    except Exception as error:  # the comment is saved either way; the next index run embeds it
        print(f"[assistant index] comment not embedded: {error}", flush=True)


# --- state -------------------------------------------------------------------------------------------------------

def state(data: Path, project: str, source: str) -> Literal["ready", "indexing", "stale", "missing"]:
    folder = data / "index"
    marker = folder / f"{project}.building"
    if marker.exists() and time.time() - marker.stat().st_mtime < STALE:  # an older one: its build died
        return "indexing"
    path = folder / f"{project}.json"
    if not path.exists():
        return "missing"
    result = data / ("library" if source == "library" else "out") / f"{project}.json"
    if result.exists() and result.stat().st_mtime > _peek_mtime(path) + 1:
        return "stale"
    return "ready"


def _peek_mtime(path: Path) -> float:
    return _load(path).source_mtime


_loaded: dict[Path, tuple[float, Index]] = {}


def _load(path: Path) -> Index:
    stamp = path.stat().st_mtime
    known = _loaded.get(path)
    if known and known[0] == stamp:
        return known[1]
    index = Index.model_validate_json(path.read_text(encoding="utf-8"))
    _loaded[path] = (stamp, index)
    return index


def indexes(data: Path, projects: Iterable[str]) -> list[Index]:
    return [_load(data / "index" / f"{p}.json") for p in projects if (data / "index" / f"{p}.json").exists()]


# --- search ------------------------------------------------------------------------------------------------------

def search(found: list[Index], query: str, embed: Embed | None = None, limit: int = 8,
           unit: str | None = None) -> tuple[list[Hit], str]:
    """The best cards for a question, and how they were found ("words and meaning" or "words only ...")."""
    pool = [card for index in found for card in index.cards if not unit or card.unit.lower() == unit.lower()]
    if not pool:
        return [], "no positions"
    words = _word_scores(pool, query)
    by_words = [n for n in sorted(range(len(pool)), key=lambda n: -words[n]) if words[n] > 0][:DEPTH]
    meaning: list[dict[str, float]] = []
    how = "words only (no VOYAGE_API_KEY in .env)"
    if embed:
        try:
            q = embed([_plain(query)], "query")[0]
            meaning = [_meaning(q, card) for card in pool]
            how = "words and meaning"
        except Exception as error:  # e.g. Voyage's per-minute limit: answer by words instead of waiting a minute
            how = f"words only (meaning search failed: {type(error).__name__})"
    ranks = [by_words]
    if meaning:
        ranks.append(sorted(range(len(pool)), key=lambda n: -_weighted(meaning[n]))[:DEPTH])
    points: Counter = Counter()
    for ranking in ranks:
        for place, n in enumerate(ranking, start=1):
            points[n] += 1 / (RRF_K + place)
    best = sorted(points, key=lambda n: (-points[n], n))[:limit]
    return [Hit(card=pool[n], score=round(points[n], 5), meaning=meaning[n] if meaning else {},
                words=round(words[n], 3)) for n in best], how


def _meaning(query: list[float], card: Card) -> dict[str, float]:
    found = {}
    for field, text in card.texts.items():
        vector = CACHE.get(text, "document")
        if vector is not None:
            found[field] = round(math.sumprod(query, vector), 4)
    return found


def _weighted(parts: dict[str, float]) -> float:
    """Short 0.5, comments 0.3, long 0.2; a field the card does not have gives its weight to the others."""
    weight = sum(WEIGHTS[field] for field in parts)
    return sum(WEIGHTS[field] * score for field, score in parts.items()) / weight if weight else 0.0


def _word_scores(pool: list[Card], query: str) -> list[float]:
    """TF-IDF cosine, with the short text counted twice so it outweighs a long text that mentions the same word."""
    docs = [_words(f"{c.texts['short']} {c.texts['short']} {c.texts.get('comments', '')} {c.texts.get('long', '')}")
            for c in pool]
    seen = Counter(word for doc in docs for word in set(doc))
    rare = {word: math.log(len(docs) / n) + 1 for word, n in seen.items()}

    def vector(words: list[str]) -> dict[str, float]:
        weights = {w: n * rare.get(w, 0.0) for w, n in Counter(words).items()}
        length = math.sqrt(sum(x * x for x in weights.values())) or 1.0
        return {w: x / length for w, x in weights.items()}

    asked = [w for w in _words(query) if w not in STOP]
    # German compounds: "rohrgraben" also finds "rohrgrabenaushub", "schieber" finds "absperrschieber"
    parts = [longer for w in asked if len(w) >= 5 and not w[0].isdigit()
             for longer in seen if longer != w and w in longer]
    own = vector(asked + parts)
    return [sum(x * vector(doc).get(w, 0.0) for w, x in own.items()) for doc in docs]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß]+|\d+(?:[.,]\d+)?", clean(text))


def _plain(text: str) -> str:
    """Without anonymiser tokens: they hide a different word in each file, so they mean nothing across projects."""
    return " ".join(re.sub(r"\[[a-z]+_\d+\]", " ", clean(text)).split())


# --- vectors -----------------------------------------------------------------------------------------------------

class Cache:
    """text -> vector, one JSON file per model, shared by the API and the runs. Re-read when another process wrote it."""

    def __init__(self, path: Path):
        self.path, self.stamp, self.vectors = path, -1.0, {}
        self.lock = threading.Lock()

    @staticmethod
    def key(text: str, kind: str) -> str:
        return hashlib.sha1(f"{kind}\x1f{text}".encode()).hexdigest()[:16]

    def _fresh(self) -> None:
        stamp = self.path.stat().st_mtime if self.path.exists() else 0.0
        if stamp != self.stamp:
            self.vectors = json.loads(self.path.read_text()) if self.path.exists() else {}
            self.stamp = stamp

    def get(self, text: str, kind: str) -> list[float] | None:
        with self.lock:
            self._fresh()
            return self.vectors.get(self.key(text, kind))

    def put(self, pairs: dict[str, list[float]], kind: str) -> None:
        with self.lock:
            self.stamp = -1.0
            self._fresh()  # merge with what another process saved meanwhile
            self.vectors |= {self.key(text, kind): [round(x, 6) for x in v] for text, v in pairs.items()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _write(self.path, json.dumps(self.vectors))
            self.stamp = self.path.stat().st_mtime


CACHE = Cache(EMBED_CACHE)
_calls: list[tuple[float, int]] = []  # (time, estimated tokens) of the Voyage calls in this process


def voyage_embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    """One vector per text (length 1, so the dot product is the cosine). Waits for Voyage's per-minute limits while
    indexing; a question ("query") does not wait but fails, so the search can fall back to words."""
    found = {text: CACHE.get(text, kind) for text in texts}
    missing = [text for text, vector in found.items() if vector is None]
    if missing:
        import voyageai  # imported here: most calls find every vector saved

        client = voyageai.Client(api_key=ai.voyage_key(), max_retries=0)
        for batch in _batches(missing):
            tokens = sum(len(text) for text in batch) // CHARS_PER_TOKEN + 1
            for attempt in range(1, ai.EMBED_TRIES + 1):
                if kind == "document":
                    _pace(tokens)
                try:
                    vectors = client.embed(batch, model=ai.EMBED_MODEL, input_type=kind).embeddings
                    break
                except (voyageai.error.RateLimitError, voyageai.error.ServiceUnavailableError) as error:
                    if kind == "query" or attempt == ai.EMBED_TRIES:
                        raise RuntimeError(f"Voyage AI ({ai.EMBED_MODEL}) is at its limit: {error}") from error
                    print("[Voyage at its per-minute limit; waiting 60 s]", flush=True)
                    time.sleep(60)
            _calls.append((time.monotonic(), tokens))
            CACHE.put(dict(zip(batch, vectors, strict=True)), kind)
            found |= dict(zip(batch, vectors, strict=True))
            if kind == "document":
                done = sum(1 for text in missing if found[text] is not None)
                print(f"  embedded {done} of {len(missing)} texts", flush=True)
    return [found[text] for text in texts]


def _batches(texts: list[str]) -> list[list[str]]:
    batches, batch, size = [], [], 0
    for text in texts:
        tokens = len(text) // CHARS_PER_TOKEN + 1
        if batch and (size + tokens > CALL_TOKENS or len(batch) >= 128):
            batches.append(batch)
            batch, size = [], 0
        batch.append(text)
        size += tokens
    return batches + ([batch] if batch else [])


def _pace(tokens: int) -> None:
    """Sleeps until one more call of `tokens` fits Voyage's limits for the last 60 seconds."""
    rpm = int(os.environ.get("VOYAGE_RPM") or ai.env("VOYAGE_RPM") or 3)
    tpm = int(os.environ.get("VOYAGE_TPM") or ai.env("VOYAGE_TPM") or 10000)
    while True:
        now = time.monotonic()
        recent = [(at, n) for at, n in _calls if now - at < 61]
        if len(recent) < rpm and sum(n for _, n in recent) + tokens <= tpm:
            return
        wait = 61 - (now - recent[0][0])
        print(f"  waiting {wait:.0f} s for Voyage's per-minute limit", flush=True)
        time.sleep(max(wait, 1))


def _write(path: Path, text: str) -> None:
    """Writes through a temporary file, so a reader never sees half a file."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
