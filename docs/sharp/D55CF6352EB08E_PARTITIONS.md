# Sharp D55CF6352EB08E V1.01 partition notes

Status: REVIEW. Public-safe metadata only; no OEM payload bytes are committed.

Exact accepted package:

- `D55CF6352EB08E_V1.01.zip`
- SHA-256 `fe3e0c350b25cbbf0a0223bf613f40a21c83dc913e7591253e2a66696e508ca2`
- contains `allupgrade_6308rtbm.bin`
- inner SHA-256 `c1d01b0e76f022139278124ee158f8fe466a40310c0e53e43adc036d557924fd`

The image begins with an MStar upgrade script that carries the exact payload
offsets and write commands. Relevant recovered payloads include:

| Payload | Image offset | Stored length | Format / role |
| --- | ---: | ---: | --- |
| KL | `0x001a2000` | `0x002908e0` | U-Boot uImage kernel |
| RTPM | `0x00442000` | `0x00010000` | raw |
| RFS | `0x00452000` | `0x0064c000` | UBIFS payload |
| MSLIB | `0x00aa2000` | `0x02d14000` | SquashFS v4 / zlib |
| CONFIG | `0x037c2000` | `0x007c0000` | UBIFS payload |
| APP | `0x03f82000` | `0x03303000` | SquashFS v4 / zlib |
| customer | `0x07292000` | `0x0026c000` | UBIFS payload |
| customerbackup | `0x07502000` | `0x00193000` | UBIFS payload |
| oad | `0x076a2000` | `0x00193000` | UBIFS payload |
| certificate | `0x07842000` | `0x00193000` | UBIFS payload |

The script also declares NAND allocation:

`UBIRO 0x500000, KL 0x300000, MSLIB 0x3500000, APP 0x3c00000, RTPM 0x80000, remaining UBI`.

## Exact MSLIB verification

The exact extracted `MSLIB` payload has SHA-256
`18058425de9074b20caf017e8d31f17bffab69bce36044810447eda40f2830b7`.
Its SquashFS filesystem contains the stock loopback input driver:

- `/directfb-1.4-0/inputdrivers/libdirectfb_mstar_loopback_input.so`
- size `15476`
- SHA-256 `9bb7cd9926bbde4ff833a5c26a0475ee659973d00622d276fc396599d98083b2`

The exact DirectFB core is:

- `/libdirectfb-1.4.so.0.2.0`
- size `854168`
- SHA-256 `edf8ac9a4d5cba2e0f43cf1cd91cfc03bebf6a9bf1e948f4b8ee3cd3e3f1ce9e`
- module version `1.4.2`
- ELF ABI `MIPS32 little-endian / o32 / mips32r2`

See `SKELETON_SHARP_BRIDGE.md` for the machine-code-verified loopback ioctl ABI.

## Build boundary

The intended derivative changes `APP` only. Every untouched payload must remain
byte-identical. The APP NAND allocation is larger than the stored payload, but
the repacked SquashFS still must fit the original stored/chunk constraints and
the rebuilt MStar container must preserve order, offsets, alignment and all
integrity metadata expected by the stock updater.

No flash action is authorized by this document.
