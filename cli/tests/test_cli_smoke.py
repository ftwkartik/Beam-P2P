"""Packaging smoke test: the `beam` command is installed and runnable."""

from typer.testing import CliRunner

from beam_cli import __version__
from beam_cli.main import app

runner = CliRunner()


def test_version_command_prints_the_package_version() -> None:
    # Milestone 10 added send/receive alongside version, so Typer no longer
    # collapses the app into a single implicit command -- `beam version` now needs
    # its subcommand name, unlike the Milestone 1 placeholder this test dates from.
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_send_and_receive() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "send" in result.stdout
    assert "receive" in result.stdout
