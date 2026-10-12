from __future__ import annotations

import sys
import unicodedata
from datetime import UTC, datetime
from typing import Any

import click
import httpx

DEFAULT_BASE_URL = "http://localhost:8000"

# Seconds to wait for the server's answer. A single check searches two engines in turn, and the
# server gives each SerpAPI request up to 20 s, so a slow but healthy check can take about 40 s.
DEFAULT_TIMEOUT = 60.0


def _base_url(ctx: click.Context) -> str:
    return ctx.obj["base_url"]


def _request(
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    timeout = click.get_current_context().obj["timeout"]
    try:
        resp = httpx.request(method, url, json=json, timeout=timeout)
    except httpx.ConnectError:
        click.secho(f"Error: cannot connect to {url}", fg="red", err=True)
        click.echo("Is the hunter-bargain server running?", err=True)
        sys.exit(1)
    except httpx.TimeoutException:
        click.secho(f"Error: no answer from {url} within {timeout:g} s", fg="red", err=True)
        click.echo(
            "The server may still finish the request. Use --timeout to wait longer.", err=True
        )
        sys.exit(1)
    except httpx.HTTPError as e:
        click.secho(f"Error: request to {url} failed ({type(e).__name__})", fg="red", err=True)
        sys.exit(1)
    return resp


def _clean(value: object) -> str:
    """Return the value as text without control characters (Unicode category Cc).

    Stored item names and keywords, and the sources and URLs of third-party listings, can hold
    ESC, CR or other control characters that would let them hide or rewrite terminal output.
    """
    return "".join(c for c in str(value) if unicodedata.category(c) != "Cc")


# Block characters for a sparkline, lowest to highest.
_SPARKS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """One block per value, its height scaled between the lowest and the highest value."""
    low, high = min(values), max(values)
    if high == low:
        return _SPARKS[3] * len(values)
    return "".join(_SPARKS[round((v - low) / (high - low) * (len(_SPARKS) - 1))] for v in values)


def _print_lows(lows: list[tuple[str, dict[str, Any] | None]]) -> None:
    """Print each low that exists: its label, price, date and engine."""
    for label, low in lows:
        if low:
            click.echo(
                f"    {label}: ${low['price']:.2f} on {_clean(low['checked_at'])[:10]}"
                f" ({_clean(low['source'])})"
            )


def _print_item(item: dict[str, Any]) -> None:
    target = f"${item['target_price']:.2f}" if item.get("target_price") else "—"
    keywords = item.get("keywords") or "—"
    click.echo(
        f"  [{item['id']}]  {_clean(item['name'])}"
        f"  |  target: {target}"
        f"  |  keywords: {_clean(keywords)}"
        f"  |  email: {_clean(item['notify_email'])}"
    )


@click.group()
@click.option(
    "--url",
    envvar="HUNTER_BARGAIN_URL",
    default=DEFAULT_BASE_URL,
    show_default=True,
    help="Base URL of the hunter-bargain API server.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="Seconds to wait for the server to answer.",
)
@click.version_option(package_name="hunter-bargain")
@click.pass_context
def cli(ctx: click.Context, url: str, timeout: float) -> None:
    """hunter-bargain CLI — manage tracked items from the terminal."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = url.rstrip("/")
    ctx.obj["timeout"] = timeout


@cli.command()
@click.argument("name")
@click.option("-e", "--email", required=True, help="Notification email address.")
@click.option(
    "-t", "--target-price", type=float, default=None, help="Alert when price drops to this."
)
@click.option(
    "-k",
    "--keywords",
    default=None,
    help='Comma-separated terms, e.g. "256GB,unlocked": a listing\'s title must have every word.',
)
@click.pass_context
def add(
    ctx: click.Context, name: str, email: str, target_price: float | None, keywords: str | None
) -> None:
    """Add an item to track.

    NAME is the product name to search for.
    """
    payload: dict[str, Any] = {"name": name, "notify_email": email}
    if target_price is not None:
        payload["target_price"] = target_price
    if keywords is not None:
        payload["keywords"] = keywords

    resp = _request("POST", f"{_base_url(ctx)}/api/v1/items/", json=payload)

    if resp.status_code == 201:
        item = resp.json()
        click.secho(f"Added item #{item['id']}: {_clean(item['name'])}", fg="green")
        _print_item(item)
    elif resp.status_code == 422:
        errors = resp.json().get("detail", [])
        click.secho("Validation error:", fg="red", err=True)
        for err in errors:
            loc = " → ".join(str(x) for x in err.get("loc", []))
            click.echo(f"  {loc}: {err.get('msg', '')}", err=True)
        sys.exit(1)
    else:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)


@cli.command("rm")
@click.argument("item_id", type=int)
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def remove(ctx: click.Context, item_id: int, yes: bool) -> None:
    """Remove an item from tracking by ID."""
    if not yes:
        resp = _request("GET", f"{_base_url(ctx)}/api/v1/items/{item_id}")
        if resp.status_code == 404:
            click.secho(f"Item {item_id} not found.", fg="red", err=True)
            sys.exit(1)
        item = resp.json()
        if not click.confirm(f"Remove '{_clean(item['name'])}' (#{item_id})?"):
            click.echo("Cancelled.")
            return

    resp = _request("DELETE", f"{_base_url(ctx)}/api/v1/items/{item_id}")

    if resp.status_code == 204:
        click.secho(f"Removed item #{item_id}.", fg="green")
    elif resp.status_code == 404:
        click.secho(f"Item {item_id} not found.", fg="red", err=True)
        sys.exit(1)
    else:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)


@cli.command("ls")
@click.pass_context
def list_items(ctx: click.Context) -> None:
    """List all tracked items."""
    resp = _request("GET", f"{_base_url(ctx)}/api/v1/items/")

    if resp.status_code != 200:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)

    items = resp.json()
    if not items:
        click.echo("No items tracked yet. Use 'hb add' to start.")
        return

    click.echo(f"Tracking {len(items)} item(s):\n")
    for item in items:
        _print_item(item)


@cli.command()
@click.argument("item_id", type=int)
@click.option("-n", "--name", default=None, help="New product name.")
@click.option("-e", "--email", default=None, help="New notification email.")
@click.option("-t", "--target-price", type=float, default=None, help="New target price.")
@click.option(
    "-k",
    "--keywords",
    default=None,
    help="New keywords: comma-separated terms; a listing's title must have every word.",
)
@click.pass_context
def update(
    ctx: click.Context,
    item_id: int,
    name: str | None,
    email: str | None,
    target_price: float | None,
    keywords: str | None,
) -> None:
    """Update a tracked item's fields by ID."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if email is not None:
        payload["notify_email"] = email
    if target_price is not None:
        payload["target_price"] = target_price
    if keywords is not None:
        payload["keywords"] = keywords

    if not payload:
        click.echo("Nothing to update — provide at least one option.", err=True)
        sys.exit(1)

    resp = _request("PATCH", f"{_base_url(ctx)}/api/v1/items/{item_id}", json=payload)

    if resp.status_code == 200:
        item = resp.json()
        click.secho(f"Updated item #{item_id}:", fg="green")
        _print_item(item)
    elif resp.status_code == 404:
        click.secho(f"Item {item_id} not found.", fg="red", err=True)
        sys.exit(1)
    elif resp.status_code == 422:
        errors = resp.json().get("detail", [])
        click.secho("Validation error:", fg="red", err=True)
        for err in errors:
            loc = " → ".join(str(x) for x in err.get("loc", []))
            click.echo(f"  {loc}: {err.get('msg', '')}", err=True)
        sys.exit(1)
    else:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)


@cli.command()
@click.argument("item_id", type=int, required=False, default=None)
@click.pass_context
def check(ctx: click.Context, item_id: int | None) -> None:
    """Trigger an on-demand price check.

    If ITEM_ID is given, checks that item and prints its prices. Otherwise starts a check of all
    items in the background, as the daily job does: alerts are emailed, and nothing is printed.
    """
    if item_id is None:
        _start_check_all(ctx)
        return

    resp = _request("POST", f"{_base_url(ctx)}/api/v1/prices/check/{item_id}")

    if resp.status_code == 404:
        click.secho(f"Item {item_id} not found.", fg="red", err=True)
        sys.exit(1)
    if resp.status_code != 200:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)

    results = [resp.json()]

    for result in results:
        name = _clean(result["item_name"])
        count = result["results_count"]
        lowest = result.get("lowest_price")
        source = _clean(result.get("lowest_source", ""))
        merchant = _clean(result.get("lowest_merchant") or "")
        if merchant:
            source = f"{merchant} via {source}"
        lowest_url = result.get("lowest_url")
        engine_errors = result.get("engine_errors") or []

        if lowest is not None:
            click.echo(f"  {name}: ${lowest:.2f} ({source}) — {count} result(s)")
            if lowest_url:
                click.echo(f"    -> {_clean(lowest_url)}")
        elif engine_errors:
            click.secho(f"  {name}: no prices, {len(engine_errors)} engine error(s)", fg="red")
        else:
            click.secho(f"  {name}: no results found", fg="yellow")
        _print_lows(
            [
                ("lowest in 90 days", result.get("low_90_days")),
                ("all-time low", result.get("all_time_low")),
            ]
        )
        # A failed engine may have missed a lower price, so its error shows either way.
        for error in engine_errors:
            click.echo(f"    engine error: {_clean(error)}")


@cli.command()
@click.argument("item_id", type=int)
@click.option(
    "--days",
    type=click.IntRange(1, 3650),
    default=90,
    show_default=True,
    help="How many days of price history to show.",
)
@click.pass_context
def history(ctx: click.Context, item_id: int, days: int) -> None:
    """Show an item's price history: its lowest price on each day, and its lows."""
    resp = _request("GET", f"{_base_url(ctx)}/api/v1/items/{item_id}/stats?days={days}")

    if resp.status_code == 404:
        click.secho(f"Item {item_id} not found.", fg="red", err=True)
        sys.exit(1)
    if resp.status_code != 200:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)

    stats = resp.json()
    name = _clean(stats["item_name"])
    per_day = stats["per_day"]
    if not per_day:
        click.echo(f"  {name}: no prices recorded in the last {days} days")
    else:
        lows = [day["low"] for day in per_day]
        click.echo(
            f"  {name}: lowest price each day, {per_day[0]['day']} to {per_day[-1]['day']}"
            f" ({len(per_day)} day(s) with prices)"
        )
        click.echo(f"    {_sparkline(lows)}  ${min(lows):.2f} to ${max(lows):.2f}")
    _print_lows(
        [
            (f"lowest in {days} days", stats["lowest_in_days"]),
            ("all-time low", stats["all_time_low"]),
        ]
    )


def _start_check_all(ctx: click.Context) -> None:
    resp = _request("POST", f"{_base_url(ctx)}/api/v1/prices/check-all")
    if resp.status_code == 202:
        click.secho("Started a price check of all items in the background.", fg="green")
        click.echo("Alerts are emailed as usual; run 'hb check ITEM_ID' to see one item's prices.")
    elif resp.status_code == 409:
        click.secho("A price check of all items is already running.", fg="yellow", err=True)
        sys.exit(1)
    else:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)


def _utc_time(value: str | None) -> str:
    """An API time (ISO 8601) as "2026-10-12 09:00:41 UTC"; "—" for none."""
    if value is None:
        return "—"
    return f"{datetime.fromisoformat(value).astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"


def _count(value: int | None) -> str:
    """A run's count; "—" while the run lasts, or for a run from before counts were recorded."""
    return "—" if value is None else str(value)


_STATUS_COLORS = {"succeeded": "green", "failed": "red", "running": "yellow"}


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the last price check of all items: its result, engine errors and searches used."""
    resp = _request("GET", f"{_base_url(ctx)}/api/v1/runs?limit=1")
    if resp.status_code != 200:
        click.secho(f"Error {resp.status_code}: {resp.text}", fg="red", err=True)
        sys.exit(1)

    runs = resp.json()
    if not runs:
        click.echo("No price check of all items is recorded yet.")
        return

    run = runs[0]
    click.secho(
        f"Last price check of all items: {run['status']}", fg=_STATUS_COLORS.get(run["status"])
    )
    click.echo(f"  Started:       {_utc_time(run['started_at'])}")
    click.echo(f"  Finished:      {_utc_time(run['finished_at'])}")
    click.echo(f"  Items checked: {_count(run['items_checked'])}")
    click.echo(f"  Searches used: {_count(run['searches_used'])}")
    click.secho(
        f"  Engine errors: {_count(run['engine_errors'])}",
        fg="red" if run["engine_errors"] else None,
    )
    click.echo(f"  Alerts sent:   {_count(run['alerts_sent'])}")
    # The run keeps counts only: each engine error's text is in the app's log. A run whose every
    # search failed is recorded as failed without an error that ended it.
    every_search_failed = bool(run["engine_errors"]) and not run["searches_used"]
    if run["engine_errors"]:
        searches = "Every search" if every_search_failed else "Some searches"
        click.echo(f"{searches} failed: the app's log, or 'hb check ITEM_ID', shows why.")
    if run["status"] == "failed" and not every_search_failed:
        click.echo("An error ended the run: the app's log has the details.")
