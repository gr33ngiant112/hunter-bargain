from __future__ import annotations

import unicodedata
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from hunter_bargain.cli import cli

FAKE_ITEM = {
    "id": 1,
    "name": "Sony WH-1000XM5",
    "keywords": "headphones wireless",
    "target_price": 299.99,
    "notify_email": "user@example.com",
    "created_at": "2026-03-01T00:00:00",
    "updated_at": "2026-03-01T00:00:00",
}

# Colour codes (ESC [ and the one-byte C1 CSI), CR/LF, BEL and backspace: harmless stand-ins
# for control characters in a stored name or a listing field, and the text left without them.
WITH_CONTROLS = "Sony\x1b[31m WH\x9b31m-1000XM5\r\nFake\x07 line\x08"
CLEANED = "Sony[31m WH31m-1000XM5Fake line"


@pytest.fixture
def runner():
    return CliRunner()


def _control_characters(text: str) -> set[str]:
    """Control characters in CLI output, apart from the newlines that end its lines."""
    return {c for c in text if unicodedata.category(c) == "Cc"} - {"\n"}


def _mock_response(status_code: int, json_data=None) -> httpx.Response:
    kwargs: dict = {"status_code": status_code, "request": httpx.Request("GET", "http://test")}
    if json_data is not None:
        kwargs["json"] = json_data
    return httpx.Response(**kwargs)


class TestAdd:
    def test_add_success(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(201, FAKE_ITEM)
            result = runner.invoke(
                cli,
                [
                    "add",
                    "Sony WH-1000XM5",
                    "-e",
                    "user@example.com",
                    "-t",
                    "299.99",
                    "-k",
                    "headphones wireless",
                ],
            )

        assert result.exit_code == 0
        assert "Added item #1" in result.output
        assert "Sony WH-1000XM5" in result.output

        call_args = mock_req.call_args
        assert call_args[0] == ("POST", "http://localhost:8000/api/v1/items/")
        payload = call_args[1]["json"]
        assert payload["name"] == "Sony WH-1000XM5"
        assert payload["notify_email"] == "user@example.com"
        assert payload["target_price"] == 299.99
        assert payload["keywords"] == "headphones wireless"

    def test_add_validation_error(self, runner: CliRunner):
        error_body = {"detail": [{"loc": ["body", "notify_email"], "msg": "invalid email"}]}
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(422, error_body)
            result = runner.invoke(cli, ["add", "Test", "-e", "bad"])

        assert result.exit_code == 1
        assert "Validation error" in result.output

    def test_add_server_unreachable(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request", side_effect=httpx.ConnectError("refused")):
            result = runner.invoke(cli, ["add", "Test", "-e", "a@b.com"])

        assert result.exit_code == 1
        assert "cannot connect" in result.output

    def test_add_without_optional_fields(self, runner: CliRunner):
        item_no_extras = {**FAKE_ITEM, "target_price": None, "keywords": None}
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(201, item_no_extras)
            result = runner.invoke(cli, ["add", "Widget", "-e", "a@b.com"])

        assert result.exit_code == 0
        payload = mock_req.call_args[1]["json"]
        assert "target_price" not in payload
        assert "keywords" not in payload

    def test_add_strips_control_characters(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(201, {**FAKE_ITEM, "name": WITH_CONTROLS})
            result = runner.invoke(cli, ["add", "Sony WH-1000XM5", "-e", "user@example.com"])

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        assert f"Added item #1: {CLEANED}\n" in result.output


class TestRemove:
    def test_remove_with_confirm(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.side_effect = [
                _mock_response(200, FAKE_ITEM),
                _mock_response(204),
            ]
            result = runner.invoke(cli, ["rm", "1"], input="y\n")

        assert result.exit_code == 0
        assert "Removed item #1" in result.output

    def test_remove_skip_confirm(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(204)
            result = runner.invoke(cli, ["rm", "1", "-y"])

        assert result.exit_code == 0
        assert "Removed item #1" in result.output
        mock_req.assert_called_once()

    def test_remove_not_found(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            result = runner.invoke(cli, ["rm", "999", "-y"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_remove_cancelled(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, FAKE_ITEM)
            result = runner.invoke(cli, ["rm", "1"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_remove_prompt_strips_control_characters(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, {**FAKE_ITEM, "name": WITH_CONTROLS})
            result = runner.invoke(cli, ["rm", "1"], input="n\n")

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        assert f"Remove '{CLEANED}' (#1)?" in result.output


class TestList:
    def test_list_items(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [FAKE_ITEM])
            result = runner.invoke(cli, ["ls"])

        assert result.exit_code == 0
        assert "Tracking 1 item(s)" in result.output
        assert "Sony WH-1000XM5" in result.output
        assert "$299.99" in result.output

    def test_list_empty(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["ls"])

        assert result.exit_code == 0
        assert "No items tracked" in result.output

    def test_list_strips_control_characters(self, runner: CliRunner):
        item = {
            **FAKE_ITEM,
            "name": WITH_CONTROLS,
            "keywords": WITH_CONTROLS,
            "notify_email": "user@example.com\x07",
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [item])
            # color=True keeps escape sequences in the output, as when printing to a terminal.
            result = runner.invoke(cli, ["ls"], color=True)

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        assert (
            f"  [1]  {CLEANED}  |  target: $299.99  |  keywords: {CLEANED}"
            "  |  email: user@example.com\n"
        ) in result.output


class TestUpdate:
    def test_update_name(self, runner: CliRunner):
        updated = {**FAKE_ITEM, "name": "Sony XM5 Renewed"}
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, updated)
            result = runner.invoke(cli, ["update", "1", "-n", "Sony XM5 Renewed"])

        assert result.exit_code == 0
        assert "Updated item #1" in result.output
        payload = mock_req.call_args[1]["json"]
        assert payload == {"name": "Sony XM5 Renewed"}

    def test_update_no_fields(self, runner: CliRunner):
        result = runner.invoke(cli, ["update", "1"])

        assert result.exit_code == 1
        assert "Nothing to update" in result.output

    def test_update_not_found(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            result = runner.invoke(cli, ["update", "999", "-n", "X"])

        assert result.exit_code == 1
        assert "not found" in result.output


class TestCheck:
    def test_check_single_item(self, runner: CliRunner):
        check_result = {
            "item_id": 1,
            "item_name": "Sony WH-1000XM5",
            "lowest_price": 279.99,
            "lowest_source": "Google Shopping",
            "lowest_url": "https://example.com",
            "results_count": 3,
            "records": [],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", "1"])

        assert result.exit_code == 0
        assert "$279.99" in result.output
        assert "Google Shopping" in result.output
        assert "-> https://example.com" in result.output

    def test_check_without_id_starts_a_background_check_of_all_items(self, runner: CliRunner):
        """#6: check-all answers 202 at once; its results go to the log and alert emails."""
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(202, {"status": "started"})
            result = runner.invoke(cli, ["check"])

        assert result.exit_code == 0
        assert mock_req.call_args[0] == ("POST", "http://localhost:8000/api/v1/prices/check-all")
        assert result.output == (
            "Started a price check of all items in the background.\n"
            "Alerts are emailed as usual; run 'hb check ITEM_ID' to see one item's prices.\n"
        )

    def test_check_without_id_while_a_check_of_all_items_runs(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(
                409, {"detail": "A price check of all items is already running"}
            )
            result = runner.invoke(cli, ["check"])

        assert result.exit_code == 1
        assert result.output == "A price check of all items is already running.\n"

    def test_check_single_item_without_results(self, runner: CliRunner):
        check_result = {
            "item_id": 2,
            "item_name": "Item B",
            "lowest_price": None,
            "lowest_source": None,
            "lowest_url": None,
            "results_count": 0,
            "records": [],
            "engine_errors": [],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", "2"])

        assert result.exit_code == 0
        assert result.output == "  Item B: no results found\n"

    @pytest.mark.parametrize(
        ("check_result", "expected_lines"),
        [
            (
                {
                    "item_id": 1,
                    "item_name": WITH_CONTROLS,
                    "lowest_price": 279.99,
                    "lowest_source": f"Shop {WITH_CONTROLS}",
                    "lowest_url": f"https://example.com/{WITH_CONTROLS}",
                    "results_count": 3,
                    "records": [],
                },
                [
                    f"  {CLEANED}: $279.99 (Shop {CLEANED}) — 3 result(s)\n",
                    f"    -> https://example.com/{CLEANED}\n",
                ],
            ),
            (
                {
                    "item_id": 2,
                    "item_name": f"Other {WITH_CONTROLS}",
                    "lowest_price": None,
                    "lowest_source": None,
                    "lowest_url": None,
                    "results_count": 0,
                    "records": [],
                },
                [f"  Other {CLEANED}: no results found\n"],
            ),
        ],
        ids=["price", "no-results"],
    )
    def test_check_strips_control_characters(
        self, runner: CliRunner, check_result: dict, expected_lines: list[str]
    ):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", str(check_result["item_id"])])

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        for line in expected_lines:
            assert line in result.output

    def test_check_shows_merchant_next_to_engine(self, runner: CliRunner):
        check_result = {
            "item_id": 1,
            "item_name": "Folgers Classic Roast Ground Coffee",
            "lowest_price": 5.88,
            "lowest_source": "google_shopping",
            "lowest_merchant": "Walmart",
            "lowest_url": "https://www.google.com/shopping/product/16914877625280977865?gl=us",
            "results_count": 3,
            "records": [],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", "1"])

        assert result.exit_code == 0
        assert (
            "  Folgers Classic Roast Ground Coffee: $5.88 (Walmart via google_shopping)"
            " — 3 result(s)\n"
        ) in result.output

    def test_check_strips_control_characters_from_merchant(self, runner: CliRunner):
        check_result = {
            "item_id": 1,
            "item_name": "Sony WH-1000XM5",
            "lowest_price": 279.99,
            "lowest_source": "bing_shopping",
            "lowest_merchant": f"Shop {WITH_CONTROLS}",
            "lowest_url": None,
            "results_count": 3,
            "records": [],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            # color=True keeps escape sequences in the output, as when printing to a terminal.
            result = runner.invoke(cli, ["check", "1"], color=True)

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        assert (
            f"  Sony WH-1000XM5: $279.99 (Shop {CLEANED} via bing_shopping) — 3 result(s)\n"
            in result.output
        )

    def test_check_shows_engine_errors_instead_of_no_results(self, runner: CliRunner):
        """A search that failed is not reported as "no results found" (#8); exit code stays 0."""
        check_result = {
            "item_id": 1,
            "item_name": "Sony WH-1000XM5",
            "lowest_price": None,
            "lowest_source": None,
            "lowest_url": None,
            "results_count": 0,
            "records": [],
            "engine_errors": [
                "google_shopping: HTTP 401, SerpAPI rejected the API key: Invalid API key.",
                "bing_shopping: request failed (ConnectionError)",
            ],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", "1"])

        assert result.exit_code == 0
        assert result.output == (
            "  Sony WH-1000XM5: no prices, 2 engine error(s)\n"
            "    engine error: google_shopping: HTTP 401, SerpAPI rejected the API key:"
            " Invalid API key.\n"
            "    engine error: bing_shopping: request failed (ConnectionError)\n"
        )

    def test_check_shows_engine_errors_next_to_a_price(self, runner: CliRunner):
        """An engine error shows under its item, also when the other engine found a price."""
        check_result = {
            "item_id": 1,
            "item_name": "Item A",
            "lowest_price": 10.00,
            "lowest_source": "bing_shopping",
            "lowest_url": "https://example.com/item-a",
            "results_count": 1,
            "records": [],
            "engine_errors": ["google_shopping: HTTP 429, SerpAPI searches used up"],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            result = runner.invoke(cli, ["check", "1"])

        assert result.exit_code == 0
        assert result.output == (
            "  Item A: $10.00 (bing_shopping) — 1 result(s)\n"
            "    -> https://example.com/item-a\n"
            "    engine error: google_shopping: HTTP 429, SerpAPI searches used up\n"
        )

    def test_check_strips_control_characters_from_engine_errors(self, runner: CliRunner):
        """SerpAPI's error string is third-party text."""
        check_result = {
            "item_id": 1,
            "item_name": "Sony WH-1000XM5",
            "lowest_price": 279.99,
            "lowest_source": "bing_shopping",
            "lowest_url": None,
            "results_count": 3,
            "records": [],
            "engine_errors": [
                f"google_shopping: HTTP 401, SerpAPI rejected the API key: {WITH_CONTROLS}"
            ],
        }
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_result)
            # color=True keeps escape sequences in the output, as when printing to a terminal.
            result = runner.invoke(cli, ["check", "1"], color=True)

        assert result.exit_code == 0
        assert _control_characters(result.output) == set()
        assert (
            f"    engine error: google_shopping: HTTP 401, SerpAPI rejected the API key: {CLEANED}"
            "\n" in result.output
        )

    def test_check_not_found(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            result = runner.invoke(cli, ["check", "999"])

        assert result.exit_code == 1
        assert "not found" in result.output


class TestRequestErrors:
    """#6: the CLI's own request failures end with a message and exit 1, not a traceback."""

    def test_timeout_prints_a_clear_error(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request", side_effect=httpx.ReadTimeout("timed out")):
            result = runner.invoke(cli, ["check", "1"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert result.output == (
            "Error: no answer from http://localhost:8000/api/v1/prices/check/1 within 60 s\n"
            "The server may still finish the request. Use --timeout to wait longer.\n"
        )

    def test_other_transport_error_prints_a_clear_error(self, runner: CliRunner):
        error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
        with patch("hunter_bargain.cli.httpx.request", side_effect=error):
            result = runner.invoke(cli, ["ls"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert result.output == (
            "Error: request to http://localhost:8000/api/v1/items/ failed (RemoteProtocolError)\n"
        )

    def test_default_timeout_covers_a_check_of_both_engines(self, runner: CliRunner):
        """The server allows each of the two engines 20 s, so 30 s could cut off a slow check."""
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["ls"])

        assert result.exit_code == 0
        assert mock_req.call_args.kwargs["timeout"] == 60

    def test_timeout_option_sets_the_request_timeout(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["--timeout", "120", "ls"])

        assert result.exit_code == 0
        assert mock_req.call_args.kwargs["timeout"] == 120


class TestCustomUrl:
    def test_custom_url_option(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["--url", "http://myserver:9000", "ls"])

        assert result.exit_code == 0
        mock_req.assert_called_once()
        called_url = mock_req.call_args[0][1]
        assert called_url == "http://myserver:9000/api/v1/items/"

    def test_custom_url_from_env(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["ls"], env={"HUNTER_BARGAIN_URL": "http://remote:5000"})

        assert result.exit_code == 0
        called_url = mock_req.call_args[0][1]
        assert called_url == "http://remote:5000/api/v1/items/"
