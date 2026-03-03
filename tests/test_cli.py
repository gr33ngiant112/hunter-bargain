from __future__ import annotations

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


@pytest.fixture
def runner():
    return CliRunner()


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
        assert call_args[0] == ("POST", "http://localhost:8000/items/")
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

    def test_check_all(self, runner: CliRunner):
        check_results = [
            {
                "item_id": 1,
                "item_name": "Item A",
                "lowest_price": 10.00,
                "lowest_source": "Bing",
                "lowest_url": None,
                "results_count": 1,
                "records": [],
            },
            {
                "item_id": 2,
                "item_name": "Item B",
                "lowest_price": None,
                "lowest_source": None,
                "lowest_url": None,
                "results_count": 0,
                "records": [],
            },
        ]
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, check_results)
            result = runner.invoke(cli, ["check"])

        assert result.exit_code == 0
        assert "Item A: $10.00" in result.output
        assert "Item B: no results found" in result.output

    def test_check_not_found(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            result = runner.invoke(cli, ["check", "999"])

        assert result.exit_code == 1
        assert "not found" in result.output


class TestCustomUrl:
    def test_custom_url_option(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["--url", "http://myserver:9000", "ls"])

        assert result.exit_code == 0
        mock_req.assert_called_once()
        called_url = mock_req.call_args[0][1]
        assert called_url == "http://myserver:9000/items/"

    def test_custom_url_from_env(self, runner: CliRunner):
        with patch("hunter_bargain.cli.httpx.request") as mock_req:
            mock_req.return_value = _mock_response(200, [])
            result = runner.invoke(cli, ["ls"], env={"HUNTER_BARGAIN_URL": "http://remote:5000"})

        assert result.exit_code == 0
        called_url = mock_req.call_args[0][1]
        assert called_url == "http://remote:5000/items/"
