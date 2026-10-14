"""Exercise the failed INSERT path, including an unrelated integrity failure."""
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import auth
from app.db import Base
from app.models import User


class RegistrationConflictTests(unittest.TestCase):
  def setUp(self):
    self.engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(self.engine)
    self.db = Session(self.engine)
    self.existing_id = str(uuid4())
    self.db.add(User(id=self.existing_id, email="existing@example.com", password_hash="unused"))
    self.db.commit()

  def tearDown(self):
    self.db.close()
    self.engine.dispose()

  def test_insert_email_conflict_after_absent_precheck_returns_409_and_rolls_back(self):
    absent = MagicMock()
    absent.scalar_one_or_none.return_value = None
    # Simulate the precheck losing the race; let the actual unique INSERT fail.
    with patch.object(self.db, "execute", return_value=absent), patch.object(auth, "hash_password", return_value="unused"):
      with self.assertRaises(HTTPException) as failure:
        auth.register(auth.AuthRequest(email="existing@example.com", password="long-password"), self.db)
    self.assertEqual(failure.exception.status_code, 409)
    self.assertTrue(self.db.is_active)
    self.assertEqual(self.db.scalar(select(func.count()).select_from(User)), 1)

  def test_unrelated_primary_key_integrity_failure_is_not_reported_as_email_conflict(self):
    with patch.object(auth, "uuid4", return_value=self.existing_id), patch.object(auth, "hash_password", return_value="unused"):
      with self.assertRaises(IntegrityError):
        auth.register(auth.AuthRequest(email="new@example.com", password="long-password"), self.db)
    self.assertTrue(self.db.is_active)
    self.assertEqual(self.db.scalar(select(func.count()).select_from(User)), 1)


if __name__ == "__main__":
  unittest.main()
