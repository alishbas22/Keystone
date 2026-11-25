"""Provenance of every stored episode (KAN-23), following the ontology's PROV-O fields.

Graphiti rewrites the properties of its own nodes and edges when it updates them and does not
persist custom episode metadata on Neo4j. Keystone therefore keeps provenance in its own node,
which Graphiti never touches:

    (:Episodic)-[:HAS_PROVENANCE]->(:KeystoneSource {source, author, observed_at, ...})

Every fact (edge) lists the episodes it came from, so its sources, dates and business functions
are always derivable from these records ("every fact has a source").
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

SOURCE_LABEL = "KeystoneSource"
PROVENANCE_REL = "HAS_PROVENANCE"

# Words in a file name or title that reveal the kind of document, checked in order.
_NAME_HINTS = [
    ("contract", "contract"),
    ("agreement", "contract"),
    ("policy", "policy"),
    ("report", "report"),
    ("memo", "memo"),
    ("minutes", "memo"),
    ("chat", "chat"),
    ("slack", "chat"),
    ("teams", "chat"),
]
_SUFFIX_TYPES = {"xlsx": "spreadsheet", "csv": "spreadsheet", "pptx": "presentation"}


class Provenance(BaseModel):
    """Where one stored section came from. Maps to the `provenance` fields in core.yaml."""

    doc_id: str = Field(description="Content hash of the source document.")
    document_id: str = Field(description="The connector's id for the document (path, row key).")
    source: str = Field(
        description="Connector instance, e.g. 'folder:shared' (prov:wasDerivedFrom)."
    )
    title: str
    file: str | None = None
    section: str = ""
    pages: str = ""
    author: str | None = Field(default=None, description="Who wrote it (prov:wasAttributedTo).")
    observed_at: datetime = Field(description="When it was written (prov:generatedAtTime).")
    business_function: str
    confidence: float = Field(default=0.8, ge=0, le=1)
    url: str | None = None
    kind: str = Field(default="document", description="document | database | agent")

    @property
    def source_type(self) -> str:
        """Kind of source, for the authority rule in rules.yaml (contract, email, chat...)."""
        if self.kind in ("database", "agent"):
            return self.kind
        name = f"{self.file or ''} {self.title}".lower()
        suffix = (
            (self.file or "").lower().rsplit(".", 1)[-1] if self.file and "." in self.file else ""
        )
        if suffix in ("eml", "msg"):
            return "email"
        for word, kind in _NAME_HINTS:
            if word in name:
                return kind
        return _SUFFIX_TYPES.get(suffix, "document")

    @property
    def location(self) -> str:
        """Human-readable 'file › section, page' for answers and the UI."""
        name = self.file or self.title
        where = " › ".join(p for p in (name, self.section) if p)
        return f"{where} ({self.pages})" if self.pages else where

    def to_properties(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_properties(cls, props: dict[str, Any]) -> "Provenance":
        data = {k: v for k, v in props.items() if k in cls.model_fields}
        observed = data.get("observed_at")
        if observed is not None and hasattr(observed, "to_native"):  # Neo4j DateTime
            data["observed_at"] = observed.to_native()
        return cls.model_validate(data)
