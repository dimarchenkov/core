from __future__ import annotations

from core.integrations.aqsi.schemas import AqsiGoodsPayload


def aqsi_good_matches(remote: dict[str, object], expected: AqsiGoodsPayload) -> bool:
    """Compare every authoritative goods field owned by Core."""
    expected_json = expected.as_aqsi_json()
    fields = ("id", "group_id", "type", "name", "sku", "barcodes")
    if any(remote.get(field) != expected_json.get(field) for field in fields):
        return False
    if _normalize_unit(remote.get("unit")) != _normalize_unit(expected_json["unit"]):
        return False
    for field in ("tax", "unitCode", "subject", "paymentMethodType"):
        try:
            if int(remote.get(field, -1)) != int(expected_json[field]):
                return False
        except (TypeError, ValueError):
            return False
    try:
        return float(remote.get("price", -1)) == expected.price
    except (TypeError, ValueError):
        return False


def aqsi_good_is_bound_to_shop(remote: dict[str, object], shop_id: str) -> bool:
    """Return whether AQSI reports the good in the selected active shop."""
    shops = remote.get("shops")
    if not isinstance(shops, list):
        return False
    return any(
        isinstance(shop, dict)
        and str(shop.get("id")) == shop_id
        and shop.get("deletedAt") is None
        for shop in shops
    )


def _normalize_unit(value: object) -> str:
    """Treat AQSI's canonical piece abbreviation as equal to our display name."""
    normalized = str(value).strip().casefold()
    if normalized in {"штука", "шт", "шт."}:
        return "piece"
    return normalized
