"""Command-line entry point and end-to-end orchestration."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from google.auth import default
from googleapiclient.discovery import build

from .collectors.compute import (
    apply_associations,
    list_disks,
    list_gke,
    list_instances,
    list_managed_instance_groups,
)
from .collectors.databases import list_cloud_sql
from .collectors.services import (
    list_backup_dr,
    list_bigquery,
    list_cloud_logging,
    list_netapp,
    list_storage_buckets,
    list_vm_manager,
)
from .discovery import discover_projects, list_assets
from .helpers import CLOUD_PLATFORM_SCOPE
from .reporting import (
    create_asset_summary,
    create_azure_baseline,
    create_recommendations,
    dataframe,
    format_workbook,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="GCP inventory and Azure migration baseline BOQ generator")
    parser.add_argument("--scope", required=True, help="organizations/ID | folders/ID | projects/ID")
    parser.add_argument("--output-prefix", default="gcp_azure_inventory", help="Output path prefix without extension")
    parser.add_argument("--skip-assets", action="store_true", help="Skip Cloud Asset Inventory collection")
    parser.add_argument("--skip-gke", action="store_true", help="Skip GKE cluster/node-pool enrichment")
    parser.add_argument("--skip-sql", action="store_true", help="Skip Cloud SQL database collection")
    parser.add_argument("--skip-storage", action="store_true", help="Skip Cloud Storage bucket collection")
    parser.add_argument("--skip-bigquery", action="store_true", help="Skip BigQuery dataset collection")
    parser.add_argument("--skip-logging", action="store_true", help="Skip Cloud Logging sink/bucket collection")
    parser.add_argument("--skip-backupdr", action="store_true", help="Skip Backup and DR Service collection")
    parser.add_argument("--skip-netapp", action="store_true", help="Skip NetApp Volumes collection")
    parser.add_argument("--skip-vmmanager", action="store_true", help="Skip VM Manager (OS Config) collection")
    args = parser.parse_args()

    errors: List[Dict[str, str]] = []
    credentials, _ = default(scopes=[CLOUD_PLATFORM_SCOPE])
    crm = build("cloudresourcemanager", "v3", credentials=credentials, cache_discovery=False)
    compute = build("compute", "v1", credentials=credentials, cache_discovery=False)
    container = None if args.skip_gke else build("container", "v1", credentials=credentials, cache_discovery=False)
    sqladmin = None if args.skip_sql else build("sqladmin", "v1", credentials=credentials, cache_discovery=False)
    storage = None if args.skip_storage else build("storage", "v1", credentials=credentials, cache_discovery=False)
    bigquery = None if args.skip_bigquery else build("bigquery", "v2", credentials=credentials, cache_discovery=False)
    logging_svc = None if args.skip_logging else build("logging", "v2", credentials=credentials, cache_discovery=False)
    backupdr = None if args.skip_backupdr else build("backupdr", "v1", credentials=credentials, cache_discovery=False)
    netapp = None if args.skip_netapp else build("netapp", "v1", credentials=credentials, cache_discovery=False)
    osconfig = None if args.skip_vmmanager else build("osconfig", "v1", credentials=credentials, cache_discovery=False)

    print("Discovering projects recursively...")
    projects = discover_projects(args.scope, crm, errors)
    project_ids = [p["Project ID"] for p in projects if p.get("Project ID")]
    print(f"Projects discovered: {len(project_ids)}")

    project_number_to_id = {
        str(p.get("Project Number", "")): p.get("Project ID", "")
        for p in projects if p.get("Project Number")
    }

    assets: List[Dict[str, Any]] = []
    if not args.skip_assets:
        print("Collecting Cloud Asset Inventory...")
        try:
            assets = list_assets(args.scope, project_number_to_id)
        except Exception as ex:
            errors.append({"Area": "Cloud Asset Inventory", "Project": args.scope, "Error": str(ex)})
            print(f"Cloud Asset Inventory failed; continuing: {ex}", file=sys.stderr)

    all_vms: List[Dict[str, Any]] = []
    all_disks: List[Dict[str, Any]] = []
    all_nics: List[Dict[str, Any]] = []
    all_migs: List[Dict[str, Any]] = []
    all_clusters: List[Dict[str, Any]] = []
    all_nodepools: List[Dict[str, Any]] = []
    all_sql: List[Dict[str, Any]] = []
    all_storage: List[Dict[str, Any]] = []
    all_bigquery: List[Dict[str, Any]] = []
    all_logging: List[Dict[str, Any]] = []
    all_backupdr: List[Dict[str, Any]] = []
    all_netapp: List[Dict[str, Any]] = []
    all_vmmanager: List[Dict[str, Any]] = []
    machine_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for index, project in enumerate(project_ids, start=1):
        print(f"[{index}/{len(project_ids)}] Inventorying {project}...")
        disks, disk_lookup = list_disks(project, compute, errors)
        vms, nics, regions = list_instances(project, compute, disk_lookup, machine_cache, errors)
        migs, mig_map = list_managed_instance_groups(project, compute, regions, errors)

        cluster_rows: List[Dict[str, Any]] = []
        nodepool_rows: List[Dict[str, Any]] = []
        gke_map: Dict[Tuple[str, str, str], Dict[str, str]] = {}
        if container is not None:
            cluster_rows, nodepool_rows, gke_map = list_gke(project, container, compute, errors)

        apply_associations(vms, mig_map, gke_map)
        all_disks.extend(disks)
        all_vms.extend(vms)
        all_nics.extend(nics)
        all_migs.extend(migs)
        all_clusters.extend(cluster_rows)
        all_nodepools.extend(nodepool_rows)

        if sqladmin is not None:
            all_sql.extend(list_cloud_sql(project, sqladmin, errors))
        if storage is not None:
            all_storage.extend(list_storage_buckets(project, storage, errors))
        if bigquery is not None:
            all_bigquery.extend(list_bigquery(project, bigquery, errors))
        if logging_svc is not None:
            all_logging.extend(list_cloud_logging(project, logging_svc, errors))
        if backupdr is not None:
            all_backupdr.extend(list_backup_dr(project, backupdr, errors))
        if netapp is not None:
            all_netapp.extend(list_netapp(project, netapp, errors))
        if osconfig is not None:
            all_vmmanager.extend(list_vm_manager(project, osconfig, errors))

    projects_df = dataframe(projects)
    assets_df = dataframe(assets, ["Project ID", "Project Number", "Asset Type", "Name", "Location", "Version", "Ancestors"])
    asset_summary_df = create_asset_summary(assets_df)
    vm_df = dataframe(all_vms)
    disk_df = dataframe(all_disks)
    nic_df = dataframe(all_nics)
    mig_df = dataframe(all_migs)
    cluster_df = dataframe(all_clusters)
    nodepool_df = dataframe(all_nodepools)
    sql_df = dataframe(all_sql)
    storage_df = dataframe(all_storage)
    bigquery_df = dataframe(all_bigquery)
    logging_df = dataframe(all_logging)
    backupdr_df = dataframe(all_backupdr)
    netapp_df = dataframe(all_netapp)
    vmmanager_df = dataframe(all_vmmanager)
    errors_df = dataframe(errors, ["Area", "Project", "Error"])
    recommendations_df = create_recommendations()
    azure_baseline_df = create_azure_baseline(vm_df, disk_df, cluster_df, nodepool_df, mig_df, assets_df, sql_df,
                                              storage_df, bigquery_df, logging_df, backupdr_df, netapp_df, vmmanager_df)

    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    xlsx_path = prefix.with_suffix(".xlsx")

    print("Writing reports...")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        azure_baseline_df.to_excel(writer, sheet_name="Azure_Baseline", index=False)
        asset_summary_df.to_excel(writer, sheet_name="Service_Mapping", index=False)
        projects_df.to_excel(writer, sheet_name="Projects", index=False)
        assets_df.to_excel(writer, sheet_name="Assets", index=False)
        vm_df.to_excel(writer, sheet_name="Compute_VMs", index=False)
        disk_df.to_excel(writer, sheet_name="Disks", index=False)
        nic_df.to_excel(writer, sheet_name="NICs", index=False)
        mig_df.to_excel(writer, sheet_name="Managed_Groups", index=False)
        cluster_df.to_excel(writer, sheet_name="GKE_Clusters", index=False)
        nodepool_df.to_excel(writer, sheet_name="GKE_NodePools", index=False)
        sql_df.to_excel(writer, sheet_name="Cloud_SQL", index=False)
        storage_df.to_excel(writer, sheet_name="Cloud_Storage", index=False)
        bigquery_df.to_excel(writer, sheet_name="BigQuery", index=False)
        logging_df.to_excel(writer, sheet_name="Cloud_Logging", index=False)
        backupdr_df.to_excel(writer, sheet_name="Backup_DR", index=False)
        netapp_df.to_excel(writer, sheet_name="NetApp_Volumes", index=False)
        vmmanager_df.to_excel(writer, sheet_name="VM_Manager", index=False)
        recommendations_df.to_excel(writer, sheet_name="Recommendations", index=False)
        errors_df.to_excel(writer, sheet_name="Errors", index=False)

    format_workbook(xlsx_path)

    csv_outputs = {
        f"{prefix}_assets.csv": assets_df,
        f"{prefix}_asset_summary.csv": asset_summary_df,
        f"{prefix}_compute.csv": vm_df,
        f"{prefix}_disks.csv": disk_df,
        f"{prefix}_gke_nodepools.csv": nodepool_df,
        f"{prefix}_cloud_sql.csv": sql_df,
        f"{prefix}_cloud_storage.csv": storage_df,
        f"{prefix}_bigquery.csv": bigquery_df,
        f"{prefix}_cloud_logging.csv": logging_df,
        f"{prefix}_backup_dr.csv": backupdr_df,
        f"{prefix}_netapp_volumes.csv": netapp_df,
        f"{prefix}_vm_manager.csv": vmmanager_df,
    }
    for filename, df in csv_outputs.items():
        df.to_csv(filename, index=False)

    print("\n=== Azure Migration Baseline ===")
    if not azure_baseline_df.empty:
        print(azure_baseline_df.to_string(index=False))

    print("\nGenerated:")
    print(f" - {xlsx_path}")
    for filename in csv_outputs:
        print(f" - {filename}")
    if errors:
        print(f"\nCompleted with {len(errors)} warning/error entries. Review the Errors sheet.")
    return 0
