# {{ cookiecutter.project_slug }}

Keystone connector: {{ cookiecutter.description }}

## Develop

```bash
uv sync                 # installs Keystone and this connector
uv run pytest           # runs the Keystone connector contract tests
```

The example reads `sample_data/records.json`. Replace `_fetch_records` in
`src/{{ cookiecutter.package_name }}/connector.py` with calls to your system.

## Use

```bash
uv pip install -e .                  # in Keystone's environment
uv run python -m keystone.connectors # "{{ cookiecutter.connector_name }}" should be listed
```
