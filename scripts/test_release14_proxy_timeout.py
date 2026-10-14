"""Meaningful exact-scope regression for the reversible proxy timeout change."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('proxy', Path(__file__).with_name('release14_proxy_timeout_remote.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class ProxyTests(unittest.TestCase):
    original = b'server {\n server_name modish.org.ru;\n proxy_read_timeout 30s;\n proxy_send_timeout 30s;\n location / {\n  proxy_pass http://127.0.0.1:8000;\n }\n}\n'

    def test_only_read_timeout_changes(self):
        changed = p.candidate(self.original)
        self.assertEqual(changed.replace(b'proxy_read_timeout 240s;', b'proxy_read_timeout 30s;'), self.original)
        self.assertIn(b'proxy_send_timeout 30s;', changed)

    def test_other_host_upstream_multiple_or_different_timeout_fail_before_writes(self):
        for original in (self.original.replace(b'modish.org.ru', b'other.invalid'),
                         self.original.replace(b'127.0.0.1:8000', b'127.0.0.1:9000'),
                         self.original + b'proxy_read_timeout 30s;\n',
                         self.original.replace(b'proxy_read_timeout 30s;', b'proxy_read_timeout 120s;')):
            with self.subTest(original=original):
                with self.assertRaises(RuntimeError):
                    p.candidate(original)


if __name__ == '__main__':
    unittest.main()
