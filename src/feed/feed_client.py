"""
eBay Sell Feed API v1 client.

Manages the full lifecycle of bulk listing uploads: task creation,
file upload, status polling, and result download.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

FEED_API_BASE = "https://api.ebay.com/sell/feed/v1"


@dataclass
class TaskResult:
    task_id: str
    status: str
    upload_summary: Optional[dict] = None
    result_file_path: Optional[str] = None


class FeedApiError(Exception):
    """Raised when a Feed API operation fails."""

    def __init__(self, message: str, status_code: int = 0, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class EbayFeedClient:
    """Client for the eBay Sell Feed API v1 (bulk listing uploads)."""

    def __init__(self, auth_client, marketplace_id: Optional[str] = None):
        self.auth = auth_client
        self.marketplace_id = marketplace_id or os.getenv(
            "EBAY_MARKETPLACE_ID", "EBAY_ES"
        )
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth.get_valid_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _handle_response(self, response: requests.Response) -> requests.Response:
        if response.status_code == 401:
            self.auth.refresh_access_token()
            return None  # signal retry
        if response.status_code >= 400:
            raise FeedApiError(
                f"Feed API error: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Execute request with automatic 401 retry after token refresh."""
        kwargs.setdefault("headers", self._headers())
        response = self.session.request(method, url, **kwargs)

        if response.status_code == 401:
            self.auth.refresh_access_token()
            kwargs["headers"] = self._headers()
            response = self.session.request(method, url, **kwargs)

        if response.status_code >= 400:
            raise FeedApiError(
                f"Feed API error: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response

    def create_task(self, feed_type: str = "FX_LISTING") -> str:
        """Create a new upload task. Returns the task_id from the Location header."""
        url = f"{FEED_API_BASE}/task"
        payload = {
            "feedType": feed_type,
            "schemaVersion": "1.0",
        }
        response = self._request_with_retry("POST", url, json=payload)

        location = response.headers.get("Location", "")
        task_id = location.rstrip("/").split("/")[-1] if location else ""

        if not task_id:
            raise FeedApiError("No task_id returned in Location header")

        return task_id

    def upload_file(self, task_id: str, csv_path: str) -> None:
        """Upload a CSV file to an existing task."""
        url = f"{FEED_API_BASE}/task/{task_id}/upload_file"
        path = Path(csv_path)

        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        headers = {
            "Authorization": f"Bearer {self.auth.get_valid_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            "Content-Type": "multipart/form-data",
        }

        with open(path, "rb") as f:
            files = {"file": (path.name, f, "text/csv")}
            # Remove Content-Type so requests can set multipart boundary
            headers.pop("Content-Type", None)
            response = self.session.post(url, headers=headers, files=files)

        if response.status_code == 401:
            self.auth.refresh_access_token()
            headers["Authorization"] = f"Bearer {self.auth.get_valid_token()}"
            with open(path, "rb") as f:
                files = {"file": (path.name, f, "text/csv")}
                response = self.session.post(url, headers=headers, files=files)

        if response.status_code >= 400:
            raise FeedApiError(
                f"File upload failed: {response.status_code}",
                status_code=response.status_code,
                response_body=response.text,
            )

    def get_task_status(self, task_id: str) -> dict:
        """Get current task status and details."""
        url = f"{FEED_API_BASE}/task/{task_id}"
        response = self._request_with_retry("GET", url)
        return response.json()

    def wait_for_completion(
        self,
        task_id: str,
        poll_interval: int = 15,
        max_wait: int = 600,
    ) -> TaskResult:
        """Poll until the task reaches a terminal state."""
        terminal_statuses = {"COMPLETED", "COMPLETED_WITH_ERROR", "FAILED"}
        elapsed = 0

        while elapsed < max_wait:
            task_data = self.get_task_status(task_id)
            status = task_data.get("status", "UNKNOWN")

            if status in terminal_statuses:
                return TaskResult(
                    task_id=task_id,
                    status=status,
                    upload_summary=task_data.get("uploadSummary"),
                )

            time.sleep(poll_interval)
            elapsed += poll_interval

        raise FeedApiError(
            f"Task {task_id} did not complete within {max_wait}s (last status: {status})",
        )

    def download_result_file(self, task_id: str, output_dir: str = ".") -> str:
        """Download the result CSV from a completed task."""
        url = f"{FEED_API_BASE}/task/{task_id}/download_result_file"
        response = self._request_with_retry("GET", url)

        output_path = Path(output_dir) / f"result_{task_id}.csv"
        output_path.write_bytes(response.content)
        return str(output_path)

    def upload_and_wait(
        self,
        csv_path: str,
        feed_type: str = "FX_LISTING",
        poll_interval: int = 15,
        max_wait: int = 600,
    ) -> TaskResult:
        """Full flow: create task → upload file → wait for completion."""
        task_id = self.create_task(feed_type)
        self.upload_file(task_id, csv_path)
        result = self.wait_for_completion(task_id, poll_interval, max_wait)
        return result

    def upload_multiple(
        self,
        csv_paths: list[str],
        feed_type: str = "FX_LISTING",
        poll_interval: int = 15,
        max_wait: int = 600,
    ) -> list[TaskResult]:
        """Upload multiple CSV files sequentially, waiting for each to complete."""
        results = []
        for csv_path in csv_paths:
            result = self.upload_and_wait(csv_path, feed_type, poll_interval, max_wait)
            results.append(result)
        return results
