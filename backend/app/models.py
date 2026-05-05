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


class Product(Base):
  __tablename__ = "products"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  external_id: Mapped[str] = mapped_column(String(128), index=True)
  source: Mapped[str] = mapped_column(String(64), index=True)
  title: Mapped[str] = mapped_column(String(255))
  brand: Mapped[str] = mapped_column(String(128), index=True)
  category: Mapped[str] = mapped_column(String(64), index=True)
  subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
  price: Mapped[int] = mapped_column(Integer)
  currency: Mapped[str] = mapped_column(String(8), default="RUB")
  image_url: Mapped[str] = mapped_column(Text)
  product_url: Mapped[str] = mapped_column(Text)
  available_sizes: Mapped[list[str]] = mapped_column(JSON, default=list)
  colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  fit: Mapped[str | None] = mapped_column(String(32), nullable=True)
  silhouette: Mapped[str | None] = mapped_column(String(64), nullable=True)
  style_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
  is_active: Mapped[bool] = mapped_column(Integer, default=1)  # sqlite-compatible bool
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
  __table_args__ = (UniqueConstraint("external_id", "source", name="uq_product_external_source"),)


class TasteProfile(Base):
  __tablename__ = "taste_profiles"
  id: Mapped[str] = mapped_column(String(36), primary_key=True)
  user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
  liked_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_colors: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_brands: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_brands: Mapped[list[str]] = mapped_column(JSON, default=list)
  liked_styles: Mapped[list[str]] = mapped_column(JSON, default=list)
  disliked_styles: Mapped[list[str]] = mapped_column(JSON, default=list)
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
  event_weight: Mapped[int] = mapped_column(Integer, default=0)
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
  budget_min: Mapped[int] = mapped_column(Integer, default=0)
  budget_max: Mapped[int] = mapped_column(Integer, default=10_000)
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
