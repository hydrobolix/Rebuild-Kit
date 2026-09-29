# rebuild-kit

Daily rebuild snapshots for microservices, routes, and middleware when Git may not be there.

A run writes one dated folder. Source files keep their real names. Services, routes, middleware, and other shared paths are copied under one root, and each path is appended as its own tree in `MAP.txt`. The local folder is the first copy. A dock USB must be plugged in for the daily copy. Friday refuses to finish unless two different sticks are mounted.

## Install

Python 3.9 or newer. The standard library is enough. There is no package to install.

```bash
git clone https://github.com/hydrobolix/Rebuild-Kit.git
cd Rebuild-Kit
python3 rebuild_kit.py --help
```

The window is optional and is a separate Linux x86_64 binary, `rebuild-kit-gui`. It is not produced by the clone. After you place that binary next to the script:

```bash
chmod +x rebuild-kit-gui
./rebuild-kit-gui
```

## Set up

Do this once. It writes the path list the run commands read.

```bash
cp services.example.json services.json
```

Edit `services.json`. `path` is the real directory on the machine that will run the snapshot. `name` is only the label in `MAP.txt`. `kind` is one of:

- `service` — a microservice
- `middleware` — a route, gateway, filter, or other shared path
- `root` — the top folder, mapped and copied whole

Services plus middleware:

```json
{
  "keep_days": 7,
  "keep_fridays": 2,
  "services": [
    {"name": "payments", "kind": "service", "path": "/src/platform/services/payments"},
    {"name": "orders", "kind": "service", "path": "/src/platform/services/orders"},
    {"name": "auth", "kind": "middleware", "path": "/src/platform/middleware/auth"}
  ]
}
```

Top root instead of individual folders:

```json
{
  "keep_days": 7,
  "keep_fridays": 2,
  "services": [
    {"name": "platform", "kind": "root", "path": "/src/platform"}
  ]
}
```

Plug in the dock stick. Confirm the tool can see it:

```bash
python3 rebuild_kit.py drives
```

Use the mount it prints, usually `/media/$USER/USB-A`, as `--usb` below. `services.json` stays on the machine. It is not copied to the stick.

In the window, setup is the path list instead of the file: Add top root, or Add service, then Add route / middleware. Those paths are remembered. Refresh until the dock stick appears.

## Run

The dock stick must be mounted. The job exits non-zero if it is missing, not a mount, or not writable.

Daily snapshot, 6pm:

```bash
python3 rebuild_kit.py snapshot \
  --config services.json \
  --out "$HOME/backups" \
  --usb /media/$USER/USB-A \
  --keep 7
```

Friday, both sticks mounted. Same drive twice is rejected.

```bash
python3 rebuild_kit.py friday \
  --config services.json \
  --out "$HOME/backups" \
  --usb-a /media/$USER/USB-A \
  --usb-b /media/$USER/USB-B
```

Omit the USB flags to pick from the drives that are plugged in. The first successful copy labels the dock stick `A` in `REBUILD-KIT-ROLE.txt` on the drive root. Friday labels the second stick `B`. Seven dated folders are kept on the machine and on the dock stick.

In the window, check one drive and press Daily snapshot. Check two drives and press Friday, both sticks. The other button stays disabled.

Verify a day:

```bash
python3 rebuild_kit.py verify ~/backups/2026-09-28
```

## What a day looks like

```text
backups/2026-09-28/
  SNAPSHOT.json
  MAP.txt
  MANIFEST.json
  BUNDLE.llm.txt
  README.restore.md
  source/
    services/payments/...
    services/orders/...
    middleware/auth/...
    gateway/routes.yml
```

`MAP.txt` has one section per path you added. A top root is mapped and copied whole. Separate service and middleware folders are placed under their shared parent, not flattened into twenty kits.

Skipped on every run: `.git`, `node_modules`, `vendor`, `dist`, build caches, and secret names (`.env`, `*.pem`, `*.key`, `id_rsa`, `credentials.json`, and the rest of the default list). Symlinks are not followed.

## Restore

Copy `source/` out. It already has the service layout and the middleware beside it. Verify the folder, reinstall dependencies, and put secrets back from the secret store. Read `MAP.txt` for which tree is which. `BUNDLE.llm.txt` is for an agent. Do not build from it.

## Labels

- USB A: dock copy, same age as local, stays plugged in.
- USB B: Friday copy, the one that survives a lost machine.
