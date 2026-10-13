"""Compare package and current code on a reproducible isolated candidate pool."""
import hashlib,json,os,sys
from pathlib import Path
from time import perf_counter
assert os.environ.get('DATABASE_URL','').startswith('sqlite:')
backend=Path(sys.argv[1]).resolve();sys.path[:0]=[str(backend),str(backend/'tests')]
import test_shopping_intent as fixtures
from app.services import shopping_intent_service as intents
from app.services.shopping_intent_parser import parse_intent
case=fixtures.ShoppingIntentTests();case.setUp()
try:
 for n in range(1000):
  case.product(id=f'{n:036d}',external_id=f'{n:036d}',available_sizes=['48'] if n<20 else ['46'])
 spec=parse_intent('Брюки до 7000')
 timings=[];ids=None
 for _ in range(3):
  started=perf_counter();result=intents.item_results(case.db,case.user,spec);timings.append(perf_counter()-started)
  current=[row['product']['id'] for row in result]
  assert ids is None or current==ids
  ids=current
 print(json.dumps({'candidate_count':1000,'matching_size_count':20,'result_count':len(ids),
  'result_hash':hashlib.sha256(json.dumps(ids).encode()).hexdigest(),'seconds':timings,'median_seconds':sorted(timings)[1]}))
finally:
 case.doCleanups()
