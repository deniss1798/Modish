from __future__ import annotations

from fastapi import HTTPException, UploadFile


async def read_upload_bytes_limited(photo: UploadFile, max_bytes: int) -> bytes:
  total = 0
  buf = bytearray()
  while True:
    chunk = await photo.read(65536)
    if not chunk:
      break
    total += len(chunk)
    if total > max_bytes:
      raise HTTPException(status_code=400, detail="Файл больше 5 MB")
    buf.extend(chunk)
  return bytes(buf)
