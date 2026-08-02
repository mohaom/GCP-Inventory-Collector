"""GCP-to-Azure service and VM family mapping heuristics."""
from __future__ import annotations

from typing import Optional, Tuple


def azure_service_mapping(asset_type: str) -> Tuple[str, str]:
    mapping = [
        ("compute.googleapis.com/Instance", "Azure Virtual Machines / VM Scale Sets / AKS nodes", "Determine target from GKE and MIG association."),
        ("compute.googleapis.com/Disk", "Azure Managed Disks", "Map disk tier using capacity, IOPS, throughput, and latency."),
        ("compute.googleapis.com/Image", "Azure Compute Gallery", "Validate OS support and image licensing."),
        ("compute.googleapis.com/Snapshot", "Azure Disk Snapshots / Azure Backup", "Review retention and recovery requirements."),
        ("compute.googleapis.com/Network", "Azure Virtual Network", "Recreate CIDR, peering, DNS, routing, and segmentation."),
        ("compute.googleapis.com/Subnetwork", "Azure VNet Subnet", "Validate address-space overlap and delegated subnet needs."),
        ("compute.googleapis.com/Firewall", "Network Security Groups / Azure Firewall", "Translate allow/deny rules and service tags."),
        ("compute.googleapis.com/ForwardingRule", "Azure Load Balancer / Application Gateway / Front Door", "Target depends on L4/L7, internal/external, and global/regional scope."),
        ("compute.googleapis.com/BackendService", "Azure Load Balancer / Application Gateway backend pool", "Capture health probes, affinity, protocol, timeout, and capacity."),
        ("compute.googleapis.com/Address", "Azure Public IP", "Check regional/global and static/dynamic requirements."),
        ("compute.googleapis.com/Router", "Azure VPN Gateway / Route Server / NAT Gateway", "Review BGP, Cloud NAT, routes, and HA."),
        ("compute.googleapis.com/VpnTunnel", "Azure VPN Gateway connection", "Capture IKE/IPsec policy, bandwidth, and redundancy."),
        ("container.googleapis.com/Cluster", "Azure Kubernetes Service (AKS)", "Collect workloads, requests/limits, ingress, storage classes, and add-ons."),
        ("sqladmin.googleapis.com/Instance", "Azure SQL / Azure Database for PostgreSQL or MySQL", "Requires engine, version, vCPU, RAM, storage, IOPS, HA, replicas, and extensions."),
        ("storage.googleapis.com/Bucket", "Azure Blob Storage / ADLS Gen2", "Collect stored bytes, object count, access tier, versioning, lifecycle, and egress."),
        ("pubsub.googleapis.com/Topic", "Azure Service Bus / Event Grid / Event Hubs", "Choose by ordering, throughput, retention, and delivery semantics."),
        ("pubsub.googleapis.com/Subscription", "Azure Service Bus subscription / Event Grid subscription", "Capture filters, retry, dead-letter, ack deadline, and retention."),
        ("run.googleapis.com/Service", "Azure Container Apps / App Service", "Collect CPU/memory limits, concurrency, min/max instances, and networking."),
        ("cloudfunctions.googleapis.com/Function", "Azure Functions", "Collect runtime, trigger, memory, timeout, concurrency, and dependencies."),
        ("artifactregistry.googleapis.com/Repository", "Azure Container Registry", "Collect repository format, size, retention, and geo-replication."),
        ("bigquery.googleapis.com/Dataset", "Microsoft Fabric / Azure Synapse / ADLS Gen2", "Requires table sizes, query patterns, slots, pipelines, and governance."),
        ("bigquery.googleapis.com/Table", "Microsoft Fabric / Azure Synapse / ADLS Gen2", "Collect bytes, partitions, clustering, update frequency, and dependencies."),
        ("redis.googleapis.com/Instance", "Azure Managed Redis", "Collect tier, memory, throughput, clustering, persistence, and HA."),
        ("secretmanager.googleapis.com/Secret", "Azure Key Vault", "Map access policies/RBAC, rotation, versions, and private endpoints."),
        ("cloudkms.googleapis.com/CryptoKey", "Azure Key Vault / Managed HSM", "Map key type, protection level, rotation, and IAM."),
        ("dns.googleapis.com/ManagedZone", "Azure DNS / Private DNS", "Capture records, forwarding, private visibility, and DNSSEC."),
        ("iam.googleapis.com/ServiceAccount", "Microsoft Entra workload identity / Managed Identity", "Map identity usage and least-privilege roles."),
        ("logging.googleapis.com/LogSink", "Azure Monitor / Log Analytics / Event Hubs", "Map filters, destinations, retention, and volume."),
        ("monitoring.googleapis.com/AlertPolicy", "Azure Monitor alerts", "Translate signals, thresholds, evaluation windows, and actions."),
        ("spanner.googleapis.com/Instance", "Azure Cosmos DB / Azure SQL", "Requires workload-level architecture assessment; no direct universal equivalent."),
        ("dataproc.googleapis.com/Cluster", "Azure Databricks / HDInsight", "Collect node types, autoscaling, Spark configuration, jobs, and storage."),
        ("dataflow.googleapis.com/Job", "Azure Data Factory / Stream Analytics / Databricks", "Map batch/stream semantics, throughput, windows, and state."),
        ("aiplatform.googleapis.com/", "Azure Machine Learning", "Collect model endpoints, GPUs, pipelines, registries, and data dependencies."),
        ("backupdr.googleapis.com/", "Azure Backup / Azure Site Recovery", "Map protected workloads, retention, RPO/RTO, backup frequency, and immutability."),
        ("netapp.googleapis.com/", "Azure NetApp Files", "Map protocol, capacity, service level, snapshots, and export/security policy."),
        ("osconfig.googleapis.com/", "Azure Update Manager / Azure Automation / Azure Arc", "Recreate patch schedules, OS policies, reboot behavior, and instance targeting."),
    ]
    for prefix, target, note in mapping:
        if asset_type.startswith(prefix):
            return target, note
    return "Manual architecture assessment", "No one-to-one mapping is encoded; review service capabilities and workload requirements."


def azure_vm_family(vcpus: Optional[int], memory_gib: Optional[float], gpu_count: int, machine_type: str) -> Tuple[str, str]:
    if gpu_count > 0:
        return "Azure N-series", "GPU model, GPU memory, interconnect, driver, and quota must be matched explicitly."

    machine_type = (machine_type or "").lower()
    if machine_type.startswith("t2a"):
        return "Azure ARM-based Dps/Eps family or x64 fallback", "Validate application and container image compatibility with Arm64."

    if not vcpus or memory_gib is None:
        return "Manual selection", "Machine-type specifications could not be retrieved."

    ratio = memory_gib / max(vcpus, 1)
    if memory_gib >= 512 or ratio >= 12:
        family = "Azure M-series (memory optimized)"
    elif ratio >= 6:
        family = "Azure E-series (memory optimized)"
    elif ratio <= 2.5:
        family = "Azure F-series (compute optimized)"
    else:
        family = "Azure D-series (general purpose)"

    note = f"Select an available SKU with at least {vcpus} vCPU and {memory_gib:g} GiB RAM in the target Azure region."
    return family, note
