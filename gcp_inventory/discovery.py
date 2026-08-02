"""Project discovery and Cloud Asset Inventory collection."""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List

from google.cloud import asset_v1

from .helpers import basename, execute, key_value_string, parse_project_number


def discover_projects(scope: str, crm, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Recursively discover active projects below an org/folder, or return one project."""
    if scope.startswith("projects/"):
        project_ref = scope.split("/", 1)[1]
        try:
            # projects.get expects projects/PROJECT_NUMBER, but many environments also
            # resolve a project ID. If it does not, retain a minimal record.
            project = execute(crm.projects().get(name=scope))
            return [{
                "Project ID": project.get("projectId", project_ref),
                "Project Number": basename(project.get("name", "")),
                "Display Name": project.get("displayName", ""),
                "Parent": project.get("parent", ""),
                "State": project.get("state", "ACTIVE"),
                "Labels": key_value_string(project.get("labels", {})),
            }]
        except Exception as ex:
            errors.append({"Area": "Project discovery", "Project": project_ref, "Error": str(ex)})
            return [{
                "Project ID": project_ref,
                "Project Number": "",
                "Display Name": "",
                "Parent": "",
                "State": "UNKNOWN",
                "Labels": "",
            }]

    if not (scope.startswith("organizations/") or scope.startswith("folders/")):
        raise ValueError("--scope must be organizations/ID, folders/ID, or projects/ID")

    projects: List[Dict[str, Any]] = []
    queue: deque[str] = deque([scope])
    visited: set[str] = set()

    while queue:
        parent = queue.popleft()
        if parent in visited:
            continue
        visited.add(parent)

        # Direct child projects.
        token = None
        while True:
            try:
                kwargs = {"parent": parent, "pageSize": 1000, "showDeleted": False}
                if token:
                    kwargs["pageToken"] = token
                response = execute(crm.projects().list(**kwargs))
                for project in response.get("projects", []):
                    if project.get("state") == "DELETE_REQUESTED":
                        continue
                    projects.append({
                        "Project ID": project.get("projectId", ""),
                        "Project Number": basename(project.get("name", "")),
                        "Display Name": project.get("displayName", ""),
                        "Parent": project.get("parent", parent),
                        "State": project.get("state", ""),
                        "Labels": key_value_string(project.get("labels", {})),
                    })
                token = response.get("nextPageToken")
                if not token:
                    break
            except Exception as ex:
                errors.append({"Area": "Project discovery", "Project": parent, "Error": str(ex)})
                break

        # Child folders for recursion.
        token = None
        while True:
            try:
                kwargs = {"parent": parent, "pageSize": 1000, "showDeleted": False}
                if token:
                    kwargs["pageToken"] = token
                response = execute(crm.folders().list(**kwargs))
                for folder in response.get("folders", []):
                    if folder.get("state") != "DELETE_REQUESTED" and folder.get("name"):
                        queue.append(folder["name"])
                token = response.get("nextPageToken")
                if not token:
                    break
            except Exception as ex:
                errors.append({"Area": "Folder discovery", "Project": parent, "Error": str(ex)})
                break

    # De-duplicate by project ID while preserving the most complete record.
    unique: Dict[str, Dict[str, Any]] = {}
    for project in projects:
        pid = project.get("Project ID")
        if pid:
            unique[pid] = project
    return sorted(unique.values(), key=lambda row: row.get("Project ID", ""))


def list_assets(scope: str, project_number_to_id: Dict[str, str]) -> List[Dict[str, Any]]:
    client = asset_v1.AssetServiceClient()
    request = asset_v1.ListAssetsRequest(
        parent=scope,
        content_type=asset_v1.ContentType.RESOURCE,
        page_size=1000,
    )

    rows: List[Dict[str, Any]] = []
    for asset in client.list_assets(request=request):
        ancestors = list(asset.ancestors)
        project_number = parse_project_number(ancestors)
        resource = asset.resource
        rows.append({
            "Project ID": project_number_to_id.get(project_number, ""),
            "Project Number": project_number,
            "Asset Type": asset.asset_type,
            "Name": asset.name,
            "Display Name": getattr(resource, "discovery_name", ""),
            "Location": getattr(resource, "location", ""),
            "Version": getattr(resource, "version", ""),
            "Resource URL": getattr(resource, "discovery_document_uri", ""),
            "Ancestors": ";".join(ancestors),
        })
    return rows
