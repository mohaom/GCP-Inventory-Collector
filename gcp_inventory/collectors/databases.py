"""Cloud SQL database instance inventory collector."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.errors import HttpError

from ..helpers import basename, execute, join_values, key_value_string


def cloud_sql_engine(database_version: str) -> Tuple[str, str, str]:
    """Return (engine, Azure target, migration note) from a Cloud SQL databaseVersion."""
    version = (database_version or "").upper()
    if version.startswith("MYSQL"):
        return ("MySQL", "Azure Database for MySQL Flexible Server",
                "Match engine version, vCPU/RAM tier, storage/IOPS, HA, read replicas, and server flags.")
    if version.startswith("POSTGRES"):
        return ("PostgreSQL", "Azure Database for PostgreSQL Flexible Server",
                "Match engine version, vCPU/RAM tier, storage/IOPS, HA, read replicas, extensions, and server flags.")
    if version.startswith("SQLSERVER"):
        return ("SQL Server", "Azure SQL Managed Instance / SQL Server on Azure VM",
                "Match edition, version, vCPU/RAM, storage/IOPS, HA/Always On, licensing, and feature compatibility.")
    return ("Unknown", "Manual assessment", "Unrecognized Cloud SQL engine; review the target service manually.")


def parse_cloud_sql_tier(tier: str) -> Tuple[Optional[int], Optional[float]]:
    """Derive (vCPU, memory GiB) from a Cloud SQL machine tier where possible."""
    tier = (tier or "").lower()
    # Custom tiers: db-custom-<vCPU>-<memoryMB>.
    match = re.match(r"db-custom-(\d+)-(\d+)", tier)
    if match:
        return int(match.group(1)), round(int(match.group(2)) / 1024.0, 2)
    # Legacy first/second-gen N1 tiers.
    match = re.match(r"db-n1-(standard|highmem)-(\d+)", tier)
    if match:
        vcpus = int(match.group(2))
        memory_gib = vcpus * (3.75 if match.group(1) == "standard" else 6.5)
        return vcpus, round(memory_gib, 2)
    # Shared-core tiers.
    shared = {"db-f1-micro": (None, 0.6), "db-g1-small": (None, 1.7)}
    if tier in shared:
        return shared[tier]
    return None, None


def list_cloud_sql(project: str, sqladmin, errors: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    try:
        response = execute(sqladmin.instances().list(project=project))
    except HttpError as ex:
        status = getattr(getattr(ex, "resp", None), "status", None)
        if status in (403, 404):
            # API disabled or no access on this project; record once and continue.
            errors.append({"Area": "Cloud SQL (skipped, API disabled or access denied)",
                           "Project": project, "Error": str(ex)})
        else:
            errors.append({"Area": "Cloud SQL instances", "Project": project, "Error": str(ex)})
        return rows
    except Exception as ex:
        errors.append({"Area": "Cloud SQL instances", "Project": project, "Error": str(ex)})
        return rows

    for inst in response.get("items", []) or []:
        name = inst.get("name", "")
        settings = inst.get("settings", {}) or {}
        backup = settings.get("backupConfiguration", {}) or {}
        backup_retention = backup.get("backupRetentionSettings", {}) or {}
        ip_config = settings.get("ipConfiguration", {}) or {}
        maintenance = settings.get("maintenanceWindow", {}) or {}
        db_version = inst.get("databaseVersion", "")
        engine, azure_target, azure_note = cloud_sql_engine(db_version)
        tier = settings.get("tier", "")
        vcpus, memory_gib = parse_cloud_sql_tier(tier)

        ip_addresses = inst.get("ipAddresses", []) or []
        public_ips = [ip.get("ipAddress", "") for ip in ip_addresses if ip.get("type") == "PRIMARY"]
        private_ips = [ip.get("ipAddress", "") for ip in ip_addresses if ip.get("type") == "PRIVATE"]

        flags = settings.get("databaseFlags", []) or []
        flag_str = join_values(f"{f.get('name')}={f.get('value')}" for f in flags)
        authorized = ip_config.get("authorizedNetworks", []) or []
        replica_names = inst.get("replicaNames", []) or []

        # Enumerate the individual databases (schemas) hosted on the instance.
        database_names: List[str] = []
        try:
            db_response = execute(sqladmin.databases().list(project=project, instance=name))
            database_names = [db.get("name", "") for db in db_response.get("items", []) or [] if db.get("name")]
        except Exception as ex:
            errors.append({"Area": "Cloud SQL databases", "Project": project, "Error": f"{name}: {ex}"})

        rows.append({
            "Project": project,
            "Instance": name,
            "Engine": engine,
            "Database Version": db_version,
            "Edition": settings.get("edition", ""),
            "Instance Role": inst.get("instanceType", ""),
            "State": inst.get("state", ""),
            "Region": inst.get("region", ""),
            "Primary Zone": inst.get("gceZone", ""),
            "Secondary Zone": inst.get("secondaryGceZone", ""),
            "Availability Type": settings.get("availabilityType", ""),
            "High Availability": "Yes" if settings.get("availabilityType") == "REGIONAL" else "No",
            "Tier": tier,
            "vCPUs": vcpus,
            "Memory GiB": memory_gib,
            "Data Disk Size GiB": settings.get("dataDiskSizeGb", ""),
            "Data Disk Type": settings.get("dataDiskType", ""),
            "Storage Auto Resize": settings.get("storageAutoResize", ""),
            "Storage Auto Resize Limit GiB": settings.get("storageAutoResizeLimit", ""),
            "Provisioned IOPS": settings.get("dataDiskProvisionedIops", ""),
            "Provisioned Throughput": settings.get("dataDiskProvisionedThroughput", ""),
            "Activation Policy": settings.get("activationPolicy", ""),
            "Pricing Plan": settings.get("pricingPlan", ""),
            "Connection Name": inst.get("connectionName", ""),
            "Public IP Enabled": ip_config.get("ipv4Enabled", ""),
            "Private Network": basename(ip_config.get("privateNetwork", "")),
            "SSL Mode": ip_config.get("sslMode", ""),
            "Require SSL": ip_config.get("requireSsl", ""),
            "Authorized Network Count": len(authorized),
            "Public IPs": join_values(public_ips),
            "Private IPs": join_values(private_ips),
            "Master Instance": inst.get("masterInstanceName", ""),
            "Read Replicas": join_values(basename(r) for r in replica_names),
            "Read Replica Count": len(replica_names),
            "Failover Replica": (inst.get("failoverReplica", {}) or {}).get("name", ""),
            "Backups Enabled": backup.get("enabled", ""),
            "Backup Start Time": backup.get("startTime", ""),
            "Point In Time Recovery": backup.get("pointInTimeRecoveryEnabled", ""),
            "Transaction Log Retention Days": backup.get("transactionLogRetentionDays", ""),
            "Retained Backups": backup_retention.get("retainedBackups", ""),
            "Backup Retention Unit": backup_retention.get("retentionUnit", ""),
            "Deletion Protection": settings.get("deletionProtectionEnabled", ""),
            "CMEK Key": basename((inst.get("diskEncryptionConfiguration", {}) or {}).get("kmsKeyName", "")),
            "Maintenance Day": maintenance.get("day", ""),
            "Maintenance Hour": maintenance.get("hour", ""),
            "Maintenance Track": maintenance.get("updateTrack", ""),
            "Database Flags": flag_str,
            "Database Count": len(database_names),
            "Databases": join_values(database_names),
            "Service Account": inst.get("serviceAccountEmailAddress", ""),
            "Maintenance Version": inst.get("maintenanceVersion", ""),
            "Create Time": inst.get("createTime", ""),
            "Labels": key_value_string(settings.get("userLabels", {})),
            "Azure Target": azure_target,
            "Azure Sizing Note": azure_note,
        })

    return rows
