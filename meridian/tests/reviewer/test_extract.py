"""Tests for getting the words off a document.

Two properties. A file that is already text is never sent to a model — paying to
retype a file you can open is waste dressed as sophistication. And a file that
cannot become text is refused by name, because "it did not work" is not something
somebody can act on and "export it as PDF first" is.

The model path is exercised against a fake transport, so what is checked is the
shape of what gets sent: a PDF as a file the model pages through, an image as an
image. Whether the transcription is any good is the model's problem and not
testable here.
"""

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from meridian.reviewer import extract
from meridian.reviewer.extract import Transcribed


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


def model(text: str = "1. Check the paperwork."):
    async def transport(**_: Any) -> Any:
        return Turn(output=[], output_parsed=Transcribed(text=text))

    return transport


def refuse(**_: Any) -> Any:
    raise AssertionError("a file that is already text must not reach a model")


async def test_a_text_file_is_read_rather_than_transcribed(tmp_path: Path):
    written = tmp_path / "procedure.md"
    written.write_text("1. Check the paperwork.\n2. Report anything wrong.\n")

    assert "Report anything wrong" in await extract.text_of(written, transport=refuse)


@pytest.mark.parametrize("suffix", [".md", ".txt", ".markdown", ".rst"])
async def test_every_plain_kind_is_read_off_disk(tmp_path: Path, suffix: str):
    written = tmp_path / f"procedure{suffix}"
    written.write_text("a rule")
    assert await extract.text_of(written, transport=refuse) == "a rule"


async def test_a_pdf_is_sent_as_a_file_the_model_can_page_through(tmp_path: Path):
    written = tmp_path / "scan.pdf"
    written.write_bytes(b"%PDF-1.4 not really a pdf")
    seen: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return Turn(output=[], output_parsed=Transcribed(text="transcribed"))

    assert await extract.text_of(written, transport=capture) == "transcribed"

    parts = seen["input"][0]["content"]
    file_part = next(p for p in parts if p["type"] == "input_file")
    assert file_part["filename"] == "scan.pdf"
    assert file_part["file_data"].startswith("data:application/pdf;base64,")
    assert base64.b64decode(file_part["file_data"].split(",", 1)[1]) == b"%PDF-1.4 not really a pdf"


@pytest.mark.parametrize(("suffix", "media"), [(".png", "image/png"), (".jpg", "image/jpeg")])
async def test_a_photograph_of_a_page_is_sent_as_an_image(tmp_path: Path, suffix: str, media: str):
    # The case an OCR library handles worst and a vision model handles fine: a
    # phone picture of a sheet taped to a wall.
    written = tmp_path / f"sop{suffix}"
    written.write_bytes(b"\x89PNG not really")
    seen: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return Turn(output=[], output_parsed=Transcribed(text="read off the wall"))

    assert await extract.text_of(written, transport=capture) == "read off the wall"

    parts = seen["input"][0]["content"]
    image = next(p for p in parts if p["type"] == "input_image")
    assert image["image_url"].startswith(f"data:{media};base64,")


async def test_a_file_this_cannot_read_is_refused_by_name(tmp_path: Path):
    written = tmp_path / "procedure.docx"
    written.write_bytes(b"PK\x03\x04")

    with pytest.raises(extract.UnreadableError) as refused:
        await extract.text_of(written, transport=refuse)

    assert "procedure.docx" in str(refused.value)
    assert ".docx" in str(refused.value)


async def test_the_extraction_asks_for_a_transcription_not_a_summary(tmp_path: Path):
    # The one instruction that decides whether this is useful: a summarised
    # procedure loses exactly the small conditional clauses that are the reason
    # to read it at all.
    assert "do not summarise" in extract.SYSTEM.lower()
    assert "[unreadable]" in extract.SYSTEM


def test_what_can_and_cannot_be_read_is_answerable_without_opening_a_file():
    assert extract.readable(".PDF")
    assert extract.readable(".md")
    assert not extract.readable(".docx")
    assert not extract.readable("")
