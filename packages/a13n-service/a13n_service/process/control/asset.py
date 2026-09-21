"""Asset control-plane construction."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.cleanup import AssetCleanupReconciler
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.staging import AssetStaging
from a13n_service.assets.uploads import AssetUploadService
from a13n_service.process.background import BackgroundTask, periodic_task
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


@dataclass(frozen=True, slots=True)
class _AssetBundle:
    catalog: AssetCatalog
    uploads: AssetUploadService
    cleanup_task: BackgroundTask


async def build_asset_bundle(
    settings: Settings,
    shared: SharedRuntime,
) -> _AssetBundle:
    """Construct Asset storage services and cleanup reconciliation."""

    staging = await AssetStaging.create(shared.storage.files_root, limiter=shared.storage.file_limiter)
    objects = AssetObjectStore(shared.storage.objects, staging)
    uploads = AssetUploadService(
        shared.storage.sessions,
        objects,
        staging,
        max_size_bytes=settings.assets.max_size_bytes,
    )
    cleanup = AssetCleanupReconciler(
        shared.storage.sessions,
        objects,
        lease_seconds=settings.assets.cleanup_lease_seconds,
        max_attempts=settings.assets.cleanup_max_attempts,
    )
    return _AssetBundle(
        catalog=AssetCatalog(shared.storage.sessions, objects),
        uploads=uploads,
        cleanup_task=periodic_task(
            "asset_content_cleanup",
            cleanup.scan,
            interval_seconds=settings.assets.cleanup_poll_interval_seconds,
            timeout_seconds=(settings.assets.cleanup_lease_seconds + 1) * 25,
            drain=cleanup.drain,
        ),
    )


__all__ = ["build_asset_bundle"]
