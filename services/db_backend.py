"""Abstract database backend interface.

Defines the contract that both SQLite and PostgreSQL backends implement.
This allows the application to switch database backends via configuration.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List


class DatabaseBackend(ABC):
    """Abstract interface for application database backends."""

    # ── Configuration ────────────────────────────────────────

    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def set_config(self, key: str, value: Any):
        ...

    @abstractmethod
    def get_all_config(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def delete_config(self, key: str):
        ...

    # ── Users ────────────────────────────────────────────────

    @abstractmethod
    def upsert_user(self, user_id: str, display_name: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_users(self) -> List[Dict[str, Any]]:
        ...

    # ── Collections ──────────────────────────────────────────

    @abstractmethod
    def create_collection(
        self,
        name: str,
        description: str = "",
        color: str = "#3b82f6",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        embedding_model: str = None,
        owner_id: str = "default",
    ) -> str:
        ...

    @abstractmethod
    def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_collections(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def update_collection(
        self,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        embedding_model: Optional[str] = None,
        guide: Optional[str] = None,
    ):
        ...

    @abstractmethod
    def delete_collection(self, collection_id: str) -> bool:
        ...

    # ── Collection Documents ─────────────────────────────────

    @abstractmethod
    def add_document_to_collection(self, collection_id: str, document_id: str):
        ...

    @abstractmethod
    def remove_document_from_collection(self, collection_id: str, document_id: str):
        ...

    @abstractmethod
    def get_collection_documents(self, collection_id: str) -> List[str]:
        ...

    @abstractmethod
    def get_document_collections(self, document_id: str) -> List[str]:
        ...

    # ── Collection Sharing ───────────────────────────────────

    @abstractmethod
    def create_share(
        self,
        collection_id: str,
        owner_id: str,
        permission: str = "read",
        expires_at: Optional[str] = None,
    ) -> str:
        ...

    @abstractmethod
    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_shares_for_collection(self, collection_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def accept_share(self, share_id: str, user_id: str):
        ...

    @abstractmethod
    def get_shared_collections(self, user_id: str) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def revoke_share(self, share_id: str):
        ...

    @abstractmethod
    def check_share_access(self, collection_id: str, user_id: str) -> Optional[str]:
        """Check if user has share access to a collection.

        Returns:
            Permission level ('read' or 'readwrite') or None if no access.
        """
        ...

    # ── Upload Jobs ──────────────────────────────────────────

    @abstractmethod
    def create_upload_job(self, collection_id: str, total_files: int, job_type: str = "upload") -> int:
        ...

    @abstractmethod
    def update_upload_job(self, job_id: int, **kwargs):
        ...

    @abstractmethod
    def get_upload_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_active_upload_job(self, collection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_all_active_upload_jobs(self) -> List[Dict[str, Any]]:
        ...

    # ── Reindex Jobs ─────────────────────────────────────────

    @abstractmethod
    def create_reindex_job(self, config_snapshot: Dict[str, Any]) -> int:
        ...

    @abstractmethod
    def update_reindex_job(self, job_id: int, **kwargs):
        ...

    @abstractmethod
    def get_reindex_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_latest_reindex_job(self) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_active_reindex_job(self) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def mark_stale_jobs_as_orphaned(
        self, reason: str = "Orphaned: backend restarted before job completed"
    ) -> Dict[str, List[int]]:
        """Sweep stale pending/running upload_jobs + reindex_jobs at startup."""
        ...

    # ── AI Preferences ───────────────────────────────────────

    @abstractmethod
    def get_ai_preferences(self) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def set_ai_preferences(self, **kwargs):
        ...

    # ── Search History ───────────────────────────────────────

    @abstractmethod
    def add_search_history(self, query: str, top_k: int, results_count: int, **kwargs) -> int:
        ...

    @abstractmethod
    def get_search_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def delete_old_search_history(self, days: int = 30):
        ...

    # ── User Preferences ─────────────────────────────────────

    @abstractmethod
    def get_user_preference(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def set_user_preference(self, key: str, value: Any):
        ...

    @abstractmethod
    def get_all_user_preferences(self) -> Dict[str, Any]:
        ...

    # ── Agent API Keys ───────────────────────────────────────

    @abstractmethod
    def set_agent_api_key(self, provider: str, api_key: str):
        ...

    @abstractmethod
    def get_agent_api_key(self, provider: str) -> Optional[str]:
        ...

    @abstractmethod
    def delete_agent_api_key(self, provider: str):
        ...

    @abstractmethod
    def get_agent_config(self) -> Dict[str, Any]:
        ...

    # ── MCP Resources ────────────────────────────────────────

    @abstractmethod
    def get_all_mcp_resources(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_mcp_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def create_mcp_resource(self, resource_id: str, name: str, collection_id: str, repo_url: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    def update_mcp_resource(self, resource_id: str, name: Optional[str] = None, collection_id: Optional[str] = None, repo_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        ...

    @abstractmethod
    def delete_mcp_resource(self, resource_id: str) -> bool:
        ...
