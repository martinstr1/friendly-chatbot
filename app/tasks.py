from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
import json
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2
from dateutil import tz as dtz
from .config import Settings


def _client() -> tasks_v2.CloudTasksClient:
    return tasks_v2.CloudTasksClient()


def _queue_path() -> str:
    return _client().queue_path(Settings.PROJECT_ID, Settings.TASKS_REGION, "reminders")


def schedule_reminders(base_url: str, chat_id: int, event: Dict[str, Any]) -> List[str]:
    start = datetime.fromisoformat(event["start"])  # naive/offset-aware supported
    local_tz = dtz.gettz(Settings.TIMEZONE)
    local_start = start.astimezone(local_tz)

    day_before_local = local_start.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    one_hour_local = local_start - timedelta(hours=1)

    names = []
    for kind, when_local in [("day_before", day_before_local), ("one_hour", one_hour_local)]:
        when_utc = when_local.astimezone(timezone.utc)
        names.append(_create_task(base_url, chat_id, kind, event, when_utc))
    return names


def _create_task(base_url: str, chat_id: int, kind: str, event: Dict[str, Any], when: datetime) -> str:
    payload = json.dumps({"chat_id": chat_id, "type": kind, "event": event}).encode()
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{base_url}/tasks/send-reminder",
            "headers": {"Content-Type": "application/json"},
            "oidc_token": {"service_account_email": f"run-friendly@{Settings.PROJECT_ID}.iam.gserviceaccount.com"},
        },
        "schedule_time": _to_proto_ts(when),
    }
    task["http_request"]["body"] = payload
    response = _client().create_task(parent=_queue_path(), task=task)
    return response.name


def _to_proto_ts(dt: datetime) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(dt)
    return ts


def delete_tasks(names: List[str]) -> None:
    for n in names:
        try:
            _client().delete_task(name=n)
        except Exception:
            pass
