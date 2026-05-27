"""Tests for eBay Feed API client."""
import os
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest

from src.feed.feed_client import EbayFeedClient


@pytest.fixture
def client(tmp_path):
    return EbayFeedClient(
        get_token=lambda: "test_token",
        refresh_token=lambda: None,
        downloads_dir=str(tmp_path),
    )


class TestHeaders:
    def test_json_content_type(self, client):
        h = client._headers()
        assert h["Content-Type"] == "application/json"
        assert h["Authorization"] == "Bearer test_token"
        assert h["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_ES"

    def test_no_content_type(self, client):
        h = client._headers(content_type=None)
        assert "Content-Type" not in h


class TestCreateTask:
    @patch("src.feed.feed_client.requests.post")
    def test_extracts_task_id_from_location(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=201,
            headers={"Location": "https://api.ebay.com/sell/feed/v1/task/TASK-123"},
            text="",
        )
        mock_post.return_value.raise_for_status = MagicMock()

        task_id = client.create_task()
        assert task_id == "TASK-123"

    @patch("src.feed.feed_client.requests.post")
    def test_extracts_task_id_from_body(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=201,
            headers={},
            text='{"taskId": "BODY-456"}',
            json=lambda: {"taskId": "BODY-456"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        task_id = client.create_task()
        assert task_id == "BODY-456"

    @patch("src.feed.feed_client.requests.post")
    def test_raises_if_no_task_id(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=201,
            headers={},
            text="",
        )
        mock_post.return_value.raise_for_status = MagicMock()

        with pytest.raises(ValueError, match="No taskId"):
            client.create_task()

    @patch("src.feed.feed_client.requests.post")
    def test_custom_feed_type(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=201,
            headers={"Location": "https://api.ebay.com/sell/feed/v1/task/T1"},
            text="",
        )
        mock_post.return_value.raise_for_status = MagicMock()

        client.create_task(feed_type="LMS_ORDER_REPORT")
        payload = mock_post.call_args[1]["json"]
        assert payload["feedType"] == "LMS_ORDER_REPORT"

    @patch("src.feed.feed_client.requests.post")
    def test_401_retry(self, mock_post, client):
        refreshed = []
        client._refresh_token = lambda: refreshed.append(True)

        first = MagicMock(status_code=401, headers={}, text="")
        second = MagicMock(
            status_code=201,
            headers={"Location": "/task/RETRY-1"},
            text="",
        )
        second.raise_for_status = MagicMock()
        mock_post.side_effect = [first, second]

        task_id = client.create_task()
        assert task_id == "RETRY-1"
        assert len(refreshed) == 1


class TestUploadFile:
    @patch("src.feed.feed_client.requests.post")
    def test_upload_sends_file(self, mock_post, client, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("Action,SKU\nAdd,ITEM1\n")

        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        client.upload_file("TASK-1", str(csv_file))
        assert mock_post.called
        call_kwargs = mock_post.call_args
        assert "files" in call_kwargs[1]


class TestWaitForCompletion:
    @patch("src.feed.feed_client.time.sleep")
    @patch("src.feed.feed_client.requests.get")
    def test_completed(self, mock_get, mock_sleep, client):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "COMPLETED",
                "uploadSummary": {"successCount": 5, "failureCount": 0},
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()

        info = client.wait_for_completion("T1")
        assert info["status"] == "COMPLETED"

    @patch("src.feed.feed_client.time.sleep")
    @patch("src.feed.feed_client.requests.get")
    def test_completed_with_error(self, mock_get, mock_sleep, client):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "COMPLETED_WITH_ERROR",
                "uploadSummary": {"successCount": 3, "failureCount": 2},
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()

        info = client.wait_for_completion("T1")
        assert info["status"] == "COMPLETED_WITH_ERROR"

    @patch("src.feed.feed_client.time.sleep")
    @patch("src.feed.feed_client.requests.get")
    def test_failed_raises(self, mock_get, mock_sleep, client):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "FAILED"},
        )
        mock_get.return_value.raise_for_status = MagicMock()

        with pytest.raises(RuntimeError, match="failed"):
            client.wait_for_completion("T1")

    @patch("src.feed.feed_client.time.sleep")
    @patch("src.feed.feed_client.requests.get")
    def test_timeout_raises(self, mock_get, mock_sleep, client):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "IN_PROGRESS"},
        )
        mock_get.return_value.raise_for_status = MagicMock()

        with pytest.raises(TimeoutError, match="did not complete"):
            client.wait_for_completion("T1", poll_interval=0, max_wait=0)

    @patch("src.feed.feed_client.time.sleep")
    @patch("src.feed.feed_client.requests.get")
    def test_polls_until_done(self, mock_get, mock_sleep, client):
        responses = [
            MagicMock(status_code=200, json=lambda: {"status": "CREATED"}),
            MagicMock(status_code=200, json=lambda: {"status": "IN_PROGRESS"}),
            MagicMock(status_code=200, json=lambda: {
                "status": "COMPLETED",
                "uploadSummary": {"successCount": 1, "failureCount": 0},
            }),
        ]
        for r in responses:
            r.raise_for_status = MagicMock()
        mock_get.side_effect = responses

        info = client.wait_for_completion("T1", poll_interval=0)
        assert info["status"] == "COMPLETED"
        assert mock_sleep.call_count >= 2


class TestDownloadResultFile:
    @patch("src.feed.feed_client.requests.get")
    def test_saves_file(self, mock_get, client, tmp_path):
        csv_content = b"ItemID,Status\n123,Success\n"
        mock_get.return_value = MagicMock(
            status_code=200, content=csv_content,
        )
        mock_get.return_value.raise_for_status = MagicMock()

        path = client.download_result_file("TASK-ABC")
        assert os.path.exists(path)
        assert "TASK-ABC"[:8] in path
        with open(path, "rb") as f:
            assert f.read() == csv_content


class TestUploadAndWait:
    @patch("src.feed.feed_client.requests.get")
    @patch("src.feed.feed_client.requests.post")
    def test_full_flow(self, mock_post, mock_get, client, tmp_path):
        csv_file = tmp_path / "items.csv"
        csv_file.write_text("data")

        mock_post.return_value = MagicMock(
            status_code=201,
            headers={"Location": "/task/FLOW-1"},
            text="",
        )
        mock_post.return_value.raise_for_status = MagicMock()

        status_resp = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "COMPLETED",
                "uploadSummary": {"successCount": 1, "failureCount": 0},
            },
        )
        status_resp.raise_for_status = MagicMock()

        download_resp = MagicMock(status_code=200, content=b"response_data")
        download_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [status_resp, download_resp]

        info, result_path = client.upload_and_wait(str(csv_file))
        assert info["status"] == "COMPLETED"
        assert result_path != ""
