"""
Session persistence.

Stores and retrieves a Playwright ``storage_state`` so the orchestrator can skip
the slow login when a valid session exists. The store is abstracted so the same
orchestrator works locally (file on disk) or on Cloud Run (file hydrated from
GCS). Multi-user later: key sessions per (user, provider).
"""

from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


class SessionStore(ABC):
    """Abstract store for a single session's storage_state JSON."""

    @abstractmethod
    def load(self) -> Optional[str]:
        """Return a local filesystem path to a valid storage_state file, or None."""
        raise NotImplementedError

    @abstractmethod
    def save(self, local_path: str) -> None:
        """Persist the storage_state file currently at local_path."""
        raise NotImplementedError


class LocalFileSessionStore(SessionStore):
    """Keeps the session as a file on the local filesystem (dev / VPS)."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Optional[str]:
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            return self.path
        return None

    def save(self, local_path: str) -> None:
        if local_path != self.path:
            import shutil
            shutil.copyfile(local_path, self.path)


class GCSSessionStore(SessionStore):
    """
    Keeps the session in a GCS object, hydrating to / from a local cache path.
    Used on Cloud Run where the filesystem is ephemeral between invocations.
    """

    def __init__(self, bucket: str, object_name: str, local_path: str):
        self.bucket = bucket
        self.object_name = object_name
        self.local_path = local_path

    def load(self) -> Optional[str]:
        try:
            from google.cloud import storage
            client = storage.Client()
            blob = client.bucket(self.bucket).blob(self.object_name)
            if blob.exists():
                blob.download_to_filename(self.local_path)
                log.info(f"Loaded session from gs://{self.bucket}/{self.object_name}")
                return self.local_path
            log.info("No saved session object in GCS yet")
        except Exception as e:
            log.warning(f"Could not load session from GCS: {e}")
        return None

    def save(self, local_path: str) -> None:
        try:
            from google.cloud import storage
            client = storage.Client()
            blob = client.bucket(self.bucket).blob(self.object_name)
            blob.upload_from_filename(local_path)
            log.info(f"Saved session to gs://{self.bucket}/{self.object_name}")
        except Exception as e:
            log.warning(f"Could not save session to GCS: {e}")


def build_session_store_from_env(local_path: str) -> SessionStore:
    """Choose a store based on environment (GCS if a bucket is configured)."""
    bucket = os.environ.get("GCS_BUCKET", "")
    if bucket:
        obj = os.environ.get("GCS_SESSION_OBJECT", "flow_session.json")
        return GCSSessionStore(bucket, obj, local_path)
    return LocalFileSessionStore(local_path)
