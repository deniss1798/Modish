from pathlib import Path
p=Path('backend/app/api/media_proxy.py');s=p.read_text(encoding='utf-8');s=s.replace('from urllib.parse import urlparse','from urllib.parse import urlparse, urljoin');s=s.replace('    "picsum.photos",\n    "fastly.picsum.photos",\n    "placehold.co",\n    "dummyimage.com",','''    "baon.ru", "vipavenue.ru", "sportmaster.ru", "demix.ru",
    "sela.ru", "fablestore.ru", "mongolshop.ru", "tsum.com",
    "serginnetti.ru", "aimclo.ru", "postmeridiem-brand.com", "shoppinglive.ru",''');start=s.index('  headers = _upstream_headers(url)');end=s.index('\n  return Response(',start)
s=s[:start]+'''  try:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
      target = url
      for attempt in range(5):
        parsed = urlparse(target)
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or not _host_allowed_for_proxy(parsed.hostname or ""):
          raise HTTPException(status_code=403, detail="Адрес изображения не разрешён")
        async with client.stream("GET", target, headers=_upstream_headers(target)) as r:
          if r.status_code in (301, 302, 303, 307, 308):
            target = urljoin(target, r.headers.get("location", ""))
            continue
          if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Магазин не отдал фотографию")
          ct = (r.headers.get("content-type") or "").split(";")[0].lower()
          if not (ct.startswith("image/") or ct == "application/octet-stream"):
            raise HTTPException(status_code=502, detail="Магазин вернул не фотографию")
          chunks = bytearray()
          async for chunk in r.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > _MAX_BYTES:
              raise HTTPException(status_code=502, detail="Фотография слишком большая")
          body = bytes(chunks)
          if not body:
            raise HTTPException(status_code=502, detail="Пустая фотография")
          break
      else:
        raise HTTPException(status_code=502, detail="Слишком много перенаправлений")
  except httpx.RequestError as e:
    raise HTTPException(status_code=502, detail="Не удалось загрузить фотографию магазина") from e
''' +s[end:];p.write_text(s,encoding='utf-8')
p=Path('lib/core/widgets/product_image.dart');s=p.read_text(encoding='utf-8');s=s.replace('    if (!mounted) return;\n    setState(() {\n      _loading = false;', '    if (!mounted) return;\n    _requestGen++;\n    _stallTimer?.cancel();\n    setState(() {\n      _loading = false;');p.write_text(s,encoding='utf-8')
p=Path('lib/features/products/product_detail_screen.dart');s=p.read_text(encoding='utf-8').replace('    if (!await canLaunchUrl(uri)) return;\n    await launchUrl(uri, mode: LaunchMode.externalApplication);', "    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {\n      throw Exception('Не удалось открыть браузер. Попробуйте ещё раз.');\n    }");s=s.replace('imageUrl: widget.urls[i],','imageUrl: widget.urls[i],\n              fallbackImageUrls: widget.urls,');p.write_text(s,encoding='utf-8')
p=Path('lib/features/recommendations/feed_screen.dart');s=p.read_text(encoding='utf-8').replace('imageUrl: urls[i],','imageUrl: urls[i],\n                  fallbackImageUrls: urls,');p.write_text(s,encoding='utf-8')
p=Path('lib/features/products/models.dart');s=p.read_text(encoding='utf-8').replace("productUrl: (json['product_url'] ?? '').toString(),","productUrl: (json['outbound_url'] ?? json['product_url'] ?? '').toString(),");start=s.index('    final a = (affiliateUrl');end=s.index('\n  }',start);s=s[:start]+"    return productUrl.trim();"+s[end:];s=s.replace('final fromFeed = imageUrls.map(norm)', 'final fromFeed = {imageUrl, ...imageUrls}.map(norm)');p.write_text(s,encoding='utf-8')
p=Path('lib/features/profile/profile_screen.dart');s=p.read_text(encoding='utf-8');start=s.index('              MenuTile(\n                icon: Icons.history,');end=s.index('              MenuTile(',start+20);s=s[:start]+s[end:];p.write_text(s,encoding='utf-8')
