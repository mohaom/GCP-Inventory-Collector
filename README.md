# GCP to Azure Migration Inventory & Baseline BOQ Generator

A Python utility that discovers resources across a Google Cloud organization, folder, or project and produces an Azure-oriented migration inventory and baseline bill of quantities.

The script inventories:

- GCP projects, including projects inside nested folders
- Cloud Asset Inventory resources
- Compute Engine virtual machines
- VM machine type, vCPU, memory, operating system estimate, GPU, status, labels, tags, and security settings
- Persistent disks and storage configuration
- Network interfaces, internal IPs, and external IPs
- Managed Instance Groups
- GKE clusters and node pools
- Associations between VMs, GKE node pools, and Managed Instance Groups
- Preliminary Azure service and compute-family recommendations

> The Azure recommendations are configuration-based starting points. They are not final performance-based sizing or pricing recommendations.

## Requirements

- Python 3.9 or later
- Google Cloud CLI
- Access to the GCP organization, folders, or projects being inventoried
- The required GCP APIs enabled

## Installation

Clone or copy the following files into the same directory:

```text
gcp_azure_inventory.py
requirements.txt
```

Create and activate a virtual environment.

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

The expected `requirements.txt` contents are:

```text
google-cloud-asset
google-api-python-client
google-auth
pandas
openpyxl
```

## Authentication

Authenticate using Application Default Credentials:

```bash
gcloud auth application-default login
```

To use a service account instead, set `GOOGLE_APPLICATION_CREDENTIALS` to the service-account key file before running the script.

### Windows PowerShell

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\inventory-service-account.json"
```

### Linux or macOS

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/inventory-service-account.json"
```

Use read-only permissions wherever possible. The identity running the script needs permission to:

- List organizations, folders, and projects within the selected scope
- Read Cloud Asset Inventory resources
- Read Compute Engine instances, disks, machine types, instance groups, and instance-group members
- Read GKE clusters and node pools when GKE enrichment is enabled

The script records inaccessible resources and API failures in the `Errors` worksheet instead of stopping the entire inventory.

## Required APIs

Enable the following APIs in the relevant management or service projects:

```bash
gcloud services enable \
  cloudasset.googleapis.com \
  cloudresourcemanager.googleapis.com \
  compute.googleapis.com \
  container.googleapis.com
```

`container.googleapis.com` is not required when running with `--skip-gke`.

## Usage

Display the command-line help:

```bash
python gcp_azure_inventory.py --help
```

### Inventory an entire organization

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789
```

### Inventory a folder and its nested folders

```bash
python gcp_azure_inventory.py \
  --scope folders/123456789
```

### Inventory one project

```bash
python gcp_azure_inventory.py \
  --scope projects/my-project-id
```

### Set the output name and directory

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789 \
  --output-prefix output/customer-prod
```

The output directory is created automatically when it does not exist.

### Skip Cloud Asset Inventory

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789 \
  --skip-assets
```

This still collects Compute Engine and GKE information, but the `Assets` and `Service_Mapping` output will be empty.

### Skip GKE enrichment

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789 \
  --skip-gke
```

This skips GKE cluster and node-pool discovery. Compute Engine VMs and Managed Instance Groups are still collected.

### Skip both optional collectors

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789 \
  --skip-assets \
  --skip-gke
```

## Command-line arguments

| Argument | Required | Description |
|---|---:|---|
| `--scope` | Yes | GCP scope in the form `organizations/ID`, `folders/ID`, or `projects/ID` |
| `--output-prefix` | No | Output path and filename prefix without an extension. Default: `gcp_azure_inventory` |
| `--skip-assets` | No | Skips Cloud Asset Inventory collection |
| `--skip-gke` | No | Skips GKE cluster and node-pool enrichment |

## Output files

With the default prefix, the script creates:

```text
gcp_azure_inventory.xlsx
gcp_azure_inventory_assets.csv
gcp_azure_inventory_asset_summary.csv
gcp_azure_inventory_compute.csv
gcp_azure_inventory_disks.csv
gcp_azure_inventory_gke_nodepools.csv
```

With this command:

```bash
python gcp_azure_inventory.py \
  --scope organizations/123456789 \
  --output-prefix reports/customer-prod
```

The generated files are:

```text
reports/customer-prod.xlsx
reports/customer-prod_assets.csv
reports/customer-prod_asset_summary.csv
reports/customer-prod_compute.csv
reports/customer-prod_disks.csv
reports/customer-prod_gke_nodepools.csv
```

## Excel workbook sheets

| Sheet | Description |
|---|---|
| `Azure_Baseline` | High-level VM, vCPU, memory, disk, GKE, MIG, and asset totals |
| `Service_Mapping` | GCP asset counts with preliminary Azure service mappings |
| `Projects` | Discovered GCP projects and project metadata |
| `Assets` | Cloud Asset Inventory resources |
| `Compute_VMs` | Detailed Compute Engine inventory and Azure compute recommendations |
| `Disks` | Persistent disk capacity, type, attachment, IOPS, and throughput settings |
| `NICs` | VM network interfaces, VPCs, subnets, and IP addresses |
| `Managed_Groups` | Managed Instance Groups and preliminary VM Scale Set mappings |
| `GKE_Clusters` | GKE cluster configuration and preliminary AKS mapping |
| `GKE_NodePools` | GKE node-pool configuration, autoscaling, machine type, and disk details |
| `Recommendations` | Additional data required for a complete Azure migration assessment |
| `Errors` | Permission failures, inaccessible APIs, and per-project collection errors |

## VM workload association

The script attempts to classify each VM as one of the following:

- `GKE Node`
- `Managed Instance Group VM`
- `Standalone VM`

For GKE, the script queries clusters, node pools, and their backing instance groups. It then maps the instance-group members to Compute Engine VMs.

The `Compute_VMs` sheet includes fields such as:

```text
GKE Cluster
GKE Node Pool
Managed Instance Group
Workload Association
Azure Compute Target
Azure VM Family Candidate
Azure Sizing Note
```

Typical preliminary mappings include:

| GCP workload | Preliminary Azure target |
|---|---|
| GKE cluster | Azure Kubernetes Service |
| GKE node pool | AKS node pool |
| Managed Instance Group | Azure Virtual Machine Scale Set |
| Standalone Compute Engine VM | Azure Virtual Machine |
| Persistent Disk | Azure Managed Disk |

## Azure sizing guidance

The script uses provisioned GCP configuration to suggest an Azure VM family, such as:

- D-series for general-purpose workloads
- E-series or M-series for memory-intensive workloads
- F-series for compute-intensive workloads
- N-series for GPU workloads

These suggestions are not exact Azure SKUs. Final sizing should include:

- 14 to 30 days of CPU utilization
- Memory working set and peak memory
- Disk IOPS, throughput, capacity, and latency
- Network throughput and connection patterns
- Target Azure region and availability zones
- Azure SKU availability and subscription quotas
- Operating-system and database licensing
- High-availability, backup, and disaster-recovery requirements

## Important limitations

### Configuration-based sizing

The tool reports allocated resources, not actual utilization. A VM with 16 vCPUs is counted as 16 vCPUs even when its average usage is much lower.

### GKE workload sizing

The tool inventories GKE clusters, node pools, and backing VMs. It does not query Kubernetes objects such as:

- Deployments
- StatefulSets
- DaemonSets
- Pods
- CPU and memory requests or limits
- Horizontal or vertical pod autoscalers
- Persistent volume claims
- Ingress resources
- Services
- Pod disruption budgets

These details are needed for accurate AKS sizing.

### PaaS resource sizing

Cloud Asset Inventory identifies resources such as Cloud SQL, Cloud Storage, Pub/Sub, BigQuery, and Cloud Run, but asset existence and count are not enough to size their Azure equivalents.

Service-specific collectors are still required for capacity, performance, resilience, configuration, and compatibility assessment.

### Regional Managed Instance Groups

The script checks regional Managed Instance Groups only in regions where Compute Engine VMs were discovered. A zero-instance regional group in another region may not appear in the report.

### Operating-system detection

The operating system is estimated from disk images, licenses, and metadata. Custom images may be reported as `Unknown` or may require manual validation.

### Partial permissions

The script continues when individual projects or services cannot be read. Always review the `Errors` sheet before using the totals for migration planning.

## Recommended assessment additions

For a production Azure migration assessment, add or collect:

1. Performance metrics from Cloud Monitoring
2. Kubernetes workload requests, limits, autoscaling, and storage data
3. Database engine, version, performance, HA, backup, and compatibility data
4. Cloud Storage capacity, object count, access pattern, and data-transfer data
5. Network routes, firewall rules, NAT, VPN, Interconnect, load balancers, and DNS
6. Application dependencies and migration-wave grouping
7. IAM, service accounts, workload identities, KMS, and security policies
8. GCP billing exports, discounts, licenses, and Azure pricing scenarios
9. Target-region Azure SKU, quota, and availability-zone validation

## Troubleshooting

### `DefaultCredentialsError`

Run:

```bash
gcloud auth application-default login
```

Or confirm that `GOOGLE_APPLICATION_CREDENTIALS` points to a valid service-account key file.

### `403 Permission denied`

Confirm that the authenticated identity has read access at the selected organization, folder, or project scope. Also review the `Errors` sheet to identify the exact project or API call that failed.

### API not enabled

Enable the required API in the relevant project:

```bash
gcloud services enable SERVICE_NAME.googleapis.com --project PROJECT_ID
```

### Empty GKE sheets

Check that:

- `--skip-gke` was not used
- The Kubernetes Engine API is enabled
- The authenticated identity can list GKE clusters
- GKE clusters exist in the discovered projects

### Empty Assets sheet

Check that:

- `--skip-assets` was not used
- Cloud Asset Inventory API is enabled
- The authenticated identity can list assets at the selected scope

### Some projects are missing

Verify that the authenticated identity can list every folder and project under the selected scope. Missing folder permissions can prevent recursive discovery of projects below that folder.

## Example terminal output

```text
Discovering projects recursively...
Projects discovered: 3
Collecting Cloud Asset Inventory...
[1/3] Inventorying corp-prod-app...
[2/3] Inventorying corp-prod-platform...
[3/3] Inventorying corp-shared-services...
Writing reports...

=== Azure Migration Baseline ===
Projects                              3
Compute VMs - all                     8
Compute VMs - running                 7
vCPU - all VMs                       48
Memory GiB - all VMs                222
Persistent disks                     10
Persistent disk capacity GiB       2500
GKE clusters                          1
GKE node pools                        2
Managed instance groups               3

Generated:
 - gcp_azure_inventory.xlsx
 - gcp_azure_inventory_assets.csv
 - gcp_azure_inventory_asset_summary.csv
 - gcp_azure_inventory_compute.csv
 - gcp_azure_inventory_disks.csv
 - gcp_azure_inventory_gke_nodepools.csv
```

## Security notes

- Prefer Application Default Credentials or short-lived workload credentials over long-lived service-account key files.
- Do not commit credential files to source control.
- The reports can contain project names, internal IP addresses, service-account names, labels, metadata keys, and infrastructure topology. Store and share them as sensitive infrastructure documentation.
- Review VM metadata and labels before distributing the workbook outside the organization.

## Disclaimer

This tool creates a discovery inventory and preliminary Azure migration baseline. It does not replace detailed architecture validation, dependency analysis, application compatibility testing, performance-based right-sizing, security assessment, or Azure pricing validation.
