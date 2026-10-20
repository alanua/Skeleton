# Sharp LC-55CFE6352E Skeleton bridge

Status: REVIEW. Offline firmware work only. No TV/runtime mutation is authorized by this document.

## Exact target

Only `D55CF6352EB08E_V1.01.zip` is an accepted base. The verifier in
`tools/sharp/verify_d55_v101.py` must pass both the outer ZIP and inner
`allupgrade_6308rtbm.bin` SHA-256 gates before extraction or build work.
A same-marketing-model image is not interchangeable: in particular the known
`C55CF6352EB480` / `LC550DUY-SHA1` V1.02 image is rejected.

The exact operator-supplied D55 package has now been recovered privately and
verified against both canonical hashes. Its `MSLIB` payload starts at image
offset `0x00aa2000`, has allocation `0x02d14000`, and SHA-256
`18058425de9074b20caf017e8d31f17bffab69bce36044810447eda40f2830b7`.
No OEM bytes are committed to this repository.

## Control architecture

Normal stock LAN exposes DIAL and AwoX/DLNA but no verified generic Power,
Input/Source, Menu or D-pad service. The firmware already routes remote keys
through MStar DirectFB/APM, so Skeleton should inject into that stock path rather
than emulate hardware or enable dormant debug services.

Preferred path:

`HTTP allowlist -> mstarloopback -> DFB_DEV_IOC_SEND_LOOPBACK_EVENT -> dfb_input_dispatch -> stock APM/tvmain`

This path is now verified against the exact D55 V1.01 `MSLIB`, not only against
related MStar source. The exact filesystem contains:

- `/directfb-1.4-0/inputdrivers/libdirectfb_mstar_loopback_input.so`
  - size `15476`
  - SHA-256 `9bb7cd9926bbde4ff833a5c26a0475ee659973d00622d276fc396599d98083b2`
  - virtual input device string `mstarloopback`
- `/libdirectfb-1.4.so.0.2.0`
  - size `854168`
  - SHA-256 `edf8ac9a4d5cba2e0f43cf1cd91cfc03bebf6a9bf1e948f4b8ee3cd3e3f1ce9e`
- DirectFB module version `1.4.2`
- ELF ABI `MIPS32 little-endian, o32, mips32r2`

### Exact D55 loopback ABI

Machine-code inspection of the exact stripped D55 loopback driver confirms:

- `DFB_DEV_IOC_SEND_LOOPBACK_EVENT` is compared against **`0x80044405`** on
  this MIPS32 ABI. The common x86/asm-generic expectation `0x40044405` is wrong
  for this firmware and must not be hardcoded.
- the driver zero-initializes exactly **72 bytes** for the local
  `DFBInputEvent` and copies exactly 72 bytes from `InputDeviceIoctlData.param`
  before calling the stock input path;
- therefore the active request prefix consumed by the driver is
  `4-byte request + 72-byte DFBInputEvent = 76 bytes`;
- the exact `libdirectfb` core allocates and copies **84 bytes** for the full
  `InputDeviceIoctlData`, matching `int request` plus an 80-byte parameter area;
- key press and key release remain a mandatory pair.

The exact 32-bit event layout consumed by this driver is:

| Offset | Field |
| ---: | --- |
| `0x00` | `clazz` |
| `0x04` | `type` |
| `0x08` | `device_id` |
| `0x0c` | `flags` |
| `0x10` | `timestamp` (two 32-bit values) |
| `0x18` | `key_code` |
| `0x1c` | `key_id` |
| `0x20` | `key_symbol` |
| `0x24` | `modifiers` |
| `0x28` | `locks` |
| `0x2c` | `button` |
| `0x30` | `buttons` |
| `0x34` | `axis` |
| `0x38` | `axisabs` |
| `0x3c` | `axisrel` |
| `0x40` | `min` |
| `0x44` | `max` |

Total: **72 bytes**. There is no trailing `ex_device_id` in the event object
copied by this exact binary.

For key injection, use fixed mappings only, zero-initialize the event, set
`type=1` for press or `type=2` for release, and set flags to `0x38`
(`KEYCODE | KEYID | KEYSYMBOL`). The bridge should compile against the exact
firmware-compatible DirectFB ABI rather than manually serializing a guessed
foreign-platform structure.

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

`BigBang_Socket` is also not a key path. Available firmware evidence identifies
it as application/process-launch IPC; key dispatch remains a separate DirectFB
path.

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

1. exact OEM ZIP/BIN hashes pass — **verified**;
2. exact partition/update script is recovered from that BIN — **verified for the
   current partition offsets/allocation map; packaging integrity metadata still
   needs full build-path validation**;
3. exact `mstarloopback`/DirectFB ABI is confirmed — **verified**;
4. exact APP SquashFS parameters and allocation are known;
5. source/standby ABI decisions are explicit;
6. unchanged payload hashes remain identical after repack;
7. all container CRC/checksum/signature metadata validates offline.

Flashing is a separate protected action and is not part of the offline build.
