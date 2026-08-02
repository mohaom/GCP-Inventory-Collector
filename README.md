# GCP to Azure Migration Inventory & Baseline BOQ Generator

A Python utility that discovers resources across a Google Cloud organization, folder, or project and produces an Azure-oriented migration inventory and baseline bill of quantities (BOQ).

## Overview

The tool inventories:

- GCP projects, including those in nested folders
- Cloud Asset Inventory resources
- Compute Engine VMs (machine type, vCPU, memory, OS estimate, GPU, status, labels, tags, security settings)
- Persistent disks and storage configuration
- Network interfaces, internal IPs, and external IPs
- Managed Instance Groups (MIGs)
- GKE clusters and node pools
- Cloud SQL instances (MySQL, PostgreSQL, SQL Server) with engine, version, edition, tier, storage, HA, replicas, backups, and databases
- Cloud Storage buckets (location, storage class, versioning, lifecycle, retention, encryption, public-access, and access controls)
- BigQuery datasets (location, table counts/types, aggregated logical size, expiration, encryption, and labels)
- Cloud Logging sinks and log buckets (destinations, filters, retention, analytics, and CMEK)
- Backup and DR Service (management servers, backup vaults, and backup plans)
- NetApp Volumes (storage pools and volumes: protocol, capacity, service level, and encryption)
- VM Manager / OS Config (patch deployments and OS policy assignments)
- Associations between VMs, GKE node pools, and MIGs
- Preliminary Azure service and compute-family recommendations

> **Note:** Azure recommendations are configuration-based starting points, not final performance-based sizing or pricing.

## Prerequisites

- Python 3.9 or later
- Google Cloud CLI (`gcloud`)
- Read access to the GCP scope being inventoried
- Required GCP APIs enabled (see [Required APIs](#required-apis))

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Authenticate
gcloud auth application-default login

# 3. Run against your scope
python gcp_azure_inventory.py --scope organizations/123456789
```

Results are written to `gcp_azure_inventory.xlsx` and matching CSV files in the current directory.

## Installation

Place `gcp_azure_inventory.py` and `requirements.txt` in the same directory, then install dependencies (a virtual environment is recommended):

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux / macOS:  source .venv/bin/activate
pip install -r requirements.txt
```

## Authentication

Use Application Default Credentials:

```bash
gcloud auth application-default login
```

To use a service account instead, set `GOOGLE_APPLICATION_CREDENTIALS` to the key file path before running:

```bash
# Windows PowerShell
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\inventory-sa.json"

# Linux / macOS
export GOOGLE_APPLICATION_CREDENTIALS="/path/inventory-sa.json"
```

The identity needs **read-only** access to list projects/folders, Cloud Asset Inventory, Compute Engine resources, and GKE (when enabled). Inaccessible resources are logged to the `Errors` worksheet rather than stopping the run.

## Required Permissions

Grant the following predefined IAM roles to the user or service account running the script. Assign them at the **organization**, **folder**, or **project** level to match your `--scope`.

| Role | ID | Purpose |
|---|---|---|
| Browser | `roles/browser` | List and read the resource hierarchy (organizations, folders, projects) |
| Cloud Asset Viewer | `roles/cloudasset.viewer` | Read Cloud Asset Inventory (skip if using `--skip-assets`) |
| Compute Viewer | `roles/compute.viewer` | Read Compute Engine VMs, disks, machine types, and instance groups |
| Kubernetes Engine Viewer | `roles/container.viewer` | Read GKE clusters and node pools (skip if using `--skip-gke`) |
| Cloud SQL Viewer | `roles/cloudsql.viewer` | Read Cloud SQL instances and databases (skip if using `--skip-sql`) |
| BigQuery Metadata Viewer | `roles/bigquery.metadataViewer` | Read BigQuery datasets and table metadata (skip if using `--skip-bigquery`) |
| Logs Viewer | `roles/logging.viewer` | Read Cloud Logging sinks and log buckets (skip if using `--skip-logging`) |
| Backup and DR Viewer | `roles/backupdr.viewer` | Read Backup and DR management servers, vaults, and plans (skip if using `--skip-backupdr`) |
| NetApp Viewer | `roles/netapp.viewer` | Read NetApp storage pools and volumes (skip if using `--skip-netapp`) |
| OS Config Patch Deployment Viewer | `roles/osconfig.patchDeploymentViewer` | Read VM Manager patch deployments and OS policy assignments (skip if using `--skip-vmmanager`) |

> All roles are read-only. No write, delete, or admin permissions are required.

> **Cloud Storage:** Listing buckets project-wide requires the `storage.buckets.list` permission, which has no narrower predefined viewer role. Grant basic **Project Viewer** (`roles/viewer`) or a custom role that includes `storage.buckets.list`, or use `--skip-storage`. Project Viewer also satisfies every other collector above if you prefer a single role over the per-service viewer roles.

### Grant at organization scope

```bash
ORG_ID=123456789
MEMBER="user:you@example.com"   # or "serviceAccount:inventory-sa@PROJECT_ID.iam.gserviceaccount.com"

for ROLE in roles/browser roles/cloudasset.viewer roles/compute.viewer roles/container.viewer roles/cloudsql.viewer roles/bigquery.metadataViewer roles/logging.viewer roles/backupdr.viewer roles/netapp.viewer roles/osconfig.patchDeploymentViewer; do
  gcloud organizations add-iam-policy-binding "$ORG_ID" \
    --member="$MEMBER" \
    --role="$ROLE"
done
```

> Add `roles/viewer` (or a custom role with `storage.buckets.list`) if you want Cloud Storage bucket collection.

For folder scope, use `gcloud resource-manager folders add-iam-policy-binding FOLDER_ID`; for a single project, use `gcloud projects add-iam-policy-binding PROJECT_ID`.

## Required APIs

```bash
gcloud services enable \
  cloudasset.googleapis.com \
  cloudresourcemanager.googleapis.com \
  compute.googleapis.com \
  container.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  backupdr.googleapis.com \
  netapp.googleapis.com \
  osconfig.googleapis.com
```

`container.googleapis.com` is not required when using `--skip-gke`. `sqladmin.googleapis.com` is not required when using `--skip-sql`. Likewise, each of `storage`, `bigquery`, `logging`, `backupdr`, `netapp`, and `osconfig` is only needed when its collector runs (skip with `--skip-storage`, `--skip-bigquery`, `--skip-logging`, `--skip-backupdr`, `--skip-netapp`, and `--skip-vmmanager`). Any API that is disabled or inaccessible is logged to the `Errors` worksheet and the run continues.

## Usage

```bash
python gcp_azure_inventory.py --scope <SCOPE> [options]
```

`<SCOPE>` is one of `organizations/ID`, `folders/ID`, or `projects/ID`.

### Options

| Argument | Required | Default | Description |
|---|:---:|---|---|
| `--scope` | Yes | — | Target scope: `organizations/ID`, `folders/ID`, or `projects/ID` |
| `--output-prefix` | No | `gcp_azure_inventory` | Output path/filename prefix (no extension) |
| `--skip-assets` | No | off | Skip Cloud Asset Inventory collection |
| `--skip-gke` | No | off | Skip GKE cluster and node-pool enrichment |
| `--skip-sql` | No | off | Skip Cloud SQL database collection |
| `--skip-storage` | No | off | Skip Cloud Storage bucket collection |
| `--skip-bigquery` | No | off | Skip BigQuery dataset collection |
| `--skip-logging` | No | off | Skip Cloud Logging sink/bucket collection |
| `--skip-backupdr` | No | off | Skip Backup and DR Service collection |
| `--skip-netapp` | No | off | Skip NetApp Volumes collection |
| `--skip-vmmanager` | No | off | Skip VM Manager (OS Config) collection |

### Examples

```bash
# Entire organization
python gcp_azure_inventory.py --scope organizations/123456789

# Folder (and its nested folders)
python gcp_azure_inventory.py --scope folders/123456789

# Single project
python gcp_azure_inventory.py --scope projects/my-project-id

# Custom output location (directory created automatically)
python gcp_azure_inventory.py --scope organizations/123456789 --output-prefix reports/customer-prod

# Skip optional collectors
python gcp_azure_inventory.py --scope organizations/123456789 --skip-assets --skip-gke --skip-sql --skip-storage --skip-bigquery --skip-logging --skip-backupdr --skip-netapp --skip-vmmanager
```

## Output

Each run produces one Excel workbook plus supporting CSV files, named from `--output-prefix`:

```text
<prefix>.xlsx
<prefix>_assets.csv
<prefix>_asset_summary.csv
<prefix>_compute.csv
<prefix>_disks.csv
<prefix>_gke_nodepools.csv
<prefix>_cloud_sql.csv
<prefix>_cloud_storage.csv
<prefix>_bigquery.csv
<prefix>_cloud_logging.csv
<prefix>_backup_dr.csv
<prefix>_netapp_volumes.csv
<prefix>_vm_manager.csv
```

### Workbook sheets

| Sheet | Description |
|---|---|
| `Azure_Baseline` | High-level VM, vCPU, memory, disk, GKE, MIG, and asset totals |
| `Service_Mapping` | GCP asset counts with preliminary Azure service mappings |
| `Projects` | Discovered GCP projects and metadata |
| `Assets` | Cloud Asset Inventory resources |
| `Compute_VMs` | Detailed Compute Engine inventory and Azure compute recommendations |
| `Disks` | Persistent disk capacity, type, attachment, IOPS, and throughput |
| `NICs` | VM network interfaces, VPCs, subnets, and IP addresses |
| `Managed_Groups` | Managed Instance Groups and preliminary VM Scale Set mappings |
| `GKE_Clusters` | GKE cluster configuration and preliminary AKS mapping |
| `GKE_NodePools` | GKE node-pool configuration, autoscaling, machine type, and disks |
| `Cloud_SQL` | Cloud SQL instances (MySQL, PostgreSQL, SQL Server): engine, version, edition, tier, storage, HA, replicas, backups, and databases |
| `Cloud_Storage` | Cloud Storage buckets: location, storage class, versioning, lifecycle, retention, encryption, and access controls |
| `BigQuery` | BigQuery datasets: location, table counts/types, aggregated logical size, expiration, encryption, and labels |
| `Cloud_Logging` | Cloud Logging sinks and log buckets: destinations, filters, retention, analytics, and CMEK |
| `Backup_DR` | Backup and DR Service: management servers, backup vaults, and backup plans |
| `NetApp_Volumes` | NetApp storage pools and volumes: protocol, capacity, service level, and encryption |
| `VM_Manager` | VM Manager (OS Config): patch deployments and OS policy assignments |
| `Recommendations` | Additional data required for a complete Azure migration assessment |
| `Errors` | Permission failures, inaccessible APIs, and per-project collection errors |

## Azure Mapping & Sizing

The tool classifies each VM as `GKE Node`, `Managed Instance Group VM`, or `Standalone VM`, then suggests a preliminary Azure target:

| GCP workload | Preliminary Azure target |
|---|---|
| GKE cluster | Azure Kubernetes Service (AKS) |
| GKE node pool | AKS node pool |
| Managed Instance Group | Azure Virtual Machine Scale Set |
| Standalone Compute Engine VM | Azure Virtual Machine |
| Persistent Disk | Azure Managed Disk |

VM family suggestions are derived from provisioned configuration (D-series general purpose, E/M-series memory optimized, F-series compute optimized, N-series GPU). These are **not** exact SKUs. Final sizing should incorporate utilization metrics (CPU, memory, disk IOPS/throughput/latency, network), target region and availability zones, SKU/quota availability, licensing, and HA/DR requirements.

## Limitations

- **Configuration-based, not utilization-based.** Allocated resources are reported as-is; a 16-vCPU VM counts as 16 vCPU regardless of actual usage.
- **GKE workloads are not inspected.** Kubernetes objects (Deployments, Pods, requests/limits, autoscalers, PVCs, Ingress, etc.) are not queried and are required for accurate AKS sizing.
- **PaaS resources are counted, not sized.** Cloud Asset Inventory identifies services (Cloud SQL, Storage, Pub/Sub, BigQuery, Cloud Run, etc.), but service-specific collectors are needed for capacity and performance assessment.
- **Regional MIGs** are checked only in regions where VMs were discovered; zero-instance regional groups elsewhere may not appear.
- **OS detection is an estimate** from disk images, licenses, and metadata; custom images may show as `Unknown`.
- **Partial permissions are tolerated.** The run continues on per-project errors — always review the `Errors` sheet before trusting totals.

## Recommended Assessment Additions

For a production migration assessment, also collect:

1. Cloud Monitoring performance metrics (14–30 days)
2. Kubernetes workload requests, limits, autoscaling, and storage
3. Database engine, version, performance, HA, backup, and compatibility data
4. Cloud Storage capacity, object count, access patterns, and data transfer
5. Network routes, firewall rules, NAT, VPN, Interconnect, load balancers, and DNS
6. Application dependencies and migration-wave grouping
7. IAM, service accounts, workload identities, KMS, and security policies
8. GCP billing exports, discounts, licensing, and Azure pricing scenarios
9. Target-region Azure SKU, quota, and availability-zone validation

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `DefaultCredentialsError` | Run `gcloud auth application-default login`, or verify `GOOGLE_APPLICATION_CREDENTIALS`. |
| `403 Permission denied` | Confirm read access at the target scope; check the `Errors` sheet for the failing project/API. |
| API not enabled | Run `gcloud services enable SERVICE.googleapis.com --project PROJECT_ID`. |
| Empty GKE sheets | Ensure `--skip-gke` was not used, the Kubernetes Engine API is enabled, and clusters exist. |
| Empty Assets sheet | Ensure `--skip-assets` was not used and the Cloud Asset Inventory API is enabled. |
| Missing projects | Verify the identity can list all folders/projects under the scope (folder permissions gate recursion). |

## Security

- Prefer Application Default Credentials or short-lived workload credentials over long-lived key files.
- Never commit credential files to source control.
- Reports may contain project names, internal IPs, service-account names, labels, metadata keys, and topology — treat them as sensitive infrastructure documentation and review before external distribution.

## Disclaimer

This tool produces a discovery inventory and preliminary Azure migration baseline. It does not replace detailed architecture validation, dependency analysis, application compatibility testing, performance-based right-sizing, security assessment, or Azure pricing validation.
