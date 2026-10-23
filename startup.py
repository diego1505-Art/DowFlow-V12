"""DowFlow's animated terminal startup screen."""

from time import sleep

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _triangle_rows(console: Console, frame_number: int) -> Table:
    """Build the triangle on fixed terminal cells to keep both edges symmetric."""
    unicode_terminal = console.encoding.lower().startswith("utf")
    glyph = "●" if unicode_terminal else "#"
    counts = (9, 7, 5, 3, 1)
    highlight_row = (frame_number // 2) % len(counts)
    grid = Table.grid(padding=(0, 1), expand=False)
    for _ in range(9):
        grid.add_column(width=1, justify="center")
    for row_number, count in enumerate(counts):
        style = "bold #ff5a3d" if row_number == highlight_row else "bold #b82c2e"
        start = (9 - count) // 2
        cells = [Text("") for _ in range(9)]
        for column in range(start, start + count):
            cells[column] = Text(glyph, style=style)
        grid.add_row(*cells)
    return grid


def _frame(console: Console, frame_number: int, complete: bool = False) -> Align:
    unicode_terminal = console.encoding.lower().startswith("utf")
    bar_empty = "·" if unicode_terminal else "."
    bar_fill = "━" if unicode_terminal else "="
    progress_width = 30
    progress = progress_width if complete else min(progress_width, frame_number + 1)
    status = "Initialisation terminée" if complete else "Initialisation en cours"
    dots = "" if complete else "." * ((frame_number // 5) % 4)
    bar = Text(bar_fill * progress + bar_empty * (progress_width - progress), style="bold #ff5a3d")
    content = [
        Text(""),
        _triangle_rows(console, frame_number),
        Text(""),
        Text("D O W F L O W", style="bold white", justify="center"),
        Text("VIDEO   AUDIO   MEDIA", style="dim", justify="center"),
        Text(""),
        bar,
        Text(f"{status}{dots}", style="dim", justify="center"),
    ]
    panel = Panel(
        Group(*content),
        title=" DOWFLOW / STARTUP ",
        title_align="center",
        border_style="#7d2829",
        box=ROUNDED,
        padding=(1, 4),
    )
    return Align.center(panel, vertical="middle", height=console.size.height)


def main() -> None:
    console = Console()
    with Live(_frame(console, 0), console=console, refresh_per_second=15, transient=False) as live:
        for frame_number in range(30):
            live.update(_frame(console, frame_number))
            sleep(0.09)
        live.update(_frame(console, 30, complete=True))
    console.print()


if __name__ == "__main__":
    main()