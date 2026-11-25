# Industry packs

A pack extends the core ontology for one industry. It can **add** entity types, relations,
interfaces and rules. It can never redefine anything from the core; the loader rejects that
("extend, don't modify").

| Pack | Adds |
|---|---|
| `retail` | Store, ProductLine, Shipment, CustomerSegment; supplier, stock and shipment relations |

## Turning packs on

Packs listed in the `ONTOLOGY_PACKS` setting are loaded on top of the core (default: retail):

```
ONTOLOGY_PACKS=["retail"]
```

## Writing a pack

1. Create `ontology/packs/<name>.yaml` starting with a header:
   ```yaml
   pack:
     name: banking
     version: 0.1.0
     description: Accounts, loans and guarantors.
   ```
2. Add `entity_types`, `relation_types` and `rules` in the same format as `core.yaml`.
   Map each type to a standard term (`maps_to: schema:BankAccount`) where one exists.
3. Join core relations through interfaces instead of editing them. For example,
   `implements: [Party]` lets a new type be a customer, and `implements: [Locatable]` lets it
   be `located_in` a place.
4. Run `uv run pytest`. The tests check names, references, Graphiti compatibility and that
   every `maps_to` exists in the standard vocabularies.
