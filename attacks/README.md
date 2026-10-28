# Load-script variants

[Back to README](../README.md) · [Automated experiments](../docs/running-experiments.md)

This repository contains different controller/agent implementations. Their final
command arguments have different meanings, so keep each controller paired with
its matching agent. Use the supplied generated lab as the target environment.

## Timed connection-holding agents

The standard `simulate.sh` workflow uses files imported from `configs/`:

| Component | Source |
| --- | --- |
| Controller | `configs/attacker/attacker.py` |
| Agents | `configs/zombie_1/zombie.py`, `configs/zombie_2/zombie.py`, `configs/zombie_3/zombie.py` |
| Command | `ATTACK <ip> <port> <connections> <duration_seconds>` on TCP 9999 |
| Completion | Workers stop after their requested duration; the listener stays running |

The controller defaults to 300 connections per zombie and 30 seconds when invoked
directly. The simulator explicitly supplies its own defaults: 40 connections and
60 seconds. The controller caps requests at 1,000 connections and 90 seconds;
each supplied agent further caps them at 100 connections and 180 seconds.
Together, the normal workflow is limited to 100 connections per agent and a
requested duration of 90 seconds.

These agents hold TCP sockets without selecting a menu option, reconnecting in
cycles. They do not implement `STOP` or a successful `PING` response. The
simulator's readiness probe accepts any returned response, so an agent can log a
rejected `PING` while still being considered ready. Wait for load expiry or stop
the lab to end the experiment. Do not use the root `zombies_stop.py` as a stop
mechanism for these timed agents.

The copy at `configs/attacker/zombie.py` uses the same timed implementation but is
not a separate agent node in the supplied YAML.

## Agents that run until STOP

The separate implementation under this directory pairs:

- `attacks/attacker/attacker.py` with `attacks/zombie/zombie.py`;
- command `ATTACK <ip> <port> <workers> <delay_seconds>` on TCP 9999;
- `PING` for readiness and `STOP` to end the current load while keeping the
  listener available.

These workers repeatedly connect, send `ZOMBIE_TRAFFIC\n`, and close the socket.
There is no duration argument or automatic end time. The controller defaults to
30 workers and a 0.01-second delay, and caps workers at 100 and delay at 2 seconds.
Those are controller-side bounds, not validation performed by the agent itself.

The host helper `python3 zombies_start.py --lab dos_lab` copies this STOP-capable
agent to the generated lab's `shared/zombie.py` and starts it on discovered
`zombie*` nodes. It does not replace already running agents unless `--restart`
is passed. Options include `--nodes zombie_1,zombie_2`, `--source PATH`, and
`--shared-script NAME`. With `--restart`, matching Python processes are forcibly
terminated before the selected script starts. Logs are written beside the shared
script, normally `shared/zombie.log`; multiple agents may write that same path.

To end load for this implementation, `python3 zombies_stop.py --lab dos_lab`
dispatches `STOP` through the attacker node; `--attacker-node` selects a different
controller node. The matching in-container helper is
`attacks/attacker/zombies_stop.py`. An address list must exist in one of the paths
searched by the controller/stop helper. The root helpers locate the repository's
`labs/` directory and default to lab name `dos_lab`.

`zombies_start.py` only selects an agent script; it does not copy or switch the
attacker controller. Do not use its default agent as a drop-in replacement in
the timed simulator: the timed controller's duration would be interpreted as a
delay, and load would not expire. Use a separate lab configuration and a matched
controller/agent pair for this workflow.

## Other experimental scripts

`attacks/zombie.py` is an older netcat-loop prototype. It expects only
`ATTACK <ip> <port>`, launches a shell loop, and implements neither duration nor
`STOP`. It is not selected by the standard generator/simulator workflow or by
the default `zombies_start.py` source.

The two `direct_attack.py` files are also distinct:

| Script | Interface and behavior |
| --- | --- |
| `configs/attacker/direct_attack.py` | Positional target host/port, `--connections`, `--interval`, `--duration`; sends incomplete HTTP headers and periodic keep-alive headers |
| `attacks/attacker/direct_attack.py` | Positional target host/port, `--connections`, `--workers`, `--duration`, `--lab-only`; uses concurrent TCP workers with a private/loopback or allowlisted-name target check |

Neither is invoked by `simulate.sh`. The included service is a menu-based TCP
application, so an incomplete HTTP-header experiment does not model the standard
connection-holding experiment. Read each script's `--help` and implementation
before choosing it. The `MAX_DURATION` constant in the latter direct script is
not enforced by its argument validation; it is not a guaranteed duration cap.
