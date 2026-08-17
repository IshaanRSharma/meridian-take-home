"""Written procedures attached to a board.

Claude.md §18 names three CRUD surfaces — board, threads, documents — and this is
the third. It stayed a CLI command longer than the other two because nothing on
the canvas needed it, and the moment a canvas can accept a dropped file it does.

A reference document *describes* the process rather than flowing through it, which
is the whole reason it is not a card and not in the frozen spec. What it buys the
reviewer is the one question class that is **checkable** rather than speculative:
every other question notices a silence and guesses that the silence matters,
while "your procedure says a description of the problem goes in the report and
this step sends two identifiers" is a difference between two things that both
exist.

Upload is synchronous and can take seconds, because a scan goes to a model to be
read. That is the honest shape: the caller wants to know the text came out before
it decides the upload worked, and a background job returning an id would move the
failure somewhere nobody is looking. Extraction is off the event loop, so a slow
one does not block the server.

Nothing here distils. Alignment onto anchors happens in the next review round,
against the board as it stands then — which is right, because which anchors
resolve changes every time somebody edits the canvas.
"""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from meridian.api.dependencies import Connection
from meridian.domain.review import DocKind, ReferenceDoc
from meridian.repositories import boards, reference_docs
from meridian.reviewer import extract

router = APIRouter(prefix="/boards/{board_id}/documents", tags=["document"])

# Hoisted so it is not a call in a default argument. FastAPI reads it either way.
UPLOAD = File(description="a written procedure describing this process")

# A written procedure is a few pages. This is not a storage limit — it is the
# point past which somebody has attached the wrong thing, and saying so beats
# sending a 40MB scan to a model and charging for it.
MAX_BYTES = 10 * 1024 * 1024


@router.get("", response_model=list[ReferenceDoc])
async def list_documents(board_id: UUID, connection: Connection) -> list[ReferenceDoc]:
    """Every document attached to this board, with the text that was read from it.

    The text comes back rather than only the filename, because the interface has
    to be able to show what the reviewer is actually working from. A scan that
    transcribed badly is invisible otherwise, and it would look like the reviewer
    ignoring a procedure it had simply never been able to read.
    """
    return list(await reference_docs.for_board(connection, board_id))


@router.post("", response_model=ReferenceDoc, status_code=status.HTTP_201_CREATED)
async def attach_document(
    board_id: UUID,
    connection: Connection,
    file: UploadFile = UPLOAD,
    kind: DocKind = "sop",
) -> ReferenceDoc:
    """Attach a procedure, reading it with whatever it takes.

    Markdown and text are decoded directly. A PDF or a photograph of a printed
    sheet goes to the model, which reads the page — no OCR library, because the
    model that reads the procedure can read a scan and that keeps one seam to
    OpenAI rather than a second parsing stack.

    Refuses a file it cannot read by naming the extension and what would fix it.
    "Upload failed" sends somebody to read logs; "export it as PDF or markdown
    first" is something they can act on.
    """
    # 404 before doing any work, so an upload against a board that is not there
    # does not spend a model call finding out.
    await boards.get(connection, board_id)

    name = file.filename or "attachment"
    suffix = name[name.rfind(".") :] if "." in name else ""
    if not extract.readable(suffix):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(extract.UnreadableError(Path(name))),
        )

    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{name} is {len(raw) // 1024 // 1024}MB; a written procedure is a few pages",
        )

    text = await extract.text_of_bytes(raw, name)
    doc = ReferenceDoc(kind=kind, filename=name, text=text)
    doc_id = await reference_docs.save(connection, board_id, doc, storage_path=name)
    return doc.model_copy(update={"id": doc_id})


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_document(board_id: UUID, document_id: UUID, connection: Connection) -> None:
    """Detach a document, so the next round stops reading it.

    Deleting rather than deactivating: a document is evidence the reviewer reads
    fresh every round, never something a settled statement points back at, so
    there is no provenance to preserve. A statement the document *provoked* lives
    on its own conversation and is untouched by this.
    """
    if not await reference_docs.delete(connection, board_id, document_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no document {document_id}"
        )
