"""Client for the Österreichische Post app backend (``https://api.post.at/bff/``).

Endpoints, API versions and headers mirror the Android app 2.16.0. Endpoints whose
responses were verified against a real account return models from
:mod:`post_at.models`; the shop/checkout and identification endpoints return the
decoded JSON as-is. PDF endpoints return ``bytes``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from .auth import (
    SCOPE_APP,
    SCOPE_ID_AUSTRIA,
    SCOPE_USER_CONTEXT,
    Authenticator,
    PendingLogin,
    Session,
)
from .errors import ApiError, GraphQLError
from .models import (
    Address,
    AddressValidation,
    Branch,
    BranchType,
    CollectionTime,
    EcoAddress,
    MarketingSection,
    Order,
    OrderHistory,
    Place,
    PushSetting,
    ReceivingOptionAddress,
    ServiceFilter,
    Shipment,
    ShipmentList,
    ShipmentRedirections,
    User,
)

BASE_URL = "https://api.post.at/bff/"

# Which extra token an endpoint needs on top of the app token:
#   "app"       Authorization: Bearer <PostApp2.AllAccess>
#   "user"      + X-Authorization: Bearer <KIAM.Service.UserContext.All>
#   "idaustria" + X-Authorization: Bearer <Kiam.Identifications.IdAustria.Create>
_Auth = Literal["app", "user", "idaustria"] | None
_X_AUTH_SCOPE = {"user": SCOPE_USER_CONTEXT, "idaustria": SCOPE_ID_AUSTRIA}


@cache
def _document(name: str) -> str:
    return resources.files(__package__).joinpath(f"graphql/{name}.graphql").read_text().strip()


@cache
def _adapter(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def _parse(tp: Any, data: Any) -> Any:
    return None if data is None else _adapter(tp).validate_python(data)


def _omit_none(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


class PostAtClient:
    """Synchronous client. Use as a context manager or call :meth:`close`.

    ``session_file`` persists the login: it is loaded if present and rewritten
    whenever tokens rotate (B2C refresh tokens are single-use), and deleted on
    :meth:`logout`. For other storage, pass ``on_session_change``; it receives
    the new :class:`Session` (or ``None`` after logout) on the calling thread.
    """

    def __init__(
        self,
        *,
        session: Session | None = None,
        session_file: str | Path | None = None,
        on_session_change: Callable[[Session | None], None] | None = None,
        language: str = "de-AT",
        theme: Literal["light", "dark"] = "light",
        timeout: float = 45.0,
        http: httpx.Client | None = None,
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._language = language
        self._theme = theme
        self._session_file = Path(session_file) if session_file is not None else None
        self._on_session_change = on_session_change
        if session is None and self._session_file is not None and self._session_file.exists():
            session = Session.load(self._session_file)
        self.auth = Authenticator(self._http, session, self._persist_session)

    def __enter__(self) -> PostAtClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- authentication ------------------------------------------------------

    @property
    def session(self) -> Session | None:
        return self.auth.session

    @property
    def logged_in(self) -> bool:
        return self.auth.session is not None

    def login(self, email: str, password: str) -> Session:
        """Log in with post.at credentials (no MFA). See :meth:`Authenticator.login`."""
        return self.auth.login(email, password)

    def begin_browser_login(self) -> PendingLogin:
        return self.auth.begin_browser_login()

    def complete_browser_login(self, pending: PendingLogin, redirect_url: str) -> Session:
        return self.auth.complete_browser_login(pending, redirect_url)

    def logout(self) -> None:
        self.auth.logout()

    def _persist_session(self, session: Session | None) -> None:
        if self._on_session_change is not None:
            self._on_session_change(session)
        if self._session_file is None:
            return
        if session is None:
            self._session_file.unlink(missing_ok=True)
        else:
            session.save(self._session_file)

    # -- transport -----------------------------------------------------------

    def _headers(self, auth: _Auth) -> dict[str, str]:
        headers = {"User-Agent": "Android", "Accept-Language": self._language}
        if auth is not None:
            headers["Authorization"] = f"Bearer {self.auth.access_token(SCOPE_APP)}"
            if auth in _X_AUTH_SCOPE:
                headers["X-Authorization"] = f"Bearer {self.auth.access_token(_X_AUTH_SCOPE[auth])}"
        return headers

    def _send(self, method: str, path: str, auth: _Auth, headers: dict[str, str], **kwargs: Any) -> httpx.Response:
        def attempt() -> httpx.Response:
            return self._http.request(
                method, BASE_URL + path, headers=self._headers(auth) | headers, **kwargs
            )

        response = attempt()
        if response.status_code == 401 and auth is not None:
            # Token revoked or clock skew: force a refresh once, like MSAL's silent retry.
            self.auth.invalidate(SCOPE_APP)
            if auth in _X_AUTH_SCOPE:
                self.auth.invalidate(_X_AUTH_SCOPE[auth])
            response = attempt()
        if not response.is_success:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
            raise ApiError(response.status_code, body, response.headers.get("x-operation-id"))
        return response

    def _rest(
        self,
        method: str,
        path: str,
        api_version: str,
        *,
        auth: _Auth = None,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
        version_header: Literal["Content-Type", "Accept"] = "Content-Type",
        binary: bool = False,
    ) -> Any:
        # The app declares the API version in the media type of either header.
        headers = {version_header: f"application/json; charset=utf-8; api-version={api_version}"}
        if body is not None and version_header == "Accept":
            headers["Content-Type"] = "application/json; charset=utf-8"
        # Models serialize to their wire names; nulls are omitted like the app's Moshi does.
        content = None
        if body is not None:
            content = json.dumps(to_jsonable_python(body, by_alias=True, exclude_none=True)).encode()
        response = self._send(
            method,
            path,
            auth,
            headers,
            params={k: v for k, v in (params or {}).items() if v is not None},
            content=content,
        )
        if binary:
            return response.content
        return response.json() if response.content else None

    def _graphql(self, endpoint: str, operation: str, variables: Mapping[str, Any], *, auth: _Auth) -> Any:
        response = self._send(
            "POST",
            endpoint,
            auth,
            {"Content-Type": "application/json", "x-ui-theme": self._theme},
            json={"operationName": operation, "variables": variables, "query": _document(operation)},
        )
        body = response.json()
        if body.get("errors"):
            raise GraphQLError(body["errors"], body.get("data"))
        return body["data"]

    def _account_graphql(self, operation: str, variables: Mapping[str, Any]) -> Any:
        return self._graphql("auth/shipments", operation, variables, auth="app")

    # -- shipments (GraphQL) -------------------------------------------------

    def track(self, tracking_number: str) -> Shipment | None:
        """Public tracking of any shipment; ``None`` if the number is unknown."""
        data = self._graphql("public/shipments", "ShipmentPublic", {"id": tracking_number}, auth=None)
        return _parse(Shipment, data["einzelsendung"])

    def shipments(self, count: int = 100, previous_count: int = 0) -> ShipmentList:
        """The account's sent and received shipments, newest first."""
        variables = {"elementcount": count, "previousSendungenCount": previous_count}
        return _parse(ShipmentList, self._account_graphql("ShipmentList", variables)["pagedSendungen"])

    def shipment(self, tracking_number: str) -> Shipment | None:
        """Full details of one shipment as seen by the logged-in user."""
        data = self._account_graphql("ShipmentAuth", {"sendungsnummer": tracking_number})
        return _parse(Shipment, data["einzelsendung"])

    def add_shipment(self, tracking_number: str, *, outbound: bool = False) -> Any:
        """Add a shipment to the account as incoming (default) or outgoing."""
        operation, field = ("AddShipmentOutbound", "addSendungVersand") if outbound else ("AddShipmentInbound", "addSendungEmpfang")
        return self._account_graphql(operation, {"trackingNumber": tracking_number})[field]

    def remove_shipment(self, tracking_number: str, *, outbound: bool = False) -> Any:
        operation, field = ("RemoveShipmentOutbound", "removeSendungVersand") if outbound else ("RemoveShipmentInbound", "removeSendungEmpfang")
        return self._account_graphql(operation, {"trackingNumber": tracking_number})[field]

    def rename_shipment(self, tracking_number: str, title: str) -> Any:
        variables = {"trackingNumber": tracking_number, "title": title}
        return self._account_graphql("RenameShipment", variables)["renameSendung"]

    def possible_redirections(self, tracking_number: str) -> ShipmentRedirections | None:
        """Which redirections (neighbour, parcel locker, post office, place) are available and why not."""
        data = self._account_graphql("PossibleRedirections", {"sendungsnummer": tracking_number})
        return _parse(ShipmentRedirections, data["einzelsendung"])

    def redirect_to_neighbor(
        self,
        tracking_number: str,
        first_name: str,
        last_name: str,
        street: str,
        house_number: str | None = None,
        door: str | None = None,
    ) -> Any:
        redirection = _omit_none(
            sendungsnummer=tracking_number,
            vorname=first_name,
            nachname=last_name,
            strasse=street,
            hausnummer=house_number,
            tuer=door,
        )
        return self._account_graphql("RedirectShipmentNeighbor", {"redirection": redirection})["setNeighborRedirection"]

    def redirect_to_parcel_locker(self, tracking_number: str, branch_key: str | None = None) -> Any:
        redirection = _omit_none(sendungsnummer=tracking_number, selectedbranch=branch_key)
        return self._account_graphql("RedirectShipmentParcelLocker", {"redirection": redirection})["setParcelLockerRedirection"]

    def redirect_to_post_office(self, tracking_number: str, branch_key: str | None = None) -> Any:
        redirection = _omit_none(sendungsnummer=tracking_number, selectedbranch=branch_key)
        return self._account_graphql("RedirectShipmentPostOffice", {"redirection": redirection})["setPostOfficeRedirection"]

    def redirect_to_place(self, tracking_number: str, place: str | Place, description: str | None = None) -> Any:
        """Ask the carrier to leave the parcel at a place at the address; ``place`` is a key from :meth:`places`."""
        key = place.key if isinstance(place, Place) else place
        request = _omit_none(sendungsnummer=tracking_number, abstellort=key, beschreibung=description)
        return self._account_graphql("RedirectShipmentPlace", {"redirectionRequest": request})["setPlaceRedirection"]

    def reset_redirection(
        self, tracking_number: str, kind: Literal["neighbor", "parcel_locker", "post_office", "place"]
    ) -> Any:
        operation, field = {
            "neighbor": ("ResetNeighborShipmentRedirection", "resetNeighborRedirection"),
            "parcel_locker": ("ResetParcelLockerShipmentRedirection", "resetParcelLockerRedirection"),
            "post_office": ("ResetPostOfficeShipmentRedirection", "resetPostOfficeRedirection"),
            "place": ("ResetPlaceShipmentRedirection", "resetPlaceRedirection"),
        }[kind]
        return self._account_graphql(operation, {"trackingNumber": tracking_number})[field]

    # -- branches / locations ------------------------------------------------

    def branches(
        self,
        *,
        north_west: tuple[float, float] | None = None,
        south_east: tuple[float, float] | None = None,
        branch_types: Iterable[BranchType | str] | None = None,
        service_ids: Iterable[int] | None = None,
        max_count: int | None = None,
        branch_keys: Iterable[str] | None = None,
    ) -> list[Branch]:
        """Post offices, partners, parcel lockers and letter boxes.

        ``north_west``/``south_east`` are ``(latitude, longitude)`` corners of a
        bounding box; ``service_ids`` come from :meth:`service_filters`.
        """
        box = None
        if north_west is not None or south_east is not None:
            if north_west is None or south_east is None:
                raise ValueError("north_west and south_east must be given together")
            box = {
                "coordinatesnw": {"latitudewgs84": north_west[0], "longitudewgs84": north_west[1]},
                "coordinatesso": {"latitudewgs84": south_east[0], "longitudewgs84": south_east[1]},
            }
        branch_filter = _omit_none(
            boundingbox=box,
            branchtype=None if branch_types is None else [str(t) for t in branch_types],
            serviceIds=None if service_ids is None else list(service_ids),
            maxcount=max_count,
            branchkeys=None if branch_keys is None else list(branch_keys),
        )
        data = self._graphql("public/branches", "BoundingBox", {"filter": branch_filter}, auth=None)
        return _parse(list[Branch], data["branches"]) or []

    def service_filters(self, language: str = "de") -> list[ServiceFilter]:
        data = self._rest("GET", "public/serviceFilter", "2.0", params={"language": language}, version_header="Accept")
        return _parse(list[ServiceFilter], data)

    def places(self) -> list[Place]:
        """Drop-off places for :meth:`redirect_to_place`."""
        return _parse(list[Place], self._rest("GET", "public/places", "2.0", version_header="Accept"))

    # -- public configuration ------------------------------------------------

    def app_config(self) -> dict[str, Any]:
        return self._rest("GET", "public/appconfig", "2.0", version_header="Accept")

    def strings(self, language: str = "de") -> dict[str, str]:
        """The app's localized string table (also used to render status texts)."""
        response = self._send("GET", "public/resourceFile", None, {"Accept-Language": language})
        # The file contains raw control characters inside strings; the app's Moshi reader is lenient.
        return json.loads(response.text, strict=False)

    def master_data(self) -> dict[str, Any]:
        """Selectable values for profile editing (countries, titles, company legal forms, ...)."""
        return self._rest("GET", "public/masterData", "2.0", version_header="Accept")

    def validate_address(
        self,
        street: str,
        street_number: str,
        postal_code: str,
        city: str,
        *,
        house_number: str | None = None,
        door_number: str | None = None,
        post_office_id: str | None = None,
        full_address_text: str | None = None,
    ) -> AddressValidation:
        """Normalize an Austrian address; ``result[].pac`` is the post address code order endpoints need."""
        body = _omit_none(
            street=street,
            streetNumber=street_number,
            postalCode=postal_code,
            city=city,
            houseNumber=house_number,
            doorNumber=door_number,
            postOfficeId=post_office_id,
            fullAddressText=full_address_text,
        )
        return _parse(AddressValidation, self._rest("POST", "public/AddressValidation", "3.0", body=body))

    def register_app_id(self, app_id: str, os: str = "Android") -> Any:
        """``public/Paul/appId`` – the app registers its installation id here."""
        return self._rest("POST", "public/Paul/appId", "2.0", body={"appId": app_id, "os": os}, version_header="Accept")

    # -- user profile --------------------------------------------------------

    def user(self) -> User:
        return _parse(User, self._rest("GET", "auth/user", "2.0", auth="user"))

    def update_user(self, user: User) -> User:
        """Save the profile; pass a modified copy of :meth:`user`."""
        return _parse(User, self._rest("PUT", "auth/user", "2.0", auth="user", body=user))

    def add_address(self, address: Address) -> Address:
        return _parse(Address, self._rest("POST", "auth/userAddress", "2.0", auth="user", body=address))

    def update_address(self, address: Address) -> list[Address]:
        """Save a modified address from :attr:`User.addresses`; returns all addresses."""
        path = f"auth/userAddress/{address.address_sso_id}"
        return _parse(list[Address], self._rest("PUT", path, "2.0", auth="user", body=address))

    def delete_address(self, address: Address) -> Any:
        return self._rest("DELETE", f"auth/userAddress/{address.address_sso_id}", "2.0", auth="user")

    def marketing_preferences(self) -> list[MarketingSection]:
        data = self._rest("GET", "auth/user-preferences/marketing", "1.0", auth="app")
        return self._marketing_sections(data)

    def set_marketing_preferences(self, consents: Mapping[str, bool]) -> list[MarketingSection]:
        """Enable or disable purposes by id (``MarketingPurpose.id``); others stay unchanged."""
        body = {"marketingConsents": [{"id": id_, "enabled": enabled} for id_, enabled in consents.items()]}
        data = self._rest("POST", "auth/user-preferences/marketing", "1.0", auth="app", body=body)
        return self._marketing_sections(data)

    @staticmethod
    def _marketing_sections(data: Any) -> list[MarketingSection]:
        sections = [s for pref in data["marketingPreferences"] for s in pref["sections"]]
        return _parse(list[MarketingSection], sections)

    # -- receiving options (Wunschzustellung) --------------------------------

    def receiving_options(self) -> list[ReceivingOptionAddress]:
        """Standing delivery instructions per profile address."""
        data = self._rest("GET", "auth/receiving-options", "1.0", auth="user")
        return _parse(list[ReceivingOptionAddress], data["addresses"])

    def create_receiving_option(self, address_sso_id: int, option: Mapping[str, Any]) -> dict[str, Any]:
        """``option`` fields: ``type, startDate, id, dropOff {location, freeText},
        neighbor {firstname, lastname, street, streetNumber, doorNumber},
        post {branchKey, branchLongName, street, houseNumber, postalCode, city, isPostBranch}``."""
        return self._rest("POST", f"auth/receiving-options/{address_sso_id}", "1.0", auth="user", body=option)

    def update_receiving_option(self, address_sso_id: int, option: Mapping[str, Any]) -> dict[str, Any]:
        return self._rest("PUT", f"auth/receiving-options/{address_sso_id}", "1.0", auth="user", body=option)

    def delete_receiving_option(self, address_sso_id: int) -> dict[str, Any]:
        return self._rest("DELETE", f"auth/receiving-options/{address_sso_id}", "1.0", auth="user")

    def refer_delivery_agent(self, delivery_agent_id: str, campaign_id: str) -> None:
        body = {"deliveryAgentId": delivery_agent_id, "campaignId": campaign_id}
        self._rest("POST", "auth/referrals/receiving-options", "1.0", auth="user", body=body)

    def receiving_option_types(self) -> list[dict[str, Any]]:
        return self._rest("GET", "public/receiving-options/types", "1.0")["receivingOptionsTypes"]

    def receiving_option_drop_off_locations(self) -> list[dict[str, Any]]:
        return self._rest("GET", "public/receiving-options/drop-off-locations", "1.0")["dropOffLocations"]

    def receiving_option_activation_config(self) -> dict[str, Any]:
        return self._rest("GET", "public/receiving-options/activation-config", "1.0")

    # -- push notifications --------------------------------------------------

    def push_settings(self, language: str = "de") -> list[PushSetting]:
        data = self._rest("GET", "auth/pushSettings", "4.0", auth="app", params={"language": language})
        return _parse(list[PushSetting], data["pushSettings"])

    def update_push_settings(self, settings: Iterable[PushSetting], language: str = "de") -> list[PushSetting]:
        """Save modified settings from :meth:`push_settings`."""
        body = {"pushSettings": list(settings)}
        data = self._rest("PUT", "auth/pushSettings", "4.0", auth="app", body=body, params={"language": language})
        return _parse(list[PushSetting], data["pushSettings"])

    def register_push_device(self, device: Mapping[str, Any]) -> dict[str, Any]:
        """``device`` fields: ``pushToken, deviceIdentifier, platform, pushTokenType, language,
        client, hardwareModelInformation, osVersion, developmentDevice, pushEnabled``."""
        return self._rest("POST", "auth/pushdevice", "2.0", auth="app", body=device, version_header="Accept")

    def push_device(self, push_token: str) -> dict[str, Any]:
        return self._rest("GET", f"auth/pushdevice/{push_token}", "2.0", auth="app", version_header="Accept")

    def update_push_device(self, registration_key: str, device: Mapping[str, Any]) -> dict[str, Any]:
        return self._rest("PUT", f"auth/pushdevice/{registration_key}", "2.0", auth="app", body=device, version_header="Accept")

    def delete_push_device(self, registration_key: str) -> dict[str, Any]:
        return self._rest("DELETE", f"auth/pushdevice/{registration_key}", "2.0", auth="app", version_header="Accept")

    def push_notifications(self, registration_key: str) -> dict[str, Any]:
        """Notification inbox of a registered push device."""
        return self._rest("GET", f"auth/pushnotification/{registration_key}", "3.0", auth="app")

    def delete_push_notification(self, notification_id: str) -> None:
        self._rest("DELETE", f"auth/pushnotification/{notification_id}", "2.0", auth="app", version_header="Accept")

    def delete_push_notifications(self, registration_key: str) -> None:
        """Clear the whole inbox of a push device."""
        self._rest("DELETE", f"auth/pushnotification/devices/{registration_key}", "2.0", auth="app", version_header="Accept")

    # -- orders / e-commerce -------------------------------------------------

    def order_history(
        self,
        limit: int | None = None,
        offset: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> OrderHistory:
        """``date_from``/``date_to`` are ISO-8601 local date-times (``2026-01-01T00:00:00``)."""
        body = _omit_none(limit=limit, offset=offset, **{"from": date_from, "to": date_to})
        return _parse(OrderHistory, self._rest("POST", "auth/order/orderHistory", "3.0", auth="app", body=body))

    def order_details(self, order_id: str) -> Order:
        return _parse(Order, self._rest("GET", f"auth/order/orderDetails/{order_id}", "3.0", auth="app"))

    def order_attachment(self, order_id: str, attachment_id: str) -> bytes:
        """Download e.g. the invoice PDF (``attachment_id`` is ``Order.invoice.id``)."""
        params = {"orderId": order_id, "attachmentId": attachment_id}
        return self._rest("GET", "auth/order/attachments", "3.0", auth="app", params=params, binary=True)

    def active_collection_services(self) -> list[dict[str, Any]]:
        """Booked parcel pick-ups that are not done yet (``orderId, timeFrame, address``)."""
        data = self._rest("GET", "auth/order/collectionServices/active", "3.0", auth="app")
        return data["activeCollectionServices"]

    def collection_service_times(self, address: EcoAddress) -> CollectionTime:
        """Next pick-up slot for an address; ``post_address_code`` is required (``pac`` from :meth:`validate_address`)."""
        data = self._rest("POST", "public/order/collectionService/time", "2.0", body=address, version_header="Accept")
        return _parse(CollectionTime, data)

    def order_collection_service(self, address: EcoAddress, collection_data: Mapping[str, Any]) -> dict[str, Any]:
        """Put a parcel pick-up (Abholservice) into the cart.

        ``collection_data`` fields: ``personal, collectionLocation, collectionTime``.
        """
        body = {"address": address, "collectionData": collection_data}
        return self._rest("POST", "auth/order/collectionService", "3.0", auth="app", body=body)

    def parcel_stamp_config(self) -> dict[str, Any]:
        return self._rest("GET", "public/order/parcelStamp/config", "3.0")

    def parcel_stamp_size(self, width: int, length: int, height: int) -> str:
        """Size class (e.g. ``"45"``) for parcel dimensions in centimetres."""
        params = {"width": width, "length": length, "height": height}
        return self._rest("GET", "public/order/parcelStamp/size", "3.0", params=params)["size"]

    def last_parcel_stamp_shipper_address(self) -> EcoAddress | None:
        data = self._rest("GET", "auth/order/parcelStamps/shipperAddresses/last", "3.0", auth="app")
        return _parse(EcoAddress, data)

    def order_parcel_stamp(self, order: Mapping[str, Any]) -> dict[str, Any]:
        """Put a parcel stamp (Paketmarke) into the cart.

        ``order`` fields: ``id, label, shipperAddress, consigneeAddress, branch, infoPhone,
        infoEmail, additionalServices, productId, entityId, price, coupons``.
        """
        return self._rest("POST", "auth/order/parcelStamp", "3.0", auth="app", body=order)

    def parcel_stamp_pdf(self, parcel_stamp_id: str) -> bytes:
        params = {"parcelStampId": parcel_stamp_id}
        return self._rest("GET", "auth/order/parcelStamp/pdf", "3.0", auth="app", params=params, binary=True)

    def parcel_stamps_pdf(self, order_id: str) -> bytes:
        """All parcel stamps of an order as one PDF."""
        params = {"orderNumber": order_id}
        return self._rest("GET", "auth/order/parcelStamps/pdf", "3.0", auth="app", params=params, binary=True)

    def order_apd(self, ident_code: str) -> dict[str, Any]:
        """Order an APD (pay-on-delivery / customs fee) payment for a shipment ident code."""
        return self._rest("POST", "auth/order/apd", "3.0", auth="app", body={"identCode": ident_code})

    def apply_coupon(self, context_token: str, coupon_code: str) -> dict[str, Any]:
        body = {"contextToken": context_token, "couponCode": coupon_code}
        return self._rest("POST", "auth/order/coupon", "3.0", auth="app", body=body)

    def remove_line_items(self, context_token: str, ids: Iterable[str]) -> dict[str, Any]:
        body = {"contextToken": context_token, "ids": list(ids)}
        return self._rest("DELETE", "auth/order/lineItem", "3.0", auth="app", body=body)

    def recreate_cart(self, context_token: str, order_id: str) -> dict[str, Any]:
        body = {"contextToken": context_token, "orderId": order_id}
        return self._rest("POST", "auth/order/cart", "3.0", auth="app", body=body)

    def start_payment(
        self,
        context_token: str,
        account_type: str,
        success_url: str,
        error_url: str,
        *,
        send_snail_mail: bool = False,
        billing_address: EcoAddress | None = None,
    ) -> dict[str, Any]:
        """Start checkout for the cart identified by ``context_token``; returns a payment URL."""
        body = _omit_none(
            contextToken=context_token,
            accountType=account_type,
            successUrl=success_url,
            errorUrl=error_url,
            sendSnailMail=send_snail_mail,
            billingAddress=billing_address,
        )
        return self._rest("POST", "auth/order/payment", "3.0", auth="app", body=body)

    # -- identification ------------------------------------------------------

    def jumio_sdk_token(self) -> dict[str, Any]:
        return self._rest("GET", "auth/Identification/Jumio/SdkToken", "3.0", auth="idaustria")

    def id_austria_client_id(self) -> dict[str, Any]:
        return self._rest("GET", "auth/Identification/IdAustria/ClientId", "3.0", auth="idaustria")

    def submit_id_austria(self, code: str, nonce: str) -> None:
        """Finish an ID Austria identification with the code returned to
        ``https://www.post.at/sh/redirectidaustria?clientId=…&nonce=…``."""
        self._rest("POST", "auth/Identification/IdAustria", "3.0", auth="idaustria", body={"code": code, "nonce": nonce})
