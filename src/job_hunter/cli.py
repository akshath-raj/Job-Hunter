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

    # Only ask for REQUIRED details the resume didn't contain — no standard
    # questionnaire. Everything else (marks, CGPA, ...) is extracted from the
    # resume, and anything still unknown is asked later only if an application
    # actually needs it.
    missing = profile_mod.missing_required_fields(prof, include_recommended=False)
    answers = {}
    if missing:
        console.print("[dim]A few required details weren't found on your resume:[/]")
        for field, question in missing.items():
            val = typer.prompt(question, default="")
            if val:
                answers[field] = val
        prof = profile_mod.apply_answers(prof, answers)
    profile_mod.save(prof)

    if prof.extra:
        preview = ", ".join(list(prof.extra.keys())[:4])
        console.print(f"[green]Pulled {len(prof.extra)} extra detail(s) from your resume[/] "
                      f"[dim]({preview}…)[/]")
    console.print("[green]Profile saved.[/] I'll ask for anything else only if a specific "
                  "application needs it. Run [bold]job-hunter search[/] next.")


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

    # One-time: collect preferences that aren't on a resume (salary, location...).
    if profile_mod.needs_search_preferences(p):
        console.print("[dim]A couple of preferences for this search "
                      "(asked once, press Enter to skip):[/]")
        q = profile_mod.SEARCH_PREF_QUESTIONS
        salary = typer.prompt(q["expected salary"], default="")
        locations = typer.prompt(q["preferred locations"], default="")
        remote = typer.confirm("Only remote roles?", default=False)
        profile_mod.set_search_preferences(
            p, expected_salary=salary or None,
            locations=locations or None, remote_only=remote,
        )
        profile_mod.save(p)

    console.print("[cyan]Searching LinkedIn + researching salaries (parallel)...[/]")
    res = _run(service.search_jobs(p, queries=query or None, max_per_query=max))
    console.print_json(data=res)
    if res.get("excel"):
        console.print(f"[green]📊 Spreadsheet written:[/] {res['excel']}")


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
    limit: int = typer.Option(10, "--limit", "-n", help="Max applications (auto mode)."),
    job: str = typer.Option(None, "--job", "-j", help="Apply to one job id."),
    mode: str = typer.Option("auto", "--mode", help="'auto' or 'select' (human-in-the-loop)."),
    concurrency: int = typer.Option(2, "--concurrency", "-c", help="Parallel applications."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM answers (skip on unknowns)."),
):
    """Apply to jobs — fully autonomous ('auto') or pick-your-own ('select')."""
    p = profile_mod.load()
    if job:
        res = _run(service.apply_single(p, job, use_llm=not no_llm))
        console.print_json(data=res)
        return

    selected_ids = None
    if mode == "select":
        candidates = service.pending_eligible()
        if not candidates:
            console.print("[yellow]No eligible pending jobs. Run a search first.[/]")
            return
        table = Table("#", "title", "company", "salary")
        for i, j in enumerate(candidates):
            table.add_row(str(i), j.title[:40], j.company[:24], j.salary or "—")
        console.print(table)
        raw = typer.prompt("Enter the # of jobs to apply to (comma-separated, or 'all')")
        if raw.strip().lower() == "all":
            selected_ids = [j.id for j in candidates]
        else:
            picks = [int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()]
            selected_ids = [candidates[i].id for i in picks if 0 <= i < len(candidates)]

    res = _run(service.apply_batch(
        p, limit=limit, mode=mode, selected_ids=selected_ids,
        use_llm=not no_llm, concurrency=concurrency,
    ))
    console.print_json(data=res)


@app.command()
def enrich(limit: int = typer.Option(25, "--limit", "-n", help="Jobs to enrich.")):
    """Research company/salary/qualifications for jobs (cheap research subagents)."""
    console.print("[cyan]Enriching jobs (this may hit the web for salaries)...[/]")
    res = _run(service.enrich_jobs(limit=limit))
    console.print_json(data=res)


@app.command()
def export(
    status: str = typer.Option(None, "--status", "-s", help="Filter by status."),
    path: str = typer.Option(None, "--path", "-p", help="Output .xlsx path."),
):
    """Export jobs to an Excel spreadsheet."""
    st = JobStatus(status) if status else None
    res = service.export_excel(status=st, path=path)
    console.print(f"[green]Wrote {res['rows']} jobs to[/] {res['path']}")


@app.command()
def run(
    resume: str = typer.Option(..., "--resume", "-r"),
    description: str = typer.Option("", "--description", "-d"),
    limit: int = typer.Option(10, "--limit", "-n"),
    max: int = typer.Option(25, "--max", "-m"),
    mode: str = typer.Option("auto", "--mode", help="'auto' or 'select'."),
):
    """Full pipeline: onboard -> search (+enrich +Excel) -> apply."""
    onboard(resume=resume, description=description)
    search(query=None, max=max)   # also enriches and writes the spreadsheet
    apply(limit=limit, job=None, mode=mode, concurrency=2, no_llm=False)


@app.command()
def status():
    """Show counts by job status."""
    console.print_json(data=store.counts_by_status())


if __name__ == "__main__":
    app()
