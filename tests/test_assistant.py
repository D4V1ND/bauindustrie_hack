"""The assistant's index, tools, tabs and waiting rule, without Claude or Voyage: the model is a scripted fake."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lv import api, assistant, index
from lv.model import LV, Comment, LineItem, save


def _item(project, oz, short, unit="St", price=None, long="", section=("06 Armaturen",), comments=()):
    return LineItem(id=f"{project}/{oz}", oz=oz, section=list(section), short_text=short, long_text=long,
                    quantity=1.0, unit=unit, unit_price=price, comments=[Comment(text=c) for c in comments])


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "CACHE", index.Cache(tmp_path / "embeddings" / "cache.json"))
    library = LV(project="P1", name="Pumpwerk", date=None, source_file="p1.d86", items=[
        _item("P1", "06.01.4", "Absperrschieber DN 200, PN 10", price=774.53),
        _item("P1", "06.01.6", "Absperrschieber handbetätigt versetzen, DN 200", price=250.70,
              section=("06 Armaturen", "06.01 Absperrarmaturen handbetätigt")),
        _item("P1", "02.01.1", "Asphalttragschicht 14 cm", unit="m2", price=48.60),
        _item("P1", "02.02.1", "Pumpe 10 l/s pro h", unit="h", price=6.80)])
    tender = LV(project="H1", name="Neubau", date=None, source_file="h1.x83", items=[
        _item("H1", "01.0010", "Absperrschieber DN 150"), _item("H1", "01.0020", "Baustelleneinrichtung", "psch")])
    save(library, tmp_path / "library")
    save(tender, tmp_path / "out")
    index.build(library, tmp_path, "library", None, say=lambda _: None)
    index.build(tender, tmp_path, "tender", None, say=lambda _: None)
    return tmp_path


def test_a_card_is_found_by_short_text_comments_and_long_text_in_that_order(data):
    card = next(c for c in index.indexes(data, ["P1"])[0].cards if c.oz == "06.01.6")
    assert card.texts["short"].startswith("06 armaturen > 06.01 absperrarmaturen handbetätigt | absperrschieber")
    assert "comments" not in card.texts and "long" not in card.texts  # nothing to embed for them
    assert index._weighted({"short": 1.0, "comments": 0.0, "long": 0.0}) == 0.5
    assert index._weighted({"short": 1.0, "long": 0.0}) == pytest.approx(0.5 / 0.7)  # no comments: weight shared


def test_question_words_do_not_find_items_and_the_meaning_search_uses_all_three_fields(data):
    hits, how = index.search(index.indexes(data, ["P1"]), "Was kostet Asphalttragschicht pro m2?")
    assert how.startswith("words only") and hits[0].card.oz == "02.01.1" and len(hits) == 1  # "pro" finds no pump

    def fake(texts, kind):  # "handrad" means the hand-operated valve, as a real embedding would say
        return [[1.0, 0.0] if ("handbetätigt" in t or "handrad" in t) else [0.0, 1.0] for t in texts]
    index.CACHE.put({t: v for card in index.indexes(data, ["P1"])[0].cards for t, v in
                     zip(card.texts.values(), fake(list(card.texts.values()), "document"))}, "document")
    hits, how = index.search(index.indexes(data, ["P1"]), "Schieber mit Handrad", fake)
    assert how == "words and meaning"
    best = {hit.card.oz: hit for hit in hits[:2]}  # words rank 06.01.4 first ("schieber" in "absperrschieber")
    assert "06.01.6" in best and best["06.01.6"].meaning == {"short": 1.0}


def _reply(*blocks, stop="end_turn"):
    content = [SimpleNamespace(**b, model_dump=lambda mode=None, exclude_none=None, b=b: b) for b in blocks]
    return SimpleNamespace(content=content, stop_reason=stop)


def test_the_assistant_searches_then_comments_only_on_its_own_tender(data):
    tab = assistant.new_tab(data, "H1")
    script = iter([
        _reply({"type": "tool_use", "id": "t1", "name": "search_positions",
                "input": {"query": "Absperrschieber DN 200", "scope": "library", "unit": None, "limit": 3}},
               stop="tool_use"),
        _reply({"type": "tool_use", "id": "t2", "name": "save_comment",
                "input": {"oz": "01.0010", "text": "Preis wie P1 06.01.4 prüfen", "price": None, "doubt": False}},
               {"type": "tool_use", "id": "t3", "name": "save_comment",
                "input": {"oz": "06.01.4", "text": "x", "price": None, "doubt": False}}, stop="tool_use"),
        _reply({"type": "text", "text": "774,53 €/St (P1, 06.01.4). Kommentar gespeichert."})])
    sent = []

    def create(**request):
        sent.append(json.loads(json.dumps(request["messages"])))
        return next(script)

    ready = assistant.Readiness(state="ready", message="", library=["P1"])
    reply = assistant.ask(data, tab, "Was kostet ein Schieber DN 200? Schreib es an 01.0010.", ready,
                          running=lambda p: False, create=create)
    assert reply.state == "answered" and reply.text.startswith("774,53")
    assert reply.actions == ["Kommentar an 01.0010 gespeichert"] and "P1/06.01.4" in reply.positions
    results = sent[2][-1]["content"]
    assert results[0].get("is_error") is None and results[1]["is_error"]  # the library cannot be changed
    saved = json.loads((data / "out" / "H1.json").read_text())["items"][0]["comments"][0]
    assert saved["author"] == "assistant" and saved["text"] == "Preis wie P1 06.01.4 prüfen"
    again = assistant.get_tab(data, tab.id)
    assert [m.role for m in again.shown] == ["user", "assistant"] and len(again.messages) == 6


def test_bidder_questions_and_memory_are_saved_and_writes_wait_for_the_run(data):
    tab = assistant.new_tab(data, "H1")
    session = assistant.Session(data, tab, running=lambda p: False)
    draft = session.draft_bidder_question(["01.0020"], "Baustelleneinrichtung", "Ist ... anzusetzen?", "Teuer")
    assert draft["saved"]["id"] == "BF01" and assistant.bidder_questions(data, "H1")[0].reason_internal == "Teuer"
    session.update_bidder_question("BF01", None, None, None, "ready")
    assert assistant.bidder_questions(data, "H1")[0].status == "ready"
    fact = session.remember("Wir bieten nur bis 5 Mio.", False)["saved"]
    assert "5 Mio." in assistant._context(data, tab) and session.forget(fact["id"])
    busy = assistant.Session(data, tab, running=lambda p: True)
    with pytest.raises(assistant.ToolError, match="running"):
        busy.save_comment("01.0010", "x", None, False)


@pytest.fixture
def client(data, monkeypatch):
    monkeypatch.setattr(api, "DATA", data)
    monkeypatch.setattr(api, "runs", {})
    return TestClient(api.app)


def test_tabs_and_the_waiting_answer_while_the_extraction_runs(client, data):
    tab = client.post("/assistant/tabs", json={"project": "H1", "title": "Preise"}).json()
    second = client.post("/assistant/tabs", json={"project": "H1"}).json()
    assert [t["title"] for t in client.get("/assistant/tabs?project=H1").json()] == ["Preise", "Chat H1"]
    assert client.patch(f"/assistant/tabs/{second['id']}", json={"title": "Risiken"}).json()["title"] == "Risiken"
    assert client.delete(f"/assistant/tabs/{second['id']}").status_code == 204
    assert client.post("/assistant/tabs", json={"project": "H9"}).status_code == 404

    api.runs["H1"] = SimpleNamespace(poll=lambda: None)  # a run of H1 is going
    assert client.get("/assistant/status?project=H1").json()["state"] == "extracting"
    reply = client.post(f"/assistant/tabs/{tab['id']}/messages", json={"text": "Was kostet 01.0010?"}).json()
    assert reply["state"] == "waiting" and "warte" in reply["text"]
    assert client.get(f"/assistant/tabs/{tab['id']}").json()["shown"] == []  # nothing saved, ask again later

    api.runs["H1"] = SimpleNamespace(poll=lambda: 0)  # done
    assert client.get("/assistant/status?project=H1").json()["state"] == "ready"
    assert client.post(f"/assistant/tabs/{tab['id']}/messages", json={"text": "Hallo"}).status_code == 503  # no key

    fact = client.post("/assistant/memory", json={"text": "Beton immer C30/37 prüfen"}).json()
    assert fact["by"] == "estimator" and len(client.get("/assistant/memory").json()) == 1
    assert client.delete(f"/assistant/memory/{fact['id']}").status_code == 204


def test_tool_schemas_follow_the_strict_rules_claude_checks():
    """Claude rejects an enum next to a type list (["string", "null"]): a nullable enum must be anyOf."""
    def walk(schema):
        if "enum" in schema:
            assert isinstance(schema.get("type"), str), schema
        for value in schema.values():
            for child in value.values() if isinstance(value, dict) else value if isinstance(value, list) else []:
                if isinstance(child, dict):
                    walk(child)
    for tool in assistant.TOOLS:
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
        walk(schema)
