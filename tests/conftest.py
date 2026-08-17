import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="run LLM eval fixtures (costs money)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-evals"):
        return
    skip = pytest.mark.skip(reason="needs --run-evals")
    for item in items:
        if "evals" in item.keywords:
            item.add_marker(skip)


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory, monkeypatch):
    """Never let tests read or write the developer's real config file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
