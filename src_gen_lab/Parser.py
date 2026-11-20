#!/usr/bin/env python3
"""Syntax, structure and semantic analysis of an SK-F lab file.

The Parser receives the token tree built by the Lexer and works in two phases.

1. Syntax and structure, driven by the dictionary: each key must be allowed
   in the section where it appears, required keys must be present, lists,
   mappings and sections must have the right shape and every value must have
   the right type (validator rule 3).

2. Semantics: the lab is turned into a model (``Lab``, ``Node``,
   ``Interface``) and checked against the remaining validator rules.
   Everything the framework derives by itself is computed here, once:
   default gateways, the interfaces where each protocol runs, BGP session
   addresses, loopbacks and redistribution. The translator only renders the
   model.
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
    update_source: IPv4Address | None = None  # iBGP over OSPF: own router ID as source
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
    frr_config: str | None = None
    ospf: dict[str, Any] | None = None  # the node's sections, as written
    rip: dict[str, Any] | None = None
    bgp: dict[str, Any] | None = None
    # Derived by the Parser
    routing: list[str] = field(default_factory=list)  # protocols with a section, in ospf, rip, bgp order
    asn: int | None = None
    ospf_router_id: IPv4Address | None = None
    bgp_router_id: IPv4Address | None = None
    ospf_areas: list[tuple[int | str, str]] = field(default_factory=list)  # (area, type)
    bgp_networks: list[IPv4Network] = field(default_factory=list)
    loopback: IPv4Address | None = None  # address added to lo and announced in OSPF
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

        return self._scalar(key.target, token, path)

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
                frr_config=raw.get("frr_config") if node_type == "router" else None,
            )
            node = nodes[name]
            if node.is_router:
                node.ospf, node.rip, node.bgp = raw.get("ospf"), raw.get("rip"), raw.get("bgp")
                node.routing = [protocol for protocol in ("ospf", "rip", "bgp") if protocol in raw]
                if node.ospf:
                    node.ospf_router_id = IPv4Address(node.ospf["router_id"])
                if node.bgp:
                    node.asn = node.bgp["asn"]
                    node.bgp_router_id = IPv4Address(node.bgp["router_id"])

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
        self._resolve_protocols(lab)  # rules 15, 16, 17, 18
        self._check_addresses(lab)  # rules 5, 6, 7, 8, 9
        self._check_capture(lab)
        self._resolve_gateways(lab)  # rules 10, 11
        self._check_routes(lab)  # rule 12
        self._check_routers(lab)  # rule 13
        self._check_files(lab)  # rule 33
        self._check_ospf(lab)  # rules 19, 20, 21, 22
        self._resolve_bgp(lab)  # rules 23-31
        self._resolve_redistribution(lab)  # rule 32

    # -- rule 14 --------------------------------------------------------------

    def _check_router_only_keys(self, lab: Lab, raw_nodes: dict[str, Any]) -> None:
        node_keys = [key.name for key in self.dictionary.sections["node"].keys.values() if key.router_only]
        for name, node in lab.nodes.items():
            if node.is_router:
                continue
            for key in node_keys:
                if key in raw_nodes[name]:
                    self._error(f"'{key}' is only for routers ('{name}' is a host)", ("nodes", name, key), at_key=True)

    # -- rules 15, 16, 17, 18 -------------------------------------------------

    def _interfaces_on(self, node: Node, network: str, path: ConfigPath) -> list[Interface]:
        """The node's addressed interfaces on ``network``; reports why there are none."""
        attached = [interface for interface in node.interfaces if interface.network == network]
        if not attached:
            own = list(dict.fromkeys(interface.network for interface in node.interfaces))
            close = difflib.get_close_matches(network, own, n=1, cutoff=0.6)
            hint = f": did you mean '{close[0]}'?" if close else f" (its networks: {', '.join(own)})"
            self._error(f"'{node.name}' has no interface on network '{network}'{hint}", path)
            return []
        addressed = [interface for interface in attached if interface.ip is not None]
        if not addressed:
            self._error(f"the interface of '{node.name}' on network '{network}' has no ip", path)
        return addressed

    def _check_repeated(self, names: list[str], path: ConfigPath, what: str) -> None:
        for index, name in enumerate(names):
            if name in names[:index]:
                self._error(f"{what} '{name}' is listed twice", path + (index,))

    def _resolve_protocols(self, lab: Lab) -> None:
        """Mark the interfaces where OSPF and RIP run, with their OSPF area."""
        for node in lab.nodes.values():
            if not node.is_router:
                continue
            base = ("nodes", node.name)
            if node.frr_config is not None and node.routing:
                self._error(
                    f"frr_config together with {', '.join(node.routing)}: write the routing in frr_config",
                    base + ("frr_config",),
                    at_key=True,
                )
                continue

            if node.ospf:
                area_of: dict[str, int | str] = {}
                seen_areas: dict[int, int] = {}
                for index, area in enumerate(node.ospf["areas"]):
                    path = base + ("ospf", "areas", index)
                    number = area_number(area["id"])
                    if number in seen_areas:
                        self._error(f"area {area['id']} is listed twice", path + ("id",))
                        continue
                    seen_areas[number] = index
                    if number == 0 and area["type"] != "normal":
                        self._error(f"area 0 is the backbone and cannot be {area['type']}", path + ("type",))
                    node.ospf_areas.append((area["id"], area["type"]))
                    for position, network in enumerate(area["networks"]):
                        network_path = path + ("networks", position)
                        if network in area["networks"][:position]:
                            self._error(f"network '{network}' is listed twice", network_path)
                            continue
                        if network in area_of:
                            self._error(f"network '{network}' is already in area {area_of[network]}", network_path)
                            continue
                        area_of[network] = area["id"]
                        for interface in self._interfaces_on(node, network, network_path):
                            interface.routing.append("ospf")
                            interface.area = area["id"]

            if node.rip:
                networks = node.rip["networks"]
                self._check_repeated(networks, base + ("rip", "networks"), "network")
                for position, network in enumerate(dict.fromkeys(networks)):
                    for interface in self._interfaces_on(node, network, base + ("rip", "networks", networks.index(network))):
                        interface.routing.append("rip")

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

    # -- rule 33 --------------------------------------------------------------

    def _check_files(self, lab: Lab) -> None:
        for node in lab.nodes.values():
            if node.files is not None and not node.files.is_dir():
                written = node.files.relative_to(lab.source.parent)
                self._error(f"folder '{written}' does not exist next to {lab.source.name}", ("nodes", node.name, "files"))

    # -- rules 19, 20, 21, 22 -------------------------------------------------

    def _check_router_ids(self, lab: Lab, protocol: str) -> None:
        owners: dict[IPv4Address, str] = {}
        for node in lab.nodes.values():
            router_id = node.ospf_router_id if protocol == "ospf" else node.bgp_router_id
            if router_id is None or node.frr_config is not None:
                continue
            if router_id in owners:
                self._error(
                    f"{protocol.upper()} router ID {router_id} is already used by '{owners[router_id]}'",
                    ("nodes", node.name, protocol, "router_id"),
                )
            owners.setdefault(router_id, node.name)

    def _check_ospf(self, lab: Lab) -> None:
        # Rule 19: the routers on a network must put it in the same area.
        for network_name, attachments in lab.networks().items():
            areas: dict[int, tuple[int | str, str]] = {}
            for node, interface in attachments:
                if not interface.runs("ospf"):
                    continue
                number = area_number(interface.area)
                if areas and number not in areas:
                    first_area, first_owner = next(iter(areas.values()))
                    self._error(
                        f"network '{network_name}' is in area {interface.area} on '{node.name}' "
                        f"but in area {first_area} on '{first_owner}'",
                        self._area_network_path(node, network_name),
                    )
                areas.setdefault(number, (interface.area, node.name))

        # Rule 20: every router of an area declares the same type.
        types: dict[int, tuple[str, str]] = {}
        areas_by_router: dict[str, set[int]] = {}
        first_use: dict[int, ConfigPath] = {}
        for node in lab.nodes.values():
            if not node.ospf or node.frr_config is not None:
                continue
            for index, (area, area_type) in enumerate(node.ospf_areas):
                number = area_number(area)
                path = ("nodes", node.name, "ospf", "areas", index)
                areas_by_router.setdefault(node.name, set()).add(number)
                first_use.setdefault(number, path + ("id",))
                if number in types and types[number][0] != area_type:
                    other_type, other = types[number]
                    self._error(f"area {area} is {area_type} here but {other_type} on '{other}'", path + ("type",))
                types.setdefault(number, (area_type, node.name))

        # Rule 21: every area other than 0 needs a router that connects it to area 0.
        for number, path in first_use.items():
            if number != 0 and not any({0, number} <= areas for areas in areas_by_router.values()):
                self._error(f"no router connects area {self._area_label(number)} to area 0", path)

        # Rule 22: router IDs are unique.
        self._check_router_ids(lab, "ospf")

    @staticmethod
    def _area_label(number: int) -> str:
        return str(number) if number < 256 else str(IPv4Address(number))

    @staticmethod
    def _area_network_path(node: Node, network: str) -> ConfigPath:
        for index, area in enumerate(node.ospf["areas"]):
            if network in area["networks"]:
                return ("nodes", node.name, "ospf", "areas", index, "networks", area["networks"].index(network))
        return ("nodes", node.name)

    # -- BGP, rules 23-31 -----------------------------------------------------

    def _resolve_bgp(self, lab: Lab) -> None:
        speakers = {node.name: node for node in lab.nodes.values() if node.bgp and node.frr_config is None}
        if not speakers:
            return
        self._check_router_ids(lab, "bgp")  # rule 23
        reported: set[frozenset[str]] = set()  # pairs already reported by rules 26-27

        for node in speakers.values():
            base = ("nodes", node.name, "bgp")
            names = node.bgp["neighbors"]
            self._check_repeated(names, base + ("neighbors",), "neighbor")
            for index, peer_name in enumerate(names):
                if peer_name in names[:index]:
                    continue
                path = base + ("neighbors", index)
                peer = self._bgp_peer(lab, node, peer_name, path)  # rules 24, 25
                if peer is None:
                    continue
                shared = [
                    (own, other)
                    for own in node.interfaces
                    for other in peer.interfaces
                    if own.network == other.network and own.ip is not None and other.ip is not None
                ]
                pair = frozenset((node.name, peer.name))
                if peer.asn != node.asn:
                    if not shared:  # rule 26
                        if pair in reported:
                            continue
                        reported.add(pair)
                        self._error(
                            f"'{node.name}' (AS {node.asn}) and '{peer_name}' (AS {peer.asn}) share no network: "
                            "eBGP multihop is outside the dictionary, use frr_config",
                            path,
                        )
                        continue
                    node.bgp_neighbors.append(BgpNeighbor(shared[0][1].ip.ip, peer.asn, peer.name, internal=False))
                elif node.ospf and peer.ospf:
                    # iBGP across the AS: sessions between router IDs, reachable through OSPF.
                    node.bgp_neighbors.append(
                        BgpNeighbor(peer.bgp_router_id, peer.asn, peer.name, internal=True, update_source=node.bgp_router_id)
                    )
                elif shared:
                    node.bgp_neighbors.append(BgpNeighbor(shared[0][1].ip.ip, peer.asn, peer.name, internal=True))
                elif pair not in reported:  # rule 27
                    reported.add(pair)
                    self._error(
                        f"'{node.name}' and '{peer_name}' are both in AS {node.asn} but share no network: "
                        "give both an ospf section so the session can cross the AS",
                        path,
                    )

            if any(not neighbor.internal for neighbor in node.bgp_neighbors):
                for neighbor in node.bgp_neighbors:
                    neighbor.next_hop_self = neighbor.internal
            self._resolve_bgp_networks(lab, node)  # rule 28
            self._resolve_loopback(lab, node)  # rules 29, 30

        self._check_full_mesh(speakers)  # rule 31

    def _bgp_peer(self, lab: Lab, node: Node, peer_name: str, path: ConfigPath) -> Node | None:
        if peer_name == node.name:
            self._error("a router cannot be its own BGP neighbor", path)
            return None
        peer = lab.nodes.get(peer_name)
        if peer is None:
            close = difflib.get_close_matches(peer_name, list(lab.nodes), n=1, cutoff=0.6)
            self._error(f"node '{peer_name}' does not exist" + (f": did you mean '{close[0]}'?" if close else ""), path)
            return None
        if not peer.bgp:
            self._error(f"'{peer_name}' has no bgp section", path)
            return None
        if peer.frr_config is not None:
            self._error(f"'{peer_name}' uses frr_config: its BGP sessions are written there", path)
            return None
        if node.name not in peer.bgp["neighbors"]:
            self._error(f"the session is declared only here: add '{node.name}' to the neighbors of '{peer_name}'", path)
            return None
        return peer

    def _resolve_bgp_networks(self, lab: Lab, node: Node) -> None:
        names = node.bgp.get("networks", [])
        path = ("nodes", node.name, "bgp", "networks")
        self._check_repeated(names, path, "network")
        networks = lab.networks()
        for index, name in enumerate(names):
            if name in names[:index]:
                continue
            if name not in networks:
                close = difflib.get_close_matches(name, list(networks), n=1, cutoff=0.6)
                self._error(f"network '{name}' does not exist" + (f": did you mean '{close[0]}'?" if close else ""), path + (index,))
            elif name not in self._subnets:
                self._error(f"network '{name}' has no addressed interface, so it has no subnet to announce", path + (index,))
            else:
                node.bgp_networks.append(self._subnets[name])

    def _resolve_loopback(self, lab: Lab, node: Node) -> None:
        """iBGP sessions across the AS use the router ID: it must be an address announced in OSPF."""
        if not any(neighbor.update_source for neighbor in node.bgp_neighbors):
            return
        router_id = node.bgp_router_id
        path = ("nodes", node.name, "bgp", "router_id")
        own = [interface for interface in node.interfaces if interface.ip is not None and interface.ip.ip == router_id]
        if own:
            if not own[0].runs("ospf"):  # rule 29
                self._error(
                    f"router ID {router_id} is the address of {own[0].name} ({own[0].network}), which is not in OSPF: "
                    "iBGP neighbors could not reach it",
                    path,
                )
            return
        for other in lab.nodes.values():  # rule 30
            for interface in other.interfaces:
                if interface.ip is not None and interface.ip.ip == router_id:
                    self._error(f"router ID {router_id} is the address of {other.name}:{interface.name}", path)
                    return
        for network, subnet in self._subnets.items():
            if router_id in subnet:
                self._error(f"router ID {router_id} is inside network '{network}' ({subnet})", path)
                return
        node.loopback = router_id

    def _check_full_mesh(self, speakers: dict[str, Node]) -> None:
        by_as: dict[int, list[Node]] = defaultdict(list)
        for node in speakers.values():
            by_as[node.asn].append(node)
        for asn, members in by_as.items():
            for left, right in combinations(members, 2):
                if right.name not in left.bgp["neighbors"] and left.name not in right.bgp["neighbors"]:
                    self._warning(
                        f"'{left.name}' and '{right.name}' are both in AS {asn} but not BGP neighbors: "
                        "iBGP needs every pair of routers of the AS to be neighbors",
                        ("nodes", right.name, "bgp", "neighbors"),
                    )

    # -- redistribution, rule 32 ----------------------------------------------

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
            # BGP announces only its explicit networks; OSPF and RIP receive the routes of the others.
            node.redistribute = {
                protocol: [other for other in node.routing if other != protocol] for protocol in node.routing if protocol != "bgp"
            }
            if "ospf" in node.routing and "rip" in node.routing:
                # Routes can loop back only between routers in the same domain of both protocols.
                pairs[("ospf", "rip", domains["ospf"][node.name], domains["rip"][node.name])].append(node.name)

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
