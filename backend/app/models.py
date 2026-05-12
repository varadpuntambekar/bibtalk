from typing import Literal

from pydantic import BaseModel, Field


class LibraryOut(BaseModel):
    id: str
    name: str
    paper_count: int = 0


class PaperOut(BaseModel):
    id: str
    library_id: str
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    journal: str | None = None
    source_file: str | None = None
    shortlisted: bool = False


class UploadResponse(BaseModel):
    imported: int
    skipped_duplicates: int
    paper_ids: list[str]
    library_id: str
    library_ids: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatStreamRequest(BaseModel):
    messages: list[ChatMessage]
    library_id: str
    selected_paper_ids: list[str] | None = None
    retrieval_mode: Literal["auto", "always", "never"] = "auto"


class ShortlistUpdate(BaseModel):
    paper_ids: list[str]
    shortlisted: bool
