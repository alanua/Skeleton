# Sharp LC-55CFE6352E Skeleton bridge

Status: REVIEW. Offline firmware work only. No TV/runtime mutation is authorized by this document.

## Exact target

Only `D55CF6352EB08E_V1.01.zip` is an accepted base. The verifier in
`tools/sharp/verify_d55_v101.py` must pass both the outer ZIP and inner
`allupgrade_6308rtbm.bin` SHA-256 gates before extraction or build work.
A same-marketing-model image is not interchangeable: in particular the known
`C55CF6352EB480` / `LC550DUY-SHA1` V1.02 image is rejected.

## Control architecture

Normal stock LAN exposes DIAL and AwoX/DLNA but no verified generic Power,
Input/Source, Menu or D-pad service. The firmware already routes remote keys
through MStar DirectFB/APM, so Skeleton should inject into that stock path rather
than emulate hardware or enable dormant debug services.

Preferred path:

`HTTP allowlist -> mstarloopback -> DFB_DEV_IOC_SEND_LOOPBACK_EVENT -> dfb_input_dispatch -> stock APM/tvmain`

Before enabling this path in a derived image, the exact OEM `MSLIB` must be
checked for the `mstarloopback` input driver (or an ABI-identical equivalent)
and the exact DirectFB ioctl ABI. If either check fails, the build must stop;
MSLIB must not be silently replaced.

Recovered Sharp key symbols:

| Action | Symbol |
| --- | ---: |
| Left | `0xf000` |
| Right | `0xf001` |
| Up | `0xf002` |
| Down | `0xf003` |
| Power | `0xf00f` |
| Menu | `0xf012` |
| Back/Exit | `0xf062` |
| Input Source | `0xf068` |
| OK/Select | `0x000d` |

Every injected key is a fixed bridge mapping and must be emitted as a press and
release pair. The HTTP caller must never be allowed to supply a raw key symbol.

`libdirectfb_mstar_networkir.so` and `/dev/shm/socket_server` are deliberately
not used as the generic bridge. Matching MStar source shows that normal builds
recognize only hardcoded DIAL test strings, while the generic parser is compiled
out. Unknown datagrams are therefore not a safe arbitrary-key transport.

## HTTP surface

The intended daemon is `skeleton-sharp-bridge`, added to `APP` only after exact
ABI validation. Its public surface is intentionally small:

- `GET /status`
- `POST /power/standby`
- `POST /source/hdmi1`
- `POST /source/hdmi2`
- `POST /source/hdmi3` only if the exact source table confirms HDMI3
- `POST /key/up`
- `POST /key/down`
- `POST /key/left`
- `POST /key/right`
- `POST /key/ok`
- `POST /key/back`
- `POST /key/menu`

No shell endpoint, raw key endpoint, arbitrary URL, filesystem endpoint,
firmware endpoint or debug/service endpoint is permitted. Request size, method,
path and source-network policy must fail closed.

## Direct source switching

Direct HDMI switching is preferable to opening the Source menu. Related MStar
Supernova code exposes `MSrv_Control::SetInputSource(...)` and
`GetCurrentInputSource()`, and the exact Sharp image contains native source
handlers plus factory-local `Main_CVTE_SetTVSource/GetTVSource` evidence.
The derived build must resolve the exact Sharp non-UART ABI before exposing
`/source/*`. Reference enum values from other MStar generations are not enough.
If the ABI cannot be proven, source endpoints stay disabled.

Factory UART helpers are evidence only and are not a normal control transport.

## Power

Power-on is not provided by the in-TV daemon because that daemon may not exist
in standby/off. Wake-on-LAN remains the preferred ON candidate and requires a
separate physical verification.

For standby, prefer an exact high-level stock power-down request if its ABI is
recoverable. Otherwise a fixed stock POWER loopback event may be used while the
TV is awake. Command acceptance must not be reported as physical screen-off
without an independent postcondition.

## Packaging boundary

The target is an APP-only derivative. `KL`, `RFS`, `MSLIB`, `CONFIG`,
`customer` and every other untouched payload remain byte-identical. The build
must preserve the exact MStar container layout, partition alignment, allocation,
compression parameters and integrity metadata. It must fail if the repacked APP
exceeds the original allocation.

Secure verification and boot verification must never be bypassed. `telnetd`,
`gdbserver`, `targetbot_server`, BQwidget HTTP and other dormant debug paths must
remain disabled.

OEM and derived firmware blobs remain private. The public repository contains
only hashes, manifests, source/tooling and a reproducible patch description.

## Build gate

A candidate `D55CF6352EB08E_V1.01-Skeleton-01.bin` is buildable only after:

1. exact OEM ZIP/BIN hashes pass;
2. exact partition/update script is recovered from that BIN;
3. exact `mstarloopback`/DirectFB ABI is confirmed;
4. exact APP SquashFS parameters and allocation are known;
5. source/standby ABI decisions are explicit;
6. unchanged payload hashes remain identical after repack;
7. all container CRC/checksum/signature metadata validates offline.

Flashing is a separate protected action and is not part of the offline build.
