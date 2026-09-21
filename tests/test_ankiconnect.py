import httpx

from highlightanki import ankiconnect

CARDS = [
    {"notetype": "Cloze", "front": "{{c1::A}} b.", "back": "why", "tags": ["Alpha", "alpha", "b c"]},
    {"notetype": "Basic", "front": "q?", "back": "a.", "tags": ["beta"]},
]


def test_notes_for_field_mapping_and_tag_normalization():
    notes = ankiconnect.notes_for(CARDS, "T — https://u.test", "Study")
    assert notes[0] == {
        "deckName": "Study",
        "modelName": "Cloze",
        "fields": {"Text": "{{c1::A}} b.", "Back Extra": "why", "Source": "T — https://u.test"},
        "tags": ["alpha", "b-c"],
        "options": {"allowDuplicate": False},
    }
    assert notes[1]["modelName"] == "Basic"
    assert notes[1]["fields"] == {"Front": "q?", "Back": "a.", "Source": "T — https://u.test"}


def test_import_cards_unreachable_returns_none(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(ankiconnect.httpx, "post", boom)
    assert ankiconnect.import_cards(CARDS, "s", "Default") is None


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_import_cards_counts_nulls_as_failures(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["action"])
        if json["action"] == "version":
            return FakeResponse({"result": 6, "error": None})
        assert json["action"] == "addNotes"
        assert len(json["params"]["notes"]) == 2
        return FakeResponse({"result": [123, None], "error": None})

    monkeypatch.setattr(ankiconnect.httpx, "post", fake_post)
    assert ankiconnect.import_cards(CARDS, "s", "Default") == {"imported": 1, "failed": 1}
    assert calls == ["version", "addNotes"]


def test_import_cards_add_failure_reports_all_failed(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        if json["action"] == "version":
            return FakeResponse({"result": 6, "error": None})
        return FakeResponse({"result": None, "error": "collection is not available"})

    monkeypatch.setattr(ankiconnect.httpx, "post", fake_post)
    assert ankiconnect.import_cards(CARDS, "s", "Default") == {"imported": 0, "failed": 2}
