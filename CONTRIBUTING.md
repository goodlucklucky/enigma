# Contributing to Enigma (SN 63)

Thank you for contributing to Enigma! This guide covers development setup, testing, linting, and the contribution process.

## Development Setup

### 1. Clone and enter the repository

```bash
git clone https://github.com/qbittensor-labs/enigma.git
cd enigma
```

### 2. Create a Python virtual environment (3.12+ required)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows use `.venv\Scripts\activate`.

### 3. Install the package in editable (`-e`) mode

```bash
pip install -e .
```

**This step is critical.** The editable install:

- Makes the `qbittensor` package importable from your source checkout.
- Registers console scripts (e.g. `check-validation`).
- Ensures local code changes take effect immediately without reinstalls.
- Is required for correct resolution of local data directories and database paths when running neurons or CLIs.

See `qbittensor/database/db_connection.py` for notes on how editable installs + multiple checkouts affect `data/` directory selection.

### 4. Install development dependencies

```bash
pip install -r requirements-dev.txt
```

This pulls in `pytest` (for tests) and `flake8` (for linting), plus any future dev tools.

## Running Tests

```bash
# Run the default test suite (unit tests only; integration tests that require Docker are skipped)
pytest .

# Include integration tests (requires a working Docker daemon)
pytest -m integration
```

Configuration lives in [pytest.ini](pytest.ini):

- Tests are discovered under `tests/`
- `qbittensor/miner/` is excluded from recursion
- Default markers and warning filters are applied

## Linting

The project uses [flake8](https://flake8.pycqa.org/) with custom settings defined in [.flake8](.flake8) (120 char line length, selected ignores for common formatting, exclusion of build/venv artifacts).

```bash
flake8 .
```

Fix all violations before submitting a PR. The CI pipeline (see below) will fail if `flake8` reports any issues.

## Continuous Integration

All pushes and pull requests to `main` run the workflow defined in [.github/workflows/ci.yml](.github/workflows/ci.yml):

1. Checkout
2. Setup Python 3.12
3. `pip install -e .`
4. `pip install -r requirements-dev.txt`
5. `flake8 .`
6. `pytest .`

A passing CI run (lint + tests) is required for merge.

## Pull Request Guidelines

- Branch from `main` (or the current development branch).
- Keep changes focused and reasonably sized.
- Run `flake8 .` and `pytest .` locally and confirm they pass.
- Update documentation (README, docstrings, etc.) when behavior changes.
- Reference related issues in the PR description.

## Additional Development Notes

- **Environment variables**: API configuration (e.g. `CHALLENGES_API_URL`, `TELEMETRY_API_URL`) is loaded via `python-dotenv`. Create a local `.env` file as needed. Never commit secrets or `.env*` files (they are gitignored).
- **Database paths**: The supported launch commands (`python neurons/validator.py`, `python neurons/miner.py`, `python cli/mine_enigma.py`, and `python cli/check_validation.py`) reliably place SQLite databases in `<checkout>/data/` via `sys.argv[0]` (and cwd fallback) heuristics inside `_resolve_db_dir()`. This works even when the current working directory is outside the repository or when multiple editable installs exist. Setting `ENIGMA_DATA_DIR` or `ENIGMA_REPO_ROOT` provides explicit overrides if needed. An editable install (`pip install -e .`) of the checkout you are actively developing in is the most reliable setup.
- **Entry points**: After `pip install -e .` you can run `check-validation` directly from the command line.
- **Docker**: Several integration tests and the full validator solution pipeline require Docker for building/running untrusted solution containers.

## Questions?

Open an issue or reach out on the [Discord](https://discord.gg/xJ9JKPMJQD).
