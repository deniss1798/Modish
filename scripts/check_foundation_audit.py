"""Ensure the optimized audit coverage equals all 15 actual profile filters."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import itertools
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
with patch.dict(os.environ, {'DATABASE_URL':'sqlite:///:memory:', 'JWT_SECRET':'isolated-audit-check',
                            'PYTHON_DOTENV_DISABLED':'1'}, clear=True), patch('dotenv.load_dotenv'):
    from audit_foundation_live import coverage, SIZES, BUDGETS
    from app.services.activation_service import product_ok
    from app.models import Product
    total = 0
    for gender, kind, price, sizes, active in itertools.product(
        ('womenswear','menswear'), ('Рубашки','Обувь'), (250,3999,10000,25001),
        (['S','M'],['38'],['UNKNOWN'],['XL','L']), (0,1)):
        p = Product(source='baon',title='Женская рубашка' if kind == 'Рубашки' else 'Женские ботинки',
            category=kind,price=price,currency='RUB',available_sizes=sizes,gender_target=gender,
            is_active=active,is_available=1,is_deleted_from_feed=0,
            image_url='https://shop.example/photo.jpg',original_url='https://shop.example/product')
        expected = [f'{size}/{budget}' for size in SIZES for budget in BUDGETS
            if product_ok(p,SimpleNamespace(gender_target='womenswear',clothing_size=size,budget_max=budget),
                          SimpleNamespace(shoe_size='38'),rules={})]
        assert coverage(p,{}) == expected, (gender,kind,price,sizes,active)
        total += 1
    print(f'Coverage equivalence passed: {total} product cases x 15 profiles')
