"""Пресеты Admitad CSV (export_adv_products)."""

from __future__ import annotations

from dataclasses import dataclass


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
    feed_url=(
      "http://export.admitad.com/ru/webmaster/websites/2939491/products/"
      "export_adv_products/?user=denis_demianchuk0bded&code=bw80xy5bd3"
      "&feed_id=25560&format=csv"
    ),
  ),
  AdmitadCsvSourcePreset(
    code="aimclo",
    name="Aim Clo",
    network="admitad",
    advertiser_id="21747",
    feed_url=(
      "http://export.admitad.com/ru/webmaster/websites/2939491/products/"
      "export_adv_products/?user=denis_demianchuk0bded&code=bw80xy5bd3"
      "&feed_id=21747&format=csv"
    ),
  ),
)
