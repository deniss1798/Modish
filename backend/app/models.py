from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utc_now() -> datetime:
  return datetime.now(timezone.utc)


class User(Base):
  __tablename__ = "users"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
  password_hash: Mapped[str] = mapped_column(String(256))
  plan: Mapped[str] = mapped_column(String(32), default="plus")
  subscription_status: Mapped[str] = mapped_column(String(32), default="trial")
  height_cm: Mapped[int] = mapped_column(Integer, default=170)
  weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
  fit_preference: Mapped[str] = mapped_column(String(32), default="regular")
  trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

  style_profile: Mapped["StyleProfile"] = relationship(back_populates="user", uselist=False)


class StyleProfile(Base):
  __tablename__ = "style_profiles"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
  style_target: Mapped[str] = mapped_column(String(32), default="unknown")
  confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
  profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
  age_group: Mapped[str | None] = mapped_column(String(16), nullable=True)
  photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
  photo_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
  body_shape: Mapped[str | None] = mapped_column(String(64), nullable=True)
  color_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
  height_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
  style_preferences: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
  price_segment: Mapped[str | None] = mapped_column(String(32), nullable=True)
  onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
  completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
  user: Mapped[User] = relationship(back_populates="style_profile")


class Recommendation(Base):
  __tablename__ = "recommendations"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  type: Mapped[str] = mapped_column(String(32), default="outfit")
  title: Mapped[str] = mapped_column(String(255))
  description: Mapped[str] = mapped_column(Text)
  content_json: Mapped[dict] = mapped_column(JSON, default=dict)
  tags_json: Mapped[dict] = mapped_column(JSON, default=dict)
  status: Mapped[str] = mapped_column(String(32), default="active")
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RecommendationEvent(Base):
  __tablename__ = "recommendation_events"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
  event_type: Mapped[str] = mapped_column(String(32))
  event_weight: Mapped[int] = mapped_column(Integer, default=0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SavedRecommendation(Base):
  __tablename__ = "saved_recommendations"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  __table_args__ = (UniqueConstraint("user_id", "recommendation_id", name="uq_saved_user_rec"),)


class UserPreference(Base):
  __tablename__ = "user_preferences"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  category: Mapped[str] = mapped_column(String(32), index=True)
  key: Mapped[str] = mapped_column(String(128), index=True)
  score: Mapped[int] = mapped_column(Integer, default=0)
  source: Mapped[str | None] = mapped_column(String(64), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
  __table_args__ = (UniqueConstraint("user_id", "category", "key", name="uq_pref_user_cat_key"),)


class UserRecommendationSummary(Base):
  __tablename__ = "user_recommendation_summaries"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
  summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
  confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
  based_on_events_count: Mapped[int] = mapped_column(Integer, default=0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class UserLimits(Base):
  __tablename__ = "user_limits"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
  period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  photo_analysis_used: Mapped[int] = mapped_column(Integer, default=0)
  recommendation_batches_used: Mapped[int] = mapped_column(Integer, default=0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ProductSource(Base):
  """Источник каталога (affiliate / Admitad program и т.д.)."""
  __tablename__ = "product_sources"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
  name: Mapped[str] = mapped_column(String(128))
  network: Mapped[str] = mapped_column(String(64), index=True)
  advertiser_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
  feed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
  deeplink_template: Mapped[str | None] = mapped_column(Text, nullable=True)
  status: Mapped[str] = mapped_column(String(32), default="pending")
  last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CatalogSyncRun(Base):
  __tablename__ = "catalog_sync_runs"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  source_id: Mapped[str] = mapped_column(ForeignKey("product_sources.id"), index=True)
  status: Mapped[str] = mapped_column(String(32), default="running")
  total_received: Mapped[int] = mapped_column(Integer, default=0)
  created_count: Mapped[int] = mapped_column(Integer, default=0)
  updated_count: Mapped[int] = mapped_column(Integer, default=0)
  deactivated_count: Mapped[int] = mapped_column(Integer, default=0)
  skipped_count: Mapped[int] = mapped_column(Integer, default=0)
  ingested_count: Mapped[int] = mapped_column(Integer, default=0)
  skip_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
  error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
  started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AffiliateClick(Base):
  __tablename__ = "affiliate_clicks"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
  source_id: Mapped[str | None] = mapped_column(ForeignKey("product_sources.id"), nullable=True, index=True)
  affiliate_url: Mapped[str] = mapped_column(Text)
  click_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
  user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
  ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceRule(Base):
  __tablename__ = "source_rules"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  source_id: Mapped[str] = mapped_column(ForeignKey("product_sources.id"), index=True)
  rule_type: Mapped[str] = mapped_column(String(64), index=True)
  rule_value: Mapped[str] = mapped_column(Text)
  is_active: Mapped[bool] = mapped_column(Integer, default=1)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Product(Base):
  __tablename__ = "products"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  external_id: Mapped[str] = mapped_column(String(128), index=True)
  source: Mapped[str] = mapped_column(String(64), index=True)
  source_id: Mapped[str | None] = mapped_column(ForeignKey("product_sources.id"), nullable=True, index=True)
  title: Mapped[str] = mapped_column(String(255))
  brand: Mapped[str] = mapped_column(String(128), index=True)
  category: Mapped[str] = mapped_column(String(64), index=True)
  subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
  price: Mapped[int] = mapped_column(Integer)
  currency: Mapped[str] = mapped_column(String(8), default="RUB")
  old_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
  discount_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
  availability_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
  image_url: Mapped[str] = mapped_column(Text)
  product_url: Mapped[str] = mapped_column(Text)
  affiliate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
  original_url: Mapped[str | None] = mapped_column(Text, nullable=True)
  group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  image_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
  barcode: Mapped[str | None] = mapped_column(String(128), nullable=True)
  vendor_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
  category_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
  raw_params_json: Mapped[dict] = mapped_column(JSON, default=dict)
  size_original: Mapped[str | None] = mapped_column(String(64), nullable=True)
  color_original: Mapped[str | None] = mapped_column(String(128), nullable=True)
  feed_raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
  merchant_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
  merchant_subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
  external_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_seen_in_feed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  is_deleted_from_feed: Mapped[bool] = mapped_column(Integer, default=0)
  available_sizes: Mapped[list[str]] = mapped_column(JSON, default=list)
  available_sizes_detailed: Mapped[list[dict]] = mapped_column(JSON, default=list)
  size_system: Mapped[str | None] = mapped_column(String(32), nullable=True)
  colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  color_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
  material: Mapped[str | None] = mapped_column(String(64), nullable=True)
  season: Mapped[str | None] = mapped_column(String(32), nullable=True)
  occasion: Mapped[str | None] = mapped_column(String(32), nullable=True)
  gender_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
  fit: Mapped[str | None] = mapped_column(String(32), nullable=True)
  silhouette: Mapped[str | None] = mapped_column(String(64), nullable=True)
  style_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
  image_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
  is_available: Mapped[bool] = mapped_column(Integer, default=1)  # sqlite-compatible bool
  last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  is_active: Mapped[bool] = mapped_column(Integer, default=1)  # sqlite-compatible bool
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
  __table_args__ = (UniqueConstraint("external_id", "source", name="uq_product_external_source"),)


class TasteProfile(Base):
  __tablename__ = "taste_profiles"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
  # Legacy lists (kept for migration compatibility)
  liked_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_brands: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_brands: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_styles: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_styles: Mapped[list[str]] = mapped_column(JSON, default=list)
  # New: weighted preferences
  category_weights: Mapped[dict] = mapped_column(JSON, default=dict)
  brand_weights: Mapped[dict] = mapped_column(JSON, default=dict)
  color_weights: Mapped[dict] = mapped_column(JSON, default=dict)
  style_weights: Mapped[dict] = mapped_column(JSON, default=dict)
  price_min: Mapped[int] = mapped_column(Integer, default=0)
  price_max: Mapped[int] = mapped_column(Integer, default=10_000)
  preferred_fit: Mapped[str] = mapped_column(String(32), default="regular")
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RecommendationEventV2(Base):
  __tablename__ = "recommendation_events_v2"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
  outfit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
  event_type: Mapped[str] = mapped_column(String(32))
  event_weight: Mapped[float] = mapped_column(Float, default=0.0)
  meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FitProfile(Base):
  __tablename__ = "fit_profiles"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
  height_cm: Mapped[int] = mapped_column(Integer)
  weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
  gender_target: Mapped[str] = mapped_column(String(32), default="unisex")
  clothing_size: Mapped[str] = mapped_column(String(32), default="M")
  body_proportions: Mapped[str] = mapped_column(Text, default="")
  contrast_level: Mapped[str] = mapped_column(String(32), default="")
  color_palette: Mapped[list[str]] = mapped_column(JSON, default=list)
  avoid_colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  recommended_silhouettes: Mapped[list[str]] = mapped_column(JSON, default=list)
  avoid_silhouettes: Mapped[list[str]] = mapped_column(JSON, default=list)
  recommended_fit: Mapped[str] = mapped_column(String(32), default="regular")
  avoid_fit: Mapped[list[str]] = mapped_column(JSON, default=list)
  style_constraints: Mapped[dict] = mapped_column(JSON, default=dict)
  interest_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
  style_scenarios: Mapped[list[str]] = mapped_column(JSON, default=list)
  budget_min: Mapped[int] = mapped_column(Integer, default=0)
  budget_max: Mapped[int] = mapped_column(Integer, default=10_000)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RecommendationCandidatePool(Base):
  """Пул товаров-кандидатов до ранжирования (ТЗ §13)."""
  __tablename__ = "recommendation_candidates"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
  product_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class UserRecommendationCache(Base):
  """Закэшированный топ рекомендаций для пользователя."""
  __tablename__ = "user_recommendation_cache"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
  top_product_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Outfit(Base):
  __tablename__ = "outfits"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  items_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {top: product_id, bottom:..., shoes:...}
  total_price: Mapped[int] = mapped_column(Integer, default=0)
  style_direction: Mapped[str] = mapped_column(String(128), default="")
  reason: Mapped[str] = mapped_column(Text, default="")
  score: Mapped[float] = mapped_column(Float, default=0.0)
  is_saved: Mapped[bool] = mapped_column(Integer, default=0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class MetricEvent(Base):
  __tablename__ = "metric_events"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
  name: Mapped[str] = mapped_column(String(64), index=True)
  meta_json: Mapped[dict] = mapped_column(JSON, default=dict)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductImpression(Base):
  """Показ товара в персональной ленте (P7 analytics)."""
  __tablename__ = "product_impressions"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
  source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UserProductState(Base):
  __tablename__ = "user_product_states"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
  product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
  hidden_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  event_strength: Mapped[float] = mapped_column(Float, default=0.0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
  __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_user_product_state"),)
