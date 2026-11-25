"""List installed connectors:  uv run python -m keystone.connectors"""

from keystone.connectors.registry import available_connectors, connector_problems


def main() -> None:
    connectors = available_connectors()
    print(f"{len(connectors)} connector(s) installed:")
    for name, cls in connectors.items():
        manifest = cls.manifest
        print(f"  {name:<12} v{manifest.version:<8} {manifest.description}")
    for name, problem in connector_problems().items():
        print(f"  {name:<12} BROKEN: {problem}")


if __name__ == "__main__":
    main()
