"""Entry point for the `beam` command-line peer: `send` and `receive`, built on
aiortc, interoperable with the browser client via the shared beam_protocol package
(ADR-008, Milestone 10).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from beam_cli import __version__, ui
from beam_cli.manifest import build_manifest
from beam_cli.receiver import FileReadyResult, OfferedManifest, ReceiverProgress
from beam_cli.sender import SenderProgress
from beam_cli.session import run_receive, run_send
from beam_protocol.sas import SasSymbol

app = typer.Typer(help="Beam: send files directly between devices, browser or terminal.")

#: Same-origin default matches how the browser client is served (infra/nginx.conf
#: proxies /api and /ws); override for a differently-hosted server.
DEFAULT_SERVER_URL = os.environ.get("BEAM_SERVER_URL", "http://localhost:8080")


@app.command()
def version() -> None:
    """Print the CLI version."""
    typer.echo(f"beam {__version__}")


@app.command()
def send(
    paths: list[Path] = typer.Argument(..., help="Files and/or directories to send."),
    server: str = typer.Option(DEFAULT_SERVER_URL, "--server", help="Beam server URL."),
) -> None:
    """Send one or more files or folders. Prints a room code for the receiver."""
    asyncio.run(_send(paths, server))


@app.command()
def receive(
    code: str = typer.Argument(..., help="The room code shown by `beam send`."),
    directory: Path = typer.Option(Path.cwd(), "--dir", help="Where to save received files."),
    yes: bool = typer.Option(False, "--yes", help="Accept the incoming transfer without asking."),
    server: str = typer.Option(DEFAULT_SERVER_URL, "--server", help="Beam server URL."),
) -> None:
    """Receive files offered by whoever created the room for CODE."""
    asyncio.run(_receive(code, directory, yes, server))


async def _send(paths: list[Path], server: str) -> None:
    # Built again inside run_send() -- this copy is only so the progress display can
    # show each file's real path instead of a bare index; build_manifest() is cheap
    # (a filesystem walk, no I/O on file contents) and paths are re-validated there too.
    entries_by_index = {e.offer.index: e.offer.path for e in build_manifest(paths)}
    progress_display = ui.TransferProgressDisplay("Sending")

    def on_room_ready(code: str, _room_id: str) -> None:
        share_url = f"{server}/r#{code}"
        ui.print_room_ready(code, share_url)

    def on_sas(symbols: list[SasSymbol]) -> None:
        ui.print_sas(symbols)

    def on_progress(progress: SenderProgress) -> None:
        path = entries_by_index.get(progress.file_index, f"file {progress.file_index}")
        progress_display.update_sender(progress, path)

    def on_error(message: str) -> None:
        ui.print_error(message)

    with progress_display:
        sender = await run_send(
            paths,
            server,
            on_room_ready=on_room_ready,
            on_sas=on_sas,
            on_progress=on_progress,
            on_error=on_error,
        )

    phase = sender.get_phase()
    if phase == "completed":
        ui.print_success("Transfer complete.")
    elif phase == "declined":
        ui.print_error("The other device declined the transfer.")
        raise typer.Exit(code=1)
    else:
        ui.print_error(f"Transfer ended: {phase}")
        raise typer.Exit(code=1)


async def _receive(code: str, directory: Path, yes: bool, server: str) -> None:
    progress_display = ui.TransferProgressDisplay("Receiving")
    saved: list[FileReadyResult] = []
    paths_by_index: dict[int, str] = {}

    def on_sas(symbols: list[SasSymbol]) -> None:
        ui.print_sas(symbols)

    def on_offer(manifest: OfferedManifest) -> bool:
        paths_by_index.update({f.index: f.path for f in manifest.files})
        if yes:
            return True
        return ui.prompt_accept_offer(manifest)

    def on_progress(progress: ReceiverProgress) -> None:
        path = paths_by_index.get(progress.file_index, f"file {progress.file_index}")
        progress_display.update_receiver(progress, path)

    def on_file_ready(result: FileReadyResult) -> None:
        saved.append(result)

    def on_error(message: str) -> None:
        ui.print_error(message)

    with progress_display:
        receiver = await run_receive(
            code,
            directory,
            server,
            auto_accept=yes,
            on_sas=on_sas,
            on_offer=on_offer,
            on_progress=on_progress,
            on_file_ready=on_file_ready,
            on_error=on_error,
        )

    phase = receiver.get_phase()
    if phase == "completed":
        ui.print_success(f"Received {len(saved)} file(s) into {directory}:")
        for result in saved:
            typer.echo(f"  {result.path}")
    elif phase == "declined":
        ui.print_error("Declined.")
        raise typer.Exit(code=1)
    else:
        ui.print_error(f"Transfer ended: {phase}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
