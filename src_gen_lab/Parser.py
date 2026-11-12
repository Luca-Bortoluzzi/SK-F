#!/usr/bin/env python3
"""Syntax, structure and semantic analysis of an SK-F lab file.

The Parser receives the token tree built by the Lexer and works in two phases.

1. Syntax and structure, driven by the dictionary: each key must be allowed
   in the section where it appears, required keys must be present, lists,
   mappings and sections must have the right shape and every value must have
   the right type (validator rule 3).

2. Semantics: the lab is turned into a model (``Lab``, ``Node``,
   ``Interface``) and checked against the remaining validator rules (5-23).
   Everything the framework derives by itself is computed here, once:
   default gateways, router IDs, loopbacks, BGP neighbors and redistribution.
   The translator only renders the model.
"""

from __future__ import annotations

import difflib
import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from src_gen_lab.issues import ConfigPath, IssueReport, Position
from src_gen_lab.Lexer import Mapping, Node as Token, Scalar, Sequence, describe, tokenize
from src_gen_lab.loader import Dictionary, Key, Section, default_dictionary

IPv4Interface = ipaddress.IPv4Interface
IPv4Address = ipaddress.IPv4Address
IPv4Network = ipaddress.IPv4Network


# ---------------------------------------------------------------------------
# Lab model
# ---------------------------------------------------------------------------


@dataclass
class Interface:
    index: int
    network: str
    ip: IPv4Interface | None
    routing: list[str] = field(default_factory=list)  # protocols running on this interface
    area: int | str | None = None  # OSPF area, when OSPF runs here

    @property
    def name(self) -> str:
        return f"eth{self.index}"

    def runs(self, protocol: str) -> bool:
        return self.ip is not None and protocol in self.routing


@dataclass
class Route:
    to: IPv4Network
    via: IPv4Address


@dataclass
class BgpNeighbor:
    address: IPv4Address
    remote_as: int
    peer: str
    internal: bool  # iBGP (same AS)
    via_loopback: bool = False
    next_hop_self: bool = False


@dataclass
class Node:
    name: str
    type: str
    image: str
    interfaces: list[Interface]
    gateway: IPv4Address | None = None
    gateway_derived: bool = False
    routes: list[Route] = field(default_factory=list)
    files: Path | None = None
    startup: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    kathara: dict[str, Any] = field(default_factory=dict)
    routing: list[str] = field(default_factory=list)
    asn: int | None = None
    frr_config: str | None = None
    # Derived by the Parser
    router_id: IPv4Address | None = None
    loopback: IPv4Address | None = None
    bgp_neighbors: list[BgpNeighbor] = field(default_factory=list)
    redistribute: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_router(self) -> bool:
        return self.type == "router"

    @property
    def runs_frr(self) -> bool:
        return self.is_router and bool(self.routing or self.frr_config)


@dataclass
class Lab:
    name: str
    source: Path
    nodes: dict[str, Node]
    description: str | None = None
    version: str | None = None
    author: str | None = None
    email: str | None = None
    web: str | None = None
    capture: list[str] = field(default_factory=list)

    def networks(self) -> dict[str, list[tuple[Node, Interface]]]:
        """Collision domains in order of appearance, with the interfaces attached to each."""
        result: dict[str, list[tuple[Node, Interface]]] = {}
        for node in self.nodes.values():
            for interface in node.interfaces:
                result.setdefault(interface.network, []).append((node, interface))
        return result


def area_number(area: int | str) -> int:
    """OSPF area as a 32-bit number: 10 and 0.0.0.10 are the same area."""
    return area if isinstance(area, int) else int(IPv4Address(area))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class Parser:
    def __init__(self, dictionary: Dictionary, report: IssueReport, source: Path) -> None:
        self.dictionary = dictionary
        self.report = report
        self.source = source
        self.value_positions: dict[ConfigPath, Position] = {}
        self.key_positions: dict[ConfigPath, Position] = {}
        self._subnets: dict[str, IPv4Network] = {}  # network name -> subnet, filled by _check_addresses

    def parse(self, root: Token) -> Lab | None:
        values = self._section(self.dictionary.root, root, ())
        if not self.report.valid or values is None:
            return None
        lab = self._build(values)
        self._analyse(lab, values)
        return lab

    # -- positions ------------------------------------------------------------

    def _at(self, path: ConfigPath) -> Position | None:
        while path not in self.value_positions and path:
            path = path[:-1]
        return self.value_positions.get(path)

    def _at_key(self, path: ConfigPath) -> Position | None:
        return self.key_positions.get(path) or self._at(path)

    def _error(self, message: str, path: ConfigPath, at_key: bool = False) -> None:
        self.report.error(message, self._at_key(path) if at_key else self._at(path), path)

    def _warning(self, message: str, path: ConfigPath, at_key: bool = False) -> None:
        self.report.warning(message, self._at_key(path) if at_key else self._at(path), path)

    # =========================================================================
    # Phase 1: syntax and structure
    # =========================================================================

    def _shape_name(self, token: Token) -> str:
        if isinstance(token, Mapping):
            return "a mapping"
        if isinstance(token, Sequence):
            return "a list"
        return describe(token.value)

    def _section(self, section: Section, token: Token, path: ConfigPath) -> dict[str, Any] | None:
        self.value_positions[path] = token.position
        if not isinstance(token, Mapping):
            self._error(f"expected {section.description} (a mapping), got {self._shape_name(token)}", path)
            return None

        values: dict[str, Any] = {}
        for word, entry in token.entries.items():
            child = path + (word,)
            self.key_positions[child] = entry.key_position
            key = section.keys.get(word)
            if key is None:
                owners = [other.description for other in self.dictionary.sections_with_key(word)]
                self._error(
                    f"'{word}' is not allowed in {section.description}; it belongs to {' or '.join(owners)}",
                    child,
                    at_key=True,
                )
                continue
            value = self._value(key, entry.value, child)
            if value is not None:
                values[word] = value

        for name, key in section.keys.items():
            if name in token.entries:
                continue
            if key.required:
                misspelled = any(
                    isinstance(rejected, str) and difflib.get_close_matches(rejected, [name], cutoff=0.6)
                    for rejected in token.rejected
                )
                if not misspelled:  # already reported by the Lexer as an unknown key
                    self._error(f"missing required key '{name}' in {section.description}", path)
            elif key.default is not None:
                values[name] = key.default
        return values

    def _value(self, key: Key, token: Token, path: ConfigPath) -> Any:
        self.value_positions[path] = token.position

        if key.shape == "section":
            return self._section(self.dictionary.sections[key.target], token, path)

        if key.shape == "map":
            if not isinstance(token, Mapping):
                self._error(f"expected a mapping of names, got {self._shape_name(token)}", path)
                return None
            if len(token.entries) < key.min_items and not token.rejected:
                self._error(f"'{key.name}' needs at least {key.min_items} element(s)", path)
            section = self.dictionary.sections[key.target]
            result = {}
            for name, entry in token.entries.items():
                self.key_positions[path + (name,)] = entry.key_position
                result[name] = self._section(section, entry.value, path + (name,))
            return result

        if key.shape == "list":
            if not isinstance(token, Sequence):
                self._error(f"expected a list, got {self._shape_name(token)}", path)
                return None
            if len(token.items) < key.min_items:
                self._error(f"'{key.name}' needs at least {key.min_items} element(s)", path)
            items = []
            for index, item in enumerate(token.items):
                item_path = path + (index,)
                if self.dictionary.is_section(key.target):
                    items.append(self._section(self.dictionary.sections[key.target], item, item_path))
                else:
                    items.append(self._scalar(key.target, item, item_path))
            return items

        # A single value, or with one_or_list a value or a list of values.
        if key.one_or_list and isinstance(token, Sequence):
            if not token.items:
                self._error(f"'{key.name}' cannot be an empty list", path)
                return None
            items = [self._scalar(key.target, item, path + (index,)) for index, item in enumerate(token.items)]
            repeated = sorted({item for item in items if item is not None and items.count(item) > 1})
            if repeated:
                self._error(f"'{key.name}' repeats {', '.join(repeated)}", path)
            return items
        value = self._scalar(key.target, token, path)
        if key.one_or_list and value is not None:
            return [value]
        return value

    def _scalar(self, type_name: str, token: Token, path: ConfigPath) -> Any:
        self.value_positions[path] = token.position
        value_type = self.dictionary.types[type_name]
        if not isinstance(token, Scalar):
            self._error(f"expected {value_type.description}, got {self._shape_name(token)}", path)
            return None
        if token.malformed:
            return None  # already reported by the Lexer
        if type_name not in token.types:
            self._error(f"expected {value_type.description}, got {describe(token.value)}", path)
            return None
        return token.value

    # =========================================================================
    # Phase 2: model
    # =========================================================================

    def _build(self, values: dict[str, Any]) -> Lab:
        lab_values = values["lab"]
        images = self.dictionary.settings["images"]
        nodes: dict[str, Node] = {}

        for name, raw in values["nodes"].items():
            node_type = raw["type"]
            interfaces = [
                Interface(
                    index=index,
                    network=item["network"],
                    ip=IPv4Interface(item["ip"]) if "ip" in item else None,
                )
                for index, item in enumerate(raw["interfaces"])
            ]
            nodes[name] = Node(
                name=name,
                type=node_type,
                image=raw.get("image", images[node_type]),
                interfaces=interfaces,
                gateway=IPv4Address(raw["gateway"]) if "gateway" in raw else None,
                routes=[Route(IPv4Network(item["to"]), IPv4Address(item["via"])) for item in raw.get("routes", [])],
                files=(self.source.parent / raw["files"]) if "files" in raw else None,
                startup=[str(command) for command in raw.get("startup", [])],
                roles=list(raw.get("roles", [])),
                kathara=dict(raw.get("kathara", {})),
                routing=list(raw.get("routing", [])) if node_type == "router" else [],
                asn=raw.get("asn") if node_type == "router" else None,
                frr_config=raw.get("frr_config") if node_type == "router" else None,
            )

        return Lab(
            name=lab_values["name"],
            source=self.source,
            nodes=nodes,
            description=lab_values.get("description"),
            version=lab_values.get("version"),
            author=lab_values.get("author"),
            email=lab_values.get("email"),
            web=lab_values.get("web"),
            capture=list(values.get("capture", {}).get("networks", [])),
        )

    # =========================================================================
    # Phase 2: semantic rules and derivations
    # =========================================================================

    def _analyse(self, lab: Lab, values: dict[str, Any]) -> None:
        raw_nodes = values["nodes"]
        self._check_router_only_keys(lab, raw_nodes)  # rule 14
        self._resolve_interface_routing(lab, raw_nodes)  # rules 15, 16
        self._check_routing_keys(lab, raw_nodes)  # rules 17, 22
        self._check_addresses(lab)  # rules 5, 6, 7, 8, 9
        self._check_capture(lab)
        self._resolve_gateways(lab)  # rules 10, 11
        self._check_routes(lab)  # rule 12
        self._check_routers(lab)  # rule 13
        self._check_files(lab)  # rule 23
        self._check_ospf_areas(lab)  # rules 18, 19
        self._assign_router_ids(lab)
        self._resolve_bgp(lab)  # rule 20
        self._resolve_redistribution(lab)  # rule 21

    # -- rule 14 --------------------------------------------------------------

    def _check_router_only_keys(self, lab: Lab, raw_nodes: dict[str, Any]) -> None:
        node_keys = [key.name for key in self.dictionary.sections["node"].keys.values() if key.router_only]
        interface_keys = [key.name for key in self.dictionary.sections["interface"].keys.values() if key.router_only]
        for name, node in lab.nodes.items():
            if node.is_router:
                continue
            raw = raw_nodes[name]
            for key in node_keys:
                if key in raw:
                    self._error(f"'{key}' is only for routers ('{name}' is a host)", ("nodes", name, key), at_key=True)
            for index, interface in enumerate(raw["interfaces"]):
                for key in interface_keys:
                    if key in interface:
                        path = ("nodes", name, "interfaces", index, key)
                        self._error(f"'{key}' is only for routers ('{name}' is a host)", path, at_key=True)

    # -- rules 15, 16 ---------------------------------------------------------

    def _resolve_interface_routing(self, lab: Lab, raw_nodes: dict[str, Any]) -> None:
        for name, node in lab.nodes.items():
            if not node.is_router:
                continue
            for interface, raw in zip(node.interfaces, raw_nodes[name]["interfaces"]):
                path = ("nodes", name, "interfaces", interface.index)
                if "routing" in raw:
                    unused = [protocol for protocol in raw["routing"] if protocol not in node.routing]
                    if unused:
                        self._error(
                            f"{', '.join(unused)} not in the routing of '{name}' ({', '.join(node.routing) or 'none'})",
                            path + ("routing",),
                        )
                    interface.routing = [protocol for protocol in raw["routing"] if protocol in node.routing]
                else:
                    interface.routing = list(node.routing)

                if "area" in raw:
                    if "ospf" not in interface.routing:
                        self._error("'area' on an interface where OSPF does not run", path + ("area",), at_key=True)
                    else:
                        interface.area = raw["area"]
                elif "ospf" in interface.routing:
                    interface.area = 0

    # -- rules 17, 22 ---------------------------------------------------------

    def _check_routing_keys(self, lab: Lab, raw_nodes: dict[str, Any]) -> None:
        for name, node in lab.nodes.items():
            if not node.is_router:
                continue
            path = ("nodes", name)
            uses_bgp = "bgp" in node.routing
            if node.asn is not None and not uses_bgp:
                self._error("'asn' without bgp in routing", path + ("asn",), at_key=True)
            if uses_bgp and node.asn is None and node.frr_config is None:
                self._error("bgp in routing needs 'asn'", path + ("routing",))

            if node.frr_config is not None:
                if node.asn is not None:
                    self._error("'asn' together with frr_config: write the routing in frr_config", path + ("asn",), at_key=True)
                for index, raw in enumerate(raw_nodes[name]["interfaces"]):
                    if "area" in raw:
                        self._error(
                            "'area' together with frr_config: write the routing in frr_config",
                            path + ("interfaces", index, "area"),
                            at_key=True,
                        )
                if not node.routing:
                    self._warning(
                        "frr_config without routing: no routing daemon will be started", path + ("frr_config",), at_key=True
                    )

    # -- rules 5, 6, 7, 8, 9 --------------------------------------------------

    def _check_addresses(self, lab: Lab) -> None:
        owners: dict[IPv4Address, list[str]] = defaultdict(list)
        for node in lab.nodes.values():
            for interface in node.interfaces:
                if interface.ip is None:
                    continue
                path = ("nodes", node.name, "interfaces", interface.index, "ip")
                owners[interface.ip.ip].append(f"{node.name}:{interface.name}")
                if len(owners[interface.ip.ip]) > 1:
                    self._error(f"{interface.ip.ip} is already used by {owners[interface.ip.ip][0]}", path)
                network = interface.ip.network
                if network.prefixlen < 31 and interface.ip.ip in (network.network_address, network.broadcast_address):
                    kind = "network" if interface.ip.ip == network.network_address else "broadcast"
                    self._error(f"{interface.ip} is the {kind} address of {network}", path)

        subnets: dict[str, IPv4Network] = {}
        for network_name, attachments in lab.networks().items():
            node, interface = attachments[0]
            if len(attachments) == 1:
                self._warning(
                    f"network '{network_name}' has only one node attached",
                    ("nodes", node.name, "interfaces", interface.index, "network"),
                )
            seen: dict[IPv4Network, str] = {}
            for node, interface in attachments:
                if interface.ip is None:
                    continue
                subnet = interface.ip.network
                if seen and subnet not in seen:
                    first_subnet, first_owner = next(iter(seen.items()))
                    self._error(
                        f"network '{network_name}' uses {first_subnet} on {first_owner}, but {subnet} here",
                        ("nodes", node.name, "interfaces", interface.index, "ip"),
                    )
                seen.setdefault(subnet, f"{node.name}:{interface.name}")
            if seen:
                subnets[network_name] = next(iter(seen))

        for (left, left_net), (right, right_net) in combinations(subnets.items(), 2):
            if left_net.overlaps(right_net):
                node, interface = lab.networks()[right][0]
                self.report.error(
                    f"networks '{left}' ({left_net}) and '{right}' ({right_net}) overlap",
                    self._at(("nodes", node.name, "interfaces", interface.index, "ip")),
                )
        self._subnets = subnets

    # -- capture ----------------------------------------------------------------

    def _check_capture(self, lab: Lab) -> None:
        networks = lab.networks()
        for index, network in enumerate(lab.capture):
            if network not in networks:
                self._error(f"network '{network}' does not exist", ("capture", "networks", index))
        capture_node = self.dictionary.settings["capture"]["node"]
        if lab.capture and capture_node in lab.nodes:
            self._error(
                f"a node is named '{capture_node}', the name of the capture node: rename it",
                ("nodes", capture_node),
                at_key=True,
            )

    # -- rules 10, 11 ---------------------------------------------------------

    @staticmethod
    def _local_subnets(node: Node) -> list[IPv4Network]:
        return [interface.ip.network for interface in node.interfaces if interface.ip is not None]

    def _resolve_gateways(self, lab: Lab) -> None:
        networks = lab.networks()
        for node in lab.nodes.values():
            path = ("nodes", node.name)
            if node.gateway is not None:
                if not any(node.gateway in subnet for subnet in self._local_subnets(node)):
                    self._error(f"gateway {node.gateway} is outside every local subnet", path + ("gateway",))
                continue
            if node.is_router or not self._local_subnets(node):
                continue

            candidates: list[tuple[str, IPv4Address]] = []
            for interface in node.interfaces:
                if interface.ip is None:
                    continue
                for other, other_interface in networks[interface.network]:
                    if other.is_router and other_interface.ip is not None:
                        candidates.append((other.name, other_interface.ip.ip))
            if len(candidates) == 1:
                node.gateway = candidates[0][1]
                node.gateway_derived = True
            elif len(candidates) > 1:
                routers = ", ".join(f"{router} ({address})" for router, address in candidates)
                self._error(f"more than one router on the host's LANs ({routers}): set 'gateway'", path, at_key=True)

    # -- rule 12 --------------------------------------------------------------

    def _check_routes(self, lab: Lab) -> None:
        for node in lab.nodes.values():
            for index, route in enumerate(node.routes):
                if not any(route.via in subnet for subnet in self._local_subnets(node)):
                    self._error(f"next hop {route.via} is outside every local subnet", ("nodes", node.name, "routes", index, "via"))

    # -- rule 13 --------------------------------------------------------------

    def _check_routers(self, lab: Lab) -> None:
        for node in lab.nodes.values():
            if node.is_router and len(node.interfaces) < 2:
                self._warning("router with a single interface: it cannot forward between networks", ("nodes", node.name), at_key=True)

    # -- rule 23 --------------------------------------------------------------

    def _check_files(self, lab: Lab) -> None:
        for node in lab.nodes.values():
            if node.files is not None and not node.files.is_dir():
                self._error(f"folder {node.files} does not exist", ("nodes", node.name, "files"))

    # -- rules 18, 19 ---------------------------------------------------------

    def _check_ospf_areas(self, lab: Lab) -> None:
        for network_name, attachments in lab.networks().items():
            areas: dict[int, tuple[int | str, str]] = {}
            for node, interface in attachments:
                if not interface.runs("ospf"):
                    continue
                number = area_number(interface.area)
                if areas and number not in areas:
                    first_area, first_owner = next(iter(areas.values()))
                    self._error(
                        f"network '{network_name}' is in area {interface.area} here but in area {first_area} on {first_owner}",
                        ("nodes", node.name, "interfaces", interface.index),
                    )
                areas.setdefault(number, (interface.area, node.name))

        areas_by_router: dict[str, set[int]] = {}
        first_use: dict[int, ConfigPath] = {}
        for node in lab.nodes.values():
            for interface in node.interfaces:
                if interface.runs("ospf"):
                    number = area_number(interface.area)
                    areas_by_router.setdefault(node.name, set()).add(number)
                    first_use.setdefault(number, ("nodes", node.name, "interfaces", interface.index))
        for number, path in first_use.items():
            if number == 0:
                continue
            if not any({0, number} <= areas for areas in areas_by_router.values()):
                self._error(f"no router connects area {number} to area 0", path)

    # -- router IDs -----------------------------------------------------------

    def _assign_router_ids(self, lab: Lab) -> None:
        routers = sorted(node.name for node in lab.nodes.values() if node.is_router)
        if not routers:
            return
        pool = IPv4Network(self.dictionary.settings["router_id_pool"])
        addresses = list(pool.hosts()) if pool.prefixlen < 31 else list(pool)
        if len(routers) > len(addresses):
            self.report.error(f"{len(routers)} routers but the router ID pool {pool} has only {len(addresses)} addresses")
            return
        for network_name, subnet in self._subnets.items():
            if subnet.overlaps(pool):
                node, interface = lab.networks()[network_name][0]
                self._error(
                    f"network '{network_name}' ({subnet}) overlaps the router ID pool {pool} (settings.router_id_pool)",
                    ("nodes", node.name, "interfaces", interface.index, "ip"),
                )
        for name, address in zip(routers, addresses):
            lab.nodes[name].router_id = address

    # -- BGP, rule 20 ---------------------------------------------------------

    def _resolve_bgp(self, lab: Lab) -> None:
        speakers = [
            node for node in lab.nodes.values() if node.is_router and "bgp" in node.routing and node.asn is not None
        ]
        if not speakers:
            return
        networks = lab.networks()
        speaker_names = {node.name for node in speakers}
        by_as: dict[int, list[Node]] = defaultdict(list)
        for node in speakers:
            by_as[node.asn].append(node)
        # iBGP over loopbacks needs an IGP: only when every router of the AS runs OSPF.
        loopback_as = {asn for asn, members in by_as.items() if len(members) > 1 and all("ospf" in m.routing for m in members)}

        adjacent: set[tuple[str, str]] = set()
        for node in speakers:
            if node.frr_config is not None:
                continue
            for interface in node.interfaces:
                if not interface.runs("bgp"):
                    continue
                for peer, peer_interface in networks[interface.network]:
                    if peer is node or peer.name not in speaker_names or not peer_interface.runs("bgp"):
                        continue
                    adjacent.add((node.name, peer.name))
                    internal = peer.asn == node.asn
                    if internal and node.asn in loopback_as:
                        continue  # covered by the loopback full mesh
                    node.bgp_neighbors.append(BgpNeighbor(peer_interface.ip.ip, peer.asn, peer.name, internal))

        for asn, members in by_as.items():
            if asn in loopback_as:
                for node in members:
                    node.loopback = node.router_id
                for node in members:
                    for peer in members:
                        if peer is not node and peer.router_id is not None:
                            node.bgp_neighbors.append(BgpNeighbor(peer.router_id, asn, peer.name, True, via_loopback=True))
                continue
            for left, right in combinations(members, 2):
                if (left.name, right.name) not in adjacent:
                    self._error(
                        f"'{left.name}' and '{right.name}' are both in AS {asn} but not directly connected: "
                        f"add ospf to the routing of every router of AS {asn}",
                        ("nodes", right.name, "asn"),
                    )

        for node in speakers:
            if node.frr_config is not None:
                continue
            if any(not neighbor.internal for neighbor in node.bgp_neighbors):
                for neighbor in node.bgp_neighbors:
                    neighbor.next_hop_self = neighbor.internal
            if not node.bgp_neighbors:
                self._warning("router uses BGP but has no BGP neighbor", ("nodes", node.name, "routing"))

    # -- redistribution, rule 21 ----------------------------------------------

    @staticmethod
    def _routing_domains(lab: Lab) -> dict[str, dict[str, str]]:
        """For each protocol, the routing domain of every router: routers linked by that protocol share one."""
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(item: tuple[str, str]) -> tuple[str, str]:
            parent.setdefault(item, item)
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(protocol: str, left: str, right: str) -> None:
            parent[find((protocol, left))] = find((protocol, right))

        for attachments in lab.networks().values():
            for protocol in ("ospf", "rip"):
                routers = [node.name for node, interface in attachments if node.is_router and interface.runs(protocol)]
                for other in routers[1:]:
                    union(protocol, routers[0], other)
        for node in lab.nodes.values():
            for neighbor in node.bgp_neighbors:
                union("bgp", node.name, neighbor.peer)

        return {
            protocol: {node.name: find((protocol, node.name))[1] for node in lab.nodes.values()}
            for protocol in ("ospf", "rip", "bgp")
        }

    def _resolve_redistribution(self, lab: Lab) -> None:
        domains = self._routing_domains(lab)
        pairs: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        for node in lab.nodes.values():
            if not node.is_router or node.frr_config is not None or len(node.routing) < 2:
                continue
            node.redistribute = {
                protocol: [other for other in node.routing if other != protocol] for protocol in node.routing
            }
            for left, right in combinations(sorted(node.routing), 2):
                # Routes can loop back only between routers in the same domain of both protocols.
                pairs[(left, right, domains[left][node.name], domains[right][node.name])].append(node.name)
            summary = "; ".join(
                f"{interface.name} ({interface.network}): {', '.join(interface.routing) or 'none'}"
                for interface in node.interfaces
                if interface.ip is not None
            )
            self.report.note(f"'{node.name}' runs {summary}", self._at_key(("nodes", node.name)), ("nodes", node.name))

        for (left, right, _, _), routers in pairs.items():
            if len(routers) > 1:
                self.report.warning(
                    f"routers {', '.join(routers)} all redistribute between {left} and {right} in the same domains: "
                    "each route is redistributed at most once, but paths may be suboptimal; "
                    "use frr_config for finer control",
                    self._at_key(("nodes", routers[1])),
                    ("nodes", routers[1]),
                )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_lab(path: Path, dictionary: Dictionary | None = None) -> tuple[Lab | None, IssueReport]:
    """Run the Lexer and the Parser on a lab file. The lab is ``None`` if there are errors."""
    dictionary = dictionary or default_dictionary()
    report = IssueReport(source=path)
    tokens = tokenize(path, dictionary, report)
    lab = None
    if tokens is not None:
        lab = Parser(dictionary, report, path).parse(tokens)
    return (lab if report.valid else None), report
