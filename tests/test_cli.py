"""Command line: error output and exit codes."""
import pytest


def test_errors_are_one_line_on_stderr(monkeypatch, capsys):
    from paramiko.ssh_exception import NoValidConnectionsError

    from frameport import cli

    def boom():
        raise NoValidConnectionsError({("10.0.0.9", 22): OSError("refused")})

    monkeypatch.delenv("FRAMEPORT_DEBUG", raising=False)
    monkeypatch.setattr(cli, "app", boom)
    with pytest.raises(SystemExit) as e:
        cli.main()
    err = capsys.readouterr().err
    assert e.value.code == 1 and err.startswith("Error: Can't reach your Frame") and "Traceback" not in err
    monkeypatch.setenv("FRAMEPORT_DEBUG", "1")
    with pytest.raises(NoValidConnectionsError):
        cli.main()
