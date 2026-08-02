"""Compute Engine, Managed Instance Group, and GKE inventory collectors."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from googleapiclient.errors import HttpError

from ..helpers import (
    basename,
    compact_json,
    execute,
    extract_path_value,
    infer_os,
    join_values,
    key_value_string,
    metadata_to_dict,
    parse_custom_machine_type,
    zone_to_region,
)
from ..mapping import azure_vm_family


def list_disks(project: str, compute, errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    by_self_link: Dict[str, Dict[str, Any]] = {}
    token = None

    while True:
        try:
            kwargs: Dict[str, Any] = {"project": project, "maxResults": 500}
            if token:
                kwargs["pageToken"] = token
            response = execute(compute.disks().aggregatedList(**kwargs))
        except Exception as ex:
            errors.append({"Area": "Compute disks", "Project": project, "Error": str(ex)})
            break

        for scope_name, scoped in response.get("items", {}).items():
            for disk in scoped.get("disks", []) or []:
                zone = basename(disk.get("zone", ""))
                region = basename(disk.get("region", "")) or zone_to_region(zone)
                users = disk.get("users", []) or []
                row = {
                    "Project": project,
                    "Disk": disk.get("name", ""),
                    "Scope": "Regional" if disk.get("region") else "Zonal",
                    "Region": region,
                    "Zone": zone,
                    "Size GiB": int(disk.get("sizeGb", 0) or 0),
                    "Disk Type": basename(disk.get("type", "")),
                    "Status": disk.get("status", ""),
                    "Attached To": join_values(basename(user) for user in users),
                    "Attached Count": len(users),
                    "Source Image": disk.get("sourceImage", ""),
                    "Source Snapshot": disk.get("sourceSnapshot", ""),
                    "Licenses": join_values(disk.get("licenses", []) or []),
                    "Provisioned IOPS": disk.get("provisionedIops", ""),
                    "Provisioned Throughput": disk.get("provisionedThroughput", ""),
                    "Physical Block Size Bytes": disk.get("physicalBlockSizeBytes", ""),
                    "Architecture": disk.get("architecture", ""),
                    "Labels": key_value_string(disk.get("labels", {})),
                    "Self Link": disk.get("selfLink", ""),
                    "Azure Target": "Azure Managed Disk",
                }
                rows.append(row)
                if disk.get("selfLink"):
                    by_self_link[disk["selfLink"]] = row

        token = response.get("nextPageToken")
        if not token:
            break

    return rows, by_self_link


def get_machine_type_specs(project: str, zone: str, machine_type: str, compute,
                           cache: Dict[Tuple[str, str, str], Dict[str, Any]],
                           errors: List[Dict[str, str]]) -> Dict[str, Any]:
    key = (project, zone, machine_type)
    if key in cache:
        return cache[key]

    try:
        value = execute(compute.machineTypes().get(
            project=project,
            zone=zone,
            machineType=machine_type,
        ))
        result = {
            "vCPUs": int(value.get("guestCpus", 0) or 0),
            "Memory GiB": round(float(value.get("memoryMb", 0) or 0) / 1024.0, 2),
            "Shared CPU": bool(value.get("isSharedCpu", False)),
            "Maximum Persistent Disks": value.get("maximumPersistentDisks", ""),
            "Maximum Persistent Disk Size Gb": value.get("maximumPersistentDisksSizeGb", ""),
        }
    except Exception as ex:
        vcpus, memory_gib = parse_custom_machine_type(machine_type)
        result = {
            "vCPUs": vcpus,
            "Memory GiB": memory_gib,
            "Shared CPU": "",
            "Maximum Persistent Disks": "",
            "Maximum Persistent Disk Size Gb": "",
        }
        errors.append({
            "Area": "Machine type lookup",
            "Project": project,
            "Error": f"{zone}/{machine_type}: {ex}",
        })

    cache[key] = result
    return result


def list_instances(project: str, compute, disk_lookup: Dict[str, Dict[str, Any]],
                   machine_cache: Dict[Tuple[str, str, str], Dict[str, Any]],
                   errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], set[str]]:
    vm_rows: List[Dict[str, Any]] = []
    nic_rows: List[Dict[str, Any]] = []
    regions: set[str] = set()
    token = None

    while True:
        try:
            kwargs: Dict[str, Any] = {"project": project, "maxResults": 500}
            if token:
                kwargs["pageToken"] = token
            response = execute(compute.instances().aggregatedList(**kwargs))
        except Exception as ex:
            errors.append({"Area": "Compute instances", "Project": project, "Error": str(ex)})
            break

        for _, scoped in response.get("items", {}).items():
            for inst in scoped.get("instances", []) or []:
                zone = basename(inst.get("zone", ""))
                region = zone_to_region(zone)
                if region:
                    regions.add(region)
                machine_type = basename(inst.get("machineType", ""))
                specs = get_machine_type_specs(project, zone, machine_type, compute, machine_cache, errors)

                metadata = metadata_to_dict(inst)
                labels = inst.get("labels", {}) or {}
                tags = inst.get("tags", {}).get("items", []) or []
                scheduling = inst.get("scheduling", {}) or {}
                provisioning_model = scheduling.get("provisioningModel", "STANDARD")

                disk_details: List[str] = []
                total_disk_gib = 0
                boot_disk_gib = 0
                persistent_disk_count = 0
                local_ssd_count = 0
                disk_types: List[str] = []
                source_images: List[str] = []
                licenses: List[str] = []

                for attached in inst.get("disks", []) or []:
                    source = attached.get("source", "")
                    disk = disk_lookup.get(source, {})
                    if attached.get("type") == "SCRATCH":
                        local_ssd_count += 1
                        disk_details.append(f"{attached.get('deviceName', '')}:local-ssd")
                        continue
                    persistent_disk_count += 1
                    size = int(disk.get("Size GiB", 0) or 0)
                    total_disk_gib += size
                    if attached.get("boot"):
                        boot_disk_gib = size
                    dtype = disk.get("Disk Type", "")
                    if dtype:
                        disk_types.append(dtype)
                    if disk.get("Source Image"):
                        source_images.append(disk["Source Image"])
                    if disk.get("Licenses"):
                        licenses.extend(disk["Licenses"].split(";"))
                    disk_details.append(
                        f"{attached.get('deviceName', basename(source))}:{size}GiB:{dtype}:"
                        f"boot={bool(attached.get('boot'))}:mode={attached.get('mode', '')}"
                    )

                internal_ips: List[str] = []
                external_ips: List[str] = []
                networks: List[str] = []
                subnets: List[str] = []
                network_tiers: List[str] = []

                for nic in inst.get("networkInterfaces", []) or []:
                    internal_ip = nic.get("networkIP", "")
                    access_configs = nic.get("accessConfigs", []) or []
                    nic_external_ips = [cfg.get("natIP", "") for cfg in access_configs if cfg.get("natIP")]
                    tiers = [cfg.get("networkTier", "") for cfg in access_configs if cfg.get("networkTier")]
                    internal_ips.extend([internal_ip] if internal_ip else [])
                    external_ips.extend(nic_external_ips)
                    networks.append(basename(nic.get("network", "")))
                    subnets.append(basename(nic.get("subnetwork", "")))
                    network_tiers.extend(tiers)
                    nic_rows.append({
                        "Project": project,
                        "VM": inst.get("name", ""),
                        "Region": region,
                        "Zone": zone,
                        "NIC": nic.get("name", ""),
                        "Network": basename(nic.get("network", "")),
                        "Subnetwork": basename(nic.get("subnetwork", "")),
                        "Internal IP": internal_ip,
                        "External IPs": join_values(nic_external_ips),
                        "Network Tiers": join_values(tiers),
                        "IP Stack Type": nic.get("stackType", ""),
                        "IPv6 Address": nic.get("ipv6Address", ""),
                        "Queue Count": nic.get("queueCount", ""),
                        "NIC Type": nic.get("nicType", ""),
                    })

                gpu_count = sum(int(gpu.get("acceleratorCount", 0) or 0) for gpu in inst.get("guestAccelerators", []) or [])
                gpu_types = [basename(gpu.get("acceleratorType", "")) for gpu in inst.get("guestAccelerators", []) or []]
                os_name = infer_os(join_values(source_images), licenses, metadata)
                azure_family, azure_note = azure_vm_family(
                    specs.get("vCPUs"), specs.get("Memory GiB"), gpu_count, machine_type
                )

                vm_rows.append({
                    "Project": project,
                    "VM": inst.get("name", ""),
                    "Instance ID": inst.get("id", ""),
                    "Region": region,
                    "Zone": zone,
                    "Status": inst.get("status", ""),
                    "Creation Timestamp": inst.get("creationTimestamp", ""),
                    "Machine Type": machine_type,
                    "vCPUs": specs.get("vCPUs"),
                    "Memory GiB": specs.get("Memory GiB"),
                    "Shared CPU": specs.get("Shared CPU"),
                    "CPU Platform": inst.get("cpuPlatform", ""),
                    "Minimum CPU Platform": inst.get("minCpuPlatform", ""),
                    "OS Guess": os_name,
                    "Provisioning Model": provisioning_model,
                    "Preemptible": bool(scheduling.get("preemptible", False)),
                    "Automatic Restart": scheduling.get("automaticRestart", ""),
                    "On Host Maintenance": scheduling.get("onHostMaintenance", ""),
                    "Availability Domain": scheduling.get("nodeAffinities", ""),
                    "Deletion Protection": bool(inst.get("deletionProtection", False)),
                    "Confidential Compute": bool(inst.get("confidentialInstanceConfig", {}).get("enableConfidentialCompute", False)),
                    "Shielded Secure Boot": bool(inst.get("shieldedInstanceConfig", {}).get("enableSecureBoot", False)),
                    "Shielded vTPM": bool(inst.get("shieldedInstanceConfig", {}).get("enableVtpm", False)),
                    "Shielded Integrity Monitoring": bool(inst.get("shieldedInstanceConfig", {}).get("enableIntegrityMonitoring", False)),
                    "GPU Count": gpu_count,
                    "GPU Types": join_values(gpu_types),
                    "Persistent Disk Count": persistent_disk_count,
                    "Local SSD Count": local_ssd_count,
                    "Total Persistent Disk GiB": total_disk_gib,
                    "Boot Disk GiB": boot_disk_gib,
                    "Disk Types": join_values(sorted(set(disk_types))),
                    "Disk Details": join_values(disk_details),
                    "NIC Count": len(inst.get("networkInterfaces", []) or []),
                    "Networks": join_values(sorted(set(networks))),
                    "Subnetworks": join_values(sorted(set(subnets))),
                    "Internal IPs": join_values(internal_ips),
                    "External IPs": join_values(external_ips),
                    "External IP Count": len(external_ips),
                    "Network Tiers": join_values(sorted(set(network_tiers))),
                    "Can IP Forward": bool(inst.get("canIpForward", False)),
                    "Service Accounts": join_values(sa.get("email", "") for sa in inst.get("serviceAccounts", []) or []),
                    "Tags": join_values(tags),
                    "Labels": key_value_string(labels),
                    "Metadata Keys": join_values(sorted(metadata.keys())),
                    "GKE Cluster": labels.get("goog-k8s-cluster-name", ""),
                    "GKE Node Pool": labels.get("goog-k8s-node-pool-name", labels.get("goog-gke-node-pool", "")),
                    "Managed Instance Group": "",
                    "MIG Scope": "",
                    "MIG Current Action": "",
                    "Workload Association": "GKE Node" if labels.get("goog-k8s-cluster-name") else "Standalone VM",
                    "Azure Compute Target": "AKS Node Pool" if labels.get("goog-k8s-cluster-name") else "Azure Virtual Machine",
                    "Azure VM Family Candidate": azure_family,
                    "Azure Sizing Note": azure_note,
                    "Self Link": inst.get("selfLink", ""),
                })

        token = response.get("nextPageToken")
        if not token:
            break

    return vm_rows, nic_rows, regions


def list_managed_instance_groups(project: str, compute, regions: set[str],
                                 errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str, str], Dict[str, str]]]:
    mig_rows: List[Dict[str, Any]] = []
    vm_map: Dict[Tuple[str, str, str], Dict[str, str]] = {}

    # Zonal MIGs.
    token = None
    while True:
        try:
            kwargs: Dict[str, Any] = {"project": project, "maxResults": 500}
            if token:
                kwargs["pageToken"] = token
            response = execute(compute.instanceGroupManagers().aggregatedList(**kwargs))
        except Exception as ex:
            errors.append({"Area": "Zonal MIG inventory", "Project": project, "Error": str(ex)})
            break

        for _, scoped in response.get("items", {}).items():
            for mig in scoped.get("instanceGroupManagers", []) or []:
                zone = basename(mig.get("zone", ""))
                name = mig.get("name", "")
                members: List[Dict[str, Any]] = []
                try:
                    member_response = execute(compute.instanceGroupManagers().listManagedInstances(
                        project=project, zone=zone, instanceGroupManager=name
                    ))
                    members = member_response.get("managedInstances", []) or []
                except Exception as ex:
                    errors.append({"Area": "Zonal MIG members", "Project": project, "Error": f"{zone}/{name}: {ex}"})

                for member in members:
                    vm_url = member.get("instance", "")
                    vm_zone = extract_path_value(vm_url, "zones") or zone
                    vm_name = extract_path_value(vm_url, "instances") or basename(vm_url)
                    vm_map[(project, vm_zone, vm_name)] = {
                        "Managed Instance Group": name,
                        "MIG Scope": "Zonal",
                        "MIG Current Action": member.get("currentAction", ""),
                    }

                mig_rows.append({
                    "Project": project,
                    "MIG": name,
                    "Scope": "Zonal",
                    "Region": zone_to_region(zone),
                    "Zone": zone,
                    "Target Size": mig.get("targetSize", ""),
                    "Current Members": len(members),
                    "Instance Template": basename(mig.get("instanceTemplate", "")),
                    "Base Instance Name": mig.get("baseInstanceName", ""),
                    "Status Stable": mig.get("status", {}).get("isStable", ""),
                    "Distribution Policy": compact_json(mig.get("distributionPolicy", {})),
                    "Update Policy": compact_json(mig.get("updatePolicy", {})),
                    "Autohealing Policies": compact_json(mig.get("autoHealingPolicies", [])),
                    "Azure Target": "Azure Virtual Machine Scale Sets",
                })

        token = response.get("nextPageToken")
        if not token:
            break

    # Regional MIGs. Query only regions containing discovered VMs to avoid dozens
    # of unnecessary API calls. A zero-instance MIG in an otherwise unused region
    # may therefore not appear and will be recorded in the recommendations sheet.
    for region in sorted(regions):
        token = None
        while True:
            try:
                kwargs = {"project": project, "region": region, "maxResults": 500}
                if token:
                    kwargs["pageToken"] = token
                response = execute(compute.regionInstanceGroupManagers().list(**kwargs))
            except Exception as ex:
                # API-disabled/permission errors can repeat by region; retain one row.
                errors.append({"Area": "Regional MIG inventory", "Project": project, "Error": f"{region}: {ex}"})
                break

            for mig in response.get("instanceGroupManagers", []) or []:
                name = mig.get("name", "")
                members: List[Dict[str, Any]] = []
                try:
                    member_response = execute(compute.regionInstanceGroupManagers().listManagedInstances(
                        project=project, region=region, instanceGroupManager=name
                    ))
                    members = member_response.get("managedInstances", []) or []
                except Exception as ex:
                    errors.append({"Area": "Regional MIG members", "Project": project, "Error": f"{region}/{name}: {ex}"})

                for member in members:
                    vm_url = member.get("instance", "")
                    vm_zone = extract_path_value(vm_url, "zones")
                    vm_name = extract_path_value(vm_url, "instances") or basename(vm_url)
                    vm_map[(project, vm_zone, vm_name)] = {
                        "Managed Instance Group": name,
                        "MIG Scope": "Regional",
                        "MIG Current Action": member.get("currentAction", ""),
                    }

                mig_rows.append({
                    "Project": project,
                    "MIG": name,
                    "Scope": "Regional",
                    "Region": region,
                    "Zone": "",
                    "Target Size": mig.get("targetSize", ""),
                    "Current Members": len(members),
                    "Instance Template": basename(mig.get("instanceTemplate", "")),
                    "Base Instance Name": mig.get("baseInstanceName", ""),
                    "Status Stable": mig.get("status", {}).get("isStable", ""),
                    "Distribution Policy": compact_json(mig.get("distributionPolicy", {})),
                    "Update Policy": compact_json(mig.get("updatePolicy", {})),
                    "Autohealing Policies": compact_json(mig.get("autoHealingPolicies", [])),
                    "Azure Target": "Azure Virtual Machine Scale Sets",
                })

            token = response.get("nextPageToken")
            if not token:
                break

    return mig_rows, vm_map


def list_instance_group_members(project: str, group_url: str, compute,
                                errors: List[Dict[str, str]]) -> List[Tuple[str, str, str]]:
    zone = extract_path_value(group_url, "zones")
    group_name = extract_path_value(group_url, "instanceGroups") or extract_path_value(group_url, "instanceGroupManagers") or basename(group_url)
    if not zone or not group_name:
        return []

    members: List[Tuple[str, str, str]] = []
    token = None
    while True:
        try:
            kwargs: Dict[str, Any] = {
                "project": project,
                "zone": zone,
                "instanceGroup": group_name,
                "body": {"instanceState": "ALL"},
                "maxResults": 500,
            }
            if token:
                kwargs["pageToken"] = token
            response = execute(compute.instanceGroups().listInstances(**kwargs))
            for item in response.get("items", []) or []:
                vm_url = item.get("instance", "")
                vm_zone = extract_path_value(vm_url, "zones") or zone
                vm_name = extract_path_value(vm_url, "instances") or basename(vm_url)
                members.append((project, vm_zone, vm_name))
            token = response.get("nextPageToken")
            if not token:
                break
        except HttpError as ex:
            # GKE (especially Autopilot, "gk3-" clusters) reports instance group
            # URLs that can be stale or transient. The backing group may already
            # be deleted by the time we query it, yielding a benign 404. Record it
            # as an informational skip rather than a hard error.
            if getattr(ex, "resp", None) is not None and ex.resp.status == 404:
                errors.append({"Area": "GKE node group members (skipped, transient/not found)",
                               "Project": project, "Error": f"{zone}/{group_name}: instance group no longer exists (404)"})
            else:
                errors.append({"Area": "GKE node group members", "Project": project, "Error": f"{zone}/{group_name}: {ex}"})
            break
        except Exception as ex:
            errors.append({"Area": "GKE node group members", "Project": project, "Error": f"{zone}/{group_name}: {ex}"})
            break
    return members


def list_gke(project: str, container, compute, errors: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[Tuple[str, str, str], Dict[str, str]]]:
    cluster_rows: List[Dict[str, Any]] = []
    nodepool_rows: List[Dict[str, Any]] = []
    vm_map: Dict[Tuple[str, str, str], Dict[str, str]] = {}

    try:
        response = execute(container.projects().locations().clusters().list(
            parent=f"projects/{project}/locations/-"
        ))
    except Exception as ex:
        errors.append({"Area": "GKE clusters", "Project": project, "Error": str(ex)})
        return cluster_rows, nodepool_rows, vm_map

    for cluster in response.get("clusters", []) or []:
        cluster_name = cluster.get("name", "")
        location = cluster.get("location", cluster.get("zone", ""))
        autopilot = bool(cluster.get("autopilot", {}).get("enabled", False))
        private_config = cluster.get("privateClusterConfig", {}) or {}
        release_channel = cluster.get("releaseChannel", {}).get("channel", "")

        cluster_rows.append({
            "Project": project,
            "Cluster": cluster_name,
            "Location": location,
            "Mode": "Autopilot" if autopilot else "Standard",
            "Status": cluster.get("status", ""),
            "Current Master Version": cluster.get("currentMasterVersion", ""),
            "Current Node Version": cluster.get("currentNodeVersion", ""),
            "Current Node Count": cluster.get("currentNodeCount", ""),
            "Initial Cluster Version": cluster.get("initialClusterVersion", ""),
            "Network": basename(cluster.get("network", "")),
            "Subnetwork": basename(cluster.get("subnetwork", "")),
            "Cluster IPv4 CIDR": cluster.get("clusterIpv4Cidr", ""),
            "Services IPv4 CIDR": cluster.get("servicesIpv4Cidr", ""),
            "Private Nodes": private_config.get("enablePrivateNodes", ""),
            "Private Endpoint": private_config.get("enablePrivateEndpoint", ""),
            "Master IPv4 CIDR": private_config.get("masterIpv4CidrBlock", ""),
            "Release Channel": release_channel,
            "Workload Identity Pool": cluster.get("workloadIdentityConfig", {}).get("workloadPool", ""),
            "Logging Service": cluster.get("loggingService", ""),
            "Monitoring Service": cluster.get("monitoringService", ""),
            "Database Encryption": compact_json(cluster.get("databaseEncryption", {})),
            "Resource Labels": key_value_string(cluster.get("resourceLabels", {})),
            "Azure Target": "Azure Kubernetes Service (AKS)",
        })

        for nodepool in cluster.get("nodePools", []) or []:
            config = nodepool.get("config", {}) or {}
            autoscaling = nodepool.get("autoscaling", {}) or {}
            group_urls = (
                nodepool.get("instanceGroupUrls", [])
                or nodepool.get("managedInstanceGroupUrls", [])
                or []
            )
            member_keys: List[Tuple[str, str, str]] = []
            for group_url in group_urls:
                member_keys.extend(list_instance_group_members(project, group_url, compute, errors))

            pool_name = nodepool.get("name", "")
            for key in member_keys:
                vm_map[key] = {
                    "GKE Cluster": cluster_name,
                    "GKE Node Pool": pool_name,
                    "Workload Association": "GKE Node",
                    "Azure Compute Target": "AKS Node Pool",
                }

            accelerators = config.get("accelerators", []) or []
            gpu_count_per_node = sum(int(a.get("acceleratorCount", 0) or 0) for a in accelerators)
            gpu_types = [a.get("acceleratorType", "") for a in accelerators]
            machine_type = config.get("machineType", "")

            nodepool_rows.append({
                "Project": project,
                "Cluster": cluster_name,
                "Cluster Location": location,
                "Cluster Mode": "Autopilot" if autopilot else "Standard",
                "Node Pool": pool_name,
                "Status": nodepool.get("status", ""),
                "Version": nodepool.get("version", ""),
                "Machine Type": machine_type,
                "Image Type": config.get("imageType", ""),
                "Disk Size GiB": config.get("diskSizeGb", ""),
                "Disk Type": config.get("diskType", ""),
                "Local SSD Count": config.get("localSsdCount", ""),
                "Boot Disk KMS Key": config.get("bootDiskKmsKey", ""),
                "Service Account": config.get("serviceAccount", ""),
                "Spot": config.get("spot", ""),
                "Preemptible": config.get("preemptible", ""),
                "Initial Node Count": nodepool.get("initialNodeCount", ""),
                "Discovered VM Count": len(set(member_keys)),
                "Locations": join_values(nodepool.get("locations", []) or []),
                "Autoscaling Enabled": autoscaling.get("enabled", ""),
                "Autoscaling Min Nodes": autoscaling.get("minNodeCount", ""),
                "Autoscaling Max Nodes": autoscaling.get("maxNodeCount", ""),
                "Autoscaling Total Min Nodes": autoscaling.get("totalMinNodeCount", ""),
                "Autoscaling Total Max Nodes": autoscaling.get("totalMaxNodeCount", ""),
                "Max Pods Per Node": nodepool.get("maxPodsConstraint", {}).get("maxPodsPerNode", ""),
                "GPU Count per Node": gpu_count_per_node,
                "GPU Types": join_values(gpu_types),
                "Labels": key_value_string(config.get("labels", {})),
                "Resource Labels": key_value_string(nodepool.get("resourceLabels", {})),
                "Taints": compact_json(config.get("taints", [])),
                "Tags": join_values(config.get("tags", []) or []),
                "OAuth Scopes": join_values(config.get("oauthScopes", []) or []),
                "Instance Groups": join_values(group_urls),
                "Auto Upgrade": nodepool.get("management", {}).get("autoUpgrade", ""),
                "Auto Repair": nodepool.get("management", {}).get("autoRepair", ""),
                "Upgrade Strategy": nodepool.get("upgradeSettings", {}).get("strategy", ""),
                "Max Surge": nodepool.get("upgradeSettings", {}).get("maxSurge", ""),
                "Max Unavailable": nodepool.get("upgradeSettings", {}).get("maxUnavailable", ""),
                "Shielded Secure Boot": config.get("shieldedInstanceConfig", {}).get("enableSecureBoot", ""),
                "Shielded Integrity Monitoring": config.get("shieldedInstanceConfig", {}).get("enableIntegrityMonitoring", ""),
                "GVNIC Enabled": config.get("gvnic", {}).get("enabled", ""),
                "Azure Target": "AKS Node Pool",
                "Azure Sizing Note": "Match node count/autoscaling plus pod CPU-memory requests, limits, daemonsets, system reserve, and availability-zone design.",
            })

    return cluster_rows, nodepool_rows, vm_map


def apply_associations(vm_rows: List[Dict[str, Any]], mig_map: Dict[Tuple[str, str, str], Dict[str, str]],
                       gke_map: Dict[Tuple[str, str, str], Dict[str, str]]) -> None:
    for row in vm_rows:
        key = (row.get("Project", ""), row.get("Zone", ""), row.get("VM", ""))
        if key in mig_map:
            row.update(mig_map[key])
            if row.get("Workload Association") != "GKE Node":
                row["Workload Association"] = "Managed Instance Group VM"
                row["Azure Compute Target"] = "Azure Virtual Machine Scale Set"
        if key in gke_map:
            row.update(gke_map[key])
