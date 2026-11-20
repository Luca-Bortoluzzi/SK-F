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
4. **Routing is explicit, the rest is derived.** Router IDs, OSPF areas and their
   types, BGP neighbors and announced networks are written by the user. Default
   gateways, session addresses and redistribution come from the topology.
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
| `area_type` | `normal`, `stub`, `totally_stub`, `nssa` | `stub` |
| `path` | path relative to the YAML file | `server/` |
| `text` | string; quote numbers | `"2.0"` |
| `bool` | `true` or `false` | `true` |
| `number` | positive number | `0.5` |
| `count` | non-negative integer | `2` |

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
| `ospf` | section | none | routers only, see [OSPF](#ospf) |
| `rip` | section | none | routers only, see [RIP](#rip) |
| `bgp` | section | none | routers only, see [BGP](#bgp) |
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

## Routing

A router runs a protocol when it has the protocol's section: `ospf`, `rip`,
`bgp`. Each section says explicitly where the protocol runs; networks are
referred to by name, and the router must have an addressed interface on them.

### OSPF

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `router_id` | `ip` | yes | OSPF router ID, unique among the OSPF routers |
| `areas` | list of areas | yes | the areas the router belongs to |

Each area has:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | `area` | required | `0` is the backbone |
| `type` | `area_type` | `normal` | `stub`, `totally_stub` (stub without inter-area routes) or `nssa`; the backbone is always `normal` |
| `networks` | list of `name` | required | the router's networks in this area; OSPF runs on those interfaces |

```yaml
r2:
  type: router
  interfaces:
    - {network: lan_b, ip: 10.10.2.1/24}
    - {network: r1_r2, ip: 10.0.12.2/30}
    - {network: r2_r4, ip: 10.0.24.1/30}
  ospf:
    router_id: 2.2.2.2
    areas:
      - id: 0
        networks: [r1_r2]
      - id: 10
        type: totally_stub
        networks: [lan_b]
      - id: 20
        type: stub
        networks: [r2_r4]
```

`r2` is an area border router between areas 0, 10 and 20. Every router of an
area must declare the same type: FRR needs it on all of them. Interfaces whose
network is not listed do not run OSPF.

### RIP

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `networks` | list of `name` | yes | the router's networks where RIP version 2 runs |

```yaml
rip:
  networks: [rip_net, lan_r]
```

### BGP

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `asn` | `asn` | yes | AS number of the router |
| `router_id` | `ip` | yes | BGP router ID, unique among the BGP routers |
| `neighbors` | list of node names | yes | BGP neighbors; every session is declared on both routers |
| `networks` | list of `name` | no | networks announced in BGP; the router does not need to be attached to them |

```yaml
a1:
  type: router
  interfaces:
    - {network: a_core, ip: 10.1.0.1/30}
    - {network: wan,    ip: 10.255.0.1/30}
  ospf:
    router_id: 1.1.1.1
    areas:
      - id: 0
        networks: [a_core]
  bgp:
    asn: 65010
    router_id: 1.1.1.1
    neighbors: [b1]
    networks: [lan_a]
```

iBGP and eBGP are not declared: two neighbors with the same `asn` form an iBGP
session, with different `asn` an eBGP session. The session addresses come from
the topology:

- **eBGP:** the neighbor's address on a network both routers are attached to.
- **iBGP, both routers with an `ospf` section:** the neighbor's BGP `router_id`,
  with the router's own `router_id` as source. If the router ID is not the
  address of one of the router's interfaces, it is added to `lo` as a `/32` and
  announced in OSPF; if it is, that interface must be in an OSPF area.
- **iBGP, otherwise:** the neighbor's address on a shared network.

iBGP needs every pair of routers of an AS to be neighbors (full mesh). Routers
with eBGP neighbors use `next-hop-self` towards their iBGP neighbors. A network
listed in `networks` is announced only once the router has a route to it, for
example through OSPF.

## Automatic behavior

**Nodes:** `type` is `host` when missing; the image comes from the type; a host
without `gateway` uses the only router of its LANs.

**Routers:** IP forwarding is enabled (`net.ipv4.ip_forward=1` in `lab.conf`);
FRR is started with `systemctl start frr` and only the daemons of the router's
sections are enabled (all of them with `frr_config`).

**BGP:** `no bgp ebgp-requires-policy` lets eBGP routes pass without policies.

**Redistribution:** on a router with OSPF or RIP and another protocol, the routes
of the others are redistributed into OSPF and RIP: routes learned through BGP
reach the routers without BGP, and OSPF and RIP exchange their routes. Nothing
is redistributed into BGP, which announces only its `networks`. Redistributed
routes are tagged and tagged routes are never redistributed again, so a route
crosses protocols at most once and cannot loop back into the domain it came from.

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
| 8 | Subnets of different networks overlapping | error |
| 9 | Network with a single node | warning |
| 10 | `gateway` outside every local subnet | error |
| 11 | Host without `gateway` on LANs with more than one router | error |
| 12 | Static route with `via` outside every local subnet | error |
| 13 | Router with a single interface | warning |
| 14 | `ospf`, `rip`, `bgp` or `frr_config` on a host | error |
| 15 | Network in `ospf` or `rip` where the router has no interface, or whose interface has no `ip` | error, with suggestion |
| 16 | Network listed twice, or in two areas of the same router; area listed twice | error |
| 17 | Area 0 declared `stub`, `totally_stub` or `nssa` | error |
| 18 | `frr_config` together with `ospf`, `rip` or `bgp` | error: write the routing in `frr_config` |
| 19 | Two routers putting the same network in different areas | error |
| 20 | Routers of the same area declaring different types | error |
| 21 | Area other than 0 with no router connecting it to area 0 | error |
| 22 | Same OSPF router ID on two routers | error |
| 23 | Same BGP router ID on two routers | error |
| 24 | BGP neighbor that does not exist, is the router itself, has no `bgp` section or uses `frr_config` | error |
| 25 | BGP session declared on one router only | error |
| 26 | eBGP neighbors sharing no network (multihop) | error |
| 27 | iBGP neighbors sharing no network, without `ospf` on both | error |
| 28 | Network in `bgp.networks` that does not exist or has no subnet | error |
| 29 | iBGP over OSPF with a router ID that is an interface address not in OSPF | error |
| 30 | iBGP over OSPF with a router ID that belongs to another node or lies inside a lab network | error |
| 31 | Two routers of the same AS that are not BGP neighbors | warning |
| 32 | Routers redistributing between OSPF and RIP in the same domains | warning |
| 33 | `files` folder missing | error |

The Parser also reports capture networks that do not exist and a node named
`wireshark` when capturing.

Keys of the previous syntax (`lab_name`, `metadata`, `frr`, `routing`, `area`,
`default_gateway`, `commands`, `ospf_area`, `wireshark`, `services`, ...) are
reported with what replaces them.

A successful report does not check container images, command syntax or FRR
convergence: inspect the generated files and test connectivity when adding a
topology.

## Outside the dictionary

Use `frr_config` for: OSPF link costs, passive interfaces, virtual links;
hand-picked redistribution, filters and route-maps; BGP peers outside the lab
and multihop sessions; a router in several AS (`local-as`). With `frr_config`
the router has no `ospf`, `rip` or `bgp` section and FRR starts every routing
daemon.

## Controller address lists

`simulate.sh` discovers `zombie*` nodes and rewrites the attacker's
`zombies.txt` before every run. To write the list at generation time instead:

```bash
python3 generate_lab.py dos_lab --zombies manual --zombies-ips '10.20.1.11,10.20.1.12,10.20.1.13'
```

This writes `labs/dos_lab/attacker/zombies.txt` (the node with role `attacker`,
or `--zombies-node`).
