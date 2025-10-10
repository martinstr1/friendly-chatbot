from __future__ import annotations
from typing import Dict, Any, List
from google.cloud import firestore
from .config import Settings


def _client() -> firestore.Client:
    return firestore.Client(project=Settings.PROJECT_ID)


def _doc(chat_id: int):
    return _client().collection(Settings.FIRESTORE_COLLECTION).document(str(chat_id))


def append_message(chat_id: int, role: str, text: str, ts: int) -> None:
    ref = _doc(chat_id)
    data = {"messages": firestore.ArrayUnion([{ "role": role, "text": text, "ts": ts}])}
    ref.set(data, merge=True)
    snap = ref.get()
    if snap.exists:
        msgs: List[Dict[str, Any]] = snap.to_dict().get("messages", [])
        if len(msgs) > 50:
            msgs = msgs[-50:]
            ref.update({"messages": msgs})


def get_event(chat_id: int) -> Dict[str, Any] | None:
    snap = _doc(chat_id).get()
    if not snap.exists:
        return None
    return snap.to_dict().get("event")


def set_event(chat_id: int, event: Dict[str, Any] | None) -> None:
    if event is None:
        _doc(chat_id).set({"event": firestore.DELETE_FIELD}, merge=True)
    else:
        _doc(chat_id).set({"event": event}, merge=True)


def set_task_names(chat_id: int, names: List[str]) -> None:
    _doc(chat_id).set({"task_names": names}, merge=True)


def get_task_names(chat_id: int) -> List[str]:
    snap = _doc(chat_id).get()
    if not snap.exists:
        return []
    return snap.to_dict().get("task_names", [])
