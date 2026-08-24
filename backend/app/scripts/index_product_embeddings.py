from __future__ import annotations

import argparse
import json

from ..db import SessionLocal
from ..services.embedding_service import index_active_product_embeddings


def main() -> None:
  parser = argparse.ArgumentParser(description="Index embeddings for active catalog products.")
  parser.add_argument("--source", default=None, help="Optional product source code.")
  parser.add_argument("--limit", type=int, default=None, help="Optional max number of products to index.")
  args = parser.parse_args()

  db = SessionLocal()
  try:
    summary = index_active_product_embeddings(db, source=args.source, limit=args.limit)
    db.commit()
    print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
  except Exception:
    db.rollback()
    raise
  finally:
    db.close()


if __name__ == "__main__":
  main()
