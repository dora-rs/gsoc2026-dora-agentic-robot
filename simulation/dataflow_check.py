"""Static connectivity checker for dora dataflow YAML files (Week 3).

Validates that every node input is wired to a real output:
  - builtin sources (`dora/timer/...`) are ignored
  - every `node/output` input references a declared node and one of its outputs

This catches dangling wires before `dora start` — without MuJoCo, dora-moveit2,
or a running dataflow. Usable as a library (`check`) or a CLI:

    python -m simulation.dataflow_check dataflows/ur5e_full_pipeline.yml
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import yaml

BUILTIN_PREFIXES = ("dora/",)


@dataclass
class Node:
    id: str
    inputs: dict[str, str] = field(default_factory=dict)   # input_name -> "src_node/output"
    outputs: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    nodes: dict[str, Node]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def _input_source(value) -> str:
    """An input value may be a bare 'node/output' string or a mapping with 'source'."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("source", ""))
    return ""


def load_nodes(path: str) -> dict[str, Node]:
    with open(path) as f:
        spec = yaml.safe_load(f)
    nodes: dict[str, Node] = {}
    for entry in spec.get("nodes", []):
        nid = entry["id"]
        raw_inputs = entry.get("inputs", {}) or {}
        inputs = {name: _input_source(val) for name, val in raw_inputs.items()}
        nodes[nid] = Node(id=nid, inputs=inputs, outputs=list(entry.get("outputs", []) or []))
    return nodes


def check(path: str) -> CheckResult:
    nodes = load_nodes(path)
    errors: list[str] = []

    for nid, node in nodes.items():
        for in_name, source in node.inputs.items():
            if not source or source.startswith(BUILTIN_PREFIXES):
                continue
            if "/" not in source:
                errors.append(f"{nid}.{in_name}: malformed source '{source}' (expected 'node/output')")
                continue
            src_node, src_out = source.split("/", 1)
            if src_node not in nodes:
                errors.append(f"{nid}.{in_name}: references unknown node '{src_node}'")
            elif src_out not in nodes[src_node].outputs:
                errors.append(
                    f"{nid}.{in_name}: '{src_node}' does not declare output '{src_out}' "
                    f"(declares: {nodes[src_node].outputs})"
                )
    return CheckResult(nodes=nodes, errors=errors)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m simulation.dataflow_check <dataflow.yml> [...]")
        return 2

    rc = 0
    for path in argv:
        result = check(path)
        if result.ok:
            print(f"OK   {path}: {len(result.nodes)} nodes, all input edges resolve")
        else:
            rc = 1
            print(f"FAIL {path}:")
            for err in result.errors:
                print(f"  - {err}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
