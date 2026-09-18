# Skeleton Media extraction

The media implementation is now owned by the public repository `alanua/skeleton-media`.

## Ownership boundary

Skeleton keeps the control plane: project routing, approvals, registered executors, audit, memory routing, device/runtime authority and generic Home Edge contracts.

Skeleton Media owns media-domain implementation: Skeleton Cast, resolver/player/discovery, IPTV, media state, media clients, adaptive media remote, release monitoring and video-understanding code.

## Source separation

Skeleton Core no longer contains canonical media implementation copies.
core.skeleton_media_bridge accepts only the external skeleton_media package
and fails closed when it is unavailable. Media-specific tests, schemas,
runtime scripts and implementation code live in alanua/skeleton-media.

## Production

Repository extraction did not change the live Home Edge installation.

The standalone Skeleton Media repository intentionally disables direct production deployment. A production cutover requires a registered Skeleton Home Edge operation, rollback, audit receipt and independent verification of the real media state.
