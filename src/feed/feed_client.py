"""
eBay Feed API client for automated bulk listing operations.

Uses the Seller Hub feed type FX_LISTING to upload CSV files
(same format as File Exchange) via the Feed API.

Task lifecycle:
  1. createTask  -> get task_id (from Location header or response body)
  2. uploadFile  -> upload CSV as multipart/form-data
  3. getTask     -> poll until COMPLETED / COMPLETED_WITH_ERROR
  4. getResultFile -> download response CSV with ItemIDs and errors

Automatic 401 retry: if any call gets 401, the token is refreshed
and the call is retried once.
"""
import logging
import os
import time
from datetime import datetime
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ebay.com/sell/feed/v1"
MARKETPLACE_ID = "EBAY_ES"


class EbayFeedClient:
    """Client for eBay Sell Feed API (Seller Hub feed types)."""

    TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_ERROR"}
    FAILED_STATUSES = {"FAILED", "ABORTED"}

    def __init__(
        self,
        get_token: Callable[[], str],
        refresh_token: Callable[[], None],
        marketplace_id: str = MARKETPLACE_ID,
        downloads_dir: Optional[str] = None,
    ):
        """
        Args:
            get_token: Callable returning a valid access token.
            refresh_token: Callable that refreshes the access token.
            marketplace_id: eBay marketplace (default EBAY_ES).
            downloads_dir: Directory for result file downloads.
        """
        self._get_token = get_token
        self._refresh_token = refresh_token
        self._marketplace_id = marketplace_id
        self._downloads_dir = downloads_dir or os.path.join(
            os.path.expanduser("~"), "Downloads"
        )

    def _headers(self, content_type: str = "application/json") -> dict:
        h = {
            "Authorization": f"Bearer {self._get_token()}",
            "X-EBAY-C-MARKETPLACE-ID": self._marketplace_id,
        }
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _retry_on_401(self, method, *args, **kwargs):
        self._refresh_token()
        kwargs["headers"] = self._headers(
            kwargs.pop("_content_type", "application/json")
        )
        return method(*args, **kwargs)

    def create_task(self, feed_type: str = "FX_LISTING") -> str:
        """Create an upload task. Returns the task_id."""
        url = f"{BASE_URL}/task"
        payload = {"feedType": feed_type, "schemaVersion": "1.0"}

        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        if resp.status_code == 401:
            resp = self._retry_on_401(
                requests.post, url, json=payload, timeout=30
            )
        resp.raise_for_status()

        location = resp.headers.get("Location", "")
        task_id = location.rstrip("/").split("/")[-1] if location else ""

        if not task_id:
            data = resp.json() if resp.text else {}
            task_id = data.get("taskId", "")

        if not task_id:
            raise ValueError(
                f"No taskId returned. Status={resp.status_code}, "
                f"Headers={dict(resp.headers)}"
            )

        logger.info("Task created: %s (feedType=%s)", task_id, feed_type)
        return task_id

    def upload_file(self, task_id: str, csv_path: str):
        """Upload a CSV file to an existing task."""
        url = f"{BASE_URL}/task/{task_id}/upload_file"
        filename = os.path.basename(csv_path)

        with open(csv_path, "rb") as f:
            files = {"file": (filename, f, "text/csv")}
            resp = requests.post(
                url, files=files, headers=self._headers(content_type=None), timeout=120
            )

        if resp.status_code == 401:
            with open(csv_path, "rb") as f:
                files = {"file": (filename, f, "text/csv")}
                resp = self._retry_on_401(
                    requests.post, url, files=files, timeout=120,
                    _content_type=None,
                )

        resp.raise_for_status()
        logger.info("File uploaded: %s -> task %s", filename, task_id)

    def get_task_status(self, task_id: str) -> dict:
        """Get the current status of a task."""
        url = f"{BASE_URL}/task/{task_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 401:
            resp = self._retry_on_401(requests.get, url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(
        self, task_id: str, poll_interval: int = 15, max_wait: int = 600,
    ) -> dict:
        """Poll task status until completion. Returns final task info."""
        start = time.time()
        last_status = ""

        while time.time() - start < max_wait:
            info = self.get_task_status(task_id)
            status = info.get("status", "UNKNOWN")

            if status != last_status:
                elapsed = int(time.time() - start)
                logger.info("[%ds] Task %s -> %s", elapsed, task_id[:12], status)
                last_status = status

            if status in self.TERMINAL_STATUSES:
                summary = info.get("uploadSummary", {})
                logger.info(
                    "Task done: %d success, %d failures",
                    summary.get("successCount", 0),
                    summary.get("failureCount", 0),
                )
                return info

            if status in self.FAILED_STATUSES:
                raise RuntimeError(f"Task failed with status={status}")

            time.sleep(poll_interval)

        raise TimeoutError(f"Task {task_id} did not complete in {max_wait}s")

    def download_result_file(self, task_id: str) -> str:
        """Download the result CSV and save to downloads directory."""
        url = f"{BASE_URL}/task/{task_id}/download_result_file"
        resp = requests.get(
            url, headers=self._headers(content_type=None), timeout=120
        )
        if resp.status_code == 401:
            resp = self._retry_on_401(
                requests.get, url, timeout=120, _content_type=None,
            )
        resp.raise_for_status()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"ebay_api_response_{timestamp}_{task_id[:8]}.csv"
        filepath = os.path.join(self._downloads_dir, filename)

        os.makedirs(self._downloads_dir, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(resp.content)

        logger.info("Result file downloaded: %s", filepath)
        return filepath

    def upload_and_wait(
        self, csv_path: str, feed_type: str = "FX_LISTING",
    ) -> tuple[dict, str]:
        """
        Full flow: create task -> upload -> wait -> download result.
        Returns (task_info, result_file_path).
        """
        task_id = self.create_task(feed_type)
        self.upload_file(task_id, csv_path)
        task_info = self.wait_for_completion(task_id)

        result_path = ""
        try:
            result_path = self.download_result_file(task_id)
        except Exception as e:
            logger.warning("Could not download result file: %s", e)

        return task_info, result_path

    def upload_multiple(
        self, csv_paths: list[str], feed_type: str = "FX_LISTING",
    ) -> list[str]:
        """Upload multiple CSV files sequentially. Returns result file paths."""
        result_files = []
        for i, path in enumerate(csv_paths, 1):
            logger.info("Uploading file %d/%d: %s", i, len(csv_paths), path)
            _, result_path = self.upload_and_wait(path, feed_type)
            if result_path:
                result_files.append(result_path)
        return result_files
