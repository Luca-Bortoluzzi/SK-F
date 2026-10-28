# Results and metrics

[Back to README](../README.md) · [Running experiments](running-experiments.md)

## Output locations

A full simulator run produces this layout. The number shown is an example; use
the directory printed by your run.

```text
results/dos_lab/simulation_0001/
  connection_results_baseline.csv
  connection_results_dos.csv
  server_metrics.csv
  server_container_metrics.csv
  plots/                                  # only with --plot
    connection_results_baseline_metrics.png
    connection_results_dos_metrics.png
logs/dos_lab/simulation_0001/
  connection_tests_baseline.log
  connection_tests_dos.log
logs/dos_lab/pc_a1/simulation_0001/          # one directory per measured client
  baseline.log
  dos.log
```

Baseline-only and attack-only runs contain only their requested connection CSV.
`server_metrics.csv` is copied from the generated lab's
`shared/server_metrics.csv` if available. `server_container_metrics.csv` can
contain only a header if no Docker sample was collected. Standalone connection
tests produce the connection CSVs and logs, not these two server metrics files.

The simulator anchors `results/` and `logs/` to the repository containing
`simulate.sh`. Standalone tests default to the current working directory unless
`--output-dir` is set. The generated lab directory's basename determines the
result subdirectory; different paths with the same basename share that namespace.

## Display reports and plots

```bash
python3 show_connection_results.py results/dos_lab/simulation_0001/connection_results_baseline.csv
python3 show_connection_results.py results/dos_lab/simulation_0001/connection_results_dos.csv --plot
python3 show_server_metrics.py results/dos_lab/simulation_0001/server_metrics.csv \
  --container results/dos_lab/simulation_0001/server_container_metrics.csv
```

Without a CSV argument, `show_connection_results.py` recursively selects the
most recently modified `connection_results_*.csv` under the current directory's
`results/`. This may belong to another lab. Use an explicit path for comparisons.

Connection reports include availability, timeouts, experiment duration, latency
distributions (mean, median, p95, minimum, maximum), per-client summaries, and
error counts. PNGs contain ICMP, TCP, and application timings by client and round,
plus application availability by round. Matplotlib is only needed for plots.

`--plot-output PATH` changes the PNG destination when used with `--plot`:

```bash
python3 show_connection_results.py results/dos_lab/simulation_0001/connection_results_dos.csv \
  --plot --plot-output /tmp/dos-metrics.png
```

On a host without a display, prefix the report command with `MPLBACKEND=Agg`.
Historical CSVs using `elapsed_ms` remain readable, but their timing includes
Docker execution overhead and should not be compared directly with the current
client-side latency columns.

## Connection CSV schema

Each row represents one request attempt by one client in one round. With eight
available clients and ten rounds, a completed scenario contains 80 rows. Each
client first runs a single ICMP probe, then measures a TCP/application exchange.

| Column | Meaning |
| --- | --- |
| `timestamp` | Host timestamp when the result row was constructed |
| `experiment_started_at` | Host timestamp at measurement-phase start |
| `experiment_elapsed_ms` | Host elapsed time since phase start at result collection |
| `simulation`, `scenario` | Run directory identifier and scenario label |
| `client`, `request_index` | Client node and round number, starting at 1 |
| `target_input`, `target`, `port` | Requested name/IP, resolved address, and TCP port |
| `icmp_status`, `icmp_rtt_ms`, `icmp_error` | ICMP success, measured round-trip time, and error |
| `tcp_status`, `tcp_connect_ms`, `tcp_error` | TCP connection success, `connect()` duration inside the client, and error |
| `application_status`, `application_response_ms`, `application_error` | Success on receiving `pong`, time from just before sending `1\n` until `pong`, and error |
| `request_completion_ms` | Time inside the client from just before TCP connection setup through receiving `pong`; excludes ICMP and Docker overhead |
| `host_command_completion_ms` | Host time for the entire Docker measurement command, including ICMP, TCP/application work, and process overhead |
| `status`, `error` | Compatibility aliases of application status and error |

Timings are milliseconds. An unavailable timing is an empty field, not zero.
Statuses are `OK` or `FAIL`. Application timing is recorded only for successful
responses; failed requests must still be considered when interpreting latency.

Application availability is:

```text
100 × rows with application_status == OK / total result rows
```

A successful ping or TCP handshake does not establish application availability.
The server can respond `BUSY`, or the proxy can respond `RATE_LIMITED`, while the
TCP connection succeeds. These responses normally appear as `pong_not_received`
in the connection CSV; inspect service/proxy logs for the specific reason.

Common errors include `icmp_timeout`, `icmp_unreachable_or_failed`,
`ping_not_available`, `tcp_connect_timeout`, `application_timeout`,
`pong_not_received`, and `socket_error:<errno>`. One row can contribute errors
at several layers, so the report's total error-detail counts can exceed its
number of failed application requests.

## Server metrics

`server_metrics.csv` combines periodic snapshots and events. Timestamps are
generated inside the service container; the scenario comes from
`/shared/server_metrics_scenario`, which the simulator changes between phases.

| Column | Meaning |
| --- | --- |
| `timestamp`, `scenario` | Snapshot time and current phase (`idle`, `baseline`, `dos`, or `unknown` without a scenario file) |
| `event` | `server_start`, `sample`, `accepted`, `busy`, `rejected`, or `closed` |
| `uptime_seconds` | Elapsed service process lifetime |
| `active_connections` | Currently tracked accepted connections |
| `accepted_connections`, `rejected_connections` | Cumulative accepted/rejected connection counters |
| `busy_responses` | Cumulative successfully sent `BUSY` responses |
| `completed_connections` | Cumulative accepted connections that have closed, including those without a successful application exchange |
| `max_simultaneous_connections` | High-water mark of tracked active connections |
| `average_connection_duration_ms` | Cumulative mean duration of closed accepted connections |
| `active_threads` | Current Python thread count, including service and sampling threads |

Do not sum cumulative columns across rows. The summary uses maxima for lifetime
counters and counts events for per-scenario accepted/rejected totals. Mean active
connections use periodic samples when present, avoiding a mean dominated by busy
event bursts. Localhost service readiness probes are excluded from connection
counters; load-agent connections are included.

In the mitigation lab, these values describe the backend server. A connection
rejected by the proxy never reaches it, so backend rejection counts alone cannot
measure proxy rejections. Use client availability and `/tmp/reverse_proxy.log`
alongside backend metrics.

## Container metrics

`server_container_metrics.csv` contains `timestamp`, `scenario`, `container`,
`cpu_percent`, `memory_usage`, `memory_percent`, and `pids`. Values come from
`docker stats --no-stream`; percentages retain their `%` suffix and memory usage
is a human-readable usage/limit string. Sampling pauses for about a second after
each Docker call, so the interval includes the call's own runtime.

`show_server_metrics.py --container ...` reports mean/maximum CPU and memory
percentages and maximum PID count by scenario. The sampler follows `--server`,
so the default mitigation run samples the backend, not the proxy. It is active
during measurement phases, not throughout the entire lab lifetime.

## Comparing experiments

Compare baseline and `dos` from the same run first, then compare the direct
service and proxy topologies using the same client list, rounds, timeouts, and
load settings. Examine application availability together with latency and server
occupancy: a low average latency among a few successes can coexist with many
failed requests.

Check that every requested client appears in the logs; absent containers are
skipped and do not contribute failed rows. Check controller and agent logs too:
the current controller prints individual dispatch errors without necessarily
returning a failing process exit code. A `dos` label alone does not prove that
load reached the server.

The default one-second attack-start delay allows initial pre-load rows in the
`dos` phase. Load can also finish before measurement ends. Scenario labels apply
to measurement phases, not an exact attack-active time interval. Keep the YAML,
command-line options, image versions, and relevant logs with exported results
when you need to reproduce an experiment.
