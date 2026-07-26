# How to catch and analyze a KiCad crash dump (Windows + Linux)

KiCadSpoke drives a live KiCad instance through its IPC API (`kipy`), so KiCad crashing isn't an
abstract risk — it's something you'll actually have to debug (see `techdocs/issues/` for the history
of two such bugs). This is not general theory — only what actually worked during real crash hunts on
both OSes.

## Windows

Tooling — [HuntProc](https://github.com/GrandFatherPikhto/HuntProc) on top of ProcDump + WER.

1. **Catching the dump**: `ProcDump` in first-chance monitoring mode against `kicad.exe`, or let WER
   write a full dump on crash (see `%LOCALAPPDATA%\CrashDumps` if `LocalDumps` is enabled in the
   registry). HuntProc automates exactly this — it sits and waits, grabbing the dump right after the
   crash.
2. **Symbols** — KiCad's public symbol server:
   `SRV*<local cache>*https://symbols.kicad.org/kicad-stable`.
3. **Analysis** — `cdb`/WinDbg:
   ```
   cdb -z <dump.zip\dump.dmp> -y SRV*C:\symbols*https://symbols.kicad.org/kicad-stable -c "!analyze -v; q"
   ```
   `!analyze -v` gives a verdict (`FAILURE_BUCKET_ID`), the exception address, and a symbolized stack —
   usually enough for a bug report, no manual disassembly needed.

## Linux

Based on a real hunt (KiCad 10.0.5, Flatpak `org.kicad.KiCad`, Ubuntu). Three genuine gotchas below —
all of them actually hit along the way.

### 0. Find out how KiCad is installed

```bash
flatpak list --all | grep -i kicad   # Flatpak?
dpkg -l | grep -i kicad               # or a native apt package?
```
A system can easily have both installed at once. The rest of this guide covers Flatpak; for an apt
package everything is simpler (symbols via `apt install kicad-dbgsym` or debuginfod, no sandbox
juggling).

### 1. Symbols

For Flatpak — a separate `.Debug` extension on the same branch. **Important**: it does NOT show up in
a plain `flatpak remote-ls` (Flatpak hides debug extensions from the listing by default) — install it
by exact name, don't rely on `remote-ls | grep debug` finding nothing as proof it's unavailable:

```bash
flatpak install <remote> org.kicad.KiCad.Debug//stable   # remote name is yours, see `flatpak remotes`
```

```bash
# Check what's actually on the remote for kicad (including debug extensions)
flatpak remote-ls <remote> | grep -i kicad
```

If the extension truly doesn't exist (or ~2 GB is too much to install) — fall back to `debuginfod`
(Flathub runs its own server; `gdb` fetches by build-id automatically, nothing to install):
```bash
export DEBUGINFOD_URLS="https://debuginfod.flathub.org/"
```

### 2. Catching the core itself

The first, free check (always works, no setup needed) — the kernel logs the segfault to the journal
even if no core file ever lands anywhere:
```bash
journalctl -k | grep -i segfault
# ... kernel: kicad[26482]: segfault at 0 ip ... in _eeschema.kiface[...] ...
```
Already gives you an address and module — useful as a quick "yes, it really is crashing" signal, but
without symbols.

For a full core with a backtrace: on Ubuntu, crashes go through **apport** by default
(`cat /proc/sys/kernel/core_pattern` — you'll see `.../apport ...`). Apport often fails to handle a
Flatpak-sandboxed process (the crashing process's namespace differs from what the handler sees) — you
can end up with nothing in `/var/crash/` even when `journalctl -k` honestly showed a segfault. Workaround:
point `core_pattern` at a plain absolute path (the kernel writes it using the crashing process's own
filesystem view, no external pipe handler involved — the sandbox doesn't get in the way as long as the
path is visible to the process, e.g. somewhere under `$HOME`):

```bash
mkdir -p ~/coredumps
echo "$HOME/coredumps/core.%e.%p.%t" | sudo tee /proc/sys/kernel/core_pattern
```

**Critical**: set `ulimit -c unlimited` in the SAME terminal you launch KiCad from — if you launch it
from a desktop icon/gnome-shell instead, the process inherits the systemd session's limits, not your
shell's, and the common default `ulimit -c 0` will silently forbid writing a core at all:

```bash
ulimit -c unlimited
flatpak run org.kicad.KiCad
```

Then reproduce the crash as usual and check `ls -la ~/coredumps/`.

### 3. Analysis

For a Flatpak app, run gdb INSIDE the same sandbox — binary/library paths then resolve themselves, no
need to manually hunt through `/var/lib/flatpak/app/...`:

```bash
flatpak run --command=gdb org.kicad.KiCad -batch -ex "bt full" -ex quit -c ~/coredumps/<file>
```
For a native (apt) install — plain `gdb /usr/bin/kicad <corefile>`.

### Do we need a dedicated hunter daemon (a HuntProc equivalent)?

No — catching the crash itself on Linux is synchronous and built into the kernel (`core_pattern` fires
at the moment of the crash, no service needs to be kept running, unlike Windows where WER/ProcDump *is*
a separate service). The one thing that genuinely saves time on frequent repeat hunts is a small
watcher (`inotifywait` on the core directory + auto-running `gdb -batch -ex "bt full"` on any new file),
so step 3 doesn't need to be run by hand every time. For a one-off or rare hunt, that's overengineering
— the manual steps above are enough.
