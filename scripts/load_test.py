"""Locust load test simulating ~1000 events/sec against the batch ingestion endpoint.

Usage:
    export API_KEY="pb_live_your_actual_key_here"
    locust -f scripts/load_test.py --host http://localhost:8000 --headless -u 25 -r 5 -t 1m
"""

import os
import random
import uuid
from datetime import datetime, timezone
from locust import HttpUser, between, task


EVENT_NAMES = [
    "page_view",
    "button_click",
    "signup",
    "login",
    "search",
    "add_to_cart",
    "checkout_started",
    "payment_success",
    "feature_flag_evaluated",
    "dashboard_viewed",
]

PATHS = ["/home", "/pricing", "/dashboard", "/settings", "/docs", "/checkout", "/products"]
BROWSERS = ["Chrome", "Firefox", "Safari", "Edge"]
OS_LIST = ["macOS", "Windows", "Linux", "iOS", "Android"]


def generate_event() -> dict:
    """Generate a single realistic analytics event."""
    return {
        "event_name": random.choice(EVENT_NAMES),
        "distinct_id": f"usr_{random.randint(1000, 99999)}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "properties": {
            "path": random.choice(PATHS),
            "browser": random.choice(BROWSERS),
            "os": random.choice(OS_LIST),
            "session_id": str(uuid.uuid4())[:8],
            "latency_ms": random.randint(12, 450),
        },
    }


class AnalyticsIngestionUser(HttpUser):
    """Simulated client streaming batch events."""

    # 25 users sending batches of 40 events every ~1 second yields ~1000 events/second
    wait_time = between(0.8, 1.2)

    def on_start(self) -> None:
        """Initialize user state and API key."""
        self.api_key = os.getenv("API_KEY", "pb_live_load_test_key")
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    @task(5)
    def ingest_batch_events(self) -> None:
        """Send a batch of 40 events."""
        batch_size = 40
        events = [generate_event() for _ in range(batch_size)]
        payload = {"events": events}

        with self.client.post(
            "/v1/events/batch",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /v1/events/batch [40 events]",
        ) as response:
            if response.status_code == 202:
                response.success()
            else:
                response.failure(f"Status {response.status_code}: {response.text}")

    @task(1)
    def ingest_single_event(self) -> None:
        """Send an individual event."""
        event = generate_event()

        with self.client.post(
            "/v1/events",
            json=event,
            headers=self.headers,
            catch_response=True,
            name="POST /v1/events [single]",
        ) as response:
            if response.status_code == 202:
                response.success()
            else:
                response.failure(f"Status {response.status_code}: {response.text}")
