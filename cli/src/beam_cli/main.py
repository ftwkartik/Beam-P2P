"""Entry point for the `beam` command-line peer.

This is a placeholder for Milestone 10. It exists in Milestone 1 only so the workspace
resolves and `beam --version` is a usable smoke test of the packaging setup.
"""

import typer

from beam_cli import __version__

app = typer.Typer(help="Beam: send files directly between devices, browser or terminal.")


@app.command()
def version() -> None:
    """Print the CLI version."""
    typer.echo(f"beam {__version__}")


if __name__ == "__main__":
    app()
