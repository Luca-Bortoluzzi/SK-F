# Configuration reference

[Back to README](../README.md) · [Example catalog](../configs/README.md)

## Generate or validate a lab

```bash
python3 generate_lab.py Test1 --validate-only
python3 generate_lab.py configs/Test1.yml --wireshark disabled
```

A positional argument can be an existing path or a configuration name. Names are
searched as `<name>.yml`, then `<name>.yaml`, under `configs/` by default. Output
goes to `labs/<lab_name>/`, relative to the current working directory.

| Option | Behavior |
| --- | --- |
| `--config-dir PATH` | Search directory for configuration names; default `configs` |
| `--output-dir PATH`, `-o PATH` | Parent directory for the generated lab; default `labs` |
| `--validate-only` | Load and validate the configuration without creating or changing a lab |
| `--import-dirs` | Copy matching node directories next to the YAML into the lab |
| `--force` | Overwrite generated files and merge imported files into an existing lab; unrelated old files remain |
| `--clean` | Delete the entire existing output lab directory and regenerate it |
| `--wireshark ask\|enabled\|disabled` | Control interactive capture selection; default `ask`; see precedence below |
| `--wireshark-networks SELECTION` | Explicit capture-network selection |
| `--zombies manual\|disabled` | Optionally write a controller's `zombies.txt`; default `disabled` |
| `--zombies-node NODE` | Node directory receiving that address list |
| `--zombies-ips LIST` | Addresses for manual mode; accepts spaces, commas, semicolons, and optional CIDR suffixes |

Stop a running lab before regenerating it. `--clean` also removes its `shared/`
files and any manual changes; saved results outside the lab directory remain.
Without `--force` or `--clean`, generation refuses an existing lab directory.
Validation errors prevent generation, but a later generation failure can leave
partial output; generation is not transactional.

## Minimal complete topology

Save this as `configs/my_lab.yml`:

```yaml
lab_name: my_lab
description: Two hosts connected through a router
nodes:
  pc_a:
    type: host
    interfaces:
      - network: lan_a
        ip: 10.50.1.10/24
    default_gateway: 10.50.1.1
  router:
    type: router
    interfaces:
      - network: lan_a
        ip: 10.50.1.1/24
      - network: lan_b
        ip: 10.50.2.1/24
  pc_b:
    type: host
    interfaces:
      - network: lan_b
        ip: 10.50.2.10/24
    default_gateway: 10.50.2.1
```

```bash
python3 generate_lab.py my_lab --validate-only
python3 generate_lab.py my_lab --wireshark disabled
bash start.sh labs/my_lab
```

`network` names identify shared Ethernet collision domains: interfaces with the
same name are connected together. They are not IP prefixes. Interface order
determines `eth0`, `eth1`, and so on.

## Top-level and node fields

| Field | Meaning |
| --- | --- |
| `lab_name` | Required output directory name; use a simple name such as `my_lab` |
| `nodes` | Required, nonempty mapping of node names to definitions |
| `description` | Fallback description for `lab.conf` |
| `metadata` | Optional `description`, `version`, `author`, `email`, and `web` values for `lab.conf` |
| `wireshark` | Optional capture settings described below |

Node names and network names must start with a letter or digit and otherwise
contain only letters, digits, `.`, `_`, or `-`.

| Node field | Meaning |
| --- | --- |
| `type` | `host` (default), `router`, `switch`, or `firewall` |
| `image` | Explicit container image; overrides image selection |
| `interfaces` | Required nonempty list; each item needs `network` and may have `ip` in CIDR notation |
| `default_gateway` | Default route through an address on a local interface subnet |
| `routes` | List of static routes, each with `to` and `via` |
| `commands` | List of shell commands appended to the startup file, in order |
| `frr` | FRR configuration mapping described below |

Example additions to a node definition:

```yaml
routes:
  - to: 10.60.0.0/16
    via: 10.50.1.1
commands:
  - "python3 /hostlab/server/service.py > /tmp/service.log 2>&1 &"
```

Commands run inside the node after its address configuration, routes, and FRR
startup. Background long-running services with `&` so startup can finish. Quote
commands containing YAML-sensitive punctuation such as `: `.

The `router`, `switch`, and `firewall` types select the FRR image and generate FRR
files. They do not automatically create an Ethernet bridge or install firewall
rules. A host with an enabled, nonempty `frr` section also uses FRR. The generator
starts FRR with `systemctl start frr`; it does not emit an IPv4 forwarding command.
Custom routing images must provide the required forwarding and FRR setup.

Use IPv4 for the supplied experiments: the services, agents, and TCP measurement
client use IPv4 sockets. The validator accepting an IPv6 address does not imply
that the experiment pipeline supports IPv6.

## FRRouting

Set `frr.protocol: ospf`, `rip`, or `bgp` for one protocol, or use
`frr.protocols: [ospf, bgp]` for several. Protocols are also enabled by direct
sections such as `frr.ospf: {}`, a mapping under `frr.protocols`, or daemon flags
such as `frr.daemons: {ospfd: true}`.

Protocol-specific settings can live under `frr.protocols.<protocol>` or
`frr.<protocol>`. If both exist, values in the direct section take precedence.
Use one form consistently. Do not rely on `frr.enabled: false` to disable FRR on
a `router`, `switch`, or `firewall`: those types are always treated as FRR devices
by generation, while semantic validation skips the disabled section.

### OSPF

```yaml
frr:
  protocol: ospf
  router_id: 1.1.1.1
  area: 0
```

With no interface area markers, all addressed interface networks are advertised
in `frr.area` (default `0`), or the explicit `frr.networks` list is used. To choose
areas per interface, add `ospf_area`:

```yaml
interfaces:
  - network: lan_a
    ip: 10.50.1.1/24
    ospf_area: 10
  - network: backbone
    ip: 10.0.12.1/30
    ospf_area: 0
frr:
  protocol: ospf
  router_id: 1.1.1.1
  ospf:
    areas:
      - id: 10
        type: stub
        no_summary: true
    redistribute: [connected]
```

Once any addressed interface has `ospf_area`, only marked interfaces contribute
generated OSPF network statements. Area types `stub` and `nssa` are emitted;
`no_summary` or `totally_stub` appends `no-summary`. `frr.ospf.router_id` overrides
the common ID. Use interface markers or `frr.networks`; the generator does not
consume `frr.ospf.networks`, although the validator inspects it.

### RIP

```yaml
frr:
  protocol: rip
  rip:
    version: 2
    auto_summary: false
    redistribute: [connected]
```

Mark interfaces with `rip: true` to select their networks. If none are marked,
generation falls back to `frr.networks` or all addressed interfaces.
`frr.rip.networks` overrides that selection. Version defaults to 2; automatic
summary is disabled by default.

### BGP

```yaml
frr:
  protocol: bgp
  router_id: 1.1.1.1
  bgp:
    asn: 65001
    neighbors:
      - ip: 10.0.12.2
        remote_as: 65002
    networks: [10.50.1.0/24]
    redistribute: [connected]
```

Declare the peer as a node with its own BGP ASN and matching addresses.
`asn`, `neighbors`, and `networks` can also live directly under `frr` for the
legacy form used by `multi_AS.yml`. Unlike OSPF and RIP, BGP does not implicitly
advertise all interface networks. By default, generated configuration disables
`ebgp-requires-policy` and `network import-check`; set `ebgp_requires_policy` or
`network_import_check` to `true` in the BGP section to omit those disabling lines.

### Custom FRR configuration

`frr.config` replaces the generated `frr.conf` text. Declare the needed protocols
as well: daemon enablement is still generated separately. Imported
`etc/frr/daemons`, `frr.conf`, and `vtysh.conf` are overwritten by generation for
FRR devices. The validator does not parse custom FRR text.

## Import node files

With `--import-dirs`, a directory next to the YAML with the same name as a node is
merged into that node's generated directory:

```text
configs/server/service.py       -> labs/dos_lab/server/service.py
configs/server/server_metrics.py -> labs/dos_lab/server/server_metrics.py
```

No matching directory is required for a node. Directories with names absent from
the YAML are skipped with a warning. Python caches, `.git`, and `.DS_Store` are
excluded. Runtime scripts should use paths appropriate to the lab mount, such as
`/hostlab/server/service.py` for the generated `server/service.py` file. The
simulator also searches alternative locations when starting services and agents.
Its temporary remote scripts and service metrics require the lab's `shared/`
directory to be available inside containers as `/shared`.

Katharà also copies the node directory's contents into the node filesystem at
startup: `server/service.py` becomes `/service.py` on the server, and
`r1/etc/frr/frr.conf` becomes `/etc/frr/frr.conf` on `r1`. See the
[directory manual](https://www.kathara.org/man-pages/kathara-lab-dirs.7.html).

`services` declarations, including the one in `multi-area.yml`, are not consumed
by this generator. Use `commands` to start an imported service. The YAML's
`simulation` and `connection_tests` sections supply targets/clients for semantic
checks; they do **not** configure `simulate.sh` or `run_connection_tests.py`.
Pass experiment settings explicitly on the command line.

For an attacker/zombie topology, declare a target that exists in the lab:

```yaml
simulation:
  enabled: true
  target_node: server
```

Alternatively use `target_ip`. Equivalent target-reference checks exist for
`connection_tests`, `attack`, and `dos` sections and supported top-level target
keys. These declarations do not launch traffic.

## Wireshark

Attach capture to networks, not directly to a router. For example:

```bash
python3 generate_lab.py multi_AS --wireshark-networks node:r1
```

Selections accept a comma-separated list of collision-domain names, menu indices
(starting at 1), `node:<name>` for all of a node's networks, or `all`. `0` selects
none. The equivalent YAML selection is:

```yaml
wireshark:
  enabled: true
  networks: [lan_a, r1_r2]
```

Selection precedence is: explicit `--wireshark-networks`, then YAML settings,
then the interactive mode. Consequently, **`--wireshark disabled` does not
override networks explicitly configured in YAML**; use `--wireshark-networks 0`
to force no capture. `ask` prompts whether to enable capture; `enabled` skips
that question and asks for networks. Without a terminal, either mode adds no
capture unless a CLI or YAML selection is supplied.

The generator adds a `wireshark` node, bridges it, and publishes host TCP port
3000. It prints `http://localhost:3000` as the GUI address; access and authentication
depend on the installed image version. The image is not pinned. See Katharà's
[capture tutorial](https://github.com/KatharaFramework/Kathara/wiki/Capture-Packets-using-Wireshark-GUI)
for the network attachment model. Avoid a user-defined node named `wireshark`
when enabling integration, and ensure port 3000 is available.

## Controller address lists

The generator does not automatically discover zombie IPs. To write the list:

```bash
python3 generate_lab.py dos_lab --import-dirs --wireshark disabled \
  --zombies manual --zombies-node attacker \
  --zombies-ips '10.20.1.11,10.20.1.12,10.20.1.13'
```

This writes `labs/dos_lab/attacker/zombies.txt`. Manual mode without addresses
prompts in a terminal and fails without one. Even with addresses, manual mode
can prompt for the controller node in a terminal unless `--zombies-node` is set.
`--zombies-ips` alone has no effect while mode remains `disabled`.

For automated experiments, `simulate.sh` discovers `zombie*` nodes and rewrites
the generated attacker's list before execution, including baseline runs. It uses
each node's first configured IP. Manual list generation is unnecessary for that
workflow; `--zombies disabled` does not prevent a list being imported from a node
directory.

## Validation boundaries

Validation checks required structure, node and network names, supported types,
IP uniqueness, host addresses, subnet consistency and overlap, gateway subnet
membership, explicit router-forwarding disablement, routing IDs, BGP peer ASNs,
OSPF network declarations, and experiment target/client references. Warnings
cover isolated networks, one-interface routers, nonreciprocal BGP neighbors, and
skipped import directories.

A successful report does not verify container images, command syntax, service
files, all static-route fields, FRR convergence, or application availability.
Unknown fields are not rejected as a complete schema would reject them. Inspect
the generated files and test connectivity when adding a topology.
