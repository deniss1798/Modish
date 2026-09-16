"""Repair persisted product gender from merchant evidence; journal every change.

Run inside the release image. Does not alter recency, prices, users or purchases.
A protected journal permits reverting only rows that still have the repaired value.
"""
import argparse,json,os
from pathlib import Path
from datetime import datetime
from sqlalchemy import select,update,bindparam
from app.db import SessionLocal
from app.models import Product
from app.catalog_normalize import product_gender_from_model

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--journal',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--rollback',action='store_true');args=parser.parse_args()
    path=Path(args.journal)
    table=Product.__table__
    if args.rollback:
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        with SessionLocal() as db:
            for row in rows:
                db.execute(update(table).where(table.c.id==row['id'],table.c.gender_target.is_not_distinct_from(row['new'])).values(gender_target=row['old'],updated_at=table.c.updated_at))
            db.commit()
        print(json.dumps({'rolled_back':len(rows)}));return
    assert not path.exists(),'journal_already_exists'
    counts={}; total=0
    with path.open('x',encoding='utf-8') as journal,SessionLocal() as db:
        os.chmod(path,0o600)
        pending=[]
        for p in db.execute(select(Product).execution_options(yield_per=300)).scalars():
            total+=1
            resolved=product_gender_from_model(p)
            if resolved==p.gender_target:continue
            record={'id':p.id,'old':p.gender_target,'new':resolved}
            journal.write(json.dumps(record)+'\n');counts[f'{p.gender_target}->{resolved}']=counts.get(f'{p.gender_target}->{resolved}',0)+1
            pending.append({'b_id':p.id,'b_gender':resolved})
            if args.apply and len(pending)>=500:
                journal.flush()
                db.execute(update(table).where(table.c.id==bindparam('b_id')).values(gender_target=bindparam('b_gender'),updated_at=table.c.updated_at),pending);pending.clear()
        if args.apply:
            if pending:db.execute(update(table).where(table.c.id==bindparam('b_id')).values(gender_target=bindparam('b_gender'),updated_at=table.c.updated_at),pending)
            journal.flush();os.fsync(journal.fileno());db.commit()
    print(json.dumps({'result':'applied' if args.apply else 'dry_run','scanned':total,'changed':sum(counts.values()),'counts':counts}),flush=True)

if __name__=='__main__':main()
