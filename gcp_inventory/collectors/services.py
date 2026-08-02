"""Cloud Storage, BigQuery, Cloud Logging, Backup/DR, NetApp, and VM Manager collectors."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from googleapiclient.errors import HttpError

from ..helpers import basename, execute, extract_path_value, join_values, key_value_string

BQ_MAX_TABLES_PER_DATASET = 1000


def list_storage_buckets(project: str, storage, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    token = None

    while True:
        try:
            kwargs: Dict[str, Any] = {"project": project, "projection": "full", "maxResults": 1000}
            if token:
                kwargs["pageToken"] = token
            response = execute(storage.buckets().list(**kwargs))
        except HttpError as ex:
            status = getattr(getattr(ex, "resp", None), "status", None)
            if status in (403, 404):
                errors.append({"Area": "Cloud Storage (skipped, API disabled or access denied)",
                               "Project": project, "Error": str(ex)})
            else:
                errors.append({"Area": "Cloud Storage buckets", "Project": project, "Error": str(ex)})
            break
        except Exception as ex:
            errors.append({"Area": "Cloud Storage buckets", "Project": project, "Error": str(ex)})
            break

        for bucket in response.get("items", []) or []:
            iam = bucket.get("iamConfiguration", {}) or {}
            ubla = iam.get("uniformBucketLevelAccess", {}) or {}
            retention = bucket.get("retentionPolicy", {}) or {}
            autoclass = bucket.get("autoclass", {}) or {}
            soft_delete = bucket.get("softDeletePolicy", {}) or {}
            lifecycle_rules = (bucket.get("lifecycle", {}) or {}).get("rule", []) or []
            rows.append({
                "Project": project,
                "Bucket": bucket.get("name", ""),
                "Location": bucket.get("location", ""),
                "Location Type": bucket.get("locationType", ""),
                "Storage Class": bucket.get("storageClass", ""),
                "Public Access Prevention": iam.get("publicAccessPrevention", ""),
                "Uniform Bucket-Level Access": ubla.get("enabled", ""),
                "Versioning Enabled": (bucket.get("versioning", {}) or {}).get("enabled", ""),
                "Lifecycle Rule Count": len(lifecycle_rules),
                "Retention Period (s)": retention.get("retentionPeriod", ""),
                "Retention Locked": retention.get("isLocked", ""),
                "Autoclass Enabled": autoclass.get("enabled", ""),
                "Autoclass Terminal Class": autoclass.get("terminalStorageClass", ""),
                "Soft Delete Retention (s)": soft_delete.get("retentionDurationSeconds", ""),
                "Default KMS Key": basename((bucket.get("encryption", {}) or {}).get("defaultKmsKeyName", "")),
                "Requester Pays": (bucket.get("billing", {}) or {}).get("requesterPays", ""),
                "RPO": bucket.get("rpo", ""),
                "Default Event-Based Hold": bucket.get("defaultEventBasedHold", ""),
                "Log Bucket": (bucket.get("logging", {}) or {}).get("logBucket", ""),
                "Created": bucket.get("timeCreated", ""),
                "Updated": bucket.get("updated", ""),
                "Labels": key_value_string(bucket.get("labels", {})),
                "Azure Target": "Azure Blob Storage / ADLS Gen2",
                "Azure Sizing Note": "Collect stored bytes, object count, and request/egress patterns (via Cloud Monitoring) before selecting tier and redundancy.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    return rows


def list_bigquery(project: str, bigquery, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    datasets: List[Dict[str, Any]] = []
    token = None

    while True:
        try:
            kwargs: Dict[str, Any] = {"projectId": project, "all": True, "maxResults": 1000}
            if token:
                kwargs["pageToken"] = token
            response = execute(bigquery.datasets().list(**kwargs))
        except HttpError as ex:
            status = getattr(getattr(ex, "resp", None), "status", None)
            if status in (403, 404):
                errors.append({"Area": "BigQuery (skipped, API disabled or access denied)",
                               "Project": project, "Error": str(ex)})
            else:
                errors.append({"Area": "BigQuery datasets", "Project": project, "Error": str(ex)})
            return rows
        except Exception as ex:
            errors.append({"Area": "BigQuery datasets", "Project": project, "Error": str(ex)})
            return rows

        datasets.extend(response.get("datasets", []) or [])
        token = response.get("nextPageToken")
        if not token:
            break

    for ds in datasets:
        dataset_id = (ds.get("datasetReference", {}) or {}).get("datasetId", "")
        if not dataset_id:
            continue

        detail: Dict[str, Any] = {}
        try:
            detail = execute(bigquery.datasets().get(projectId=project, datasetId=dataset_id))
        except Exception as ex:
            errors.append({"Area": "BigQuery dataset detail", "Project": project, "Error": f"{dataset_id}: {ex}"})

        total_bytes = 0
        long_term_bytes = 0
        total_rows = 0
        type_counts: Counter = Counter()
        inspected = 0
        size_complete = True
        table_token = None

        while True:
            try:
                t_kwargs: Dict[str, Any] = {"projectId": project, "datasetId": dataset_id, "maxResults": 1000}
                if table_token:
                    t_kwargs["pageToken"] = table_token
                t_response = execute(bigquery.tables().list(**t_kwargs))
            except Exception as ex:
                errors.append({"Area": "BigQuery tables", "Project": project, "Error": f"{dataset_id}: {ex}"})
                break

            for table in t_response.get("tables", []) or []:
                type_counts[table.get("type", "TABLE")] += 1
                table_id = (table.get("tableReference", {}) or {}).get("tableId", "")
                if inspected < BQ_MAX_TABLES_PER_DATASET and table_id:
                    try:
                        t_detail = execute(bigquery.tables().get(
                            projectId=project, datasetId=dataset_id, tableId=table_id))
                        total_bytes += int(t_detail.get("numBytes", 0) or 0)
                        long_term_bytes += int(t_detail.get("numLongTermBytes", 0) or 0)
                        total_rows += int(t_detail.get("numRows", 0) or 0)
                    except Exception:
                        size_complete = False
                    inspected += 1
                else:
                    size_complete = False

            table_token = t_response.get("nextPageToken")
            if not table_token:
                break

        access_entries = detail.get("access", []) or []
        rows.append({
            "Project": project,
            "Dataset": dataset_id,
            "Location": detail.get("location", ds.get("location", "")),
            "Description": detail.get("description", ""),
            "Table Count": sum(type_counts.values()),
            "Table Types": key_value_string(dict(type_counts)),
            "Total Logical Bytes": total_bytes,
            "Total Logical GiB": round(total_bytes / (1024 ** 3), 3) if total_bytes else 0,
            "Long-Term Bytes": long_term_bytes,
            "Total Rows": total_rows,
            "Size Complete": "Yes" if size_complete else "No (capped or partial)",
            "Default Table Expiration (ms)": detail.get("defaultTableExpirationMs", ""),
            "Default Partition Expiration (ms)": detail.get("defaultPartitionExpirationMs", ""),
            "Default KMS Key": basename((detail.get("defaultEncryptionConfiguration", {}) or {}).get("kmsKeyName", "")),
            "Access Entry Count": len(access_entries),
            "Created (epoch ms)": detail.get("creationTime", ""),
            "Last Modified (epoch ms)": detail.get("lastModifiedTime", ""),
            "Labels": key_value_string(detail.get("labels", ds.get("labels", {})) or {}),
            "Azure Target": "Microsoft Fabric / Azure Synapse / ADLS Gen2",
            "Azure Sizing Note": "Confirm slot/query patterns, pipelines, partitioning/clustering, and governance; logical bytes are a storage baseline only.",
        })

    return rows


def list_cloud_logging(project: str, logging_svc, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    # Log routing sinks.
    token = None
    while True:
        try:
            kwargs: Dict[str, Any] = {"parent": f"projects/{project}"}
            if token:
                kwargs["pageToken"] = token
            response = execute(logging_svc.sinks().list(**kwargs))
        except HttpError as ex:
            status = getattr(getattr(ex, "resp", None), "status", None)
            if status in (403, 404):
                errors.append({"Area": "Cloud Logging (skipped, API disabled or access denied)",
                               "Project": project, "Error": str(ex)})
            else:
                errors.append({"Area": "Cloud Logging sinks", "Project": project, "Error": str(ex)})
            break
        except Exception as ex:
            errors.append({"Area": "Cloud Logging sinks", "Project": project, "Error": str(ex)})
            break

        for sink in response.get("sinks", []) or []:
            rows.append({
                "Project": project,
                "Resource Type": "Log Sink",
                "Name": basename(sink.get("name", "")),
                "Location": "global",
                "Destination": sink.get("destination", ""),
                "Filter": sink.get("filter", ""),
                "Disabled/Locked": sink.get("disabled", False),
                "Retention Days": "",
                "Analytics Enabled": "",
                "Default KMS Key": "",
                "Description": sink.get("description", ""),
                "Created": sink.get("createTime", ""),
                "Azure Target": "Azure Monitor / Log Analytics / Event Hubs",
                "Azure Sizing Note": "Recreate routing to Log Analytics/Event Hubs; capture filter, destination, and volume.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    # Log storage buckets.
    token = None
    while True:
        try:
            kwargs = {"parent": f"projects/{project}/locations/-"}
            if token:
                kwargs["pageToken"] = token
            response = execute(logging_svc.projects().locations().buckets().list(**kwargs))
        except Exception as ex:
            errors.append({"Area": "Cloud Logging buckets", "Project": project, "Error": str(ex)})
            break

        for bucket in response.get("buckets", []) or []:
            name = bucket.get("name", "")
            rows.append({
                "Project": project,
                "Resource Type": "Log Bucket",
                "Name": basename(name),
                "Location": extract_path_value(name, "locations"),
                "Destination": "",
                "Filter": "",
                "Disabled/Locked": bucket.get("locked", False),
                "Retention Days": bucket.get("retentionDays", ""),
                "Analytics Enabled": bucket.get("analyticsEnabled", ""),
                "Default KMS Key": basename((bucket.get("cmekSettings", {}) or {}).get("kmsKeyName", "")),
                "Description": bucket.get("description", ""),
                "Created": bucket.get("createTime", ""),
                "Azure Target": "Azure Monitor Log Analytics workspace",
                "Azure Sizing Note": "Map retention, analytics, and CMEK to a Log Analytics workspace; capture ingestion volume.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    return rows


def list_backup_dr(project: str, backupdr, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    parent = f"projects/{project}/locations/-"

    def paged(resource_getter, key: str, area: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            resource = resource_getter()
        except AttributeError as ex:
            errors.append({"Area": f"{area} (not available in API version)", "Project": project, "Error": str(ex)})
            return results

        token = None
        while True:
            try:
                kwargs: Dict[str, Any] = {"parent": parent}
                if token:
                    kwargs["pageToken"] = token
                response = execute(resource.list(**kwargs))
            except HttpError as ex:
                status = getattr(getattr(ex, "resp", None), "status", None)
                if status in (403, 404):
                    errors.append({"Area": f"{area} (skipped, API disabled or access denied)",
                                   "Project": project, "Error": str(ex)})
                else:
                    errors.append({"Area": area, "Project": project, "Error": str(ex)})
                break
            except Exception as ex:
                errors.append({"Area": area, "Project": project, "Error": str(ex)})
                break
            results.extend(response.get(key, []) or [])
            token = response.get("nextPageToken")
            if not token:
                break
        return results

    for server in paged(lambda: backupdr.projects().locations().managementServers(),
                        "managementServers", "Backup and DR management servers"):
        name = server.get("name", "")
        rows.append({
            "Project": project,
            "Resource Type": "Management Server",
            "Name": basename(name),
            "Location": extract_path_value(name, "locations"),
            "State": server.get("state", ""),
            "Server/Plan Type": server.get("type", ""),
            "Backup Vault": "",
            "Retention": "",
            "Stored Bytes": "",
            "Backup Count": "",
            "Protected Resource Type": "",
            "Networks": join_values(n.get("network", "") for n in server.get("networks", []) or []),
            "Created": server.get("createTime", ""),
            "Azure Target": "Azure Backup / Azure Site Recovery",
            "Azure Sizing Note": "Map protected workloads, retention, RPO/RTO, and backup frequency to Recovery Services vault policies.",
        })

    for vault in paged(lambda: backupdr.projects().locations().backupVaults(),
                       "backupVaults", "Backup and DR backup vaults"):
        name = vault.get("name", "")
        rows.append({
            "Project": project,
            "Resource Type": "Backup Vault",
            "Name": basename(name),
            "Location": extract_path_value(name, "locations"),
            "State": vault.get("state", ""),
            "Server/Plan Type": "",
            "Backup Vault": basename(name),
            "Retention": vault.get("backupMinimumEnforcedRetentionDuration", ""),
            "Stored Bytes": vault.get("totalStoredBytes", ""),
            "Backup Count": vault.get("backupCount", ""),
            "Protected Resource Type": "",
            "Networks": "",
            "Created": vault.get("createTime", ""),
            "Azure Target": "Azure Backup vault / Recovery Services vault",
            "Azure Sizing Note": "Map enforced retention, stored bytes, and immutability to Azure Backup vault policies.",
        })

    for plan in paged(lambda: backupdr.projects().locations().backupPlans(),
                      "backupPlans", "Backup and DR backup plans"):
        name = plan.get("name", "")
        rows.append({
            "Project": project,
            "Resource Type": "Backup Plan",
            "Name": basename(name),
            "Location": extract_path_value(name, "locations"),
            "State": plan.get("state", ""),
            "Server/Plan Type": "",
            "Backup Vault": basename(plan.get("backupVault", "")),
            "Retention": "",
            "Stored Bytes": "",
            "Backup Count": "",
            "Protected Resource Type": plan.get("resourceType", ""),
            "Networks": "",
            "Created": plan.get("createTime", ""),
            "Azure Target": "Azure Backup policy",
            "Azure Sizing Note": f"Recreate backup rules ({len(plan.get('backupRules', []) or [])} rule(s)) as Azure Backup policy schedules and retention.",
        })

    return rows


def list_netapp(project: str, netapp, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    parent = f"projects/{project}/locations/-"

    # Storage pools.
    token = None
    while True:
        try:
            kwargs: Dict[str, Any] = {"parent": parent}
            if token:
                kwargs["pageToken"] = token
            response = execute(netapp.projects().locations().storagePools().list(**kwargs))
        except HttpError as ex:
            status = getattr(getattr(ex, "resp", None), "status", None)
            if status in (403, 404):
                errors.append({"Area": "NetApp Volumes (skipped, API disabled or access denied)",
                               "Project": project, "Error": str(ex)})
            else:
                errors.append({"Area": "NetApp storage pools", "Project": project, "Error": str(ex)})
            break
        except Exception as ex:
            errors.append({"Area": "NetApp storage pools", "Project": project, "Error": str(ex)})
            break

        for pool in response.get("storagePools", []) or []:
            name = pool.get("name", "")
            rows.append({
                "Project": project,
                "Resource Type": "Storage Pool",
                "Name": basename(name),
                "Location": extract_path_value(name, "locations"),
                "State": pool.get("state", ""),
                "Service Level": pool.get("serviceLevel", ""),
                "Capacity GiB": pool.get("capacityGib", ""),
                "Allocated Volume GiB": pool.get("volumeCapacityGib", ""),
                "Used GiB": "",
                "Storage Pool": basename(name),
                "Protocols": "",
                "Share Name": "",
                "Network": basename(pool.get("network", "")),
                "Encryption Type": pool.get("encryptionType", ""),
                "LDAP Enabled": pool.get("ldapEnabled", ""),
                "Labels": key_value_string(pool.get("labels", {})),
                "Azure Target": "Azure NetApp Files capacity pool",
                "Azure Sizing Note": "Map service level (Standard/Premium/Ultra) and capacity to an Azure NetApp Files capacity pool.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    # Volumes.
    token = None
    while True:
        try:
            kwargs = {"parent": parent}
            if token:
                kwargs["pageToken"] = token
            response = execute(netapp.projects().locations().volumes().list(**kwargs))
        except Exception as ex:
            errors.append({"Area": "NetApp volumes", "Project": project, "Error": str(ex)})
            break

        for vol in response.get("volumes", []) or []:
            name = vol.get("name", "")
            rows.append({
                "Project": project,
                "Resource Type": "Volume",
                "Name": basename(name),
                "Location": extract_path_value(name, "locations"),
                "State": vol.get("state", ""),
                "Service Level": vol.get("serviceLevel", ""),
                "Capacity GiB": vol.get("capacityGib", ""),
                "Allocated Volume GiB": "",
                "Used GiB": vol.get("usedGib", ""),
                "Storage Pool": basename(vol.get("storagePool", "")),
                "Protocols": join_values(vol.get("protocols", []) or []),
                "Share Name": vol.get("shareName", ""),
                "Network": "",
                "Encryption Type": vol.get("encryptionType", ""),
                "LDAP Enabled": vol.get("ldapEnabled", ""),
                "Labels": key_value_string(vol.get("labels", {})),
                "Azure Target": "Azure NetApp Files volume",
                "Azure Sizing Note": "Map protocol (NFSv3/NFSv4.1/SMB), capacity, service level, snapshots, and export policy to an ANF volume.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    return rows


def list_vm_manager(project: str, osconfig, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    # Patch deployments (project-level).
    token = None
    while True:
        try:
            kwargs: Dict[str, Any] = {"parent": f"projects/{project}"}
            if token:
                kwargs["pageToken"] = token
            response = execute(osconfig.projects().patchDeployments().list(**kwargs))
        except HttpError as ex:
            status = getattr(getattr(ex, "resp", None), "status", None)
            if status in (403, 404):
                errors.append({"Area": "VM Manager (skipped, API disabled or access denied)",
                               "Project": project, "Error": str(ex)})
            else:
                errors.append({"Area": "VM Manager patch deployments", "Project": project, "Error": str(ex)})
            break
        except Exception as ex:
            errors.append({"Area": "VM Manager patch deployments", "Project": project, "Error": str(ex)})
            break

        for item in response.get("patchDeployments", []) or []:
            name = item.get("name", "")
            instance_filter = item.get("instanceFilter", {}) or {}
            patch_config = item.get("patchConfig", {}) or {}
            schedule = "OneTime" if item.get("oneTimeSchedule") else ("Recurring" if item.get("recurringSchedule") else "")
            rows.append({
                "Project": project,
                "Resource Type": "Patch Deployment",
                "Name": basename(name),
                "State": item.get("state", ""),
                "Schedule Type": schedule,
                "Reboot Config": patch_config.get("rebootConfig", ""),
                "Targets All Instances": instance_filter.get("all", ""),
                "Target Zones": join_values(instance_filter.get("zones", []) or []),
                "Target Group Label Sets": len(instance_filter.get("groupLabels", []) or []),
                "Policy Count": "",
                "Duration": item.get("duration", ""),
                "Last Execute Time": item.get("lastExecuteTime", ""),
                "Description": item.get("description", ""),
                "Created": item.get("createTime", ""),
                "Azure Target": "Azure Update Manager / Azure Arc",
                "Azure Sizing Note": "Recreate patch schedules, reboot policy, and instance targeting in Azure Update Manager (with Azure Arc for hybrid).",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    # OS policy assignments (best effort; the location wildcard may not be supported).
    token = None
    while True:
        try:
            kwargs = {"parent": f"projects/{project}/locations/-"}
            if token:
                kwargs["pageToken"] = token
            response = execute(osconfig.projects().locations().osPolicyAssignments().list(**kwargs))
        except Exception as ex:
            errors.append({"Area": "VM Manager OS policy assignments (skipped)", "Project": project, "Error": str(ex)})
            break

        for assignment in response.get("osPolicyAssignments", []) or []:
            name = assignment.get("name", "")
            instance_filter = assignment.get("instanceFilter", {}) or {}
            rows.append({
                "Project": project,
                "Resource Type": "OS Policy Assignment",
                "Name": basename(name),
                "State": assignment.get("rolloutState", ""),
                "Schedule Type": "",
                "Reboot Config": "",
                "Targets All Instances": instance_filter.get("all", ""),
                "Target Zones": "",
                "Target Group Label Sets": len(instance_filter.get("inclusionLabels", []) or []),
                "Policy Count": len(assignment.get("osPolicies", []) or []),
                "Duration": "",
                "Last Execute Time": "",
                "Description": assignment.get("description", ""),
                "Created": assignment.get("revisionCreateTime", ""),
                "Azure Target": "Azure Machine Configuration / Azure Automation",
                "Azure Sizing Note": "Recreate OS policies as Azure Machine Configuration (guest configuration) assignments.",
            })

        token = response.get("nextPageToken")
        if not token:
            break

    return rows
