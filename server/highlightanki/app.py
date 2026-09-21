"""FastAPI companion server: highlights in → Anki file / Zettelkasten notes out.

Generation is a background job (see jobs.py): POST validates and returns
202 {"job": id}; the extension polls GET /jobs/{id} for the outcome.

The server only ever binds 127.0.0.1, and additionally refuses browser
requests whose Origin is not this extension's: neither a web page you happen
to visit nor another installed extension can spend your API credits by
posting to localhost.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import ankiconnect
from . import cards as cards_mod
from . import roamsync
from . import zettel as zettel_mod
from . import export
from .config import Settings
from .jobs import JobError, Jobs
from .llm import LLMError
from .models import Highlight
from .prompts import Prompts


class HighlightsIn(BaseModel):
    title: str
    url: str
    highlights: list[Highlight]
    page_text: str = ""

    def cleaned(self) -> list[Highlight]:
        """Strip every field, dropping highlights with no text left."""
        return [h for h in (i.stripped() for i in self.highlights) if h.text]


def origin_allowed(origin: str | None, extension_id: str) -> bool:
    """No Origin (curl, tests) or exactly this extension; never a page or another extension."""
    return not origin or origin == f"chrome-extension://{extension_id}"


def create_app(llm, settings: Settings, prompts: Prompts) -> FastAPI:
    app = FastAPI(title="highlight-anki-zettel")
    jobs = app.state.jobs = Jobs()

    @app.middleware("http")
    async def reject_web_origins(request: Request, call_next):
        if not origin_allowed(request.headers.get("origin"), settings.extension_id):
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
        return await call_next(request)

    def require_highlights(inp: HighlightsIn) -> list[Highlight]:
        highlights = inp.cleaned()
        if not highlights:
            raise HTTPException(400, "no highlights")
        return highlights

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job (server restarted?)")
        return job

    @app.post("/cards", status_code=202)
    def make(inp: HighlightsIn):
        highlights = require_highlights(inp)

        def run() -> dict:
            vocabulary = export.read_tags(settings.tags_path)
            try:
                cards = cards_mod.make_cards(
                    llm, prompts, inp.title, inp.url, inp.page_text, highlights, vocabulary)
            except (LLMError, ValueError) as exc:
                raise JobError(f"card generation failed: {exc}") from exc
            path = export.write_pending(
                cards, inp.title, inp.url, settings.pending_dir, settings.deck)
            export.merge_tags(settings.tags_path, [t for c in cards for t in c["tags"]])
            anki = ankiconnect.import_cards(
                cards, export.source_of(inp.title, inp.url), settings.deck)
            return {"count": len(cards), "path": str(path), "anki": anki}

        return {"job": jobs.submit(run)}

    @app.post("/zettel", status_code=202)
    def make_zettel(inp: HighlightsIn):
        highlights = require_highlights(inp)

        def run() -> dict:
            vocabulary = zettel_mod.scan_tags(settings.notes_dir)
            try:
                notes = zettel_mod.make_notes(
                    llm, prompts, inp.title, inp.url, inp.page_text, highlights, vocabulary)
            except (LLMError, ValueError) as exc:
                raise JobError(f"note generation failed: {exc}") from exc
            paths = zettel_mod.write_notes(
                notes, inp.title, inp.url, settings.notes_dir, settings.notes_format)
            if settings.notes_format == "org":
                roamsync.sync(settings.notes_dir)
            return {"count": len(notes), "dir": str(settings.notes_dir),
                    "paths": [str(p) for p in paths]}

        return {"job": jobs.submit(run)}

    return app
