from __future__ import annotations

import re


_ANSI_CSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def clean_terminal_output(text: str) -> str:
    """Remove common terminal control sequences while retaining command output."""
    cleaned = _ANSI_CSI.sub("", text)
    cleaned = cleaned.replace("\x00", "")
    return _apply_carriage_returns(cleaned)


def _apply_carriage_returns(text: str) -> str:
    lines: list[str] = []
    current: list[str] = []
    cursor = 0
    for char in text:
        if char == "\n":
            lines.append("".join(current))
            current = []
            cursor = 0
        elif char == "\r":
            cursor = 0
        elif char == "\b":
            if cursor > 0:
                cursor -= 1
        else:
            if cursor < len(current):
                current[cursor] = char
            else:
                if cursor > len(current):
                    current.extend(" " * (cursor - len(current)))
                current.append(char)
            cursor += 1
    result = "\n".join(lines)
    if current:
        if result:
            result += "\n"
        result += "".join(current)
    elif text.endswith("\n"):
        result += "\n"
    return result
