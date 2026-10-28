# Troubleshooting

[Back to README](../README.md) · [Configuration](configuration.md) · [Experiments](running-experiments.md)

Start with local validation and an execution preview:

```bash
python3 generate_lab.py dos_lab --import-dirs --validate-only
./simulate.sh dos_lab baseline --dry-run
```

The second command requires an already generated lab. A dry run validates the
local layout and generated remote shell syntax; it does not check Docker,
container images, live routes, or service availability.

## Setup and generation

| Symptom | What to check |
| --- | --- |
| `Permission denied` when running a script | Run `chmod +x ./*.sh ./*.py` from the repository root. Invoking the simulator through Bash alone does not make its directly executed Python helpers executable. |
| `PyYAML is not installed` | Activate `.venv` and run `python3 -m pip install PyYAML`. |
| Python syntax/type-annotation errors | Use Python 3.10+ on the host and for the supplied container scripts. |
| `kathara`, `docker`, or `timeout` not found | Complete setup and check `PATH`. The simulator expects Linux/GNU utilities and Katharà's Docker backend. |
| Docker daemon or socket permission error | Verify `docker ps` works as the same user. Check Docker installation/service and user access using the official installation instructions. |
| `start.sh` cannot open terminal windows | Configure an available terminal in Katharà, or run `kathara lstart --noterminals` from the generated lab directory. The simulator already uses this headless mode. |
| Configuration not found | Use a filename stem with its exact case, such as `multi_AS`, or pass `configs/multi_AS.yml`. |
| Lab directory already exists | Stop it, then choose `--force` to merge/overwrite or `--clean` to delete and regenerate. |
| Lab not found after generation | Look at YAML `lab_name`, not just its filename: `Test1` creates `labs/test`, and `multi-area` creates `labs/Test1_multi_area`. |
| `client-router-server` fails validation | This is an unfinished placeholder with no nodes; choose a populated example. |
| A directory import is skipped | Its name does not match a node in this topology. This is expected for `configs/proxy/` when generating `dos_lab`. |

## Missing service or load files

The experiment labs need `--import-dirs`. To regenerate an existing stopped lab:

```bash
python3 generate_lab.py dos_lab --import-dirs --force --wireshark disabled
```

Check that `labs/dos_lab/server/service.py`, its sibling `server_metrics.py`,
`attacker/attacker.py`, and each `zombie_*/zombie.py` exist. Startup commands run
inside containers, so host-relative paths such as `configs/server/service.py`
will not work there. Check the files actually mounted in `/hostlab` and `/shared`.

Some `dos_lab` zombie startup commands use `/hostlab/zombie.py`; with a lab-root
mount, the file is instead under `/hostlab/zombie_1/zombie.py` (and similarly for
other agents). The simulator searches these node-specific paths and starts the
agents when needed. For a manual startup workflow, use the correct path in each
node's YAML `commands`. The `multi-area` example's `services` declaration is not
implemented by the generator; add a startup command before testing its service.

## Routes and mitigation

`Routing did not become ready` means at least one client could not ping the
selected target within `--routing-timeout`. Use `--keep-running` on a diagnostic
run to retain containers after failure. Inspect a client and router from the lab
directory:

```bash
cd labs/dos_lab
kathara exec pc_a1 ip route
kathara exec pc_a1 ping -c 1 10.30.1.10
kathara exec r1 vtysh -c 'show ip route'
kathara exec r1 vtysh -c 'show ip ospf neighbor'
```

Check matching collision domains, interface addresses, default gateways, FRR
configuration, and forwarding. Increase `--startup-wait` or `--routing-timeout`
when the configuration is correct but convergence is slow. ICMP filtering also
prevents the simulator from passing its readiness check.

For mitigation, use `--target proxy --server server`. The private backend
`10.30.2.10` is not routed from the clients; the public proxy is `10.30.1.10`.
The simulator does not start the proxy on demand. Inspect its startup and backend
reachability if clients can ping the proxy but cannot obtain `pong`.

## Inspect running services

From the generated lab directory:

```bash
kathara exec server tail -n 40 /tmp/service.log
kathara exec zombie_1 tail -n 40 /tmp/zombie.log
```

In the mitigation lab, also run:

```bash
kathara exec proxy tail -n 40 /tmp/reverse_proxy.log
```

The simulator's `kathara exec ... did not return a command result` error means it
did not receive its completion marker. Check that the node is running, `/shared`
is mounted, and the shell command can execute. For slow container startup, tune
`--connect-timeout` and `--connect-retries`.

## Unexpected measurements

| Symptom | Likely explanation or next step |
| --- | --- |
| TCP succeeds but the application fails | `BUSY`, `RATE_LIMITED`, `BACKEND_UNAVAILABLE`, or an incompatible service can yield `pong_not_received`. Inspect application/proxy logs. |
| No availability drop during `dos` | Check controller output and agent logs for actual dispatch. Also check whether the measurement window ended before load started, or extended past its duration. |
| Baseline is already overloaded | An earlier run may have left agents active with `--keep-running` or `--no-start`. Stop and restart the lab for an independent baseline. |
| `STOP` is rejected | Timed agents under `configs/zombie_*/` do not implement `STOP`. Wait for expiry or stop the lab. See [load-script variants](../attacks/README.md). |
| Fewer rows than clients × attempts | Missing containers are skipped. Inspect the main connection-test log and avoid concurrent labs with duplicate node names. |
| Server metrics missing | Check `/tmp/service.log`, availability of `/shared`, metrics environment variables, and write permissions. An already listening process may not be the instrumented service. |
| Server counters seem to include earlier runs | Reusing a running service resets the CSV, not its in-memory counters. Restart the lab for fresh counters. |
| No container resource statistics | Check Docker access, server container discovery, and whether `server_container_metrics.csv` has data beyond its header. |
| Latest report shows the wrong lab | The report searches all connection CSVs under the current `results/` by modification time. Supply the exact path. |
| Plot generation fails | Install Matplotlib in the active environment. On a headless host, run with `MPLBACKEND=Agg`. |
| Wireshark is present despite `--wireshark disabled` | A YAML network selection takes precedence. Use `--wireshark-networks 0`. |
| Wireshark GUI unavailable | Check that its container started, host port 3000 is free, and the installed image's access requirements. |

To stop a retained lab, return to the repository root and run
`bash stop.sh labs/dos_lab` (or the corresponding lab directory). This leaves
saved result files intact.
