from __future__ import annotations

from typing import Dict, Set


def parse_cpu_set(expr: str) -> Set[int]:
    expr = expr.strip()
    if not expr:
        return set()
    result: Set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                raise ValueError(f"invalid cpu range: {part}")
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    return result


def build_cpu_numa_map_from_host(host: Dict) -> Dict[int, int]:
    mapping: Dict[int, int] = {}

    nodes = host.get("numa_nodes")
    if isinstance(nodes, list):
        for node in nodes:
            node_id = int(node.get("id", 0))
            cpus_expr = str(node.get("cpus", ""))
            for cpu in parse_cpu_set(cpus_expr):
                mapping[cpu] = node_id
        return mapping

    total_cpus = int(host.get("cpus", 0))
    numa_nodes = int(nodes or 1)
    if total_cpus <= 0 or numa_nodes <= 0:
        raise ValueError("cpus and numa_nodes must be positive")

    base = total_cpus // numa_nodes
    rem = total_cpus % numa_nodes

    cpu = 0
    for node_id in range(numa_nodes):
        count = base + (1 if node_id < rem else 0)
        for _ in range(count):
            mapping[cpu] = node_id
            cpu += 1

    return mapping
