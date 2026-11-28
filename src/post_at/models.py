"""Typed views of the BFF responses.

Field names are English snake_case; the original (often German) JSON keys are the
aliases, so ``model.model_dump()`` reproduces the wire format. Unknown fields the
server adds later are kept in ``model.model_extra``. Enum-typed fields fall back
to the raw string for values this version does not know yet.
"""

from __future__ import annotations

import base64
from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic.alias_generators import to_camel

_E = TypeVar("_E", bound=StrEnum)
# Known values become the enum member; values added server-side later stay plain strings.
OpenEnum = Annotated[_E | str, Field(union_mode="left_to_right")]


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
        coerce_numbers_to_str=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _null_to_default(cls, value: Any, info: ValidationInfo) -> Any:
        # The API sends ``null`` instead of omitting a field (e.g. ``shipmentUserInfo``
        # of a shipment added by tracking number); pydantic only defaults missing keys.
        if value is None and info.field_name is not None:
            field = cls.model_fields[info.field_name]
            if not field.is_required():
                return field.get_default(call_default_factory=True)
        return value


# -- enums (values from the app's GraphQL schema) -----------------------------


class DeliveryType(StrEnum):
    DELIVERY = "DELIVERY"
    NEIGHBOR = "NEIGHBOR"
    DROPOFF = "DROPOFF"
    REDIRECTBRANCH = "REDIRECTBRANCH"
    REDIRECTSTATION = "REDIRECTSTATION"


class BranchType(StrEnum):
    FILIALE = "FILIALE"
    POSTPARTNER = "POSTPARTNER"
    ABHOLSTATION = "ABHOLSTATION"
    BRIEFKASTEN = "BRIEFKASTEN"
    HERMES = "HERMES"
    PAKET_PUNKT = "PAKET_PUNKT"
    POST_SB_ZONE = "POST_SB_ZONE"


class UserInfoType(StrEnum):
    SUCCESS = "SUCCESS"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LinkType(StrEnum):
    DEEPLINK = "DEEPLINK"
    EXTERNAL = "EXTERNAL"
    WEBVIEW = "WEBVIEW"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class PayableProduct(StrEnum):
    APD = "APD"
    COD = "COD"
    POSTPAY = "POSTPAY"
    CUSTOMS = "CUSTOMS"


class NotificationAmount(StrEnum):
    NONE = "NONE"
    NACHNAME = "NACHNAME"
    TO_PAY = "TO_PAY"


class NotificationClaim(StrEnum):
    NONE = "NONE"
    ABHOLBEREIT = "ABHOLBEREIT"
    ABHOLBEREIT_PERSOENLICH = "ABHOLBEREIT_PERSOENLICH"
    EINZAHLUNG = "EINZAHLUNG"


class Storage(StrEnum):
    PAKET_FILIALE = "PAKET_FILIALE"
    PAKET_ABHOLSTATION = "PAKET_ABHOLSTATION"
    PAKET_HERMES = "PAKET_HERMES"
    BRIEF_FILIALE = "BRIEF_FILIALE"
    BRIEF_BEHOERDLICH = "BRIEF_BEHOERDLICH"
    POST_AUFTRAG = "POST_AUFTRAG"


class StorageLimit(StrEnum):
    NONE = "NONE"
    LIMIT_SET = "LIMIT_SET"


class RedirectionType(StrEnum):
    NEIGHBOR = "NEIGHBOR"
    PLACE = "PLACE"
    POST_OFFICE = "POST_OFFICE"
    PARCEL_LOCKER = "PARCEL_LOCKER"
    DEFAULT = "DEFAULT"


class RedirectionState(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    LOCKED = "LOCKED"


class CallToActionType(StrEnum):
    IDENTIFY = "IDENTIFY"
    PAY_NOW = "PAY_NOW"
    NONE = "NONE"


# -- shipments ----------------------------------------------------------------


class Link(Model):
    text: str | None = Field(None, alias="linkText")
    uri: str | None = Field(None, alias="linkUri")
    type: OpenEnum[LinkType] | None = Field(None, alias="linkType")


class UserInfo(Model):
    """Info box shown on a shipment (customs hints, redirection notes, ...)."""

    type: OpenEnum[UserInfoType] | None = Field(None, alias="shipmentUserInfoType")
    title: str | None = Field(None, alias="shipmentUserInfoTitle")
    description: str | None = Field(None, alias="shipmentUserInfoDesc")
    pill_text: str | None = None
    link: Link | None = None


class StateInfo(Model):
    title: str | None = Field(None, alias="titleText")
    description: str | None = Field(None, alias="descText")


class ShipmentEvent(Model):
    timestamp: datetime
    state: str | None = Field(None, alias="trackingState")
    """Localized state, e.g. ``In Zustellung``."""
    state_key: str | None = Field(None, alias="trackingStateKey")
    """Stable state, e.g. ``aviso``, ``deliveryHandOver``, ``inDistribution``, ``inDelivery``, ``delivered``."""
    description: str | None = Field(None, alias="trackingDesc")
    country: str | None = Field(None, alias="eventcountry")
    postal_code: str | None = Field(None, alias="eventpostalcode")
    place: str | None = Field(None, alias="eventPlaceName")
    state_info: list[StateInfo] = Field(default_factory=list, alias="stateInfo")


class Tag(Model):
    key: str
    """``empfangen`` (incoming), ``versenden`` (outgoing), ``order``, ..."""


class PaymentInformation(Model):
    status_type: OpenEnum[PaymentStatus] | None = None
    status_text: str | None = None
    due_date_text: str | None = None
    due_date: int | None = None
    sso_sign_in_necessary: bool | None = None
    amount: Decimal | None = None
    amount_paid: Decimal | None = None
    product: OpenEnum[PayableProduct] | None = None
    is_payable: bool | None = None


class NotificationInformation(Model):
    """Pick-up notice (gelber Zettel) details."""

    amount: OpenEnum[NotificationAmount] | None = None
    claim: OpenEnum[NotificationClaim] | None = None
    end_of_storage_date: datetime | None = None
    notification_identifier: str | None = None
    sso_user_id_list: str | None = None
    storage: OpenEnum[Storage] | None = None
    storage_limit: OpenEnum[StorageLimit] | None = None


class CashOnDelivery(Model):
    amount: Decimal | None = None
    currency: str | None = None


class CustomsInformation(Model):
    customs_document_available: bool = False
    user_document_needed: bool = False


class Recipient(Model):
    name: str | None = None
    post_code: str | None = None
    street: str | None = None


class RecipientAddress(Model):
    consignee_name: str | None = None


class Dimensions(Model):
    """Centimetres."""

    height: int | None = None
    length: int | None = None
    width: int | None = None


class NeighborRedirection(Model):
    first_name: str | None = Field(None, alias="firstname")
    last_name: str | None = Field(None, alias="lastname")
    street: str | None = None
    street_number: str | None = Field(None, alias="streetNr")
    is_selected: bool = False


class BranchRedirection(Model):
    selected_branch: str | None = None
    selected_redirection_area_code: str | None = None
    is_selected: bool = False


class PlaceRedirection(Model):
    description: str | None = Field(None, alias="beschreibung")
    is_selected: bool = False


class PackageRedirection(Model):
    neighbor: NeighborRedirection | None = None
    parcel_locker: BranchRedirection | None = Field(None, alias="parcellocker")
    place: PlaceRedirection | None = None
    post_office: BranchRedirection | None = Field(None, alias="postoffice")


class Shipment(Model):
    """A shipment. Public tracking fills only a subset of the fields."""

    tracking_number: str = Field(alias="activeIdentityCode")
    original_tracking_number: str | None = Field(None, alias="originalIdentityCode")
    status: str | None = None
    """Short status code, e.g. ``AN``, ``AV``, ``ZU``. Prefer :attr:`latest_event`."""
    title: str | None = Field(None, alias="bezeichnung")
    """Name the user gave the shipment."""
    image: str | None = Field(None, alias="bild")
    """Raw link to the photo of the parcel from the sorting conveyor; use :attr:`image_url` to fetch it."""
    branch_key: str | None = Field(None, alias="branchkey")
    tags: list[Tag] = Field(default_factory=list)
    is_my_shipment: bool | None = Field(None, alias="isMeineSendung")
    is_recipient: bool | None = None
    weight: float | None = None
    """Kilograms."""
    last_event_date: datetime | None = None
    sender: str | None = None
    product_category: str | None = Field(None, alias="produktkategorie")
    has_new_flag: bool | None = None
    has_payment: bool | None = None
    preshipper: str | None = None
    parcel_visualization: str | None = None
    payments: list[PaymentInformation] = Field(default_factory=list, alias="paymentInformations")
    sso_id_match: bool | None = Field(None, alias="ssoidmatch")
    parcel_stamp_id: str | None = None
    parcel_stamp_valid_to: datetime | None = None
    notification: NotificationInformation | None = Field(None, alias="notificationInformation")
    cash_on_delivery: CashOnDelivery | None = Field(None, alias="codInformation")
    customs: CustomsInformation | None = Field(None, alias="customsInformation")
    recipient: Recipient | None = None
    recipient_address: RecipientAddress | None = None
    estimated_delivery_date_text: str | None = None
    delivery_address_text: str | None = None
    delivery_type_text: str | None = None
    delivery_type: OpenEnum[DeliveryType] | None = None
    dimensions: Dimensions | None = None
    events: list[ShipmentEvent] = Field(default_factory=list, alias="sendungsEvents")
    """Oldest first."""
    redirections: list[PackageRedirection] = Field(default_factory=list, alias="packageRedirections")
    damage_reportability: bool | None = None
    inquiry_available: bool | None = None
    user_info: list[UserInfo] = Field(default_factory=list, alias="shipmentUserInfo")

    @property
    def latest_event(self) -> ShipmentEvent | None:
        return self.events[-1] if self.events else None

    @property
    def parcel_svg(self) -> str | None:
        """The parcel size illustration as SVG markup."""
        if not self.parcel_visualization:
            return None
        return base64.b64decode(self.parcel_visualization).decode()

    @property
    def image_url(self) -> str | None:
        """URL of the photo taken of the parcel on the sorting conveyor, if there is one.

        The photo shows the address label. The URL needs no login, so treat it as
        personal data. :attr:`image` contains ``|``, which the storage server only
        accepts percent-encoded (it resets the connection otherwise); this URL is
        encoded and can be fetched as is.
        """
        if not self.image:
            return None
        # Everything RFC 3986 allows unescaped in a URL, plus existing escapes.
        return quote(self.image, safe="%:/?#[]@!$&'()*+,;=")


class ShipmentList(Model):
    total: int = Field(alias="totalSendungen")
    outgoing_count: int = Field(alias="versandSendungen")
    incoming_count: int = Field(alias="empfangsSendungen")
    shipments: list[Shipment] = Field(default_factory=list, alias="sendungen")


class CallToAction(Model):
    type: OpenEnum[CallToActionType]


class RedirectionOption(Model):
    type: OpenEnum[RedirectionType]
    state: OpenEnum[RedirectionState]
    current: bool = False
    """This option is the one currently set."""
    call_to_action: CallToAction | None = None
    unavailability_reasons: list[str] = Field(default_factory=list)
    """E.g. ``DROP_OFF``, ``SIGNATURE``, ``CASH_ON_DELIVERY``, ``ACTIVE_REDIRECTION``."""
    info: list[UserInfo] = Field(default_factory=list, alias="redirectionInfo")


class RedirectionInformation(Model):
    info_boxes: list[UserInfo] = Field(default_factory=list)
    options: list[RedirectionOption] = Field(default_factory=list, alias="redirectionOptions")


class PossibleRedirectionTargets(Model):
    parcel_locker_branches: list[str] | None = Field(None, alias="branchesParcelLocker")
    post_office_branches: list[str] | None = Field(None, alias="branchesPostOffice")
    redirection_area_code: str | None = None


class ShipmentRedirections(Model):
    tracking_number: str = Field(alias="activeIdentityCode")
    targets: PossibleRedirectionTargets | None = Field(None, alias="possibleRedirections")
    information: RedirectionInformation | None = Field(None, alias="redirectionInformation")

    @property
    def options(self) -> list[RedirectionOption]:
        return self.information.options if self.information else []


# -- branches -----------------------------------------------------------------


class Coordinates(Model):
    latitude: float = Field(alias="latitudewgs84")
    longitude: float = Field(alias="longitudewgs84")


class BranchAddress(Model):
    coordinates: Coordinates | None = None
    street: str | None = Field(None, alias="streetname")
    street_number: str | None = Field(None, alias="streetnumber")
    postal_code: str | None = Field(None, alias="postalcode")
    city: str | None = None


class TimeRange(Model):
    """For midday closures ``closing`` is when the break starts and ``opening`` when it ends."""

    opening: time | None = Field(None, alias="openinghour")
    closing: time | None = Field(None, alias="closinghour")


class OpeningHours(TimeRange):
    day_of_week: str = Field(alias="dayofweek")
    """German weekday name (``Montag`` ... ``Sonntag``)."""
    midday_closure: TimeRange | None = Field(None, alias="middayclosures")


class OpeningHoursException(TimeRange):
    date: datetime = Field(alias="exceptiondate")
    midday_closure: TimeRange | None = Field(None, alias="middayclosures")


class Branch(Model):
    branch_key: str = Field(alias="branchkey")
    type: OpenEnum[BranchType]
    name: str | None = Field(None, alias="longname")
    address: BranchAddress | None = None
    service_ids: list[int] = Field(default_factory=list)
    opening_hours: list[OpeningHours] | None = Field(None, alias="standardstorehours")
    opening_hour_exceptions: list[OpeningHoursException] | None = Field(None, alias="storehourexceptions")


class ServiceFilter(Model):
    category: str
    service_id: int = Field(alias="serviceid")
    description: str
    is_often_used: bool = False


class Place(Model):
    """A drop-off place for :meth:`post_at.PostAtClient.redirect_to_place`."""

    key: str
    text: str


# -- addresses / user ---------------------------------------------------------


class ValidatedAddress(Model):
    street: str | None = None
    street_number: str | None = Field(None, alias="streetNo")
    door_number: str | None = None
    postal_code: str | None = None
    city: str | None = None
    sub_city: str | None = None
    district: str | None = None
    country: str | None = None
    pac: str | None = None
    """Post address code; pass it as ``postAddressCode`` to order endpoints."""
    parent_pac: str | None = None
    quality: int | None = None


class AddressValidation(Model):
    result: list[ValidatedAddress] = Field(default_factory=list)
    result_status: str | None = None
    status_code: str | None = None
    description: str | None = None
    service_request_address: ValidatedAddress | None = None
    show_fuzzy_search: bool = False


class Phone(Model):
    country_code: str | None = None
    area_code: str | None = None
    number: str | None = None


class Address(Model):
    """A profile address. Build one with keyword arguments to add it to the account."""

    id: str | None = None
    address_sso_id: int | None = None
    address_type: int | None = None
    street: str | None = None
    street2: str | None = None
    house_number: str | None = None
    door_number: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = Field(None, alias="countryIsoAlpha2Code")
    post_address_code: str | None = None
    is_confirmed: bool | None = None


class CompanyData(Model):
    company_name: str | None = None
    company_sso_id: int | None = None
    contact_sso_id: int | None = None
    customer_sso_id: int | None = None
    crm_id: str | None = None
    email_address: str | None = None
    legal_form_id: int | None = None
    main_address: Address | None = None
    mobile: Phone | None = None
    phone: Phone | None = None
    organization_number: str | None = None
    sap_debitor: str | None = None
    uid: str | None = None


class User(Model):
    username: str | None = None
    """The login e-mail address."""
    salutation: str | None = None
    title_before_name: str | None = None
    first_name: str | None = Field(None, alias="firstname")
    last_name: str | None = Field(None, alias="lastname")
    title_after_name: str | None = None
    birthdate: datetime | None = None
    mobile: Phone | None = None
    phone: Phone | None = None
    addresses: list[Address] = Field(default_factory=list)
    company_data: CompanyData | None = None
    level: int | None = None
    locked: bool | None = None
    registration_date: datetime | None = None
    registration_origin: str | None = None
    login_date: datetime | None = None
    sso_id: int | None = None
    customer_sso_id: int | None = None
    contact_sso_id: int | None = None
    person_sso_id: int | None = None
    customer_number: int | None = None
    crm_id: str | None = None
    roommates: list[dict] = Field(default_factory=list)


# -- receiving options (standing delivery instructions) -----------------------


class ReceivingOption(Model):
    id: int | None = None
    receiving_option_type: str | None = None
    service_type: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    drop_off: dict | None = None
    redirect_to_neighbor: dict | None = None
    redirect_to_post: dict | None = None


class ReceivingOptionAddress(Model):
    address: Address
    receiving_options: list[ReceivingOption] = Field(default_factory=list)


# -- settings -----------------------------------------------------------------


class PushSetting(Model):
    name: str = Field(alias="pushSetting")
    category: str | None = None
    enabled: bool
    sort_order: int | None = None
    notification_keys: list[str] = Field(default_factory=list)


class MarketingPurpose(Model):
    id: str
    title: str | None = None
    description: str | None = None
    enabled: bool = False
    order: int | None = None


class MarketingSection(Model):
    id: str
    title: str | None = None
    description: str | None = None
    order: int | None = None
    global_purpose: MarketingPurpose | None = None
    purposes: list[MarketingPurpose] = Field(default_factory=list)


# -- orders -------------------------------------------------------------------


class Price(Model):
    net_price: Decimal | None = None
    tax: Decimal | None = None
    tax_rate: Decimal | None = None
    total_price: Decimal | None = None


class EcoAddress(Model):
    """Address format of the shop/order endpoints."""

    person: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    title_before_name: str | None = None
    title_after_name: str | None = None
    street: str | None = None
    house_number: str | None = None
    door_number: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = Field(None, alias="countryIsoAlpha2Code")
    post_address_code: str | None = None


class Invoice(Model):
    id: str | None = None
    date: datetime | None = None
    url: str | None = None
    address: EcoAddress | None = None


class OrderItem(Model):
    quantity: int | None = None
    product_name: str | None = None
    product_string_key: str | None = None
    price: Price | None = None
    state: str | None = None
    expiry_date: datetime | None = None
    identity_codes: list[str] = Field(default_factory=list)
    """Tracking numbers of the shipments created by this item."""
    sender: EcoAddress | None = None
    receiver: EcoAddress | None = None
    apd_receiver: EcoAddress | None = None
    data: dict | None = None
    info: dict | None = None


class Order(Model):
    order_id: str
    order_date: datetime | None = None
    total_price: Price | None = None
    order_items: list[OrderItem] = Field(default_factory=list)
    invoice: Invoice | None = None
    shipping_address: EcoAddress | None = None
    payment_type: str | None = None
    coupons: list[dict] = Field(default_factory=list)
    parcel_stamp_contract_ids: list[str] = Field(default_factory=list)


class OrderHistory(Model):
    orders: list[Order] = Field(default_factory=list)
    total_order_count: int = 0


class CollectionTime(Model):
    from_: datetime = Field(alias="from")
    to: datetime
    last_possible_activation: datetime | None = None
