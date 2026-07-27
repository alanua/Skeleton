# Kyiv apartment media endpoint

Status: **operator-confirmed topology; exact device identities pending live verification**  
Updated: 2026-07-27

## Confirmed components

- An existing Fujitsu PC runs Home Assistant in the Kyiv apartment.
- A Raspberry Pi 4 is connected to an older Samsung 32-inch television and may serve as the local direct-play media endpoint.
- An ESP-based infrared transmitter controls the television instead of the original remote.
- The apartment has gigabit local networking and fibre internet.

## Intended split

- Kyiv Fujitsu / Home Assistant: local automation, orchestration bridge, resolver work when needed, state and recovery.
- Raspberry Pi 4: MPV/direct streams/IPTV, audio, input receiver and lightweight games at 1080p.
- ESP infrared controller: television power, volume, mute and source commands.
- Samsung television: display and speakers unless a separate audio endpoint is later registered.

## Power-state verification

The operator recalls a loop or sensor on the television power conductor that may have been used to detect whether the television is actually on. The exact implementation is not yet confirmed. It may be a current-sensing loop, current transformer, relay feedback or another sensor.

Until inspected, the capability is recorded only as `power_state_sensor_pending_verification`. It must not be treated as a verified postcondition or used by automatic recovery. Required evidence:

1. identify the ESP/board and the sensor hardware;
2. trace the conductor and confirm electrical isolation and safe installation;
3. observe Home Assistant entities and their state changes;
4. compare the sensor state against the physical television state through several on/off cycles;
5. register exact operations only after independent verification.

No remote scan, command or operation is defined until the Kyiv identities, network access and current Home Assistant configuration are inspected.
