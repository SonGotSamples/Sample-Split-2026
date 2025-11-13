import threading
from typing import Dict, Optional, Any

_progress_store: Dict[str, Any] = {}
_lock = threading.Lock()

def set_progress(session_id: str, data: Dict[str, Any]) -> None:
    """Store or update progress data for a session."""
    with _lock:
        _progress_store[session_id] = data

def get_progress(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve progress data for a session."""
    with _lock:
        return _progress_store.get(session_id)

def delete_progress(session_id: str) -> None:
    """Delete progress data for a session."""
    with _lock:
        _progress_store.pop(session_id, None)

def clear_all_progress() -> None:
    """Clear all progress data."""
    with _lock:
        _progress_store.clear()
