"""DowFlow's animated terminal startup screen."""

from time import sleep

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text


def _triangle_rows(glyph: str, direction: int) -> list[str]:
    if direction in (0, 2):
        counts = list(range(1, 16, 2))
        if direction == 2:
            counts.reverse()
        return [" " * ((15 - count) // 2) + glyph * count for count in counts]

    counts = list(range(1, 9)) + list(range(7, 0, -1))
    if direction == 1:
        return [glyph * count + " " * (15 - count) for count in counts]
    return [" " * (15 - count) + glyph * count for count in counts]


def _frame(console: Console, frame_number: int, complete: bool = False) -> Align:
    symbols = ("▲", "▶", "▼", "◀")
    if not console.encoding.lower().startswith("utf"):
        symbols = ("^", ">", "v", "<")

    direction = (frame_number // 12) % len(symbols)
    color = "#ff3038" if frame_number % 2 == 0 else "#94151e"
    rows = [Text(row, style=f"bold {color}") for row in _triangle_rows(symbols[direction], direction)]
    rows.extend(
        (
            Text(""),
            Text("D O W F L O W", style="bold white"),
            Text("VIDEO   AUDIO   MEDIA", style="dim"),
            Text(""),
            Text(
                "Initialisation terminee." if complete else "Initialisation en cours...",
                style="dim",
            ),
        )
    )
    return Align.center(Group(*rows), vertical="middle", height=console.size.height)


def main() -> None:
    console = Console()
    with Live(_frame(console, 0), console=console, refresh_per_second=15, transient=False) as live:
        for frame_number in range(48):
            live.update(_frame(console, frame_number))
            sleep(0.09)
        live.update(_frame(console, 47, complete=True))
    console.print()


if __name__ == "__main__":
    main()