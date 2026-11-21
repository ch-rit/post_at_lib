"""Replays responses captured from a real account (sanitized) through the client."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from post_at import PostAtClient, Session
from post_at.auth import SCOPE_APP, SCOPE_USER_CONTEXT, AccessToken
from post_at.models import DeliveryType, PushSetting, RedirectionState, RedirectionType, Shipment

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def logged_in_client(handler: Callable[[httpx.Request], httpx.Response]) -> PostAtClient:
    valid = time.time() + 3600
    session = Session(
        "refresh",
        {SCOPE_APP: AccessToken("app-token", valid), SCOPE_USER_CONTEXT: AccessToken("user-token", valid)},
    )
    return PostAtClient(session=session, http=httpx.Client(transport=httpx.MockTransport(handler)))


def graphql(field: str, data: dict) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json={"statusCode": 200, "data": {field: data}, "errors": None})


def rest(data: dict) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=data)


def test_shipment_list() -> None:
    post = logged_in_client(graphql("pagedSendungen", fixture("shipments")))
    result = post.shipments()
    assert (result.total, result.outgoing_count, result.incoming_count) == (3, 1, 2)
    delivered = result.shipments[2]
    assert delivered.status == "ZU"
    assert delivered.latest_event.state_key == "delivered"
    assert delivered.events[0].timestamp < delivered.events[-1].timestamp
    assert result.shipments[1].delivery_type is DeliveryType.DROPOFF
    assert result.shipments[0].parcel_svg.startswith("<svg")


def test_shipment_details() -> None:
    post = logged_in_client(graphql("einzelsendung", fixture("shipment")))
    shipment = post.shipment("1000000000000000000001")
    assert shipment.dimensions.length == 16
    assert shipment.customs.user_document_needed is False
    assert shipment.redirections[0].parcel_locker is None
    assert shipment.latest_event.timestamp.tzinfo is not None


def test_shipment_image_url() -> None:
    shipment = Shipment.model_validate(
        {"activeIdentityCode": "1", "bild": "https://secure.post.at/storage/FMS|0001|aaaa-bbbb|PLC"}
    )
    assert shipment.image_url == "https://secure.post.at/storage/FMS%7C0001%7Caaaa-bbbb%7CPLC"
    assert Shipment.model_validate({"activeIdentityCode": "1", "bild": ""}).image_url is None


def test_public_tracking_and_unknown_number() -> None:
    post = PostAtClient(http=httpx.Client(transport=httpx.MockTransport(graphql("einzelsendung", fixture("track")))))
    assert post.track("1000000000000000000001").latest_event.state_key == "delivered"
    post = PostAtClient(http=httpx.Client(transport=httpx.MockTransport(graphql("einzelsendung", None))))
    assert post.track("1000000000000000000001") is None


def test_possible_redirections() -> None:
    post = logged_in_client(graphql("einzelsendung", fixture("possible_redirections")))
    options = {o.type: o for o in post.possible_redirections("1000000000000000000001").options}
    assert options[RedirectionType.PLACE].current
    assert options[RedirectionType.NEIGHBOR].state is RedirectionState.DISABLED
    assert options[RedirectionType.NEIGHBOR].unavailability_reasons == ["DROP_OFF"]


def test_unknown_enum_value_is_kept_as_string() -> None:
    data = fixture("possible_redirections")
    data["redirectionInformation"]["redirectionOptions"][0]["type"] = "DRONE"
    post = logged_in_client(graphql("einzelsendung", data))
    assert post.possible_redirections("1").options[0].type == "DRONE"


def test_update_user_sends_back_what_was_received() -> None:
    raw = fixture("user")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            sent.append(json.loads(request.content))
        return httpx.Response(200, json=raw)

    post = logged_in_client(handler)
    user = post.user()
    assert user.birthdate == datetime(1990, 1, 1)
    assert user.addresses[0].post_address_code == "101269641"
    post.update_user(user)
    body = sent[0]
    # .NET sends 7 fractional digits; Python keeps 6. Same instant to the microsecond.
    assert body.pop("loginDate") == raw["loginDate"][:-1]
    # The app's Moshi serializer drops nulls; everything else, including fields
    # this library does not model (``roommates``), must survive the round trip.
    assert body == without_nulls({k: v for k, v in raw.items() if k != "loginDate"})


def without_nulls(value: object) -> object:
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [without_nulls(v) for v in value]
    return value


def test_orders() -> None:
    post = logged_in_client(rest(fixture("order_history")))
    history = post.order_history(limit=5)
    assert history.total_order_count == 1
    assert history.orders[0].total_price.total_price == Decimal("16.54")

    post = logged_in_client(rest(fixture("order_details")))
    order = post.order_details("AP-26-0000001")
    item = order.order_items[0]
    assert item.expiry_date == datetime(2026, 8, 15, 12, 33, 9, 617451, tzinfo=timezone.utc)
    assert item.sender.post_address_code == "101269641"  # sent as a number by this endpoint
    assert order.parcel_stamp_contract_ids == ["PMC-TEST1"]


def test_settings() -> None:
    post = logged_in_client(rest(fixture("marketing_preferences")))
    sections = post.marketing_preferences()
    assert [s.title for s in sections] == ["Marketingzustimmungen", "Online Werbung", "Newsletter"]

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            sent.append(json.loads(request.content))
        return httpx.Response(200, json=fixture("push_settings"))

    post = logged_in_client(handler)
    settings = post.push_settings()
    settings[0].enabled = False
    post.update_push_settings(settings)
    assert isinstance(settings[0], PushSetting)
    assert sent[0]["pushSettings"][0] == {**fixture("push_settings")["pushSettings"][0], "enabled": False}


def test_profile_side_endpoints() -> None:
    post = logged_in_client(rest(fixture("receiving_options")))
    (entry,) = post.receiving_options()
    assert entry.address.country == "AT"
    assert entry.receiving_options == []

    post = logged_in_client(rest(fixture("last_parcel_stamp_shipper_address")))
    assert post.last_parcel_stamp_shipper_address().post_address_code == "101269641"


def test_tokens_per_endpoint_and_refresh_on_401(tmp_path: Path) -> None:
    seen: list[tuple[str, str | None, str | None]] = []
    rejected_once = False
    refreshes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal rejected_once, refreshes
        if request.url.path.endswith("/token"):
            refreshes += 1
            scope = parse_qs(request.content.decode())["scope"][0].split()[0]
            return httpx.Response(
                200,
                json={"access_token": f"{scope.rsplit('/', 1)[-1]}#{refreshes}", "expires_in": 3600, "refresh_token": f"RT{refreshes}"},
            )
        seen.append((request.url.path, request.headers.get("authorization"), request.headers.get("x-authorization")))
        if request.url.path.endswith("auth/user") and not rejected_once:
            rejected_once = True
            return httpx.Response(401, json={"statusCode": 401})
        if request.url.path.endswith("auth/user"):
            return httpx.Response(200, json=fixture("user"))
        return httpx.Response(200, json={"data": {"pagedSendungen": fixture("shipments")}, "errors": None})

    path = tmp_path / "session.json"
    Session("RT0", {SCOPE_APP: AccessToken("expired", time.time() - 1)}).save(path)
    post = PostAtClient(session_file=path, http=httpx.Client(transport=httpx.MockTransport(handler)))
    post.shipments()
    post.user()

    assert seen == [
        ("/bff/auth/shipments", "Bearer PostApp2.AllAccess#1", None),
        ("/bff/auth/user", "Bearer PostApp2.AllAccess#1", "Bearer KIAM.Service.UserContext.All#2"),
        ("/bff/auth/user", "Bearer PostApp2.AllAccess#3", "Bearer KIAM.Service.UserContext.All#4"),
    ]
    # Refresh tokens are single-use: the latest one must be on disk.
    assert Session.load(path).refresh_token == "RT4"
    post.logout()
    assert not path.exists()


def test_not_logged_in() -> None:
    post = PostAtClient(http=httpx.Client(transport=httpx.MockTransport(rest({}))))
    with pytest.raises(Exception, match="not logged in"):
        post.user()
