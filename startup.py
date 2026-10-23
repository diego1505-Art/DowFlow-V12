"""DowFlow's animated terminal startup screen."""

from time import sleep

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text


def _triangle_rows(console: Console, frame_number: int) -> list[Text]:
    """Build a centered, proportional triangle without directional glyph drift."""
    unicode_terminal = console.encoding.lower().startswith("utf")
    glyph = "●" if unicode_terminal else "#"
    counts = range(1, 14, 2)
    highlight_row = (frame_number // 2) % len(counts)
    rows = []
    for row_number, count in enumerate(counts):
        padding = (13 - count) // 2
        style = "bold #ff5a3d" if row_number == highlight_row else "bold #b82c2e"
        rows.append(Text(" " * padding + glyph * count, style=style, justify="center"))
    return rows


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
        *(_triangle_rows(console, frame_number)),
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