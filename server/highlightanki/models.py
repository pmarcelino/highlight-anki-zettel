"""The shared input record: one highlight, its excerpt, the reader's comment."""
from __future__ import annotations

from pydantic import BaseModel


class Highlight(BaseModel):
    """One highlight captured in the page.

    `context` is the page excerpt surrounding it — untrusted web content.
    `comment` is the reader's own note, written while highlighting — trusted,
    and it steers generation without ever being echoed into the output.
    """

    text: str
    context: str = ""
    comment: str = ""

    def stripped(self) -> "Highlight":
        return Highlight(text=self.text.strip(), context=self.context.strip(),
                         comment=self.comment.strip())
