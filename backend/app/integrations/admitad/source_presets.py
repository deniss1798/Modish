"""Пресеты Admitad CSV (export_adv_products)."""

from __future__ import annotations

from dataclasses import dataclass

from urllib.parse import urlencode

from ...config import get_admitad_export_config


def admitad_csv_feed_url(feed_id: str) -> str:
  config = get_admitad_export_config()
  if config is None:
    raise ValueError("Admitad export is disabled; configure ADMITAD_ENABLED first")
  if not feed_id.isdigit():
    raise ValueError("Admitad feed id must be numeric")
  website_id, user, code = config
  query = urlencode({"user": user, "code": code, "feed_id": feed_id, "format": "csv"})
  return f"https://export.admitad.com/ru/webmaster/websites/{website_id}/products/export_adv_products/?{query}"


@dataclass(frozen=True)
class AdmitadCsvSourcePreset:
  code: str
  name: str
  network: str
  feed_id: str
  advertiser_id: str | None = None

  @property
  def feed_url(self) -> str:
    return admitad_csv_feed_url(self.feed_id)


ADMITAD_CSV_SOURCES: tuple[AdmitadCsvSourcePreset, ...] = (
  AdmitadCsvSourcePreset(
    code="fable",
    name="FABLE",
    network="admitad",
    advertiser_id="25560",
    feed_id="25560",
  ),
  AdmitadCsvSourcePreset(
    code="aimclo",
    name="Aim Clo",
    network="admitad",
    advertiser_id="21738",
    feed_id="21738",
  ),
  AdmitadCsvSourcePreset(
    code="sportmaster",
    name="Спортмастер",
    network="admitad",
    advertiser_id="26327",
    feed_id="26327",
  ),
  AdmitadCsvSourcePreset(
    code="shoppinglive",
    name="Shopping Live",
    network="admitad",
    advertiser_id="25461",
    feed_id="25461",
  ),
  AdmitadCsvSourcePreset(
    code="postmeridiem",
    name="Post Meridiem",
    network="admitad",
    advertiser_id="25200",
    feed_id="25200",
  ),
  AdmitadCsvSourcePreset(
    code="baon",
    name="BAON",
    network="admitad",
    advertiser_id="19982",
    feed_id="19982",
  ),
  AdmitadCsvSourcePreset(
    code="mongolshop",
    name="MONGOLSHOP",
    network="admitad",
    advertiser_id="26461",
    feed_id="26461",
  ),
  AdmitadCsvSourcePreset(
    code="serginnetti",
    name="SERGINNETTI",
    network="admitad",
    advertiser_id="26417",
    feed_id="26417",
  ),
  AdmitadCsvSourcePreset(
    code="demix",
    name="Demix",
    network="admitad",
    advertiser_id="25664",
    feed_id="25664",
  ),
  AdmitadCsvSourcePreset(
    code="tsumoutlet",
    name="TSUM Outlet",
    network="admitad",
    advertiser_id="26118",
    feed_id="26118",
  ),
  AdmitadCsvSourcePreset(
    code="vipavenue",
    name="VIP Avenue",
    network="admitad",
    advertiser_id="24512",
    feed_id="24512",
  ),
)
