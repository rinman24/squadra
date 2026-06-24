# Fleet-host on-host smoke (F5 acceptance)

The executable acceptance for the fleet-host runtime (ADR-0002 §11). Run on
`gswa-fleet-host` — Docker and systemd are VM-only; none of this runs in the dev
container. The Python boundary logic (KV fetch, the PAT-exclusion projection, unit
rendering, repo sync) is covered by squadra's pytest suite; this runbook is the
on-host half that pytest cannot reach.

## Prerequisites

- The VM is provisioned from [`cloud-init.fleet-host.yaml`](cloud-init.fleet-host.yaml)
  (or hand-provisioned to the same substrate), and
  [`fleet-host-activate.sh`](fleet-host-activate.sh) has run successfully — i.e.
  squadra is installed into `/opt/squadra/venv` and the units are in
  `/etc/systemd/system/`.
- The VM's managed identity has a Key Vault **`get`** grant on `gswa-fleet-kv-07c17c`
  for `anthropic-api-key` and `fleet-ado-pat` (both fetched at tick time). squadra
  itself installs from public PyPI, so activation needs no Key Vault secret.
- The timer is **not** enabled yet (it shouldn't be — activation is the last step).

## 1. Install goss (pinned + checksum-verified)

goss is a test tool, not fleet runtime, so it is fetched ad hoc — not baked into the
production image (keeps the host's pinned-artifact "level B" supply-chain norm):

```bash
GOSS_VERSION=v0.4.9
GOSS_SHA256=<pin-the-published-sha256-for-this-version-and-arch>
curl -fsSL -o /tmp/goss \
  "https://github.com/goss-org/goss/releases/download/${GOSS_VERSION}/goss-linux-amd64"
echo "${GOSS_SHA256}  /tmp/goss" | sha256sum -c -
sudo install -m 0755 /tmp/goss /usr/local/bin/goss
```

## 2. Run the acceptance

```bash
export FLEET_KEY_VAULT=gswa-fleet-kv-07c17c
cd /path/to/goss.yaml   # copy docs/fleet-host/goss.yaml onto the VM
goss -g goss.yaml validate --format documentation
```

All checks must pass. What they prove:

| Check | Proves |
|---|---|
| `squadra.service` is oneshot, runs `squadra fleet-tick`, no PAT in the unit | units rendered correctly; no secret on disk |
| `squadra.timer` present, `Unit=squadra.service` | the schedule is installed |
| `squadra.timer` **not enabled / not running** | the guardrail — fleet not yet activated |
| `az login --identity` exits 0 | IMDS reachable; identity assigned |
| both tick-time KV secrets readable | the `get` grant is in place (`anthropic-api-key`, `fleet-ado-pat`) |
| **dry-run tick under systemd exits 0** | the load-bearing gate — `fleet-tick` fetched secrets from KV, synced the app repo, and planned a (non-mutating) tick under systemd |

## 3. What is asserted where

- **Agent-env minimization** (the PAT never reaches the contained agent) is the
  highest-value security property. It is proven in pytest — `tests/test_secrets.py::
  test_agent_environ_never_exposes_the_pat` — and structurally by the gswa
  `.squadra/` compose listing only `ANTHROPIC_API_KEY` on the `agent` service. A
  dry-run tick launches no agent container, so it is not asserted live in goss.
- **No-secret-on-disk** is enforced by `fleet-tick` (in-process env only) and
  checked by the goss assertion that the unit contains no PAT.

## 4. Activate (only when deliberately bringing the fleet up)

After the smoke is green and you intend to run the fleet for real:

```bash
sudo systemctl enable --now squadra.timer
systemctl list-timers squadra.timer        # confirm the schedule
journalctl -u squadra.service -f           # watch ticks
```

Re-run `goss validate` afterward with the `squadra.timer` expectations flipped to
`enabled: true` / `running: true` to confirm activation.
