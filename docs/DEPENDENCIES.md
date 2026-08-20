# Dependency boundary

The repository keeps production and development dependencies separate.

- `requirements.txt` contains runtime dependencies only and is suitable for application/runtime installs.
- `requirements-dev.txt` includes `requirements.txt` plus test and local browser-development tools.
- `pyproject.toml` mirrors the same boundary through `[project.dependencies]` and `[project.optional-dependencies].dev`.

## Runtime install

```bash
pip install -r requirements.txt
```

## Development and validation install

```bash
pip install -r requirements-dev.txt
```

The CI test job installs the development set; product browser jobs install only the runtime set plus the browser driver they execute.
