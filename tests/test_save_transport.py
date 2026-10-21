# SPDX-License-Identifier: MIT
"""Synthetic envelopes only; test optional GPL process transport without importing it."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest
from saturn_kit.api import pin, read_json, run, write_json


class SaveTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.out = self.root/'outputs'; self.profile = self.root/'profile.json'
        kit = Path(__file__).resolve().parents[1]
        write_json(self.profile, dict(schema='saturn-kit-profile-v1', game='synthetic',
            input_roots=[str(self.root), str(kit), str(Path(sys.executable).parent)], output_root=str(self.out),
            allow_external_tools=True, saves=dict(adapter=pin(kit/'addons/gpl-saves/adapter.py'),python=pin(sys.executable))))

    def call(self, name, **args):
        return run('saves', dict(profile=str(self.profile),out=str(self.out/name),**args))

    def fixture(self):
        data = bytearray(69); data[:4]=b'Vmem'; data[16:20]=b'TEST'; data[28:32]=b'NOTE'
        data[39]=1; struct.pack_into('>II',data,40,0x12345678,5);data[64:]=b'abcde'
        p=self.root/'synthetic.bup';p.write_bytes(data);return p,bytes(data)

    def test_metadata_read_only_and_exact_native_conversion(self):
        p,data=self.fixture();before=pin(p)
        summary=read_json(self.call('inspect',input=str(p))['report'])
        self.assertFalse(summary['converted']);self.assertEqual(summary['records'][0]['date_minutes_since_1980'],0x12345678)
        result=read_json(self.call('convert',input=str(p),to='native')['report'])
        native=Path(result['outputs']['native']['path']).read_bytes()
        self.assertEqual(native,data[16:40]+struct.pack('<II',0x12345678,5)+b'abcde')
        self.assertEqual(Path(result['outputs']['source_envelope']['path']).read_bytes(),data[:64])
        self.assertEqual(pin(p),before)

    def test_invalid_length_and_native_duplicate_refused(self):
        p,data=self.fixture();p.write_bytes(data[:-1])
        with self.assertRaises(ValueError):self.call('truncated',input=str(p),to='native')
        n=self.root/'native';n.write_bytes(data[16:40]+struct.pack('<II',0x12345678,5)+b'abcde')
        n.write_bytes(n.read_bytes()*2)
        with self.assertRaises(ValueError):self.call('duplicate',input=str(n),format='native')

    def test_conversion_requires_external_permission_and_fresh_output(self):
        p,data=self.fixture();self.call('convert',input=str(p),to='native')
        with self.assertRaises(ValueError):self.call('convert',input=str(p),to='native')
        cfg=read_json(self.profile);cfg['allow_external_tools']=False;self.profile.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'not enabled'):self.call('blocked',input=str(p),to='native')
