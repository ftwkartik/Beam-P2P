"""Packaging smoke test: the `beam` command is installed and runnable."""

from typer.testing import CliRunner

from beam_cli import __version__
from beam_cli.main import app

runner = CliRunner()


def test_version_command_prints_the_package_version() -> None:
    # Typer collapses a Typer app with exactly one registered command into a plain
    # command (no subcommand name needed), so it's invoked as `beam` rather than
    # `beam version` until `send`/`receive` are added in Milestone 10.
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert __version__ in result.stdout
