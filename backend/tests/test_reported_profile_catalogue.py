"""The reported L/EU38 profile works when independent fresh SKU evidence exists."""
import unittest

from app.api import fit_profile, products
import test_outfit_coverage as coverage


class ReportedProfileCatalogueTests(unittest.TestCase):
    def test_l_eu38_166cm_53kg_budget100000_has_real_feed_and_complete_outfits(self):
        # Reuse independent literal SKU labels, not the production size matcher
        # as an oracle. Import the module, not a discoverable TestCase alias.
        fixture = coverage.OutfitCoverageTests()
        profile = coverage.CoverageProfile(
            'reported-l-eu38', ('L', 'LETTER'), ('L', 'LETTER'), ('L', 'LETTER'),
            '38', 'mid', 100000, 'separates',
        )
        with fixture.catalogue(profile):
            fixture.client.app.include_router(fit_profile.router)
            fixture.client.app.include_router(products.router)
            fixture.warmup()
            patched = fixture.request('PATCH', '/fit-profile/me', json={
                'height': 166, 'weight': 53, 'gender_target': 'womenswear',
                'clothing_size': 'L', 'clothing_size_system': 'LETTER',
                'shoe_size': '38', 'shoe_size_system': 'EU', 'budget_max': 100000,
                'sizes': {slot: {'value': value, 'system': system}
                          for slot, value, system in (
                              ('top', 'L', 'LETTER'), ('bottom', 'L', 'LETTER'),
                              ('one_piece', 'L', 'LETTER'), ('shoes', '38', 'EU'),
                          )},
            })
            self.assertEqual((patched['height'], patched['weight'], patched['budget_max']),
                             (166, 53, 100000))
            self.assertEqual(fixture.request('GET', '/catalog/status')['status'], 'ready')
            wow = fixture.request('POST', '/onboarding/complete')
            fixture.assert_outfits(wow['outfits'])
            feed = fixture.request('GET', '/feed', params={'limit': 5})
            self.assertTrue(feed, 'The exact reported profile must not empty a positive catalogue')
            for item in feed:
                product = item['product']
                fixture.assert_product(product, fixture.ledger[product['id']]['slot'])
            generated = fixture.request('POST', '/outfits/generate',
                                        params={'count': 3, 'scenario': 'daily'})
            fixture.assert_outfits(generated)


if __name__ == '__main__':
    unittest.main()
