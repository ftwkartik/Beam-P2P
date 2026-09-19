"""Terminal display: progress bars, the SAS, and the consent prompt (ADR-008: Typer +
rich). Kept thin and mostly stateless on purpose, mirroring how the web client pushes
ETA/throughput computation out of the UI layer (docs/adr/007) -- this module only ever
renders numbers `session.py`'s callbacks hand it.
"""

from __future__ import annotations

from types import TracebackType

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeRemainingColumn
from rich.prompt import Confirm

from beam_cli.receiver import OfferedManifest, ReceiverProgress
from beam_cli.sender import SenderProgress
from beam_protocol.sas import SasSymbol, format_sas

console = Console()


def print_room_ready(code: str, share_url: str | None) -> None:
    console.print(f"\n[bold]Room code:[/bold] [cyan]{code}[/cyan]")
    if share_url:
        console.print(f"[bold]Share link:[/bold] {share_url}")
    console.print("Waiting for the other device to join…\n")


def print_sas(symbols: list[SasSymbol]) -> None:
    console.print(
        "\n[bold]Read these symbols aloud and check they match on the other device:[/bold]"
    )
    console.print(f"  {format_sas(tuple(symbols))}\n")
    console.print(
        "[dim]If they don't match exactly, stop -- something may be "
        "intercepting the connection.[/dim]\n"
    )


def prompt_accept_offer(manifest: OfferedManifest) -> bool:
    total_size = sum(f.size for f in manifest.files)
    console.print(
        f"\nThe other device wants to send you {len(manifest.files)} file(s) "
        f"({_format_bytes(total_size)}):"
    )
    for f in manifest.files[:10]:
        console.print(f"  {f.path} -- {_format_bytes(f.size)}")
    if len(manifest.files) > 10:
        console.print(f"  … and {len(manifest.files) - 10} more")
    return Confirm.ask("Accept?", default=True)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover - unreachable in practice


class TransferProgressDisplay:
    """One rich progress bar per file, added lazily as each file starts."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        )
        self._task_ids: dict[int, TaskID] = {}
        self._started = False

    def __enter__(self) -> TransferProgressDisplay:
        self._progress.__enter__()
        self._started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._progress.__exit__(exc_type, exc_value, traceback)

    def _task_id(self, file_index: int, path: str, total: int) -> TaskID:
        if file_index not in self._task_ids:
            description = f"{self._label} {path}"
            self._task_ids[file_index] = self._progress.add_task(description, total=total)
        return self._task_ids[file_index]

    def update_sender(self, progress: SenderProgress, path: str) -> None:
        task_id = self._task_id(progress.file_index, path, progress.file_size)
        self._progress.update(task_id, completed=progress.bytes_sent_for_file)

    def update_receiver(self, progress: ReceiverProgress, path: str) -> None:
        task_id = self._task_id(progress.file_index, path, progress.file_size)
        self._progress.update(task_id, completed=progress.bytes_verified_for_file)


def print_error(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")


def print_success(message: str) -> None:
    console.print(f"[bold green]{message}[/bold green]")
