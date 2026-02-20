"""Nova CLI — entry point for all commands."""

import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.tree import Tree

from nova.paths import PROJECTS_DIR, get_project_root
from nova.state import init_state

app = typer.Typer(
    name="nova",
    help="A deterministic multi-agent development runtime.",
    no_args_is_help=True,
)
console = Console()

PROJECT_DIRS = [
    "docs/brainstorm",
    "docs/spec",
    "docs/plans",
    "docs/tasks",
    "docs/decisions",
    "docs/retros",
    "logs/runs",
    "logs/sessions",
    "src",
]

STARTER_PREFERENCES = """\
# Project Preferences — {project_name}
# These override framework_preferences.yaml (deep merge, project wins).
# Prefix with must_ for hard guardrails.

# coding:
#   style_guide: "project-specific rules here"

# testing:
#   prefer_integration_tests: true
"""


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", "-v", help="Show version."),) -> None:
    if version:
        from nova import __version__

        console.print(f"[bold]nova[/bold] {__version__}")
        raise typer.Exit()


@app.command()
def new( project_name: str = typer.Argument(..., help="Name of the new project."),) -> None:
    """Create a new project with the full directory structure."""
    project_root = get_project_root(project_name)

    if project_root.exists():
        console.print(f"[red]Error:[/red] Project '{project_name}' already exists at {project_root}")
        raise typer.Exit(code=1)

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    for dir_path in PROJECT_DIRS:
        (project_root / dir_path).mkdir(parents=True, exist_ok=True)

    prefs_file = project_root / "preferences.yaml"
    prefs_file.write_text(STARTER_PREFERENCES.format(project_name=project_name))

    init_state(project_name)
    _git_init(project_root)
    _print_tree(project_name, project_root)

    console.print(
        f"\n[bold green]Project '{project_name}' created.[/bold green] "
        f"Start with [bold]nova brainstorm {project_name}[/bold]"
    )


def _git_init(project_root: Path) -> None:
    try:
        subprocess.run(
            ["git", "init"],
            cwd=project_root,
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        console.print(f"[yellow]Warning:[/yellow] Could not initialize git repo: {e}")


def _print_tree(project_name: str, project_root: Path) -> None:
    tree = Tree(f"[bold]{project_name}/[/bold]")
    _add_to_tree(tree, project_root, project_root)
    console.print()
    console.print(tree)


def _add_to_tree(tree: Tree, directory: Path, root: Path) -> None:
    entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            branch = tree.add(f"[bold]{entry.name}/[/bold]")
            _add_to_tree(branch, entry, root)
        else:
            tree.add(entry.name)


if __name__ == "__main__":
    app()
