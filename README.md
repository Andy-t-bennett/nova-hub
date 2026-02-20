Nova framework

Typer is used as a Cli framework to read the command line arguments

preferences:
- always takes the root preferences first then can merge project preferences
- Unless must_ is in the name then parent preference ALWAYS wins (safety)
- must_* can only be defined in parent root

per task loop:
- full.json