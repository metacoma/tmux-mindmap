from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_HTML_MARKER_RE = re.compile(r"(?is)<\s*/?\s*(?:html|body|p|div|br|li|ul|ol|pre|span|font)\b")
_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "pre", "body", "html"}
_CONTINUATION_SUFFIX_RE = re.compile(r"(?:\|{1,2}|&&|\\|\(|\{|\b(?:do|then|else|elif)\b)\s*$")


class _DetailsTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def _newline(self) -> None:
        if not self.parts or not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self._newline()
        elif tag.lower() in _BLOCK_TAGS and self.parts:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def sanitize_details_text(value: str | None) -> str:
    """Convert Freeplane detailsText HTML into shell-safe plain text."""

    text = normalize_newlines(value or "")
    if not _HTML_MARKER_RE.search(text):
        return text

    parser = _DetailsTextParser()
    parser.feed(text)
    parser.close()
    cleaned = html.unescape(parser.text()).replace("\xa0", " ")
    lines = [line.rstrip() for line in normalize_newlines(cleaned).split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


def split_shell_commands(value: str | None) -> list[str]:
    text = sanitize_details_text(value)
    return [line.strip() for line in text.split("\n") if line.strip()]


def join_shell_commands(lines: list[str]) -> str:
    cleaned = [line.strip() for line in lines if line.strip()]
    if not cleaned:
        return ""

    result = cleaned[0]
    for line in cleaned[1:]:
        if _CONTINUATION_SUFFIX_RE.search(result):
            result += " " + line
        else:
            result += "; " + line
    return result
