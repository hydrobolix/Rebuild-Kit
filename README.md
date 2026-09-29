# rebuild-kit

Daily rebuild snapshots for microservices, routes, and middleware when Git may not be there.

A run writes one dated folder. Source files keep their real names. Services, routes, middleware, and other shared paths are copied under one root, and each path is appended as its own tree in `MAP.txt`. The local folder is the first copy. A dock USB must be plugged in for the daily copy. Friday refuses to finish unless two different sticks are mounted.

This is not a second Git history. It is the kit you rebuild from if the remotes are gone.

## GitHub description

Daily rebuild snapshots of microservices, routes, and middleware. One shared root, a map of trees, checksums, and an agent bundle. Requires one USB daily and two on Friday.

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

## Window

`rebuild-kit-gui` is the operator app. Linux x86_64. After download, `chmod +x rebuild-kit-gui`.

- Add top root, or add each microservice, then add routes and middleware.
- Those paths are remembered.
- Refresh lists only USB drives that are plugged in.
- Daily snapshot stays disabled until exactly one drive is checked.
- Friday stays disabled until exactly two different drives are checked.

## Command line

Python 3.9+ and the standard library. No install step.

```bash
cp services.example.json services.json
# edit paths; set "kind" to service, middleware, or root
```

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

A top root instead of the individual folders:

```json
{"name": "platform", "kind": "root", "path": "/src/platform"}
```

Daily, 6pm. The dock stick must be mounted. The job exits non-zero if it is missing, not a mount, or not writable.

```bash
python3 rebuild_kit.py drives
python3 rebuild_kit.py snapshot \
  --config services.json \
  --out "$HOME/backups" \
  --usb /media/$USER/USB-A \
  --keep 7
```

The first successful copy labels that stick `A` in `REBUILD-KIT-ROLE.txt` on the drive root. Seven dated folders are kept on the machine and on the dock stick.

Friday requires both sticks. Same drive twice is rejected. Both copies are verified, then labeled A and B.

```bash
python3 rebuild_kit.py friday \
  --config services.json \
  --out "$HOME/backups" \
  --usb-a /media/$USER/USB-A \
  --usb-b /media/$USER/USB-B
```

Omit the USB flags to pick from the drives that are plugged in.

## Verify

```bash
python3 rebuild_kit.py verify ~/backups/2026-09-28
```

## Restore

Copy `source/` out. It already has the service layout and the middleware beside it. Verify the folder, reinstall dependencies, and put secrets back from the secret store. Read `MAP.txt` for which tree is which. `BUNDLE.llm.txt` is for an agent. Do not build from it.

## Labels

- USB A: dock copy, same age as local, stays plugged in.
- USB B: Friday copy, the one that survives a lost machine.
