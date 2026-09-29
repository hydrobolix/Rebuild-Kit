#!/usr/bin/env python3
"""Desktop window for rebuild-kit.

Daily snapshot stays disabled until one plugged-in USB is selected.
Friday stays disabled until two different plugged-in USBs are selected.
"""

from __future__ import annotations

import contextlib
import io
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import rebuild_kit as kit

SETTINGS = Path.home() / ".config" / "rebuild-kit" / "ui.json"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Rebuild Kit")
        self.geometry("860x640")
        self.minsize(760, 560)
        self.services: list[dict] = []
        self.drive_vars: list[tuple[tk.BooleanVar, dict]] = []
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self._build()
        self._load()
        self.refresh_drives()
        self.after(200, self._drain_log)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        pad = {"padx": 12, "pady": 6}

        services = ttk.LabelFrame(self, text="Paths")
        services.grid(row=0, column=0, sticky="ew", **pad)
        services.columnconfigure(0, weight=1)
        self.service_list = tk.Listbox(services, height=6, exportselection=False)
        self.service_list.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=8)
        ttk.Button(services, text="Add top root", command=lambda: self.add_service("root")).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        ttk.Button(services, text="Add service", command=lambda: self.add_service("service")).grid(row=1, column=1, sticky="w", pady=(0, 8))
        ttk.Button(services, text="Add route / middleware", command=lambda: self.add_service("middleware")).grid(row=1, column=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Button(services, text="Remove", command=self.remove_service).grid(row=1, column=3, sticky="w", pady=(0, 8))

        dest = ttk.LabelFrame(self, text="Local copy")
        dest.grid(row=1, column=0, sticky="ew", **pad)
        dest.columnconfigure(1, weight=1)
        ttk.Label(dest, text="Folder").grid(row=0, column=0, padx=8, pady=8)
        self.out_var = tk.StringVar(value=str(Path.home() / "backups"))
        ttk.Entry(dest, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", pady=8)
        ttk.Button(dest, text="Browse", command=self.pick_out).grid(row=0, column=2, padx=8, pady=8)
        ttk.Label(dest, text="Keep days").grid(row=1, column=0, padx=8, pady=(0, 8))
        self.keep_var = tk.IntVar(value=7)
        ttk.Spinbox(dest, from_=1, to=30, textvariable=self.keep_var, width=6).grid(row=1, column=1, sticky="w", pady=(0, 8))

        drives = ttk.LabelFrame(self, text="Plugged-in USB drives")
        drives.grid(row=2, column=0, sticky="ew", **pad)
        drives.columnconfigure(0, weight=1)
        self.drive_box = ttk.Frame(drives)
        self.drive_box.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(drives, text="Refresh", command=self.refresh_drives).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        self.rule = ttk.Label(drives, text="Select 1 drive for a daily snapshot. Friday needs 2.")
        self.rule.grid(row=2, column=0, sticky="w", padx=8, pady=(0, 8))

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew", **pad)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.snapshot_btn = ttk.Button(actions, text="Daily snapshot", command=self.run_snapshot)
        self.snapshot_btn.grid(row=0, column=0, padx=(0, 8))
        self.friday_btn = ttk.Button(actions, text="Friday, both sticks", command=self.run_friday)
        self.friday_btn.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Verify local", command=self.run_verify).grid(row=0, column=2)

    def _load(self) -> None:
        if not SETTINGS.is_file():
            return
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.out_var.set(data.get("out") or self.out_var.get())
        self.keep_var.set(int(data.get("keep") or 7))
        for item in data.get("services") or []:
            self.services.append(item)
            self.service_list.insert("end", f"{item.get('kind', 'service'):8}  {item['name']}    {item['path']}")

    def _save(self) -> None:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps({
            "out": self.out_var.get(),
            "keep": int(self.keep_var.get()),
            "services": self.services,
        }, indent=2) + "\n", encoding="utf-8")

    def add_service(self, kind: str) -> None:
        titles = {"root": "Top root", "service": "Microservice folder", "middleware": "Route, middleware, or other shared path"}
        path = filedialog.askdirectory(title=titles.get(kind, "Folder"))
        if not path:
            return
        name = Path(path).name
        self.services.append({"name": name, "kind": kind, "path": path})
        self.service_list.insert("end", f"{kind:8}  {name}    {path}")
        self._save()

    def remove_service(self) -> None:
        picked = self.service_list.curselection()
        if not picked:
            return
        index = picked[0]
        self.service_list.delete(index)
        del self.services[index]
        self._save()

    def pick_out(self) -> None:
        path = filedialog.askdirectory(title="Local backup folder")
        if path:
            self.out_var.set(path)
            self._save()

    def refresh_drives(self) -> None:
        for child in self.drive_box.winfo_children():
            child.destroy()
        self.drive_vars = []
        drives = kit.mounted_usb_drives()
        if not drives:
            ttk.Label(self.drive_box, text="No USB drive is plugged in.").grid(row=0, column=0, sticky="w")
        for index, drive in enumerate(drives):
            var = tk.BooleanVar(value=False)
            role = f"  [{drive['role']}]" if drive["role"] else ""
            label = drive["label"] or drive["name"] or "USB"
            text = f"{label}   {drive['size']}   {drive['mount']}{role}"
            ttk.Checkbutton(self.drive_box, text=text, variable=var, command=self._sync_buttons).grid(
                row=index, column=0, sticky="w", pady=2
            )
            self.drive_vars.append((var, drive))
        self._sync_buttons()

    def selected_mounts(self) -> list[str]:
        return [drive["mount"] for var, drive in self.drive_vars if var.get()]

    def _sync_buttons(self) -> None:
        count = len(self.selected_mounts())
        busy = self.worker is not None and self.worker.is_alive()
        self.snapshot_btn.configure(state="normal" if count == 1 and not busy else "disabled")
        self.friday_btn.configure(state="normal" if count == 2 and not busy else "disabled")
        if count == 0:
            self.rule.configure(text="Plug in a USB and refresh. Daily needs 1. Friday needs 2.")
        elif count == 1:
            self.rule.configure(text="1 drive selected. Daily snapshot is ready. Friday needs a second drive.")
        elif count == 2:
            self.rule.configure(text="2 drives selected. Friday can copy to both. Uncheck one for a daily snapshot.")
        else:
            self.rule.configure(text="Select only 1 drive for daily, or exactly 2 for Friday.")

    def write(self, line: str) -> None:
        self.log_queue.put(line)

    def _drain_log(self) -> None:
        while not self.log_queue.empty():
            line = self.log_queue.get()
            self.log.configure(state="normal")
            self.log.insert("end", line + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(200, self._drain_log)

    def _guard(self) -> bool:
        if not self.services:
            messagebox.showerror("Rebuild Kit", "Add a top root, or the service and route folders.")
            return False
        if self.worker is not None and self.worker.is_alive():
            return False
        self._save()
        return True

    def run_snapshot(self) -> None:
        mounts = self.selected_mounts()
        if len(mounts) != 1 or not self._guard():
            return
        self._start(lambda: self._snapshot(mounts[0]))

    def run_friday(self) -> None:
        mounts = self.selected_mounts()
        if len(mounts) != 2 or not self._guard():
            return
        self._start(lambda: self._friday(mounts[0], mounts[1]))

    def run_verify(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        self._start(self._verify)

    def _start(self, work) -> None:
        self._sync_buttons()
        self.worker = threading.Thread(target=self._wrap, args=(work,), daemon=True)
        self.worker.start()
        self._sync_buttons()

    def _wrap(self, work) -> None:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                work()
        except SystemExit as exc:
            self.write(str(exc) or "stopped")
        except Exception as exc:
            self.write(f"failed: {exc}")
        text = buffer.getvalue().strip()
        if text:
            self.write(text)
        self.after(0, self._sync_buttons)

    def _snapshot(self, usb: str) -> None:
        self.write(f"daily snapshot to {usb}")
        args = type("Args", (), {
            "src": None,
            "name": None,
            "out": self.out_var.get(),
            "usb": usb,
            "date": None,
            "keep": int(self.keep_var.get()),
        })()
        code = kit.cmd_snapshot(args, {"services": self.services, "keep_days": int(self.keep_var.get())})
        self.write("daily snapshot finished" if code == 0 else "daily snapshot failed")

    def _friday(self, usb_a: str, usb_b: str) -> None:
        self.write(f"friday copy to {usb_a} and {usb_b}")
        args = type("Args", (), {"out": self.out_var.get(), "usb_a": usb_a, "usb_b": usb_b})()
        code = kit.cmd_friday(args, {"services": self.services, "keep_fridays": 2})
        self.write("friday copy finished" if code == 0 else "friday copy failed")

    def _verify(self) -> None:
        root = Path(self.out_var.get()).expanduser()
        if not root.is_dir():
            self.write(f"nothing to verify at {root}")
            return
        checked, failed = kit.verify_tree(root)
        self.write(f"verified {checked} files, {failed} failed")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
