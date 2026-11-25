"""Contract tests for connector authors (KAN-22).

Call `check_connector` in your connector's tests. It verifies the rules every Keystone connector
must follow and raises AssertionError with a clear message when one is broken:

    from keystone.connectors.testing import check_connector

    def test_my_connector():
        check_connector(MyConnector({"api_key": "test"}))
"""

from typing import Any

from keystone.connectors.base import BaseConnector, CheckResult, Document, Stream

BUSINESS_FUNCTIONS = {"Sales", "Finance", "HR", "Operations"}


def check_connector(
    connector: BaseConnector[Any], min_documents: int = 1, max_documents: int = 1000
) -> list[Document]:
    """Run check(), discover() and read() and verify the connector contract.

    Returns the documents read, for further assertions in your own tests.
    """
    cls = type(connector)
    name = cls.__name__

    manifest = getattr(cls, "manifest", None)
    assert manifest is not None, (
        f"{name}: set a class attribute `manifest = ConnectorManifest(...)`"
    )
    assert getattr(cls, "config_model", None) is not None, f"{name}: set `config_model`"
    schema = cls.config_schema()
    assert schema.get("type") == "object", f"{name}: config_model must be a Pydantic model"

    result = connector.check()
    assert isinstance(result, CheckResult), f"{name}.check() must return a CheckResult"
    assert result.ok, f"{name}.check() failed: {result.message}"

    streams = connector.discover()
    assert isinstance(streams, list) and all(isinstance(s, Stream) for s in streams), (
        f"{name}.discover() must return a list of Stream"
    )
    stream_names = [s.name for s in streams]
    assert len(stream_names) == len(set(stream_names)), f"{name}: stream names must be unique"

    documents: list[Document] = []
    for document in connector.read():
        assert isinstance(document, Document), f"{name}.read() must yield Document objects"
        documents.append(document)
        if len(documents) > max_documents:
            break
    assert len(documents) >= min_documents, (
        f"{name}.read() returned {len(documents)} documents, expected at least {min_documents}"
    )

    ids = [d.id for d in documents]
    assert len(ids) == len(set(ids)), f"{name}: document ids must be unique and stable"
    for document in documents:
        assert document.source == connector.source_name, (
            f"{name}: Document.source must be connector.source_name "
            f"('{connector.source_name}'), got '{document.source}'"
        )
        if document.business_function is not None:
            assert document.business_function in BUSINESS_FUNCTIONS, (
                f"{name}: business_function must be one of {sorted(BUSINESS_FUNCTIONS)}"
            )
        if document.modified_at is not None:
            assert document.modified_at.tzinfo is not None, (
                f"{name}: modified_at must be timezone-aware (use datetime.now(UTC))"
            )

    if stream_names:
        first = stream_names[0]
        list(connector.read(stream=first))  # reading one stream must work
    return documents
