check:
    uv run ruff format --check
    uv run ruff check

test *args:
    uv run python -m pytest tests/ {{args}}

test-v:
    uv run python -m pytest tests/ -v
