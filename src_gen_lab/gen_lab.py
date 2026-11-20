#!/usr/bin/env python3
"""Translator: turns a checked SK-F lab into a Katharà lab folder.

The Lexer and the Parser have already checked the file and computed every
derived value (gateways, interfaces where each protocol runs, BGP session
addresses, loopbacks, redistribution). This module only renders that model:

    labs/<lab>/lab.conf              machines, collision domains, Katharà options
    labs/<lab>/<node>.startup        addresses, routes, FRR start, user commands
    labs/<lab>/<node>/...            the node's ``files`` folder
    labs/<lab>/<node>/etc/frr/       daemons, frr.conf, vtysh.conf (routers)
    labs/<lab>/roles.conf            node roles for the measurement tools

The folder is written next to the target and swapped in only when complete,
so a failure never leaves a half-generated lab.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import sys
from pathlib import Path

from src_gen_lab.issues import DictionaryError
from src_gen_lab.Lexer import resolve_config_path
from src_gen_lab.loader import Dictionary, default_dictionary
from src_gen_lab.Parser import Lab, Node, read_lab

FRR_DAEMONS = ("bgpd", "ospfd", "ospf6d", "ripd", "ripngd", "isisd", "pimd", "ldpd", "nhrpd", "eigrpd",
               "babeld", "sharpd", "pbrd", "bfdd", "fabricd")
PROTOCOL_DAEMON = {"ospf": "ospfd", "rip": "ripd", "bgp": "bgpd"}
IGNORED_FILES = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".DS_Store")

# Redistributed routes are tagged, and tagged routes are never redistributed
# again: a route crosses from one protocol to another at most once, so it
# cannot loop back into the domain it came from. BGP announces only its
# explicit networks, so nothing is redistributed into BGP.
REDISTRIBUTED_TAG = 1
ROUTE_MAP = "SKF-REDISTRIBUTE"
AREA_TYPE_COMMAND = {"stub": "stub", "totally_stub": "stub no-summary", "nssa": "nssa"}


# ---------------------------------------------------------------------------
# lab.conf
# ---------------------------------------------------------------------------


def _quote(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_lab_conf(lab: Lab, capture: list[str], dictionary: Dictionary) -> str:
    lines: list[str] = []
    for key, value in (
        ("LAB_DESCRIPTION", lab.description),
        ("LAB_VERSION", lab.version),
        ("LAB_AUTHOR", lab.author),
        ("LAB_EMAIL", lab.email),
        ("LAB_WEB", lab.web),
    ):
        if value is not None:
            lines.append(f"{key}={_quote(' '.join(str(value).split()))}")
    if lines:
        lines.append("")

    for node in lab.nodes.values():
        name = node.name
        for interface in node.interfaces:
            lines.append(f"{name}[{interface.index}]={_quote(interface.network)}")
        lines.append(f"{name}[image]={_quote(node.image)}")

        options = node.kathara
        sysctl = list(options.get("sysctl", []))
        if node.is_router and not any(item.replace(" ", "").startswith("net.ipv4.ip_forward=") for item in sysctl):
            sysctl.insert(0, "net.ipv4.ip_forward=1")
        if "mem" in options:
            lines.append(f"{name}[mem]={_quote(options['mem'])}")
        if "cpus" in options:
            lines.append(f"{name}[cpus]={options['cpus']}")
        if "ipv6" in options:
            lines.append(f"{name}[ipv6]={str(options['ipv6']).lower()}")
        for item in sysctl:
            lines.append(f"{name}[sysctl]={_quote(item)}")
        for item in options.get("env", []):
            lines.append(f"{name}[env]={_quote(item)}")
        for item in options.get("ports", []):
            lines.append(f"{name}[port]={_quote(item)}")
        if "bridged" in options:
            lines.append(f"{name}[bridged]={str(options['bridged']).lower()}")
        if "num_terms" in options:
            lines.append(f"{name}[num_terms]={options['num_terms']}")
        lines.append("")

    if capture:
        settings = dictionary.settings["capture"]
        node = settings["node"]
        lines.append("# Wireshark real-time packet capture: http://localhost:3000")
        for index, network in enumerate(capture):
            lines.append(f"{node}[{index}]={_quote(network)}")
        lines.append(f"{node}[image]={_quote(settings['image'])}")
        lines.append(f"{node}[bridged]=true")
        lines.append(f"{node}[port]={_quote(settings['port'])}")
        lines.append(f"{node}[num_terms]=0")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Startup files
# ---------------------------------------------------------------------------


def render_startup(node: Node) -> str:
    blocks: list[list[str]] = []

    addresses = [f"ip address add {interface.ip} dev {interface.name}" for interface in node.interfaces if interface.ip]
    if node.loopback is not None:
        addresses.append(f"ip address add {node.loopback}/32 dev lo")
    blocks.append(addresses)

    routes = []
    if node.gateway is not None:
        routes.append(f"ip route add default via {node.gateway}")
    routes.extend(f"ip route add {route.to} via {route.via}" for route in node.routes)
    blocks.append(routes)

    if node.runs_frr:
        blocks.append(["systemctl start frr"])

    blocks.append(list(node.startup))
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


# ---------------------------------------------------------------------------
# FRR
# ---------------------------------------------------------------------------


def render_daemons(node: Node) -> str:
    # With frr_config the routing is hand-written: start every daemon it may need.
    protocols = node.routing if node.frr_config is None else list(PROTOCOL_DAEMON)
    enabled = {PROTOCOL_DAEMON[protocol] for protocol in protocols}
    lines = ["# Generated by SK-F", "zebra=yes"]
    lines += [f"{daemon}={'yes' if daemon in enabled else 'no'}" for daemon in FRR_DAEMONS]
    lines += [
        "",
        "vtysh_enable=yes",
        'zebra_options="  -A 127.0.0.1"',
        'bgpd_options="   -A 127.0.0.1"',
        'ospfd_options="  -A 127.0.0.1"',
        'ripd_options="   -A 127.0.0.1"',
    ]
    return "\n".join(lines) + "\n"


def _loopback_area(node: Node) -> int | str:
    """Area for the iBGP loopback: the backbone if the router is in it, else its first OSPF area."""
    areas = [interface.area for interface in node.interfaces if interface.runs("ospf")]
    if not areas or any(str(area) in ("0", "0.0.0.0") for area in areas):
        return 0
    return areas[0]


def render_frr_conf(node: Node) -> str:
    if node.frr_config is not None:
        return node.frr_config.rstrip() + "\n"

    lines = ["frr defaults traditional", f"hostname {node.name}", "service integrated-vtysh-config", "!"]

    if "ospf" in node.routing:
        lines.append("router ospf")
        lines.append(f" ospf router-id {node.ospf_router_id}")
        for area, area_type in node.ospf_areas:
            if area_type in AREA_TYPE_COMMAND:
                lines.append(f" area {area} {AREA_TYPE_COMMAND[area_type]}")
        for interface in node.interfaces:
            if interface.runs("ospf"):
                lines.append(f" network {interface.ip.network} area {interface.area}")
        if node.loopback is not None:
            lines.append(f" network {node.loopback}/32 area {_loopback_area(node)}")
        lines += [f" redistribute {other} route-map {ROUTE_MAP}" for other in node.redistribute.get("ospf", [])]
        lines.append("!")

    if "rip" in node.routing:
        lines.append("router rip")
        lines.append(" version 2")
        for interface in node.interfaces:
            if interface.runs("rip"):
                lines.append(f" network {interface.ip.network}")
        lines += [f" redistribute {other} route-map {ROUTE_MAP}" for other in node.redistribute.get("rip", [])]
        lines.append("!")

    if "bgp" in node.routing:
        lines.append(f"router bgp {node.asn}")
        lines.append(f" bgp router-id {node.bgp_router_id}")
        lines.append(" no bgp ebgp-requires-policy")
        lines.append(" bgp network import-check")
        for neighbor in node.bgp_neighbors:
            lines.append(f" neighbor {neighbor.address} remote-as {neighbor.remote_as}")
            lines.append(f" neighbor {neighbor.address} description {neighbor.peer}")
            if neighbor.update_source is not None:
                lines.append(f" neighbor {neighbor.address} update-source {neighbor.update_source}")
        lines.append(" !")
        lines.append(" address-family ipv4 unicast")
        lines += [f"  network {network}" for network in node.bgp_networks]
        for neighbor in node.bgp_neighbors:
            if neighbor.next_hop_self:
                lines.append(f"  neighbor {neighbor.address} next-hop-self")
        lines.append(" exit-address-family")
        lines.append("!")

    if any(node.redistribute.values()):
        lines += [
            f"route-map {ROUTE_MAP} deny 10",
            f" match tag {REDISTRIBUTED_TAG}",
            "!",
            f"route-map {ROUTE_MAP} permit 20",
            f" set tag {REDISTRIBUTED_TAG}",
            "!",
        ]

    lines += ["line vty", "!"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Writing the lab folder
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o644)


def _write_lab_files(lab: Lab, lab_dir: Path, capture: list[str], dictionary: Dictionary, zombies: tuple[str, list[str]] | None) -> None:
    lab_dir.mkdir(parents=True)
    _write(lab_dir / "lab.conf", render_lab_conf(lab, capture, dictionary))

    for node in lab.nodes.values():
        node_dir = lab_dir / node.name
        if node.files is not None:
            shutil.copytree(node.files, node_dir, dirs_exist_ok=True, ignore=IGNORED_FILES)
        _write(lab_dir / f"{node.name}.startup", render_startup(node))
        if node.runs_frr:
            frr_dir = node_dir / "etc" / "frr"
            _write(frr_dir / "daemons", render_daemons(node))
            _write(frr_dir / "frr.conf", render_frr_conf(node))
            _write(frr_dir / "vtysh.conf", "service integrated-vtysh-config\n")

    roles = [f"{node.name} {' '.join(node.roles)}" for node in lab.nodes.values() if node.roles]
    if roles:
        _write(lab_dir / "roles.conf", "# node role...\n" + "\n".join(roles) + "\n")

    if zombies is not None:
        node_name, addresses = zombies
        _write(lab_dir / node_name / "zombies.txt", "\n".join(addresses) + "\n")


def write_lab(
    lab: Lab,
    output_dir: Path,
    overwrite: bool,
    capture: list[str],
    dictionary: Dictionary,
    zombies: tuple[str, list[str]] | None = None,
) -> Path:
    """Generate the lab folder. An existing folder is replaced only when ``overwrite`` is set."""
    lab_dir = output_dir / lab.name
    if lab_dir.exists() and not overwrite:
        raise FileExistsError(f"{lab_dir} already exists: use --force to replace it")

    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / f".{lab.name}.new"
    previous = output_dir / f".{lab.name}.old"
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)

    try:
        _write_lab_files(lab, staging, capture, dictionary, zombies)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if lab_dir.exists():
        lab_dir.rename(previous)
    staging.rename(lab_dir)
    if previous.exists():
        shutil.rmtree(previous)
    return lab_dir


# ---------------------------------------------------------------------------
# Command-line options that select capture networks and zombies
# ---------------------------------------------------------------------------


def select_capture(lab: Lab, selection: str | None, dictionary: Dictionary) -> list[str]:
    """Capture networks: the command line wins over the file.

    Accepts network names, ``node:<name>`` for every network of a node,
    ``all``, or ``none``/``0`` for no capture.
    """
    if selection is None:
        return list(lab.capture)
    networks = list(lab.networks())
    chosen: list[str] = []
    for token in (part.strip() for part in selection.replace(";", ",").split(",")):
        if not token:
            continue
        if token.lower() in ("none", "0"):
            return []
        if token.lower() in ("all", "*"):
            chosen.extend(networks)
        elif token.startswith("node:"):
            name = token[5:]
            if name not in lab.nodes:
                raise ValueError(f"--capture: node '{name}' does not exist")
            chosen.extend(interface.network for interface in lab.nodes[name].interfaces)
        elif token in networks:
            chosen.append(token)
        else:
            raise ValueError(f"--capture: network '{token}' does not exist (networks: {', '.join(networks)})")
    chosen = list(dict.fromkeys(chosen))
    capture_node = dictionary.settings["capture"]["node"]
    if chosen and capture_node in lab.nodes:
        raise ValueError(f"a node is named '{capture_node}', the name of the capture node: rename it")
    return chosen


def select_zombies(lab: Lab, mode: str, node_name: str | None, addresses: str | None) -> tuple[str, list[str]] | None:
    """Optional zombies.txt for the C2 node, from addresses given on the command line."""
    if mode == "disabled":
        return None
    if not addresses:
        raise ValueError("--zombies manual needs --zombies-ips")
    ips: list[str] = []
    for token in addresses.replace(";", ",").replace(" ", ",").split(","):
        if token.strip():
            ip = str(ipaddress.ip_interface(token.strip()).ip)
            if ip not in ips:
                ips.append(ip)
    if node_name is None:
        node_name = next((node.name for node in lab.nodes.values() if "attacker" in node.roles), None)
    if node_name is None:
        node_name = next((name for name in lab.nodes if "attacker" in name.lower()), None)
    if node_name is None or node_name not in lab.nodes:
        raise ValueError("cannot choose the node for zombies.txt: use --zombies-node")
    return node_name, ips


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Katharà lab from an SK-F YAML file.")
    parser.add_argument("lab", help="YAML file, or lab name looked up in --config-dir")
    parser.add_argument("--config-dir", default="configs", type=Path, help="where lab names are looked up (default: configs)")
    parser.add_argument("--output-dir", "-o", default="labs", type=Path, help="where labs are generated (default: labs)")
    parser.add_argument("--force", "--clean", action="store_true", help="replace the lab folder if it already exists")
    parser.add_argument("--validate-only", action="store_true", help="check the file and stop")
    parser.add_argument(
        "--capture",
        "--wireshark-networks",
        default=None,
        help="networks for the Wireshark node, overriding the capture section: "
        "names separated by commas, node:<name>, all, or none",
    )
    parser.add_argument("--zombies", choices=["manual", "disabled"], default="disabled", help="write zombies.txt (default: disabled)")
    parser.add_argument("--zombies-node", default=None, help="node that receives zombies.txt (default: the node with role attacker)")
    parser.add_argument("--zombies-ips", default=None, help="zombie addresses, separated by commas or spaces")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        dictionary = default_dictionary()
        config_path = resolve_config_path(args.lab, args.config_dir)
    except (DictionaryError, FileNotFoundError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    lab, report = read_lab(config_path, dictionary)
    report.print()
    if lab is None:
        return 1
    if args.validate_only:
        return 0

    try:
        capture = select_capture(lab, args.capture, dictionary)
        zombies = select_zombies(lab, args.zombies, args.zombies_node, args.zombies_ips)
        lab_dir = write_lab(lab, args.output_dir, args.force, capture, dictionary, zombies)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Lab generated: {lab_dir}")
    print(f"Start it with:  ./start.sh {lab_dir}   (or: cd {lab_dir} && kathara lstart)")
    print(f"Stop it with:   ./stop.sh {lab_dir}    (or: cd {lab_dir} && kathara lclean)")
    if capture:
        print(f"Wireshark captures {', '.join(capture)}: http://localhost:3000 (default credentials abc / abc)")
    if zombies is not None:
        print(f"zombies.txt written for {zombies[0]}: {', '.join(zombies[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
