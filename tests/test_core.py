# SPDX-License-Identifier: MIT
"""Synthetic fixtures only. No game, capture, save, BIOS or generated code."""
from pathlib import Path
import json
import struct
import tempfile
import unittest
import wave
import subprocess
import sys
from unittest.mock import patch
from PIL import Image
from saturn_kit.api import Profile, differences, pin, python_executable, read_json, run, write_json


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.profile = self.root / 'profile.json'
        self.outputs = self.root / 'outputs'
        write_json(self.profile, dict(schema='saturn-kit-profile-v1', game='synthetic',
            input_roots=[str(self.root)], output_root=str(self.outputs)))

    def call(self, command, name, **args):
        return run(command, dict(profile=str(self.profile), out=str(self.outputs/name), **args))

    def capture(self, name, state=None, colour='red', size=(3, 2)):
        image = self.root / (name + '.png'); Image.new('RGB', size, colour).save(image)
        path = self.root / (name + '.json')
        write_json(path, dict(schema='saturn-kit-capture-v1', game='synthetic',
            provenance=dict(debug_derived=False, origin='synthetic'), state=state or {'priority': 1},
            images={'composite': pin(image)}))
        return str(path)

    def test_inspect_preserves_input_and_image(self):
        source = self.capture('a'); before = pin(source)
        result = self.call('inspect', 'i', input=source)
        report = read_json(result['report'])
        self.assertEqual(report['schema'], 'saturn-kit-inspection-v1')
        self.assertEqual(pin(source), before)
        self.assertEqual(report['images']['composite']['sha256'], pin(self.root/'a.png')['sha256'])

    def test_capture_other_game_rejected(self):
        source = Path(self.capture('a')); data = read_json(source); data['game'] = 'other'
        source.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'game differs'):
            self.call('inspect', 'i', input=str(source))

    def test_no_overwrite(self):
        source = self.capture('a'); self.call('inspect', 'i', input=source)
        sentinel = (self.outputs/'i/inspection.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.call('inspect', 'i', input=source)
        self.assertEqual((self.outputs/'i/inspection.json').read_bytes(), sentinel)

    def test_output_root_and_input_root_boundaries(self):
        source = self.capture('a')
        with self.assertRaisesRegex(ValueError, 'below profile'):
            run('inspect',dict(profile=str(self.profile),input=source,out=str(self.root/'outside')))
        with self.assertRaisesRegex(ValueError, 'outside profile'):
            Profile(self.profile).input(str(self.root.parent/'unowned.txt'))

    def test_tampered_image_rejected(self):
        source = self.capture('a'); (self.root/'a.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.call('inspect','i',input=source)
        self.assertTrue((self.outputs/'i/failure.json').is_file())

    def test_image_path_escape_rejected(self):
        source = Path(self.capture('a')); data=read_json(source)
        data['images']['../escape'] = data['images'].pop('composite');source.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'image name'):
            self.call('inspect', 'i', input=str(source))

    def test_image_name_collisions_and_devices_rejected(self):
        for name, keys in [('case',['layer','LAYER']),('suffix',['layer','layer.png']),('device',['NUL'])]:
            source=Path(self.capture(name));data=read_json(source)
            row=data['images']['composite'];data['images']={key:row for key in keys};source.write_text(json.dumps(data))
            with self.assertRaises(ValueError):self.call('inspect',name,input=str(source))

    def test_diff_every_field_and_whole_image(self):
        a=self.call('inspect','ia',input=self.capture('a',{'layer':{'priority':1},'commands':[1,2]}))
        b=self.call('inspect','ib',input=self.capture('b',{'layer':{'priority':2},'commands':[1]},'blue',(4,2)))
        result=self.call('diff','d',before=a['report'],after=b['report'])
        report=read_json(result['report']);image=report['images']['composite']
        self.assertEqual({x['path'] for x in report['fields']},{'/layer/priority','/commands/1'})
        self.assertEqual((image['different_overlap_pixels'],image['missing_pixels'],image['added_pixels']),(6,0,2))
        self.assertFalse(report['conformance_claim'])
        self.assertEqual(Image.open(image['image']['path']).size,(4,2))

    def test_diff_equal_is_not_conformance(self):
        a=self.call('inspect','ia',input=self.capture('a'))
        result=self.call('diff','d',before=a['report'],after=a['report'])
        report=read_json(result['report'])
        self.assertEqual(report['fields'],[]);self.assertTrue(report['images']['composite']['equal'])
        self.assertFalse(report['conformance_claim'])

    def test_json_pointer_and_added_missing_types(self):
        rows=differences({'a/b':[1],'gone':True},{'a/b':[False,2],'new':None})
        self.assertEqual({x['path'] for x in rows},{'/a~1b/0','/a~1b/1','/gone','/new'})

    def test_hw_counts_keep_cpu_and_global_separate(self):
        p=self.root/'hw.txt';p.write_text('cpu-reg 0 R 1 FFFFFE11 3\nreg R 1 FFFFFE11 3\nwatch R 06000000 7\nhello 99\n')
        q=self.root/'hw2.txt';q.write_text('cpu-reg 0 R 1 FFFFFE11 4\n')
        result=self.call('hw-report','h',inputs=[str(p)])
        report=read_json(result['report']);counts={x['key']:x['count'] for x in report['counters']}
        self.assertEqual(counts,{'cpu-reg 0 R 1 FFFFFE11':3,'reg R 1 FFFFFE11':3,'watch R 06000000':7})
        self.assertEqual(report['event_prefix_frequencies'],{'hello':1})
        self.assertFalse(report['host_cpu_time_inferred'])
        with self.assertRaisesRegex(ValueError,'Duplicate hardware counter'):
            self.call('hw-report','duplicate-files',inputs=[str(p),str(q)])
        q.write_text('watch R 06000000 0\nwatch R 06000000 2\n')
        with self.assertRaisesRegex(ValueError,'Duplicate hardware counter'):
            self.call('hw-report','duplicate-lines',inputs=[str(q)])

    def test_malformed_json_duplicate_and_nonfinite(self):
        for content in ['{"x":1,"x":2}','{"x":NaN}']:
            p=self.root/'bad.json';p.write_text(content)
            with self.assertRaises(ValueError):read_json(p)

    def test_buffer_identity_verified(self):
        source=Path(self.capture('a'));data=read_json(source);blob=self.root/'synthetic-buffer';blob.write_bytes(b'abcdefgh')
        data['schema']='saturn-kit-dump-v1';data['buffers']={'test':dict(pin(blob),byte_order='big')}
        source.write_text(json.dumps(data));self.call('inspect','good',input=str(source));blob.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'pin mismatch'):self.call('inspect','bad',input=str(source))

    def test_save_envelopes_no_conversion(self):
        for fmt in ('bup','native'):
            header=bytearray(64 if fmt=='bup' else 32)
            if fmt=='bup':header[:4]=b'Vmem';header[16:27]=b'SYNTHETIC00';header[44:48]=struct.pack('>I',8)
            else:header[:11]=b'SYNTHETIC00';header[28:32]=struct.pack('<I',8)
            p=self.root/(fmt+'.test');p.write_bytes(header+b'abcdefgh');before=pin(p)
            result=self.call('saves',fmt,input=str(p),format=fmt);self.assertEqual(result['records'],1)
            self.assertFalse(result['converted']);self.assertEqual(pin(p),before)

    def test_save_truncation(self):
        p=self.root/'bad.test';p.write_bytes(b'Vmem'+b'\0'*10)
        with self.assertRaisesRegex(ValueError,'Truncated'):self.call('saves','s',input=str(p))

    def test_audio_silence_candidates_not_proof(self):
        p=self.root/'synthetic.wav'
        with wave.open(str(p),'wb') as f:
            f.setparams((1,2,1000,0,'NONE','not compressed'));f.writeframes(struct.pack('<2h',1,1)+b'\0'*40+struct.pack('<h',1))
        result=self.call('audio-gaps','audio',input=str(p),threshold_ms=20)
        report=read_json(result['report']);self.assertEqual(report['exact_digital_silence'],[{'start_frame':2,'frames':20}])
        self.assertFalse(report['audible_dropout_proven'])

    def test_plan_does_not_execute_and_execute_fails(self):
        p=self.root/'recording.txt';p.write_text('synthetic recording')
        result=self.call('replay','plan',recording=str(p),frames=12,execute=False)
        self.assertEqual(result['status'],'PLANNING-ONLY');self.assertFalse(result['executed'])
        with self.assertRaisesRegex(ValueError,'execution is unavailable'):
            self.call('replay','noexec',recording=str(p),frames=12,execute=True)

    def test_no_external_adapter_by_default(self):
        p=self.root/'capture.raw';p.write_bytes(b'synthetic')
        with self.assertRaisesRegex(ValueError,'not enabled'):self.call('inspect','i',input=str(p))

    def test_output_root_must_be_declared_for_result_reuse(self):
        cfg=read_json(self.profile);cfg['input_roots']=[str(self.root/'inputs')]
        self.profile.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'output_root must lie inside input_roots'):
            Profile(self.profile)

    def test_graphics_source_pin_checked_before_launch(self):
        capture=self.root/'capture.raw';capture.write_bytes(b'synthetic capture')
        adapter=self.root/'adapter.py';adapter.write_text('synthetic adapter')
        delegate=self.root/'delegate.py';delegate.write_text('synthetic delegate')
        expected=pin(delegate);delegate.write_text('changed delegate')
        cfg=read_json(self.profile)
        cfg.update(allow_external_tools=True,input_roots=[str(self.root),str(python_executable().parent)],
                   graphics=dict(adapter=pin(adapter),python=pin(sys.executable),source_pins=[expected]))
        self.profile.write_text(json.dumps(cfg))
        with patch('saturn_kit.api.subprocess.run') as launch:
            with self.assertRaisesRegex(ValueError,'File pin mismatch'):
                self.call('inspect','changed-delegate',input=str(capture))
            launch.assert_not_called()

    def test_audio_profile_default_through_actual_cli(self):
        kit=Path(__file__).resolve().parents[1]
        cfg=read_json(self.profile);cfg.update(allow_external_tools=True,
            input_roots=[str(self.root),str(kit),str(python_executable().parent)],
            audio=dict(adapter=pin(kit/'addons/gpl-audio/adapter.py'),python=pin(sys.executable),
                       rate=1000,channels=1,minimum_run_ms=5))
        self.profile.write_text(json.dumps(cfg));p=self.root/'quiet.wav'
        with wave.open(str(p),'wb') as stream:
            stream.setparams((1,2,1000,0,'NONE','not compressed'));stream.writeframes(b'\0'*12)
        result=subprocess.run([sys.executable,'-B','-E',str(kit/'saturn-kit.py'),'audio-gaps',
            '--profile',str(self.profile),'--input',str(p),'--out',str(self.outputs/'profile-default')],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        report=read_json(json.loads(result.stdout)['report'])
        self.assertEqual(report['settings']['minimum_ms'],5)
        self.assertEqual(report['whole_playback_nearzero_runs']['2'][0]['start_frame'],0)


if __name__ == '__main__':
    unittest.main()
