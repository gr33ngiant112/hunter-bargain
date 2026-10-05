from __future__ import annotations

import sys
import unicodedata
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
@click.option("-k", "--keywords", default=None, help="Extra search keywords.")
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
@click.option("-k", "--keywords", default=None, help="New search keywords.")
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
        # A failed engine may have missed a lower price, so its error shows either way.
        for error in engine_errors:
            click.echo(f"    engine error: {_clean(error)}")


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
