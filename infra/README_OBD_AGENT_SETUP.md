# OBD Agent Setup Guide

The OBD Agent (`obd_agent/`) is a standalone Python package that reads OBD-II data from ELM327 adapters and POSTs sanitised `OBDSnapshot` JSON to the `diagnostic_api` service. It supports **simulation mode** (no hardware) and **live mode** (with adapter).

> **License note:** `python-OBD` is GPL-2.0. It is isolated in this separate package to avoid license contamination of `diagnostic_api`.

---

## Quick Start (Simulation)

No hardware required. Works on any OS.

```bash
cd obd_agent
pip install -r requirements-sim.txt
python -m obd_agent --dry-run --once
```

This will:
1. Load the `misfire` simulation scenario
2. Build a single `OBDSnapshot`
3. Validate it locally (dry-run) and print the result
4. Exit

---

## Running Modes

### Continuous Polling (default)

```bash
python -m obd_agent
```

Reads a snapshot every 30 seconds (configurable via `SNAPSHOT_INTERVAL_SECONDS`) and POSTs to the diagnostic API.

### Single Snapshot

```bash
python -m obd_agent --once
```

### Dry Run (validate only, no HTTP)

```bash
python -m obd_agent --dry-run --once
```

---

## Live Mode: USB Adapter Setup

Set `OBD_PORT` to your serial port. Common values:

| OS | Port | Notes |
|----|------|-------|
| Linux (USB) | `/dev/ttyUSB0` | Add user to `dialout` group: `sudo usermod -aG dialout $USER` |
| Linux (BT) | `/dev/rfcomm0` | See Bluetooth section below |
| macOS (USB) | `/dev/tty.usbserial-XXXX` | Install CH340/FTDI driver if needed |
| macOS (BT) | `/dev/tty.OBDII-SPPDev` | See Bluetooth section below |
| Windows (USB) | `COM3` | Check Device Manager for port number |
| WSL2 | See below | Requires `usbipd-win` |

### Linux USB Setup

```bash
# 1. Plug in ELM327 USB adapter
# 2. Check the port
ls /dev/ttyUSB*

# 3. Add user to dialout group (one-time)
sudo usermod -aG dialout $USER
# Log out and back in for group change to take effect

# 4. Install full dependencies (includes python-obd GPL)
cd obd_agent
pip install -r requirements.txt

# 5. Run
OBD_PORT=/dev/ttyUSB0 python -m obd_agent --once
```

### Windows USB Setup

```bash
# 1. Plug in ELM327 USB adapter
# 2. Open Device Manager -> Ports (COM & LPT) -> note COM port number
# 3. Install dependencies
cd obd_agent
pip install -r requirements.txt

# 4. Run
set OBD_PORT=COM3
python -m obd_agent --once
```

### WSL2 + usbipd-win

USB serial devices don't pass through to WSL2 by default. Use `usbipd-win`:

```powershell
# PowerShell (Admin) on Windows host
winget install usbipd

# List USB devices
usbipd list

# Bind and attach the ELM327 (note the BUSID, e.g. 1-4)
usbipd bind --busid 1-4
usbipd attach --wsl --busid 1-4
```

Then in WSL2:

```bash
ls /dev/ttyUSB*
OBD_PORT=/dev/ttyUSB0 python -m obd_agent --once
```

---

## Live Mode: Bluetooth Adapter Setup

### Linux Bluetooth

```bash
# 1. Pair the ELM327 Bluetooth adapter
bluetoothctl
  scan on
  pair XX:XX:XX:XX:XX:XX
  trust XX:XX:XX:XX:XX:XX
  quit

# 2. Bind to rfcomm
sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX

# 3. Run
OBD_PORT=/dev/rfcomm0 python -m obd_agent --once
```

### macOS Bluetooth

1. Open **System Preferences** > **Bluetooth**
2. Pair the ELM327 adapter (PIN is usually `1234` or `0000`)
3. Check the port: `ls /dev/tty.OBDII*` or `ls /dev/tty.*SPP*`
4. Run:
   ```bash
   OBD_PORT=/dev/tty.OBDII-SPPDev python -m obd_agent --once
   ```

### Windows Bluetooth

1. Open **Settings** > **Bluetooth & devices** > pair the ELM327
2. Open **Device Manager** > **Ports (COM & LPT)** > note the outgoing COM port
3. Run:
   ```cmd
   set OBD_PORT=COM5
   python -m obd_agent --once
   ```

---

## Container Mode

Run the OBD agent as a Docker container alongside the rest of the stack:

```bash
cd infra
docker compose -f docker-compose.yml -f obd-agent.compose.override.yml up -d obd-agent
docker logs stf-obd-agent
```

The container defaults to simulation mode. For live mode with USB passthrough (Linux only):

1. Edit `obd-agent.compose.override.yml`
2. Uncomment the `devices:` section
3. Set `OBD_PORT` to the container-side device path

> **Note:** USB passthrough does not work with Docker Desktop on macOS or Windows. Use host-mode Python for live mode on those platforms.

---

## Configuration Reference

All settings can be overridden via environment variables or a `.env` file in the `obd_agent/` directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `OBD_PORT` | `sim` | Serial port or `sim` for simulation |
| `OBD_BAUDRATE` | `115200` | Serial baud rate |
| `VEHICLE_ID` | `V-SIM-001` | Pseudonymous vehicle identifier |
| `OBD_SIM_SCENARIO` | `misfire` | Simulation scenario: `healthy`, `misfire`, `lean`, `overheat` |
| `DIAGNOSTIC_API_BASE_URL` | `http://127.0.0.1:8000` | Target API base URL |
| `SNAPSHOT_INTERVAL_SECONDS` | `30` | Polling interval |
| `DRY_RUN` | `false` | Validate locally, skip HTTP POST |
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FORMAT` | `console` | `console` or `json` |
| `MAX_RETRY_ATTEMPTS` | `3` | HTTP retry count before buffering |
| `OFFLINE_BUFFER_MAX` | `100` | Max snapshots buffered when API is down |

---

## Running Tests

```bash
cd obd_agent

# Simulation-only (no GPL deps, safe for CI)
pip install -r requirements-sim.txt
pytest tests/ -v

# Full (includes python-obd)
pip install -r requirements.txt
pytest tests/ -v
```

---

## Troubleshooting

### "Permission denied" on `/dev/ttyUSB0`

Add your user to the `dialout` group and log out/in:

```bash
sudo usermod -aG dialout $USER
```

### "ImportError: python-OBD is required for live mode"

You're running in live mode (`OBD_PORT` != `sim`) but only have simulation dependencies installed. Install the full requirements:

```bash
pip install -r requirements.txt
```

### "Unknown simulation scenario"

Check available scenarios in `fixtures/simulation_scenarios.json`. Current options: `healthy`, `misfire`, `lean`, `overheat`.

### Connection timeouts / "not connected"

- Check that the ELM327 adapter LED is on
- Verify the port with `ls /dev/ttyUSB*` (Linux) or Device Manager (Windows)
- Try a different baud rate: `OBD_BAUDRATE=9600`
- Some cheap adapters need a longer connection timeout

### Agent buffers snapshots indefinitely

The `diagnostic_api` endpoint `/v1/telemetry/obd_snapshot` may not be built yet. The agent handles 404/501 gracefully and does not buffer those responses. Check the API logs if you see 500 errors.
