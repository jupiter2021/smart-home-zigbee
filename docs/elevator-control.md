# Elevator Down Call

This optional feature calls the elevator **down** for the configured floor. It is
separate from the Zigbee gateway:

```text
local server -> panel-side HTTP endpoint -> panel-side SIP component -> elevator service
```

The panel-side component sends the request through an existing, authorized
building intercom environment. The panel must already support the original
down-call function, and deployment access must be authorized. Keep the original
intercom function available. This feature exposes only the configured floor's
down call, not door access, floor permissions, other floors, or elevator motion.

## Panel Component

The `panel-elevator` directory contains the small Go component used on the
panel side. It uses Go 1.21+ and the standard library.

1. From the repository root, copy the example and enter the component
   directory. Fill in the values for your authorized intercom environment.
   Keep the default loopback listener when the caller is on the same device.

   ```bash
   cp panel-elevator/config.example.json panel-elevator/config.json
   cd panel-elevator
   ```

2. Generate a token on a local administration machine, then configure the
   same value in both the panel component and the local client. The panel does
   not need Python. The token must be at least 32 characters and must not be
   stored in JSON or YAML:

   ```bash
   # Run on a local administration machine.
   openssl rand -hex 32
   # Set SMZ_ELEVATOR_TOKEN to that same value in both environments.
   export SMZ_ELEVATOR_TOKEN="<same locally generated value>"
   ```

3. On a machine with Go installed, build for the panel's actual CPU
   architecture. This is an example for Linux ARM64; select another `GOARCH`
   when needed:

   ```bash
   CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o panel-elevator .
   ```

4. Copy the binary and `config.json` to the panel through your authorized
   deployment method. Set the token in the panel process environment, then
   start the component there:

   ```bash
   ./panel-elevator -config config.json
   ```

The sample listener is `127.0.0.1:9998`. If the local server is a separate
trusted device, bind to an explicit trusted LAN address and restrict access to
that address or segment. Plain HTTP is intended only for that trusted,
restricted network; use a trusted HTTPS proxy when transport encryption is
required.

For separate devices, update both addresses together. If the panel's trusted
LAN address is `192.0.2.10` (an example address; replace it with your own):

- Panel JSON: `"listen_addr": "192.0.2.10:9998"`
- Local client YAML: `endpoint: "http://192.0.2.10:9998/api/elevator/down"`

The build and environment commands use Bash syntax; on Windows, use Bash/WSL
or the equivalent PowerShell environment settings.

Key fields in `panel-elevator/config.json`:

| Field | Meaning |
|-------|---------|
| `listen_addr` | Client-accessible trusted listen address, for example `127.0.0.1:9998` |
| `sip.source_ip` / `sip.source_id` | Panel wired-intercom identity |
| `sip.target_ip` / `sip.target_id` | Elevator-call target |
| `sip.port` | SIP port, normally `5060` |
| `sip.floor` / `sip.family` | Values registered for the panel and household |

### SIP Configuration

Use the panel's existing room settings, available in the `talk` section of
`/dnake/cfg/sys.xml`. The original application also uses `/dnake/data/sys.xml`
as a fallback. Read these settings through your authorized access method;
do not change the original panel configuration.

For the DNAKE/V700 room-number format used by this project:

| Component | Panel configuration field |
|-----------|---------------------------|
| Building | `/sys/talk/building` |
| Unit | `/sys/talk/unit` |
| Floor | `/sys/talk/floor` |
| Household | `/sys/talk/family` |

Configure the component's `sip` fields as follows. Concatenate the ID components
as decimal text; pad unit, floor, and household to two digits, but do not add
leading zeros to the building number.

| Field | Value to configure |
|-------|--------------------|
| `source_ip` | Panel's wired-intercom IP, normally on `eth0`; it may differ from the address used for the HTTP endpoint |
| `source_id` | Building + unit (2 digits) + floor (2 digits) + household (2 digits) |
| `target_id` | Building + unit (2 digits) + `99` + `01`, the original application's down-call target ID |
| `target_ip` | IP in the panel-resolved target SIP URL, or the registered target address supplied by the building installer |
| `port` | Port in that target SIP URL, normally `5060` |
| `floor` / `family` | Copy `/sys/talk/floor` and `/sys/talk/family` respectively |

For example, building **2**, unit **3**, room **1204** gives `floor: 12`,
`family: 4`, `source_id: "2031204"`, and `target_id: "2039901"`.
Use the panel's configured room values, not a guessed numbering scheme.

The original panel resolves the target address from `target_id`; the address
cannot be calculated from the room number. These settings and an existing
target address are sufficient to configure this component without packet
capture. `/sys/sip/user` is not either of these IDs, and `/sys/talk/server`,
`/sys/sip/proxy`, and `/sys/sip/realm` are not substitutes for `target_ip`.

The local client (`smz` or the Bemfa bridge) must set the same
`SMZ_ELEVATOR_TOKEN` value as the panel component. Its `elevator.token_env`
value must name that environment variable.

## Project Configuration

Copy `config.example.yaml` to `config.yaml` and enable the elevator section:

```yaml
elevator:
  enabled: true
  endpoint: "http://127.0.0.1:9998/api/elevator/down"
  token_env: "SMZ_ELEVATOR_TOKEN"
  timeout: 5
  cooldown: 30
  topic: "dianti006"
```

Use the trusted panel address when the two components are on separate devices.
Do not put the token, personal identity, or building-specific credentials in
`config.yaml`.

## Usage

### CLI

```bash
smz elevator down
```

This command does not connect to or require the Zigbee gateway. It sends one
request to the configured endpoint and prints the returned status.

### Python API

```python
from smart_home_zigbee import ElevatorController, load_config

config = load_config()
elevator = ElevatorController(config.elevator)
result = elevator.call_down()

if result.accepted:
    print(f"Elevator request accepted: {result.request_id}")
else:
    print(f"Elevator request not accepted: {result.status}: {result.message}")
```

### XiaoAi / Bemfa

When the Bemfa bridge is enabled, it subscribes to `elevator.topic` (the
sample value is `dianti006`, a `006` switch). Create that topic in Bemfa and
sync it to Mi Home using the README's existing setup steps. Say **「小爱，打开电梯」** to
send an `on` command. Only `on` triggers the down call; other states do not
send a request. Retained and duplicate messages are ignored, and the cooldown
is applied before another request is allowed. While a request is running,
additional elevator triggers are ignored, not queued; other device commands
continue to be processed.

The cloud message is only a trigger. It does not report that the elevator has
arrived.

## Result Fields

`call_down()` returns an `ElevatorResult` with these fields:

| Field | Meaning |
|-------|---------|
| `accepted` | `true` only when SIP returns 2xx and the business result code is `200`; it does not mean the elevator has arrived |
| `status` | Machine-readable overall status |
| `message` | Human-readable status detail |
| `request_id` | Identifier for correlating the request and response |
| `sip_status` | SIP response status, when returned by the panel component |
| `result_code` | Service result code, when returned |
| `retry_after_seconds` | Remaining cooldown when another request should wait |

Treat a timeout or missing response as an unknown result. Do not retry
automatically or infer physical arrival from an HTTP or SIP response alone.
Request-ID deduplication is in memory for at least 60 seconds or the configured
panel cooldown, whichever is longer; it does not persist across restarts.
