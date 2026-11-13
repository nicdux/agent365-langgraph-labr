from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

MAX_KNOWLEDGE_BYTES = 128 * 1024
Text = Annotated[str, Field(min_length=1, max_length=1800)]


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class Profile(KnowledgeModel):
    name: str = Field(min_length=1, max_length=100)
    initials: str = Field(pattern=r"^[A-Z]{2,3}$")
    reference_url: HttpUrl

    @field_validator("reference_url")
    @classmethod
    def https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("A referencia deve usar HTTPS.")
        return value


class KnowledgeEntry(KnowledgeModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=120)
    content: Text
    source_label: str = Field(min_length=1, max_length=160)
    source_url: HttpUrl | None = None
    approved: StrictBool = False

    @field_validator("source_url")
    @classmethod
    def https_only(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("A fonte deve usar HTTPS.")
        return value


class KnowledgeBase(KnowledgeModel):
    schema_version: Literal[1]
    profile: Profile
    entries: list[KnowledgeEntry] = Field(max_length=50)

    @model_validator(mode="after")
    def unique_ids(self) -> "KnowledgeBase":
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Cada trecho deve ter um id unico.")
        return self

    @property
    def approved_entries(self) -> list[KnowledgeEntry]:
        return [entry for entry in self.entries if entry.approved]


def load_knowledge(path: Path) -> KnowledgeBase:
    with path.open("rb") as source:
        content = source.read(MAX_KNOWLEDGE_BYTES + 1)
    if len(content) > MAX_KNOWLEDGE_BYTES:
        raise ValueError("A base profissional excede 128 KiB.")
    try:
        return KnowledgeBase.model_validate_json(content)
    except ValidationError:
        # Erros de validacao nao devem copiar conteudo profissional para logs.
        raise ValueError("Base profissional invalida; confira o formato documentado.") from None
