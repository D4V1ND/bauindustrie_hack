"""Read a GAEB file (X83 XML or D83 text) directly with the LLM - no parser.

The raw file text is sent to the model, which returns the line items as JSON.
Big files are cut into text chunks. A cheap completeness check compares the
number of items found with the number of <Item> tags in the raw text.

Usage: python gaeb_llm.py path/to/file.X83  ->  out/<name>_positionen.json
"""
import json
import re
import sys
from pathlib import Path

from llm_common import chat_json, client_and_model

CHUNK_CHARS = 90_000

PROMPT = """Du erhältst den Rohtext einer GAEB-Datei (Leistungsverzeichnis, XML oder altes Textformat).
Bei grossen Dateien ist es nur ein Ausschnitt. Extrahiere JEDE Leistungsposition 1:1.

Regeln:
- oz: vollständige Ordnungszahl. In GAEB-XML steht sie verteilt: die Nummern der umgebenden Gruppen
  (BoQCtgy RNoPart) plus die der Position (Item RNoPart), zusammengesetzt mit Punkt, z. B. "04.0010".
- kurztext: Kurztext/Bezeichnung der Position (reiner Text, ohne Formatierung).
- menge: Zahl (Punkt als Dezimaltrenner) oder null, wenn keine Menge angegeben ist.
- einheit: Mengeneinheit wie angegeben (St, m, m2, m3, kg, t, h, psch ...), sonst "".
- gruppe: Titel der umgebenden Gruppe, wenn im Ausschnitt sichtbar, sonst "".
- Gruppen/Titel ohne Menge sind KEINE Positionen. Lass nichts aus, kürze und erfinde nichts.

Antworte NUR mit JSON: {"positionen": [{"oz": "", "gruppe": "", "kurztext": "", "menge": 0, "einheit": ""}]}

GAEB-Text:
"""


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Cut at item/tag boundaries so a position is not split in two."""
    out, i = [], 0
    while i < len(text):
        j = min(i + size, len(text))
        if j < len(text):
            k = text.rfind("</Item>", i, j)
            if k > i:
                j = k + len("</Item>")
            else:
                k = max(text.rfind(">", i, j), text.rfind("\n", i, j))
                j = k + 1 if k > i else j
        out.append(text[i:j])
        i = j
    return out


def extract_positions(path: Path, client=None, model=None) -> dict:
    if client is None:
        client, model = client_and_model()
    text = read_text(path)
    chunks = split_chunks(text)
    print(f"{path.name}: {len(text):,} Zeichen, {len(chunks)} Teil(e)", flush=True)
    seen, positions = set(), []
    for n, chunk in enumerate(chunks, 1):
        data = chunk if n == 1 else chunk
        res = chat_json(client, model, [{"type": "text", "text": PROMPT + data}])
        for p in res.get("positionen", []):
            if p.get("oz") in seen:
                continue
            seen.add(p.get("oz"))
            positions.append(p)
        print(f"  Teil {n}/{len(chunks)}: {len(positions)} Positionen bisher", flush=True)
    expected = len(re.findall(r"<Item[\s>]", text))
    check = {"erwartet_laut_xml_tags": expected or None, "gelesen": len(positions),
             "vollstaendig": (None if not expected else len(positions) >= expected)}
    return {"datei": path.name, "positionen": positions, "pruefung": check}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    src = Path(sys.argv[1])
    result = extract_positions(src)
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    dest = out / f"{src.stem}_positionen.json"
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(result['positionen'])} Positionen, Prüfung: {result['pruefung']} -> {dest}")
