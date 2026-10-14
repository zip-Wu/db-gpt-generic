import sys
from unittest.mock import Mock

import pytest

from dbgpt.util.code.docker_execution import _execute, _host_path


def test_reject_outside_data(monkeypatch):
    monkeypatch.setenv("DBGPT_SANDBOX_HOST_PILOT", "/srv/dbgpt/pilot")
    assert _host_path("/app/pilot/tmp/one") == "/srv/dbgpt/pilot/tmp/one"
    with pytest.raises(ValueError):
        _host_path("/app/pilot/../../etc/passwd")


@pytest.mark.parametrize("timeout", [False, True])
def test_isolation_and_cleanup(monkeypatch, timeout):
    docker = Mock()
    client = docker.from_env.return_value
    container = client.containers.create.return_value
    container.logs.return_value = b"ok"
    if timeout:
        container.wait.side_effect = TimeoutError()
    else:
        container.wait.return_value = {"StatusCode": 0}
    monkeypatch.setitem(sys.modules, "docker", docker)
    monkeypatch.setenv("DBGPT_SANDBOX_HOST_PILOT", "/srv/dbgpt/pilot")
    result = _execute(["python", "test.py"], "/app/pilot/tmp/one", {
        "SITE_APP_SECRET": "must-not-reach-sandbox", "PLOT_DIR": "/app/pilot/tmp/one",
    }, 1)
    config = client.containers.create.call_args.kwargs
    assert config["network_disabled"] and config["read_only"]
    assert "SITE_APP_SECRET" not in config["environment"]
    assert list(config["volumes"]) == ["/srv/dbgpt/pilot/tmp/one"]
    assert config["cap_drop"] == ["ALL"]
    assert result[0] is None if timeout else result[0] == 0
    container.remove.assert_called_once_with(force=True)
    client.close.assert_called_once()


def test_missing_image_never_falls_back(monkeypatch):
    docker = Mock()
    client = docker.from_env.return_value
    client.images.get.side_effect = RuntimeError("missing image")
    monkeypatch.setitem(sys.modules, "docker", docker)
    monkeypatch.setenv("DBGPT_SANDBOX_HOST_PILOT", "/srv/dbgpt/pilot")
    with pytest.raises(RuntimeError, match="missing image"):
        _execute(["python"], "/app/pilot/tmp/one", {}, 1)
    client.containers.create.assert_not_called()
    client.images.pull.assert_not_called()
    client.close.assert_called_once()
