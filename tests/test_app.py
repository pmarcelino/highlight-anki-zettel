import threading

import pytest
from fastapi.testclient import TestClient

from highlightanki import app as app_mod
from highlightanki.app import create_app
from highlightanki.llm import LLMError

from .conftest import FakeLLM, make_settings

CARDS = [
    {"notetype": "Cloze", "front": "{{c1::A}} b.", "back": "", "tags": ["alpha"]},
    {"notetype": "Basic", "front": "why?", "back": "because", "tags": ["beta", "alpha"]},
]

PAYLOAD = {"title": "Page T", "url": "https://x.test/p",
           "highlights": [{"text": "one"}, {"text": "two"}],
           "page_text": "context"}

NOTES = [
    {"title": "a claims b", "body": "A leads to B.", "tags": ["alpha"], "related": [2]},
    {"title": "c claims d", "body": "C leads to D.", "tags": ["beta"], "related": []},
]


def make_client(tmp_path, llm, prompts, **settings):
    return TestClient(create_app(llm, make_settings(tmp_path, **settings), prompts))


def finish(client, res):
    """POST accepted → wait for the job → its settled status."""
    assert res.status_code == 202, res.text
    job = res.json()["job"]
    client.app.state.jobs.wait(job, timeout=5)
    status = client.get(f"/jobs/{job}")
    assert status.status_code == 200
    body = status.json()
    assert body["state"] != "running"
    return body


@pytest.fixture(autouse=True)
def no_live_ankiconnect(monkeypatch):
    """Never touch a real AnkiConnect from the suite."""
    calls = []

    def fake_import(cards, source, deck):
        calls.append((cards, source, deck))
        return None

    monkeypatch.setattr(app_mod.ankiconnect, "import_cards", fake_import)
    return calls


@pytest.fixture(autouse=True)
def no_live_roam_sync(monkeypatch):
    """Never launch a real Emacs from the suite."""
    calls = []
    monkeypatch.setattr(app_mod.roamsync, "sync", calls.append)
    return calls


def test_health(tmp_path, prompts):
    assert make_client(tmp_path, FakeLLM(), prompts).get("/health").json() == {"ok": True}


def test_foreign_origins_are_forbidden(tmp_path, prompts):
    client = make_client(tmp_path, FakeLLM(), prompts)
    res = client.post("/cards", json=PAYLOAD, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    assert client.get("/health", headers={"Origin": "https://evil.example"}).status_code == 403
    other = {"Origin": "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"}
    assert client.post("/cards", json=PAYLOAD, headers=other).status_code == 403


def test_own_extension_origin_is_allowed(tmp_path, prompts):
    client = make_client(tmp_path, FakeLLM(), prompts)
    mine = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
    assert client.get("/health", headers=mine).status_code == 200


def test_cards_endpoint_writes_pending_file_and_merges_tags(tmp_path, prompts, no_live_ankiconnect):
    (tmp_path / "tags.txt").write_text("alpha\n")
    llm = FakeLLM(out={"cards": CARDS})
    client = make_client(tmp_path, llm, prompts, deck="Study")
    job = finish(client, client.post("/cards", json=PAYLOAD))
    assert job["state"] == "done"
    body = job["result"]
    assert body["count"] == 2
    assert body["anki"] is None

    files = list((tmp_path / "pending").glob("*.txt"))
    assert [str(f) for f in files] == [body["path"]]
    text = files[0].read_text()
    assert "Cloze\tStudy\t{{c1::A}} b.\t\tPage T — https://x.test/p\talpha" in text
    assert (tmp_path / "tags.txt").read_text() == "alpha\nbeta\n"
    # the configured deck also reaches AnkiConnect
    assert no_live_ankiconnect[0][2] == "Study"
    # vocabulary, highlights, and the prompt text from prompts.yaml all arrived
    assert "alpha" in llm.prompts[0]
    assert "1. <highlight>one</highlight>" in llm.prompts[0]
    assert "<page_title>Page T</page_title>" in llm.prompts[0]
    assert "Choosing the notetype" in llm.prompts[0]
    assert "$" not in llm.prompts[0]  # every placeholder substituted


def test_cards_endpoint_reports_anki_import(tmp_path, prompts, monkeypatch):
    monkeypatch.setattr(app_mod.ankiconnect, "import_cards",
                        lambda cards, source, deck: {"imported": len(cards), "failed": 0})
    client = make_client(tmp_path, FakeLLM(out={"cards": CARDS}), prompts)
    job = finish(client, client.post("/cards", json=PAYLOAD))
    assert job["result"]["anki"] == {"imported": 2, "failed": 0}


def test_cards_endpoint_rejects_empty_highlights(tmp_path, prompts):
    res = make_client(tmp_path, FakeLLM(), prompts).post(
        "/cards", json={**PAYLOAD, "highlights": [{"text": "  "}, {"text": ""}]})
    assert res.status_code == 400


def test_cards_endpoint_keeps_each_highlights_context_and_comment(tmp_path, prompts):
    llm = FakeLLM(out={"cards": CARDS})
    client = make_client(tmp_path, llm, prompts)
    finish(client, client.post("/cards", json={
        **PAYLOAD,
        "highlights": [
            {"text": "  ", "context": "ctx blank", "comment": "note blank"},
            {"text": "kept one", "context": "ctx one", "comment": " note one "},
            {"text": "kept two", "context": "ctx two"},
        ],
    }))
    prompt = llm.prompts[0]
    assert ("1. <highlight>kept one</highlight>\n<excerpt>\nctx one\n</excerpt>\n"
            "<comment>\nnote one\n</comment>") in prompt
    assert "2. <highlight>kept two</highlight>\n<excerpt>\nctx two\n</excerpt>" in prompt
    # the dropped highlight takes its context and its comment with it
    assert "ctx blank" not in prompt
    assert "note blank" not in prompt


def test_cards_endpoint_reports_llm_error_as_failed_job(tmp_path, prompts):
    client = make_client(tmp_path, FakeLLM(error=LLMError("boom")), prompts)
    job = finish(client, client.post("/cards", json=PAYLOAD))
    assert job["state"] == "failed"
    assert "boom" in job["detail"]
    assert not (tmp_path / "pending").exists()


def test_zettel_endpoint_writes_markdown_files(tmp_path, prompts, no_live_roam_sync):
    llm = FakeLLM(out={"notes": NOTES})
    client = make_client(tmp_path, llm, prompts)
    job = finish(client, client.post("/zettel", json=PAYLOAD))
    assert job["state"] == "done"
    body = job["result"]
    assert body["count"] == 2
    assert body["dir"] == str(tmp_path / "notes")
    files = sorted((tmp_path / "notes").glob("*.md"))
    assert [str(f) for f in files] == sorted(body["paths"])
    text = files[0].read_text()
    assert "\ntitle: a claims b\n" in text
    assert "Source: Page T — https://x.test/p" in text
    # highlights and page context reached the prompt
    assert "1. <highlight>one</highlight>" in llm.prompts[0]
    assert "context" in llm.prompts[0]
    assert "Zettelkasten" in llm.prompts[0]
    # markdown needs no org-roam sync
    assert no_live_roam_sync == []


def test_zettel_endpoint_org_format_writes_org_and_syncs(tmp_path, prompts, no_live_roam_sync):
    client = make_client(tmp_path, FakeLLM(out={"notes": NOTES}), prompts, notes_format="org")
    job = finish(client, client.post("/zettel", json=PAYLOAD))
    assert job["state"] == "done"
    files = sorted((tmp_path / "notes").glob("*.org"))
    assert len(files) == 2
    assert "#+title: a claims b" in files[0].read_text()
    assert no_live_roam_sync == [tmp_path / "notes"]


def test_zettel_endpoint_rejects_empty_highlights(tmp_path, prompts):
    res = make_client(tmp_path, FakeLLM(), prompts).post(
        "/zettel", json={**PAYLOAD, "highlights": [{"text": "  "}, {"text": ""}]})
    assert res.status_code == 400


def test_zettel_endpoint_passes_reader_comments_to_the_prompt(tmp_path, prompts):
    llm = FakeLLM(out={"notes": NOTES})
    client = make_client(tmp_path, llm, prompts)
    finish(client, client.post("/zettel", json={
        **PAYLOAD,
        "highlights": [{"text": "one", "comment": "connect to my note on X"}],
    }))
    assert "<comment>\nconnect to my note on X\n</comment>" in llm.prompts[0]


def test_zettel_endpoint_reports_llm_error_as_failed_job(tmp_path, prompts, no_live_roam_sync):
    client = make_client(tmp_path, FakeLLM(error=LLMError("boom")), prompts, notes_format="org")
    job = finish(client, client.post("/zettel", json=PAYLOAD))
    assert job["state"] == "failed"
    assert "boom" in job["detail"]
    assert not (tmp_path / "notes").exists()
    assert no_live_roam_sync == []


def test_job_is_running_until_generation_finishes(tmp_path, prompts):
    """The poll contract the extension relies on: running → done, never blocking POST."""
    gate = threading.Event()

    class SlowLLM(FakeLLM):
        def json(self, prompt, schema, max_tokens=8192):
            gate.wait(5)
            return super().json(prompt, schema, max_tokens)

    client = make_client(tmp_path, SlowLLM(out={"notes": NOTES}), prompts)
    res = client.post("/zettel", json=PAYLOAD)
    assert res.status_code == 202
    job = res.json()["job"]
    assert client.get(f"/jobs/{job}").json() == {"state": "running"}
    gate.set()
    client.app.state.jobs.wait(job, timeout=5)
    assert client.get(f"/jobs/{job}").json()["state"] == "done"


def test_unknown_job_is_404(tmp_path, prompts):
    assert make_client(tmp_path, FakeLLM(), prompts).get("/jobs/nope").status_code == 404


def test_job_crash_is_reported_not_hung(tmp_path, prompts, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("disk")
    monkeypatch.setattr(app_mod.zettel_mod, "write_notes", explode)
    client = make_client(tmp_path, FakeLLM(out={"notes": NOTES}), prompts)
    job = finish(client, client.post("/zettel", json=PAYLOAD))
    assert job["state"] == "failed"
    assert "disk" in job["detail"]
