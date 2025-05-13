# novaguard-backend/app/common/__init__.py
from .github_client import GitHubAPIClient # Export client

__all__ = [
    "GitHubAPIClient",
]