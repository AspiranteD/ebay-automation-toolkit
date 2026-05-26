"""Tests for the eBay Feed API client."""

import time
from unittest.mock import MagicMock, Mock, patch, mock_open

import pytest

from src.feed.feed_client import EbayFeedClient, FeedApiError, TaskResult


@pytest.fixture
def mock_auth():
    auth = MagicMock()
    auth.get_valid_token.return_value = "test_token"
    auth.refresh_access_token.return_value = None
    return auth


@pytest.fixture
def client(mock_auth):
    return EbayFeedClient(auth_client=mock_auth, marketplace_id="EBAY_ES")


class TestCreateTask:
    def test_creates_task_and_returns_id(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={"Location": "https://api.ebay.com/sell/feed/v1/task/task-123"},
            )

            task_id = client.create_task("FX_LISTING")
            assert task_id == "task-123"

    def test_raises_when_no_location_header(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={},
            )

            with pytest.raises(FeedApiError, match="No task_id"):
                client.create_task()

    def test_sends_correct_feed_type(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=201,
                headers={"Location": "/task/t1"},
            )

            client.create_task("FX_LISTING")

            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["json"]["feedType"] == "FX_LISTING"


class TestUploadFile:
    def test_upload_file_success(self, client, tmp_path):
        csv_file = tmp_path / "listings.csv"
        csv_file.write_text("SKU,Title\nABC,Test Product")

        with patch.object(client.session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)

            client.upload_file("task-123", str(csv_file))
            mock_post.assert_called_once()

    def test_upload_file_not_found(self, client):
        with pytest.raises(FileNotFoundError, match="CSV file not found"):
            client.upload_file("task-123", "/nonexistent/file.csv")

    def test_upload_retries_on_401(self, client, tmp_path):
        csv_file = tmp_path / "listings.csv"
        csv_file.write_text("SKU,Title\nABC,Test")

        with patch.object(client.session, "post") as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=401),
                MagicMock(status_code=200),
            ]

            client.upload_file("task-123", str(csv_file))
            assert mock_post.call_count == 2
            client.auth.refresh_access_token.assert_called_once()


class TestGetTaskStatus:
    def test_returns_task_data(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=lambda: {"taskId": "t1", "status": "IN_PROCESS"},
            )

            result = client.get_task_status("t1")
            assert result["status"] == "IN_PROCESS"


class TestWaitForCompletion:
    @patch("src.feed.feed_client.time.sleep")
    def test_returns_on_completed(self, mock_sleep, client):
        with patch.object(client, "get_task_status") as mock_status:
            mock_status.side_effect = [
                {"status": "IN_PROCESS"},
                {"status": "IN_PROCESS"},
                {"status": "COMPLETED", "uploadSummary": {"successCount": 10}},
            ]

            result = client.wait_for_completion("t1", poll_interval=1, max_wait=60)

            assert result.status == "COMPLETED"
            assert result.upload_summary["successCount"] == 10

    @patch("src.feed.feed_client.time.sleep")
    def test_returns_on_failed(self, mock_sleep, client):
        with patch.object(client, "get_task_status") as mock_status:
            mock_status.return_value = {"status": "FAILED"}

            result = client.wait_for_completion("t1", poll_interval=1, max_wait=60)
            assert result.status == "FAILED"

    @patch("src.feed.feed_client.time.sleep")
    def test_returns_on_completed_with_error(self, mock_sleep, client):
        with patch.object(client, "get_task_status") as mock_status:
            mock_status.return_value = {"status": "COMPLETED_WITH_ERROR"}

            result = client.wait_for_completion("t1", poll_interval=1, max_wait=60)
            assert result.status == "COMPLETED_WITH_ERROR"

    @patch("src.feed.feed_client.time.sleep")
    def test_timeout_raises_error(self, mock_sleep, client):
        with patch.object(client, "get_task_status") as mock_status:
            mock_status.return_value = {"status": "IN_PROCESS"}

            with pytest.raises(FeedApiError, match="did not complete"):
                client.wait_for_completion("t1", poll_interval=1, max_wait=3)


class TestDownloadResultFile:
    def test_downloads_and_saves_file(self, client, tmp_path):
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                content=b"SKU,Result\nABC,SUCCESS",
            )

            path = client.download_result_file("t1", str(tmp_path))
            assert "result_t1.csv" in path
            assert (tmp_path / "result_t1.csv").exists()


class TestUploadAndWait:
    @patch("src.feed.feed_client.time.sleep")
    def test_full_flow(self, mock_sleep, client, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("data")

        with patch.object(client, "create_task", return_value="t1"), \
             patch.object(client, "upload_file"), \
             patch.object(client, "wait_for_completion") as mock_wait:
            mock_wait.return_value = TaskResult(task_id="t1", status="COMPLETED")

            result = client.upload_and_wait(str(csv_file))
            assert result.status == "COMPLETED"


class TestUploadMultiple:
    @patch("src.feed.feed_client.time.sleep")
    def test_processes_all_files(self, mock_sleep, client, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"file_{i}.csv"
            f.write_text("data")
            files.append(str(f))

        with patch.object(client, "upload_and_wait") as mock_upload:
            mock_upload.return_value = TaskResult(task_id="t", status="COMPLETED")

            results = client.upload_multiple(files)
            assert len(results) == 3
            assert mock_upload.call_count == 3


class TestAutoRetryOn401:
    def test_request_with_retry_refreshes_token(self, client):
        with patch.object(client.session, "request") as mock_req:
            mock_req.side_effect = [
                MagicMock(status_code=401),
                MagicMock(status_code=200, json=lambda: {"status": "ok"}),
            ]

            response = client._request_with_retry("GET", "https://example.com")
            assert response.json()["status"] == "ok"
            client.auth.refresh_access_token.assert_called_once()
