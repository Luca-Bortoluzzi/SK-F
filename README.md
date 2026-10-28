# SK-F — Simple Katharà Framework

SK-F turns YAML network descriptions into Katharà labs and provides tools to
measure how a TCP service behaves before and during controlled denial-of-service
(DoS) experiments. Use it to explore routing, compare service availability, and
study a TCP proxy that limits concurrent connections.

The project is a collection of Python and Bash command-line tools. Katharà runs
the virtual network; SK-F generates its configuration, coordinates experiments,
and produces CSV files, text reports, and optional plots.

## Features

- Define hosts, routers, interfaces, collision domains, static routes, and startup
  commands in YAML.
- Generate FRRouting (FRR) configuration for OSPF, multi-area OSPF, RIP, BGP, and
  combinations of these protocols.
- Validate addresses, subnets, gateways, routing declarations, and experiment
  targets before creating files.
- Import per-node files and attach a Wireshark node to selected networks.
- Run baseline and load experiments with simultaneous requests from multiple
  clients, including automatic startup, routing checks, and cleanup.
- Measure ICMP round-trip time, TCP connection time, application response time,
  availability, server connections, and server container resources.
- Compare a directly exposed service with a service behind a connection-limiting
  proxy.

Load experiments are intended for the generated, authorized lab network. The
measurement client uses the included service's `1\n` / `pong` protocol; it is not
a general HTTP benchmark.

## Setup

Run the commands below from the repository root. The automation expects a Linux
environment with Bash 4 or newer, GNU command-line tools (including `timeout`),
Python **3.10 or newer**, and Katharà using its **Docker backend**. The measurement
tools call Docker directly, so using a different Katharà backend is insufficient.

Install Katharà and Docker using the
[official Katharà installation guide](https://github.com/KatharaFramework/Kathara/wiki/Installation-Guides).
The user running SK-F must be able to run both `kathara` and `docker ps`.

Create a Python environment and install the YAML dependency:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install PyYAML
```

For PNG plots, also install Matplotlib:

```bash
python3 -m pip install matplotlib
```

Scripts are stored without executable permissions. Enable the entry points once;
`simulate.sh` also executes the Python measurement and report scripts directly:

```bash
chmod +x ./*.sh ./*.py
```

Check the tools before running a lab:

```bash
python3 --version
python3 -c 'import yaml; print(yaml.__version__)'
kathara --version
docker ps
```

Generation and YAML validation only need Python and PyYAML. Text reports use the
Python standard library. Lab execution needs Docker images: hosts default to
`kathara/base`, routing devices to `kathara/frr`, and optional Wireshark to
`lscr.io/linuxserver/wireshark`. Initial image downloads require network access.
Client and service images must contain Python 3; clients also need `ping`.

## First experiment

Validate and generate the supplied lab. `--import-dirs` includes the application
and load-agent files stored next to the YAML configuration.

```bash
python3 generate_lab.py dos_lab --import-dirs --validate-only
python3 generate_lab.py dos_lab --import-dirs --wireshark disabled
```

This creates `labs/dos_lab/`. A warning that the `proxy` directory is skipped is
expected: this topology has no proxy node.

Preview the workflow, then run a baseline with no generated attack traffic:

```bash
./simulate.sh dos_lab baseline --dry-run
./simulate.sh dos_lab baseline
```

The simulator starts the lab without terminals, ensures the server is listening,
waits for routes, measures all eight `pc_*` clients, prints reports, and cleans up
the lab containers. It leaves generated files and results on disk. By default,
each client makes one request in each of ten simultaneous rounds.

For a baseline followed by a controlled load experiment in the same run:

```bash
./simulate.sh dos_lab full --plot
```

Omit `--plot` if Matplotlib is not installed. New runs receive an incrementing
directory under `results/dos_lab/`, such as `simulation_0001/`. The load scenario
is named `dos` in filenames even though the command-line mode is `attack`.

To compare the mitigation topology:

```bash
python3 generate_lab.py mitigation --import-dirs --wireshark disabled
./simulate.sh mitigation full --target proxy --server server --plot
```

Pass **`--target proxy`** for this topology. The YAML's `simulation.target_node`
is validated but is not read by the simulator. Its default target is `server`,
which is on the private backend network in this lab.

## Working with a network manually

For a small routing example without the experiment runner:

```bash
python3 generate_lab.py Test1 --wireshark disabled
bash start.sh labs/test
```

The output directory uses the YAML's `lab_name`, which is `test` in this example.
To inspect a node, open a terminal in the generated directory:

```bash
cd labs/test
kathara connect pc_a
```

Inside `pc_a`, try `ping -c 3 10.0.2.10`, then `exit`. From the repository root,
stop the lab with:

```bash
bash stop.sh labs/test
```

## Documentation

| Guide | What it covers |
| --- | --- |
| [Example configurations](configs/README.md) | Available topologies, their output names, and sample limitations |
| [Configuration reference](docs/configuration.md) | YAML fields, routing, file imports, validation, and Wireshark |
| [Running experiments](docs/running-experiments.md) | Simulator options, manual tests, lifecycle, and service settings |
| [Results and metrics](docs/results.md) | Output files, CSV columns, reports, and interpretation |
| [Troubleshooting](docs/troubleshooting.md) | Setup failures, routing, missing services, and unexpected results |
| [Load-script variants](attacks/README.md) | Timed agents versus agents that require an explicit stop |
| [Development notes](docs/development.md) | Code layout, extension points, and checks for changes |

All Python entry points and `simulate.sh` support `--help`. For example:

```bash
python3 generate_lab.py --help
./simulate.sh --help
python3 run_connection_tests.py --help
```

## License

SK-F is distributed under the [MIT license](LICENSE).
