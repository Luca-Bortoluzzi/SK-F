# Development notes

[Back to README](../README.md) · [Configuration contract](configuration.md)

## Code layout

| File or directory | Responsibility |
| --- | --- |
| `generate_lab.py` | Thin command-line entry point for the generator |
| `src_gen_lab/Lexer.py` | Resolve configuration paths and load YAML with `safe_load` |
| `src_gen_lab/Parser.py` | Validate required structure and collect semantic errors/warnings |
| `src_gen_lab/gen_lab.py` | Render `lab.conf`, startup scripts, FRR files, node imports, Wireshark, and optional zombie address lists |
| `configs/` | Source topology definitions and per-node runtime files |
| `start.sh`, `stop.sh`, `del_lab.sh` | Start, clean up, and delete generated labs |
| `simulate.sh` | Coordinate a lab experiment and collect server/container metrics |
| `run_connection_tests.py` | Discover running clients, execute measurements concurrently, and write CSV/logs |
| `show_connection_results.py` | Summarize connection results and optionally plot them |
| `configs/server/service.py`, `server_metrics.py` | TCP service and thread-safe event/sample writer |
| `configs/proxy/reverse_proxy.py` | TCP relay with global and per-source concurrency limits |
| `show_server_metrics.py` | Summarize service events and Docker resource samples |
| `zombies_start.py`, `zombies_stop.py`, `attacks/` | Separate manual load workflow; see the [protocol guide](../attacks/README.md) |
| `clear_results.sh` | Remove the current directory's saved results and logs |

There is no package installer or dependency lockfile. PyYAML is required for
generation; Matplotlib is optional for plotting. Other imports in the host tools
use the Python standard library.

## Generation pipeline

The CLI resolves a YAML path, loads a mapping, checks its structure, and prints a
semantic report. `--validate-only` stops there. Otherwise, `generate_lab()` checks
the configuration again, selects capture networks, prepares the output directory,
and writes files in this order:

1. `lab.conf` with metadata, interface-to-network assignments, and images.
2. Imported node directories, if requested.
3. An optional manually supplied `zombies.txt`.
4. `<node>.startup` files and `etc/frr/` files for FRR devices.
5. Removal of obsolete Wireshark helper scripts.

Imported files may be overwritten by later generated FRR files. File generation
is not atomic. Keep editable source in `configs/`, inspect errors before reusing
a partial lab, and preserve the distinction between `--force` and `--clean`.

The generated layout looks like:

```text
labs/<lab_name>/
  lab.conf
  <node>.startup
  <node>/                         # imported files and/or generated FRR files
    etc/frr/daemons
    etc/frr/frr.conf
    etc/frr/vtysh.conf
  shared/                         # created/used by runtime tools
```

Node directory contents follow Katharà's filesystem conventions; see the
[lab directory manual](https://www.kathara.org/man-pages/kathara-lab-dirs.7.html).

## Measurement contracts

`run_connection_tests.py` runs an embedded Python program inside each client via
`docker exec`. That program returns JSON for ICMP, TCP, and application results.
Timing inside the client is separate from host command execution time. Preserve
that boundary when adding metrics: Docker process startup is not network RTT.

One thread per available client runs each round. Results are written to CSV and
per-client logs after collection. Keep the embedded measurement fields, fallback
error rows, CSV field list, report functions, and [schema documentation](results.md)
consistent when changing output.

The service writes both event snapshots and periodic samples. Counters are
cumulative for the service process; scenario summaries count events separately.
The proxy currently logs decisions as text and does not use its copy of
`server_metrics.py`.

The simulator writes temporary `/shared/.simulate_<node>.sh` scripts and checks
an `__SIM_RC__:<code>` marker from their output. The marker distinguishes command
completion inside the node from a successful host-side Katharà invocation.
Cleanup collects metrics and removes temporary scripts, then cleans the lab only
if the runner started it and `--keep-running` is off.

## Checks for changes

There is currently no committed automated test suite or CI configuration. Use
checks appropriate to the area changed; validation and dry runs need no running
containers.

```bash
python3 generate_lab.py Test1 --validate-only
python3 generate_lab.py multi-area --import-dirs --validate-only
python3 generate_lab.py multi_AS --validate-only
python3 generate_lab.py dos_lab --import-dirs --validate-only
python3 generate_lab.py mitigation --import-dirs --validate-only
for script in simulate.sh start.sh stop.sh del_lab.sh clear_results.sh; do
  bash -n "$script" || break
done
```

`client-router-server.yml` is an unfinished configuration and is expected to fail
validation. The example catalog records expected import warnings.

Generate into a temporary directory to inspect changes without overwriting labs:

```bash
docs_lab_dir=$(mktemp -d /tmp/skf-check.XXXXXX)
python3 generate_lab.py dos_lab --import-dirs --wireshark disabled --output-dir "$docs_lab_dir"
bash simulate.sh "$docs_lab_dir/dos_lab" full --dry-run
```

Dry runs verify local assumptions and remote shell syntax, not container startup,
application behavior, or routing convergence. For runtime changes, also run an
appropriate baseline or controlled experiment and examine its logs and metrics.
Avoid simultaneous labs sharing node names, because client/server container
discovery is not scoped to the lab identifier.

When adding a YAML field, update both validation and generation, and document
whether it changes runtime behavior. Existing `services`, `simulation`, and
`connection_tests` declarations illustrate why accepted YAML must not be mistaken
for implemented execution behavior. When modifying a load script, keep its
controller/agent argument protocol consistent and update the variant guide.
