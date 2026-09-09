# Contributing

Thank you for contributing to OntoAgent.

## Before You Start

Use the repository issue tracker to discuss bugs, proposed changes, or larger design work before investing significant implementation effort. Do not use public issues for security vulnerabilities; see [SECURITY.md](SECURITY.md).

## Development

The project requires Python 3.13 or later and uses `uv` for dependency management.

```bash
uv sync
uv run pytest tests/unit/ -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

Add or update focused tests for behavior changes. Keep changes narrowly scoped, preserve the project's existing layering and style, and avoid committing credentials, local configuration, or generated artifacts.

## Pull Requests

Describe the problem, the change, and the validation performed. Keep each pull request reviewable and ensure the relevant checks pass before requesting review. Maintainers may request changes to keep the contribution consistent with the project.

## License

By submitting a contribution, you agree that it is provided under the terms of the Mozilla Public License 2.0. See [LICENSE](LICENSE).
