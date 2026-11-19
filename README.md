# post-at

[![PyPI](https://img.shields.io/pypi/v/post-at)](https://pypi.org/project/post-at/)
[![Python versions](https://img.shields.io/pypi/pyversions/post-at)](https://pypi.org/project/post-at/)
[![License: MIT](https://img.shields.io/github/license/ch-rit/post_at_lib)](https://github.com/ch-rit/post_at_lib/blob/main/LICENSE)
[![Tests](https://github.com/ch-rit/post_at_lib/actions/workflows/tests.yaml/badge.svg)](https://github.com/ch-rit/post_at_lib/actions/workflows/tests.yaml)

Unofficial Python client for the API behind the Österreichische Post app
(`at.post.app` 2.16.0). Endpoints, headers and API versions were taken from the
decompiled app; the library talks to `https://api.post.at/bff/` directly. No
request signing, app attestation or certificate pinning is involved.

> [!WARNING]
> **Unofficial project, use at your own risk.**
>
> - This library is **not affiliated with, endorsed by or supported by
>   Österreichische Post AG**. "Post" and "post.at" are their trademarks.
> - It uses an undocumented, private API that can change or break at any time.
> - The code is **fully vibecoded**: it was written by an AI and only lightly
>   reviewed by a human.
> - It comes with **no guarantee and no warranty** of any kind (see the
>   [MIT license](https://github.com/ch-rit/post_at_lib/blob/main/LICENSE)).
>   You are responsible for what it does with your account.

Looking for Home Assistant? The companion integration
[ha-post-at](https://github.com/ch-rit/ha-post-at) is built on this library.

## Features

- Track any parcel by tracking number, no login needed.
- Log in with e-mail and password (no browser), or through a browser when the
  account needs MFA; the rotating refresh token is persisted in a session file.
- List all sent and received parcels of your account, add, remove and rename them.
- Query and set delivery redirections (neighbour, parcel locker, post office, place).
- Search branches and parcel lockers in a map area.
- Read and update the user profile, addresses, receiving options, push and
  marketing settings; read order history, collection services and parcel stamps.
- Typed pydantic models with English field names, `py.typed` included.

## Installation

Requires Python 3.11+.

```sh
pip install post-at
```

## Usage

```python
from post_at import PostAtClient

with PostAtClient(session_file=".post_session.json") as post:
    # public, no login
    parcel = post.track("1012345678901234567890")
    print(parcel.latest_event.state, parcel.estimated_delivery_date_text)
    post.branches(north_west=(48.25, 16.30), south_east=(48.18, 16.42), branch_types=["FILIALE"])

    # login once; the session file keeps the rotating refresh token
    if not post.logged_in:
        post.login("me@example.com", "secret")

    for shipment in post.shipments().shipments:  # all sent and received parcels
        print(shipment.tracking_number, shipment.sender, shipment.latest_event.state_key)

    options = post.possible_redirections("1012345678901234567890").options
    if any(o.type == "PLACE" and o.state == "ENABLED" for o in options):
        post.redirect_to_place("1012345678901234567890", post.places()[0])

    user = post.user()
    user.mobile.number = "1234567"
    post.update_user(user)
```

The session file contains a refresh token for your account. Keep it private.

### Login

`login()` replays the Azure AD B2C sign-in page (`login.post.at`, policy
`B2C_1A_signup_signin_mobileapp`) without a browser. If B2C asks for anything
other than e-mail and password (MFA, new terms), it raises
`InteractionRequiredError`; use the browser flow instead:

```python
pending = post.begin_browser_login()
print(pending.url)  # open it and sign in
# The browser cannot open the final msal228c4360-…://at.post.app/auth?code=… URL.
# Copy it from the address bar, the error page or the devtools network tab.
post.complete_browser_login(pending, input("redirect URL: "))
```

### Tokens

The BFF needs up to two bearer tokens per request, minted from the same refresh token:

| Endpoint group | `Authorization` | `X-Authorization` |
|---|---|---|
| shipments (GraphQL), orders, push, marketing | `PostApp2.AllAccess` | – |
| user profile, addresses, receiving options | `PostApp2.AllAccess` | `KIAM.Service.UserContext.All` |
| identification (Jumio, ID Austria) | `PostApp2.AllAccess` | `Kiam.Identifications.IdAustria.Create` |

The client fetches and refreshes tokens on demand. A 401 forces one refresh and a retry.

### Errors

All exceptions derive from `PostAtError`.

- `ApiError`: non-2xx answer, with `status_code`, decoded `body` and the server's `operation_id`.
- `GraphQLError`: GraphQL `errors` array.
- `AuthenticationError` / `NotAuthenticatedError` / `InteractionRequiredError`: login and token problems.

### Models

Endpoints whose responses were verified against a real account return pydantic
models (`post_at.models`): shipments, tracking events, redirection options,
branches, user, addresses, receiving options, orders, push and marketing
settings. Field names are English snake_case. `model_dump()` gives back the
original JSON keys, and fields the server adds later stay in `model_extra`. Enum
fields fall back to the plain string for values they don't know.

The shop/checkout, push-device and identification endpoints return decoded JSON
as-is. Their responses haven't been seen yet.

The GraphQL documents the app sends are in
[`src/post_at/graphql/`](https://github.com/ch-rit/post_at_lib/tree/main/src/post_at/graphql).

### Re-capturing after app/API changes

`scripts/capture.py` logs in (or reuses `.post_session.json`), calls every
read-only endpoint and writes the responses to `captures/`. Personal data is
masked unless you pass `--no-redact`. `tests/fixtures/` holds sanitized copies
of such captures, and the tests replay them.

```sh
.venv/bin/python scripts/capture.py              # e-mail/password prompt
.venv/bin/python scripts/capture.py --browser    # sign in in a browser, paste the redirect URL
```

## Development

```sh
git clone https://github.com/ch-rit/post_at_lib.git
cd post_at_lib
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

The tests replay the fixtures in `tests/fixtures/` and make no network requests.

## Releasing

1. Bump `version` in `pyproject.toml` and move the `Unreleased` entries in
   [`CHANGELOG.md`](https://github.com/ch-rit/post_at_lib/blob/main/CHANGELOG.md)
   to a new version section.
2. Commit, then tag and push:

   ```sh
   git tag v0.1.0
   git push origin main v0.1.0
   ```

3. The `Release` workflow checks that the tag matches the version in
   `pyproject.toml`, builds sdist and wheel, publishes them to PyPI via trusted
   publishing and creates a GitHub release with the files attached.

One-time setup:

- On [pypi.org](https://pypi.org/manage/account/publishing/) add a *pending
  trusted publisher* for project `post-at`: owner `ch-rit`, repository
  `post_at_lib`, workflow `release.yaml`, environment `pypi`.
- In the GitHub repository settings, create an environment named `pypi`
  (optionally with required reviewers or a `v*` tag rule).

## License

[MIT](https://github.com/ch-rit/post_at_lib/blob/main/LICENSE) © 2026 Christoph Ritzer
