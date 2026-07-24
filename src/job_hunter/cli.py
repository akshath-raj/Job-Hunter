"""`job-hunter` command-line interface.

The interactive commands here mirror the MCP tools exactly, so you can run the
whole thing from a terminal or hand control to Claude Code — same behavior.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from . import profile as profile_mod
from . import service, store
from .linkedin import browser
from .models import JobStatus

app = typer.Typer(add_completion=False, help="Autonomous LinkedIn job-hunting agent.")
console = Console()


def _run(coro):
    return asyncio.run(coro)


@app.command()
def login():
    """Open a browser and make sure you're logged into LinkedIn (persists)."""
    console.print("[cyan]Opening browser — sign in to LinkedIn if prompted...[/]")
    ok = _run(browser.ensure_login(headless=False))
    console.print("[green]Logged in.[/]" if ok else "[red]Login not detected. Try again.[/]")


@app.command()
def onboard(
    resume: str = typer.Option(..., "--resume", "-r", help="Path to your resume (pdf/docx/txt)."),
    description: str = typer.Option("", "--description", "-d",
                                    help="Free-text: what you're looking for."),
):
    """Analyze your resume, then interactively fill any missing details."""
    console.print("[cyan]Reading and analyzing your resume...[/]")
    try:
        prof = service.ingest_resume(resume, description or None, use_llm=True)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Failed: {e}[/]")
        raise typer.Exit(1) from e

    sen = prof.seniority.value if prof.seniority else "—"
    ceiling = prof.constraints.max_seniority.value if prof.constraints.max_seniority else "—"
    console.print(f"[green]Target roles:[/] {', '.join(prof.target_roles) or '—'}")
    console.print(f"[green]Seniority:[/] {sen} | ceiling: {ceiling}")

    missing = profile_mod.missing_required_fields(prof, include_recommended=True)
    answers = {}
    for field, question in missing.items():
        val = typer.prompt(question, default="")
        if val:
            answers[field] = val
    if answers:
        prof = profile_mod.apply_answers(prof, answers)
        profile_mod.save(prof)
    console.print("[green]Profile saved.[/] Run [bold]job-hunter search[/] next.")


@app.command()
def profile():
    """Show the current profile."""
    p = profile_mod.load()
    console.print_json(p.model_dump_json(indent=2))


@app.command()
def search(
    query: list[str] = typer.Option(None, "--query", "-q", help="Override search terms."),
    max: int = typer.Option(25, "--max", "-m", help="Max results per query."),
):
    """Search LinkedIn for jobs matching your profile and store them."""
    p = profile_mod.load()
    console.print("[cyan]Searching LinkedIn...[/]")
    res = _run(service.search_jobs(p, queries=query or None, max_per_query=max))
    console.print_json(data=res)


@app.command()
def jobs(status: str = typer.Option(None, "--status", "-s", help="Filter by status.")):
    """List discovered jobs."""
    st = JobStatus(status) if status else None
    rows = store.list_jobs(status=st)
    table = Table("id", "title", "company", "status", "eligible?")
    for j in rows:
        table.add_row(j.id, j.title[:40], j.company[:24], j.status.value,
                      j.ineligible_reason or "yes")
    console.print(table)


@app.command()
def apply(
    limit: int = typer.Option(10, "--limit", "-n", help="Max applications this run."),
    job: str = typer.Option(None, "--job", "-j", help="Apply to one job id."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM answers (skip on unknowns)."),
):
    """Autonomously apply to eligible jobs (fully autonomous submit)."""
    p = profile_mod.load()
    if job:
        res = _run(service.apply_single(p, job, use_llm=not no_llm))
    else:
        res = _run(service.apply_batch(p, limit=limit, use_llm=not no_llm))
    console.print_json(data=res)


@app.command()
def run(
    resume: str = typer.Option(..., "--resume", "-r"),
    description: str = typer.Option("", "--description", "-d"),
    limit: int = typer.Option(10, "--limit", "-n"),
    max: int = typer.Option(25, "--max", "-m"),
):
    """Full pipeline: onboard -> search -> apply, autonomously."""
    onboard(resume=resume, description=description)
    search(query=None, max=max)
    apply(limit=limit, job=None, no_llm=False)


@app.command()
def status():
    """Show counts by job status."""
    console.print_json(data=store.counts_by_status())


if __name__ == "__main__":
    app()
