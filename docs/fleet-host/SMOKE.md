# Fleet-host on-host smoke (F5 acceptance)

The executable acceptance for the fleet-host runtime (ADR-0002 §11). Run on
the fleet-host VM — Docker and systemd are VM-only; none of this runs in the dev
container. The Python boundary logic (KV fetch, the PAT-exclusion projection, unit
rendering, repo sync) is covered by squadra's pytest suite; this runbook is the
on-host half that pytest cannot reach.

## Prerequisites

- The VM is provisioned from [`cloud-init.fleet-host.yaml`](cloud-init.fleet-host.yaml)
  (or hand-provisioned to the same substrate), and
  [`fleet-host-activate.sh`](fleet-host-activate.sh) has run successfully — i.e.
  squadra is installed into `/opt/squadra/venv` and the units are in
  `/etc/systemd/system/`.
- The VM's managed identity has a Key Vault **`get`** grant on `fleet-kv`
  for `anthropic-api-key` and `fleet-ado-pat` (both fetched at tick time). squadra
  itself installs from public PyPI, so activation needs no Key Vault secret.
- The timer is **not** enabled yet (it shouldn't be — activation is the last step).
- `/opt/squadra/fleet-host.env` sets `FLEET_NAME`, the fleet's `[fleet] name`.
  The units are `squadra-<name>.service` / `.timer` (N34); the acceptance below
  is templated on that name. On a host installed before fleet names, do
  [Upgrading a host](#upgrading-a-host-from-before-fleet-names) first.

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
set -a; . /opt/squadra/fleet-host.env; set +a   # FLEET_KEY_VAULT, FLEET_NAME
cd /path/to/goss.yaml   # copy docs/fleet-host/goss.yaml onto the VM
goss -g goss.yaml --vars-inline "{\"fleet_name\": \"$FLEET_NAME\"}" \
  validate --format documentation
```

Without the `fleet_name` var goss renders `<no value>` into the unit paths and
the file checks fail, so a missing name can't pass.

All checks must pass. What they prove:

| Check | Proves |
|---|---|
| `squadra-<name>.service` is oneshot, runs `squadra fleet-tick`, carries `FLEET_INSTALLED_NAME=<name>`, no PAT in the unit | units rendered correctly under the fleet's name; no secret on disk |
| `squadra-<name>.timer` present, `Unit=squadra-<name>.service` | the schedule is installed |
| no unnamed `squadra.service` / `squadra.timer` | no pre-name units left ticking the same repository |
| `squadra-<name>.timer` **not enabled / not running** | the guardrail — fleet not yet activated |
| `az login --identity` exits 0 | IMDS reachable; identity assigned |
| both tick-time KV secrets readable | the `get` grant is in place (`anthropic-api-key`, `fleet-ado-pat`) |
| **dry-run tick under systemd exits 0** | the load-bearing gate — `fleet-tick` fetched secrets from KV, synced the app repo, found the clone's `[fleet] name` equal to `FLEET_NAME`, and planned a (non-mutating) tick under systemd |

## 3. What is asserted where

- **Agent-env minimization** (the PAT never reaches the contained agent) is the
  highest-value security property. It is proven in pytest — `tests/test_secrets.py::
  test_agent_environ_never_exposes_the_pat` — and structurally by the consuming repo's
  `.squadra/` compose listing only `ANTHROPIC_API_KEY` on the `agent` service. A
  dry-run tick launches no agent container, so it is not asserted live in goss.
- **No-secret-on-disk** is enforced by `fleet-tick` (in-process env only) and
  checked by the goss assertion that the unit contains no PAT.

## 4. Activate (only when deliberately bringing the fleet up)

After the smoke is green and you intend to run the fleet for real:

```bash
sudo systemctl enable --now "squadra-${FLEET_NAME}.timer"
systemctl list-timers "squadra-${FLEET_NAME}.timer"   # confirm the schedule
journalctl -u "squadra-${FLEET_NAME}.service" -f      # watch ticks
```

Re-run `goss validate` afterward with the `squadra-<name>.timer` expectations
flipped to `enabled: true` / `running: true` to confirm activation.

## Upgrading a host from before fleet names

A host installed before `[fleet] name` (squadra before SQ5g) runs unnamed
`squadra.service` / `squadra.timer`. `install-units` refuses to install the named
units beside them while they serve the same repository, and goss fails while
they exist. In order:

1. **Name the fleet in the app repo.** Commit `[fleet] name = "<name>"` to its
   `squadra.toml` (1-24 lowercase letters, digits or hyphens). The tick reads the
   name from there only.
2. **Keep the claim tags, or have no increment dirs.** Claims made before names
   carry the old default prefix `fleet:`; the new default is `<name>:`, which
   would read them as a human's. Either commit `tag_prefix = "fleet:"` under
   `[board]` in the same change, or let in-flight increments finish and move
   `fleet_root/<id>/` aside. Otherwise the first tick refuses: an unstamped
   `fleet_root` with increment dirs and a defaulted prefix.
3. **Add the name to the env file.** Set `FLEET_NAME=<name>` in
   `/opt/squadra/fleet-host.env`, equal to step 1's name (and bump
   `SQUADRA_VERSION` or `/opt/squadra/PINNED_VERSION`).
4. **Stop and remove the unnamed units.** Let any running tick finish:

   ```bash
   sudo systemctl disable --now squadra.timer
   while systemctl is-active --quiet squadra.service; do sleep 5; done
   sudo rm /etc/systemd/system/squadra.service /etc/systemd/system/squadra.timer
   sudo systemctl daemon-reload
   ```

5. **Re-activate.** Run `fleet-host-activate.sh`; it installs
   `squadra-<name>.service` / `.timer`. Then run the acceptance (section 2) and
   activate (section 4).
