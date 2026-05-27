"""Пресеты Admitad CSV (export_adv_products)."""

from __future__ import annotations

from dataclasses import dataclass

_ADMITAD_CSV_BASE = (
  "http://export.admitad.com/ru/webmaster/websites/2939491/products/"
  "export_adv_products/?user=denis_demianchuk0bded&code=bw80xy5bd3"
)


def admitad_csv_feed_url(feed_id: str) -> str:
  return f"{_ADMITAD_CSV_BASE}&feed_id={feed_id}&format=csv"


@dataclass(frozen=True)
class AdmitadCsvSourcePreset:
  code: str
  name: str
  network: str
  feed_url: str
  advertiser_id: str | None = None


ADMITAD_CSV_SOURCES: tuple[AdmitadCsvSourcePreset, ...] = (
  AdmitadCsvSourcePreset(
    code="fable",
    name="FABLE",
    network="admitad",
    advertiser_id="25560",
    feed_url=admitad_csv_feed_url("25560"),
  ),
  AdmitadCsvSourcePreset(
    code="aimclo",
    name="Aim Clo",
    network="admitad",
    advertiser_id="21738",
    feed_url=admitad_csv_feed_url("21738"),
  ),
  AdmitadCsvSourcePreset(
    code="sportmaster",
    name="Спортмастер",
    network="admitad",
    advertiser_id="26327",
    feed_url=admitad_csv_feed_url("26327"),
  ),
  AdmitadCsvSourcePreset(
    code="shoppinglive",
    name="Shopping Live",
    network="admitad",
    advertiser_id="25461",
    feed_url=admitad_csv_feed_url("25461"),
  ),
  AdmitadCsvSourcePreset(
    code="postmeridiem",
    name="Post Meridiem",
    network="admitad",
    advertiser_id="25200",
    feed_url=admitad_csv_feed_url("25200"),
  ),
  AdmitadCsvSourcePreset(
    code="baon",
    name="BAON",
    network="admitad",
    advertiser_id="19982",
    feed_url=admitad_csv_feed_url("19982"),
  ),
  AdmitadCsvSourcePreset(
    code="mongolshop",
    name="MONGOLSHOP",
    network="admitad",
    advertiser_id="26461",
    feed_url=admitad_csv_feed_url("26461"),
  ),
  AdmitadCsvSourcePreset(
    code="serginnetti",
    name="SERGINNETTI",
    network="admitad",
    advertiser_id="26417",
    feed_url=admitad_csv_feed_url("26417"),
  ),
  AdmitadCsvSourcePreset(
    code="demix",
    name="Demix",
    network="admitad",
    advertiser_id="25664",
    feed_url=admitad_csv_feed_url("25664"),
  ),
  AdmitadCsvSourcePreset(
    code="tsumoutlet",
    name="TSUM Outlet",
    network="admitad",
    advertiser_id="26118",
    feed_url=admitad_csv_feed_url("26118"),
  ),
  AdmitadCsvSourcePreset(
    code="vipavenue",
    name="VIP Avenue",
    network="admitad",
    advertiser_id="24512",
    feed_url=admitad_csv_feed_url("24512"),
  ),
)
