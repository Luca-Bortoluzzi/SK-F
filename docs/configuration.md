# Configuration reference

[Back to README](../README.md) · [Example catalog](../configs/README.md)

An SK-F file describes a Katharà lab in YAML with a closed vocabulary: every
allowed key is listed in [`src_gen_lab/dictionary.yml`](../src_gen_lab/dictionary.yml)
and summarized here. Any other key is an error.

## Generate or validate a lab

```bash
python3 generate_lab.py Test1 --validate-only
python3 generate_lab.py configs/Test1.yml
```

The argument is a YAML path or a configuration name, searched as `<name>.yml`
then `<name>.yaml` under `configs/`. Output goes to `labs/<lab.name>/`.

| Option | Behavior |
| --- | --- |
| `--config-dir PATH` | Where configuration names are looked up; default `configs` |
| `--output-dir PATH`, `-o PATH` | Parent directory of the generated lab; default `labs` |
| `--validate-only` | Check the file and stop |
| `--force` (alias `--clean`) | Replace an existing lab folder |
| `--capture SELECTION` (alias `--wireshark-networks`) | Capture networks, overriding the `capture` section: names separated by commas, `node:<name>`, `all`, or `none` |
| `--zombies manual` | Write `zombies.txt` for the C2 node from `--zombies-ips` |
| `--zombies-node NODE` | Node receiving `zombies.txt`; default the node with role `attacker` |
| `--zombies-ips LIST` | Zombie addresses, separated by commas or spaces |

The lab is generated in a temporary folder and swapped in only when complete, so
a failure never leaves a partial lab. Replacing a lab removes its `shared/`
files; results saved by the tools live outside the lab folder. Stop a running
lab before regenerating it.

## Principles

1. **Closed vocabulary.** An unknown key is an error, with a suggestion:
   `unknown key 'gatway': did you mean 'gateway'?`.
2. **One form per concept.** Each thing is written in one way.
3. **No key without effect.** Every key in the dictionary is used by the generator.
4. **The framework derives what it can.** Router IDs, BGP neighbors, gateways and
   redistribution come from the topology.
5. **An escape hatch.** `frr_config` replaces the generated `frr.conf` for cases
   the dictionary does not cover.

## Minimal complete topology

```yaml
lab:
  name: my_lab
  description: Two hosts connected through a router

nodes:
  pc_a:
    interfaces: [{network: lan_a, ip: 10.50.1.10/24}]
  r1:
    type: router
    interfaces:
      - {network: lan_a, ip: 10.50.1.1/24}
      - {network: lan_b, ip: 10.50.2.1/24}
  pc_b:
    interfaces: [{network: lan_b, ip: 10.50.2.10/24}]
```

The hosts need no `gateway`: each LAN has a single router, so it is derived.
`network` names collision domains: interfaces with the same name are connected
together. Interface order gives `eth0`, `eth1`, and so on.

## Value types

| Type | Format | Example |
| --- | --- | --- |
| `name` | letter or digit first, then letters, digits, `_`, `.`, `-` | `r1`, `lan_a` |
| `cidr` | IPv4 address/prefix | `10.0.1.10/24` |
| `ip` | IPv4 address without prefix | `10.0.1.1` |
| `subnet` | network/prefix, no host bits | `10.0.1.0/24` |
| `asn` | integer 1–4294967295 | `65001` |
| `area` | integer or dotted notation | `0`, `10`, `0.0.0.10` |
| `protocols` | `ospf`, `rip`, `bgp`, or a list | `ospf`, `[ospf, bgp]` |
| `path` | path relative to the YAML file | `server/` |
| `text` | string; quote numbers | `"2.0"` |

A value of the wrong type is an error. Text keys need quotes around values YAML
would read as numbers: `version: "2.0"`, not `version: 2.0`.

## Sections

The file has two required sections, `lab` and `nodes`, and an optional `capture`.

**`lab`**: `name` (required, `name`; also the output folder), `description`,
`version`, `author`, `email`, `web` (all `text`, written to the `LAB_*` lines of
`lab.conf`; missing ones are not written).

**`capture`**: `networks` (required, list of `name`): the generator adds a
Wireshark node attached to those networks and publishes its GUI on
`http://localhost:3000`. Without the section there is no capture. `--capture`
overrides it. Do not name a node `wireshark` when capturing.

### Node

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `type` | `host` \| `router` | `host` | `router` enables IP forwarding and FRR |
| `interfaces` | list of interfaces | required | at least one |
| `image` | `text` | `kathara/base` for hosts, `kathara/frr` for routers | Docker image |
| `gateway` | `ip` | derived when the LAN has a single router | default route |
| `routes` | list of `{to: subnet, via: ip}` | none | static routes |
| `files` | `path` | none | folder copied into the root of the container |
| `startup` | list of `text` | none | commands run at startup, after the network |
| `roles` | list of `name` | none | roles for the measurement tools, e.g. `[server]`; written to `roles.conf` |
| `kathara` | section | none | native machine options, below |
| `routing` | `protocols` | none | routers only, see Routing |
| `asn` | `asn` | none | routers with BGP only |
| `frr_config` | `text` | none | routers only: hand-written `frr.conf` |

A server is a host: what makes it a server is `files`, `startup` and `roles`.

```yaml
srv_web:
  interfaces: [{network: lan_srv, ip: 10.30.2.10/24}]
  files: srv_web/
  startup:
    - python3 /service.py > /tmp/service.log 2>&1 &
  roles: [server]
```

`files: srv_web/` copies `srv_web/` (next to the YAML) into the node folder of
the lab. Katharà copies that folder into the container root at startup, so
`srv_web/service.py` becomes `/service.py`; the lab folder is also mounted as
`/hostlab`. Python caches, `.git` and `.DS_Store` are skipped. Background
long-running commands with `&` so startup can finish.

**`kathara`** options are written to `lab.conf` with Katharà's names:
`mem` (text, `256m`), `cpus` (number, `0.5`), `ipv6` (bool), `sysctl` (list,
`net.ipv4.tcp_syncookies=1`), `env` (list, `MODE=test`), `ports` (list,
`"8080:80/tcp"`), `bridged` (bool), `num_terms` (integer).

### Interface

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `network` | `name` | required | collision domain |
| `ip` | `cidr` | none | without `ip` the interface is connected but not configured |
| `routing` | `protocols` | all the node's protocols | routers only: protocols running here |
| `area` | `area` | `0` | routers with OSPF only |

## Routing

Routing is described by three keys: `routing` (on the node and, when needed, on
the interface), `area` and `asn`.

- `routing` on the node: the protocols the router uses.
- `routing` on an interface: which of those run there. Only needed when the
  node uses more than one protocol.
- `area` on an interface: its OSPF area, default 0.
- `asn` on the node: the AS number, required with BGP.

iBGP and eBGP are not declared: two routers with the same `asn` form an iBGP
session, with different `asn` an eBGP session.

```yaml
# OSPF multi-area
r1:
  type: router
  routing: ospf
  interfaces:
    - {network: lan_a, ip: 10.10.1.1/24, area: 10}
    - {network: r1_r2, ip: 10.0.12.1/30}

# BGP
r2:
  type: router
  routing: bgp
  asn: 65002
  interfaces:
    - {network: r1_r2, ip: 10.0.12.2/30}
    - {network: r2_r3, ip: 10.0.23.1/30}

# OSPF inside, BGP towards the outside
a1:
  type: router
  routing: [ospf, bgp]
  asn: 65010
  interfaces:
    - {network: a_core, ip: 10.1.0.1/30}
    - {network: wan,    ip: 10.255.0.1/30, routing: bgp}
```

## Automatic behavior

**Nodes:** `type` is `host` when missing; the image comes from the type; a host
without `gateway` uses the only router of its LANs.

**Routers:** IP forwarding is enabled (`net.ipv4.ip_forward=1` in `lab.conf`);
FRR is started with `systemctl start frr` and only the daemons of `routing` are
enabled; router IDs are assigned in order of router name from
`settings.router_id_pool` in the dictionary (default `10.255.255.0/24`, which
must not overlap the lab); each protocol announces the networks of the
interfaces where it runs.

**BGP:** neighbors are the BGP routers on shared networks, on interfaces where
BGP runs; their address and AS come from their node. When every router of an AS
also runs OSPF, the routers of that AS form an iBGP full mesh over loopback
addresses (the router IDs, announced in OSPF), and border routers use
`next-hop-self`. Otherwise routers of the same AS must be directly connected.
`no bgp ebgp-requires-policy` lets eBGP routes pass without policies.

**Redistribution:** on a router with more than one protocol, the routes of each
are redistributed into the others. Redistributed routes are tagged and tagged
routes are never redistributed again, so a route crosses protocols at most once
and cannot loop back into the domain it came from.

## Validation

The Lexer and the Parser report every problem at once, with line and column.
Errors block generation; warnings do not.

| # | Rule | Result |
| --- | --- | --- |
| 1 | Key not in the dictionary | error, with suggestion |
| 2 | Key repeated in the same section | error |
| 3 | Value of the wrong type | error |
| 4 | Invalid node or network name | error |
| 5 | Same IP on two interfaces | error |
| 6 | IP equal to the network or broadcast address | error |
| 7 | Interfaces of the same network with different subnets | error |
| 8 | Subnets of different networks overlapping (or overlapping the router ID pool) | error |
| 9 | Network with a single node | warning |
| 10 | `gateway` outside every local subnet | error |
| 11 | Host without `gateway` on LANs with more than one router | error |
| 12 | Static route with `via` outside every local subnet | error |
| 13 | Router with a single interface | warning |
| 14 | `routing`, `area`, `asn` or `frr_config` on a host | error |
| 15 | Interface `routing` with a protocol the node does not use | error |
| 16 | `area` on an interface where OSPF does not run | error |
| 17 | `asn` without `bgp` in `routing`, or `bgp` without `asn` | error |
| 18 | Two routers on the same network with different areas | error |
| 19 | Area other than 0 with no router connecting it to area 0 | error |
| 20 | Routers of the same AS not directly connected and without OSPF | error |
| 21 | Routers redistributing between the same two protocols in the same domains | warning |
| 22 | `area` or `asn` together with `frr_config` | error: write the routing in `frr_config` |
| 23 | `files` folder missing | error |

Keys of the previous syntax (`lab_name`, `metadata`, `frr`, `default_gateway`,
`commands`, `ospf_area`, `wireshark`, `services`, ...) are reported with what
replaces them. Each router with more than one protocol is listed in a note with
the protocols of each interface.

A successful report does not check container images, command syntax or FRR
convergence: inspect the generated files and test connectivity when adding a
topology.

## Outside the dictionary

Use `frr_config` for: OSPF stub/NSSA areas, link costs, passive interfaces;
hand-picked redistribution, filters and route-maps; hand-picked BGP neighbors,
peers outside the lab, multihop sessions; a router in several AS (`local-as`).
`frr_config` needs `routing` to know which daemons to start.

## Controller address lists

`simulate.sh` discovers `zombie*` nodes and rewrites the attacker's
`zombies.txt` before every run. To write the list at generation time instead:

```bash
python3 generate_lab.py dos_lab --zombies manual --zombies-ips '10.20.1.11,10.20.1.12,10.20.1.13'
```

This writes `labs/dos_lab/attacker/zombies.txt` (the node with role `attacker`,
or `--zombies-node`).
