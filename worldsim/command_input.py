from __future__ import annotations


GLOBAL_COMMANDS = {
    "quit",
    "exit",
    "help",
    "inventory",
    "inv",
    "items",
    "end conversation",
    "end dialogue",
    "goodbye",
}


def normalize_command_input(entered: str, dialogue_active: bool) -> str:
    """Preserve global commands while treating other bare dialogue input as speech."""

    cleaned = entered.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("/"):
        return cleaned[1:].strip()
    if dialogue_active and cleaned.casefold() not in GLOBAL_COMMANDS:
        return f"say {cleaned}"
    return cleaned
