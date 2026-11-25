# Standard vocabularies

Unmodified copies of the standard ontologies that `core.yaml` maps onto. They are read with
rdflib by `keystone.ontology.standards` to verify that every `maps_to` term really exists.

| File | Source | Licence |
|---|---|---|
| `schemaorg.ttl` | https://schema.org/version/latest/schemaorg-current-https.ttl | [CC BY-SA 3.0](https://schema.org/docs/terms.html) |
| `org.ttl` | https://www.w3.org/ns/org.ttl (W3C Organization Ontology) | [W3C Software and Document License](https://www.w3.org/copyright/software-license/) |
| `prov-o.ttl` | https://www.w3.org/ns/prov-o.ttl (W3C PROV-O) | [W3C Software and Document License](https://www.w3.org/copyright/software-license/) |

Downloaded 2026-11-25. To update, download the files again and run `uv run pytest`.
