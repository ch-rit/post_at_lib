# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-21

### Added

- `Shipment.image_url`: the photo of the parcel from the sorting conveyor, with the
  `|` in the raw `image` link percent-encoded so the storage server accepts it.

### Removed

- The author e-mail address from the package metadata.

## [0.1.0] - 2026-11-19

Initial release.

### Added

- `PostAtClient`: synchronous `httpx` client for the backend-for-frontend API
  (`https://api.post.at/bff/`) used by the Österreichische Post Android app 2.16.0.
- Login via Azure AD B2C (`login.post.at`) without a browser (`login()`), and a
  browser-based PKCE flow (`begin_browser_login()` / `complete_browser_login()`)
  for accounts that need MFA or have to accept new terms.
- Session persistence in a JSON file that keeps the rotating refresh token;
  access tokens for every scope are minted and refreshed on demand, and a 401
  triggers one refresh and a retry.
- Public endpoints without login: parcel tracking, branch/parcel locker search,
  service filters, places, app config, strings, master data, address validation.
- Shipments (GraphQL): list sent and received parcels, single shipment, add,
  remove and rename shipments.
- Redirections: query possible redirections; redirect to a neighbour, parcel
  locker, post office or place; reset redirections.
- User profile, addresses, receiving options, marketing preferences, push
  settings and push devices/notifications.
- Order history, order details and attachments, collection service, parcel
  stamps, shop/checkout and identification (Jumio, ID Austria) endpoints.
- Pydantic models (`post_at.models`) with English snake_case fields for all
  responses verified against a real account; unknown fields are preserved and
  unknown enum values fall back to plain strings.
- Error hierarchy: `PostAtError`, `ApiError`, `GraphQLError`,
  `AuthenticationError`, `NotAuthenticatedError`, `InteractionRequiredError`.
- `scripts/capture.py` to re-capture read-only API responses (redacted by
  default) after app or API changes.
- PEP 561 `py.typed` marker.

[Unreleased]: https://github.com/ch-rit/post_at_lib/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ch-rit/post_at_lib/releases/tag/v0.2.0
[0.1.0]: https://github.com/ch-rit/post_at_lib/releases/tag/v0.1.0
