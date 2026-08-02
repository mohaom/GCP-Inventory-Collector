"""General helper utilities shared across the inventory collectors."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Optional, Tuple

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def execute(request):
    """Execute a Google API request with a consistent user agent/error surface."""
    return request.execute(num_retries=3)


def basename(value: str) -> str:
    return value.rstrip("/").split("/")[-1] if value else ""


def extract_path_value(url: str, segment: str) -> str:
    if not url:
        return ""
    parts = url.rstrip("/").split("/")
    try:
        return parts[parts.index(segment) + 1]
    except (ValueError, IndexError):
        return ""


def zone_to_region(zone: str) -> str:
    return zone.rsplit("-", 1)[0] if zone and "-" in zone else ""


def compact_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def join_values(values: Iterable[Any], separator: str = ";") -> str:
    return separator.join(str(v) for v in values if v not in (None, ""))


def key_value_string(values: Dict[str, Any]) -> str:
    return ";".join(f"{k}={v}" for k, v in sorted(values.items()))


def metadata_to_dict(instance: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in instance.get("metadata", {}).get("items", []) or []:
        key = item.get("key")
        if key:
            result[key] = item.get("value", "")
    return result


def infer_os(source_image: str, licenses: Iterable[str], metadata: Dict[str, str]) -> str:
    text = " ".join([source_image or "", *list(licenses), *metadata.values()]).lower()
    checks = [
        ("windows", "Windows Server"),
        ("sql-server", "Windows Server / SQL Server"),
        ("ubuntu-pro", "Ubuntu Pro"),
        ("ubuntu", "Ubuntu"),
        ("debian", "Debian"),
        ("rhel", "Red Hat Enterprise Linux"),
        ("red-hat", "Red Hat Enterprise Linux"),
        ("rocky", "Rocky Linux"),
        ("centos", "CentOS"),
        ("sles", "SUSE Linux Enterprise"),
        ("suse", "SUSE Linux Enterprise"),
        ("cos-cloud", "Container-Optimized OS"),
        ("container-optimized", "Container-Optimized OS"),
        ("fedora", "Fedora"),
        ("oracle-linux", "Oracle Linux"),
    ]
    for token, name in checks:
        if token in text:
            return name
    return "Unknown"


def parse_project_number(ancestors: Iterable[str]) -> str:
    for ancestor in ancestors:
        if ancestor.startswith("projects/"):
            return ancestor.split("/", 1)[1]
    return ""


def parse_custom_machine_type(machine_type: str) -> Tuple[Optional[int], Optional[float]]:
    # Example: custom-4-8192 or custom-4-8192-ext (memory is MiB).
    match = re.match(r"custom-(\d+)-(\d+)", machine_type or "")
    if not match:
        return None, None
    return int(match.group(1)), round(int(match.group(2)) / 1024.0, 2)
