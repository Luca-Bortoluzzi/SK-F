# Running experiments

[Back to README](../README.md) · [Results](results.md) · [Troubleshooting](troubleshooting.md)

Complete the [setup](../README.md#setup), activate the Python environment, and
run commands from the repository root. Generate the selected lab with
`--import-dirs` before invoking the simulator.

## Automated workflow

```bash
./simulate.sh dos_lab full
./simulate.sh dos_lab baseline
./simulate.sh dos_lab attack
```

The syntax is `./simulate.sh [LAB] [full|baseline|attack] [options]`. `LAB` is a
generated directory containing `lab.conf`, or a name under this repository's
`labs/`. It is not a YAML filename. `--mode` is equivalent to the positional
mode; `dos` is an alias for `attack`.

| Mode | Measurements |
| --- | --- |
| `full` (default) | Baseline, then measurements while starting the timed load |
| `baseline` | Baseline only; the runner does not start load agents or dispatch an attack |
| `attack` / `dos` | Load measurements only, stored with scenario name `dos` |

The default lab is `mitigation`, but its correct public target must be supplied:

```bash
./simulate.sh mitigation full --target proxy --server server
```

`--target` chooses the endpoint tested by clients and load agents. `--server`
chooses the node running `service.py` and sampled with Docker stats. For
`dos_lab`, both default to `server`. In `mitigation`, the target is `proxy` and
the measured application server is still named `server`. The help text's
`backend_server` reference does not match this configuration.

For every mode, the simulator requires a server node with `service.py`, a node
named exactly `attacker` with `attacker.py`, at least one `zombie*` node with
`zombie.py`, and client nodes. This also applies to baseline and dry-run modes.
Nodes and addresses are read from `lab.conf` and the first `ip address add` line
of each `.startup` file. Target IPs must match one of those first addresses.

The runner:

1. Resolves nodes, selects a simulation number, and rewrites the generated
   attacker's `zombies.txt` from the discovered zombie addresses.
2. Starts Katharà without terminals unless `--no-start` is set.
3. Ensures the TCP service is listening and waits for every selected client to
   ping the target.
4. Runs the requested measurements and samples server container resources.
5. Copies server metrics, prints reports, and optionally creates PNG plots.
6. Cleans up containers it started unless `--keep-running` is set.

Routing readiness requires ICMP reachability. It does not test a proxy's backend
connection or establish that the application will return `pong`.

## Options and environment variables

Command-line options override their corresponding environment variables. Boolean
environment variables use `0` or `1`.

| Option | Default | Environment variable | Purpose |
| --- | --- | --- | --- |
| `--mode MODE` | `full` | `SIM_MODE` | Select measurement phases |
| `--target NODE_OR_IP` | `server` | `SIM_TARGET` | Client and load target |
| `--server NODE` | `server` | `SIM_SERVER` | Service and resource-metrics node |
| `--port N` | `9000` | `SIM_PORT` | Target TCP port and simulator-started service port |
| `--clients "pc_a1 pc_a2"` | Nodes starting `pc_` or `pc-` | `SIM_CLIENTS` | Space-separated client list |
| `--attempts N` | `10` | `SIM_ATTEMPTS` | Simultaneous rounds per client |
| `--timeout N` | `3` | `SIM_TIMEOUT` | Timeout for individual client measurements, in seconds |
| `--delay N` | `1` | `SIM_DELAY` | Seconds between rounds; may be fractional |
| `--startup-wait N` | `25` | `SIM_STARTUP_WAIT` | Wait after starting the lab |
| `--attack-connections N` | `40` | `SIM_ATTACK_CONNECTIONS` | Requested connections per zombie |
| `--attack-duration N` | `60` | `SIM_ATTACK_DURATION` | Requested load duration, capped at 90 seconds |
| `--attack-start-delay N` | `1` | `SIM_ATTACK_START_DELAY` | Delay before dispatching load during the `dos` phase |
| `--connect-timeout N` | `60` | `SIM_CONNECT_TIMEOUT` | Timeout for a Katharà remote command |
| `--connect-retries N` | `3` | `SIM_CONNECT_RETRIES` | Remote-command attempts |
| `--routing-timeout N` | `120` | `SIM_ROUTING_TIMEOUT` | Maximum wait for client reachability |
| `--simulation N` | Next available number | `SIM_SIMULATION` | Explicit positive result-directory number |
| `--no-start` | Off | `SIM_NO_START` | Reuse an already running lab |
| `--keep-running` | Off | `SIM_KEEP_RUNNING` | Keep a lab started by this run alive afterward |
| `--plot` | Off | `SIM_PLOT` | Generate connection-metric plots |
| `--dry-run` | Off | `SIM_DRY_RUN` | Validate local prerequisites and print actions without running the lab |

The port must be 1–65535. Attempts, measurement timeout, connection counts, load
duration, remote-command timeout/retries, and routing timeout must be positive
integers. Startup wait and attack-start delay may be zero. Use a nonnegative
number for the delay between rounds.

For example, reduce the clients and round count during exploration:

```bash
./simulate.sh dos_lab full --clients "pc_a1 pc_b1" --attempts 5
```

The supplied timed agents cap the effective count at **100 connections per
zombie**, even though the controller accepts up to 1,000. Workers start gradually
and reconnect in roughly eight-second cycles. Requested connections are not a
guarantee of that many successful or simultaneously active server connections.

The measurement phase and the load duration are independent. The runner waits
for the controller to send commands, not for every agent's load duration to
expire. A short run may stop the lab while load is still active; a long run may
include post-load recovery. The `dos` phase starts before the delayed attack, so
its first rows may be pre-load measurements. Interpret timestamps and logs
alongside the scenario label.

## Lab lifecycle and repeat runs

To inspect a lab after a baseline, then reuse it for a load run:

```bash
./simulate.sh dos_lab baseline --keep-running
./simulate.sh dos_lab attack --no-start
bash stop.sh labs/dos_lab
```

`--no-start` leaves the existing lab running on completion or failure, regardless
of `--keep-running`. Otherwise, cleanup runs on normal exit and caught failures
or interrupts. `--keep-running` preserves a lab even after an error for inspection.
Stopping the lab ends remaining load agents. Baseline mode on an existing lab
does not stop load left by a previous experiment.

Each invocation normally chooses one greater than the highest existing
`simulation_<number>` directory for that lab. Both phases of a `full` run share
the same number. Reusing `--simulation N` overwrites matching scenario CSVs and
logs, and the simulator resets its server/container metrics files. Other old
files in that directory may remain. Use automatic numbering for fresh runs.

At startup, the simulator truncates `shared/server_metrics.csv` even when reusing
a lab. An existing service keeps its in-memory counters, so clean lab starts are
preferable when comparing independent runs. Run one experiment at a time: node
container lookup selects the first matching node name across running containers,
and run-number selection has no locking.

## Standalone measurements

`run_connection_tests.py` measures an already running service. It does not start
the lab, server, proxy, load agents, or server resource sampler.

Interactive mode prompts for the lab and measurement settings:

```bash
python3 run_connection_tests.py --lab dos_lab
```

For automation, supply `--non-interactive` and explicit settings:

```bash
python3 run_connection_tests.py --lab dos_lab --target server --port 9000 \
  --clients "pc_a1 pc_b1" --scenario baseline \
  --attempts 10 --timeout 3 --delay 1 --non-interactive
```

This command requires a running `dos_lab`; one way to prepare it is
`./simulate.sh dos_lab baseline --keep-running`. Stop it afterward with
`bash stop.sh labs/dos_lab`.

| Option | Standalone behavior |
| --- | --- |
| `--lab NAME_OR_PATH` | Select a generated lab; otherwise infer it from the current directory or `labs/`, prompting if necessary |
| `--target NODE_OR_IP` | Required in noninteractive mode; resolve node names from `.startup` files |
| `--port N` | Required in noninteractive mode; interactive default 9000 |
| `--clients LIST` | Space- or comma-separated names; otherwise discover clients, preferring running Docker containers |
| `--scenario NAME` | Output label, default `baseline`; use a simple filename-safe name |
| `--attempts N`, `--timeout N`, `--delay N` | Defaults 10 rounds, 3 seconds, and 1 second |
| `--simulation N` | Explicit positive number; default next available for this lab |
| `--output-dir PATH` | Parent for `results/` and `logs/`; defaults to the current directory (advanced option, omitted from help) |
| `--non-interactive` | Suppress prompts; otherwise prompts still appear even when options are supplied |

All selected clients run concurrently within each round. The next round starts
after the previous one completes and the delay has elapsed. Missing containers
are logged and skipped; the run fails if none are found. A completed command can
return exit status 0 even when application requests fail: inspect the report's
availability and error counts.

The included service sends a menu on connection. Sending `1\n` returns `pong`,
`2\n` returns server information, and `3\n` closes the connection. The measurement
client always uses option 1 and counts success only when it receives `pong`.

## Service and proxy settings

Set these variables in a node's YAML startup command and regenerate the stopped
lab. Exporting them on the host does not automatically pass them into an existing
container process. If the simulator finds no running service, it launches one
with its selected port, `SERVICE_MAX_ACTIVE=20`, and `SERVICE_BACKLOG=20`.
It does not start or repair the proxy; the mitigation YAML starts that process.

| Service variable | Default in `service.py` | Meaning |
| --- | --- | --- |
| `SERVICE_PORT` | `9000` | Listening TCP port on `0.0.0.0` |
| `SERVICE_BACKLOG` | `40` | Listen backlog; example YAML and simulator use 20 |
| `SERVICE_MAX_ACTIVE` | `20` | Application connection slots |
| `SERVICE_CLIENT_TIMEOUT` | `10` | Seconds to wait for the client's selection |
| `SERVICE_METRICS_PATH` | `/shared/server_metrics.csv` | Structured event/sample CSV |
| `SERVICE_METRICS_SCENARIO_PATH` | `/shared/server_metrics_scenario` | File containing the current scenario label |
| `SERVICE_METRICS_INTERVAL` | `1` | Periodic sample interval in seconds, with a 0.1-second minimum |

The service sends `BUSY` when no slot is available. Accepted connections use a
worker thread and release their slot when closed. Localhost readiness probes are
excluded from its connection counters.

| Proxy variable | Default | Meaning |
| --- | --- | --- |
| `PROXY_LISTEN_HOST`, `PROXY_LISTEN_PORT` | `0.0.0.0`, `9000` | Public listening endpoint |
| `PROXY_BACKEND_HOST`, `PROXY_BACKEND_PORT` | `10.30.2.10`, `9000` | Backend destination |
| `PROXY_BACKLOG` | `64` | Listen backlog |
| `PROXY_MAX_ACTIVE` | `32` | Global concurrent connection limit |
| `PROXY_MAX_PER_SOURCE` | `2` | Concurrent connections per source IP |
| `PROXY_CONNECT_TIMEOUT` | `2` | Backend connection timeout in seconds |
| `PROXY_IDLE_TIMEOUT` | `12` | Relay inactivity timeout in seconds |

The proxy sends `RATE_LIMITED` when capacity is exhausted and
`BACKEND_UNAVAILABLE` when it cannot connect to the backend. It relays TCP bytes
without parsing the application's protocol. Source limits count IP addresses,
so clients sharing an address also share that limit. The proxy writes a text log
to `/tmp/reverse_proxy.log` in the supplied topology; it does not emit the service
metrics CSV.

Changing `--port` alone does not reconfigure an already running service or the
proxy. Keep the YAML service/proxy ports and the measurement port consistent.

## Stop and remove generated data

`bash stop.sh labs/dos_lab` runs `kathara lclean` in that directory. It removes
the lab containers while retaining the generated directory and saved results.
`start.sh` and `stop.sh` take directory paths; they do not resolve configuration
names automatically.

After stopping a lab, `bash del_lab.sh labs/dos_lab` deletes the supplied
directory using `sudo rm -rf`. The argument is not restricted to `labs/`, so check
it before running. `bash del_lab.sh all` removes non-hidden entries under the
current directory's `labs/`; it does not stop containers first.

`bash clear_results.sh` recursively removes **both `results/` and `logs/`** under
the current working directory. It has no selection or confirmation step. Export
any measurements you want to keep before using these removal helpers.
