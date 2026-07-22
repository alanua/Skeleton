# Home Edge ESP Lab

## Canonical product target

Home Edge ESP Lab must reach functional parity with the upstream MIT-licensed
`thelastoutpostworkshop/ESPConnect` project. The read-only connector is only the
first delivery stage, not the final feature boundary.

The product uses two complementary layers:

1. **Local ESPConnect operator UI** — a pinned, locally hosted upstream build or
   maintained compatible fork for immediate manual access to the complete browser
   toolset.
2. **Home Edge ESP Lab Worker** — typed native operations for Skeleton/Home Edge,
   with explicit approvals, audit receipts, private artifacts and deterministic
   automation.

The UI and Worker must share one board/session model and one capability matrix.
A feature available in upstream ESPConnect may be temporarily marked
`manual_ui_only` while its native worker operation is being implemented, but it
must not be silently omitted from the final product target.

## Full parity capability matrix

### Device and flash information

- chip family and revision;
- MAC address as private evidence;
- flash size;
- crystal frequency;
- chip capabilities and feature groups;
- USB/serial bridge and connection diagnostics.

### Partition inspection

- complete partition table;
- graphical and tabular partition map;
- offsets, sizes, types and subtypes;
- unused flash calculation;
- partition-table backup/download.

### Filesystems

Support SPIFFS, LittleFS and FATFS where the board/layout permits:

- browse and filter files;
- pagination and storage-usage gauges;
- preview text, images and supported audio;
- upload, download and delete individual files;
- drag-and-drop upload;
- stage edits before committing;
- save staged changes to flash;
- filesystem image backup;
- restore filesystem image;
- format filesystem after explicit approval;
- reject oversized transfers before writing.

### Application and OTA inspection

- enumerate app/OTA slots;
- identify active slot;
- firmware/build metadata;
- image sizes and identifying fields;
- staged/next-slot visibility.

### Flash and maintenance

- flash one or more `.bin` files at explicit offsets;
- common offset presets;
- full-chip erase after explicit destructive approval;
- partition backup;
- used-flash backup;
- arbitrary bounded-region backup;
- full flash backup and restore;
- MD5 integrity checks for explicit offset/length;
- register read;
- register write after high-risk approval;
- cancellation and progress reporting for long operations.

### Serial console

- continuous UART stream;
- selectable baud rate;
- clear console;
- send text/bytes and Ctrl+C;
- reset board;
- bounded capture/export;
- session history.

### NVS Inspector

- detect NVS v1/v2;
- list namespaces and keys;
- decode integers, strings and blobs;
- heuristic float/double decoding;
- page state, sequence and CRC inspection;
- entry usage and occupancy visualization;
- preserve upstream status as experimental/read-only until independently proven.

### Logs and receipts

- chronological connect, inspect, flash, backup, filesystem and warning log;
- private detailed artifacts;
- public-safe Home Edge/Skeleton receipts;
- reproducible operation IDs and integrity hashes.

### Supported boards

Target current upstream support for ESP32, ESP32-C3, ESP32-C5, ESP32-C6,
ESP32-H2, ESP32-P4, ESP32-S2, ESP32-S3 and limited ESP8266. ESP8266 limitations
must be represented explicitly rather than reported as generic failures.

## Delivery stages

### Stage 1 — practical read-only connector

- real Windows COM discovery;
- real chip identification;
- real `read-mac` and `flash-id`;
- receive-only serial monitoring;
- authenticated LAN/Tailscale dispatch;
- private observation and public receipt.

### Stage 2 — non-destructive deep inspection

- partition map;
- OTA/app metadata;
- NVS inspector;
- MD5 checks;
- read-only filesystem browsing and previews;
- partition/region/full-flash backups.

### Stage 3 — controlled filesystem and firmware writes

- file upload/delete/save;
- filesystem restore/format;
- firmware flashing at explicit offsets;
- board reset and interactive serial transmission;
- mandatory backup-before-write and post-write verification.

### Stage 4 — advanced maintenance

- full restore;
- erase operations;
- register writes;
- test firmware workflows;
- stronger operator approval and recovery receipts.

## Runtime topology

The Windows workstation is the physical ESP test bench. Home Edge is the
controller and audit point. Skeleton sends only typed jobs through Home Edge.
At home, the preferred route is authenticated local LAN; remote administration
uses Tailscale. There is no generic remote shell or unrestricted process/device
access.

## Safety model

Safety gates do not remove product capabilities. They determine which operations
can run automatically and which require explicit approval. Read-only inspection
may be automated. Backup, write, format, erase, restore and register-write
operations require progressively stronger approval, preflight checks and recovery
artifacts.

## Upstream tracking

Track a pinned upstream ESPConnect release and record:

- upstream version/commit;
- license attribution;
- capability parity state for every feature;
- locally adapted components;
- deviations and test evidence.

The upstream browser application remains local: no backend, account or telemetry.
Firmware, backups, logs and diagnostics stay on the operator workstation/Home Edge
unless an explicit export job is approved.
