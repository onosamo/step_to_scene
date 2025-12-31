check:
    ruff format --exit-non-zero-on-format
    ruff check --fix --exit-non-zero-on-fix

# Run tests (isolates from ROS2 environment)
test *args:
    #!/usr/bin/env bash
    unset ROS_PACKAGE_PATH
    unset PYTHONPATH
    uv run python -m pytest tests/ {{args}}

# Run tests with verbose output
test-v:
    #!/usr/bin/env bash
    unset ROS_PACKAGE_PATH
    unset PYTHONPATH
    uv run python -m pytest tests/ -v
