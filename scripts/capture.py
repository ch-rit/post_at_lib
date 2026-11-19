"""Log in and dump the raw responses of every read-only endpoint to ``captures/``.

    .venv/bin/python scripts/capture.py              # e-mail/password prompt
    .venv/bin/python scripts/capture.py --browser    # sign in in a browser, paste the redirect URL

Personal values (names, addresses, e-mail, phone, ...) are masked unless
``--no-redact`` is given; structure, enums and status values are kept. Nothing is
modified on the account.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from pydantic_core import to_jsonable_python

from post_at import ApiError, GraphQLError, PostAtClient

ROOT = Path(__file__).resolve().parent.parent
PII_KEY = re.compile(
    r"name|street|strasse|email|mail|phone|mobile|number|birth|address(text)?$|recipient|consignee|"
    r"sender|username|ssoid|crmid|customer|contact|person|city|postalcode|postcode|doornumber|housenumber|"
    r"iban|token|identcode|identitycode|sendungsnummer|bezeichnung|bild|freetext|text$",
    re.I,
)
# Keys that match PII_KEY but carry no personal data and are needed to model the API.
KEEP_KEY = re.compile(r"^(statusText|dueDateText|stateInfo|trackingState\w*|typeName|__typename|productName)$")


def redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str) and value and PII_KEY.search(key) and not KEEP_KEY.match(key):
        return f"<{key}:{len(value)}>"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and PII_KEY.search(key) and not KEEP_KEY.match(key):
        return 0
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="log in through a browser instead of e-mail/password")
    parser.add_argument("--no-redact", action="store_true", help="keep personal data in the dumps")
    parser.add_argument("--out", type=Path, default=ROOT / "captures")
    args = parser.parse_args()
    args.out.mkdir(exist_ok=True)

    with PostAtClient(session_file=ROOT / ".post_session.json") as post:
        if not post.logged_in:
            if args.browser:
                pending = post.begin_browser_login()
                print("1. Open devtools (F12) > Network, enable 'Preserve log'.")
                print("2. Open this URL and sign in:\n\n" + pending.url + "\n")
                print("3. Copy the msal228c4360-…://at.post.app/auth?code=… URL from the last")
                print("   request's 'Location' response header (or the address bar).")
                post.complete_browser_login(pending, input("redirect URL: ").strip())
            else:
                post.login(input("e-mail: ").strip(), getpass.getpass("password: "))
        print("logged in; token claims:", sorted(post.session.claims))

        def dump(name: str, call: Callable[[], Any]) -> Any:
            entry: dict[str, Any]
            result = None
            try:
                result = call()
                # Models dump back to wire names; fields unknown to the models are kept.
                entry = {"ok": True, "data": to_jsonable_python(result, by_alias=True)}
            except ApiError as e:
                entry = {"ok": False, "status": e.status_code, "operation_id": e.operation_id, "body": e.body}
            except GraphQLError as e:
                entry = {"ok": False, "graphql_errors": e.errors, "data": e.data}
            except Exception:  # keep going; the traceback is what we need to fix the client
                entry = {"ok": False, "exception": traceback.format_exc()}
            if not args.no_redact:
                entry = redact(entry)
            (args.out / f"{name}.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False))
            print(f"{'ok ' if entry['ok'] else 'ERR'} {name}")
            return result

        shipments = dump("shipments", post.shipments)
        for i, item in enumerate(shipments.shipments[:3] if shipments else []):
            number = item.tracking_number
            dump(f"shipment_{i}", lambda: post.shipment(number))
            dump(f"possible_redirections_{i}", lambda: post.possible_redirections(number))
            dump(f"track_{i}", lambda: post.track(number))

        dump("user", post.user)
        dump("receiving_options", post.receiving_options)
        dump("marketing_preferences", post.marketing_preferences)
        dump("push_settings", post.push_settings)
        dump("active_collection_services", post.active_collection_services)
        dump("last_parcel_stamp_shipper_address", post.last_parcel_stamp_shipper_address)
        orders = dump("order_history", lambda: post.order_history(limit=5, offset=0))
        for i, order in enumerate(orders.orders[:2] if orders else []):
            order_id = order.order_id
            dump(f"order_details_{i}", lambda: post.order_details(order_id))

    print(f"\nwrote {args.out}/  (session kept in .post_session.json; delete it to log out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
