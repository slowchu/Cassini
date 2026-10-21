# SPDX-License-Identifier: MIT
"""Synthetic PCM/packet/counter tests. No game assets or recording is included."""
import csv
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

ADDON=Path(__file__).resolve().parents[1]/'addons/gpl-audio'
_old=list(sys.path)
sys.path.insert(0,str(ADDON))
try:
    spec=importlib.util.spec_from_file_location('saturn_kit_audio_test_adapter',ADDON/'adapter.py')
    audio=importlib.util.module_from_spec(spec); spec.loader.exec_module(audio)
except ModuleNotFoundError as error:
    if error.name=='numpy':
        raise unittest.SkipTest('Optional audio add-on requires existing NumPy')
    raise
finally:
    sys.path[:]=_old


class Delivery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ.get('SATURN_KIT_TEST_TMP'))
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.inputs=self.root/'inputs'; self.inputs.mkdir()
        self.outputs=self.root/'outputs'; self.outputs.mkdir()
        self.profile=dict(schema='saturn-kit-profile-v1',game='synthetic',
            input_roots=[str(self.inputs)],output_root=str(self.outputs),
            audio=dict(rate=1000,channels=2,thresholds_s16=[2,16],minimum_run_ms=5,
                       secondary_minimum_run_ms=20,windows={'scene':[1,3]}))
        self.playback=self.inputs/'playback.wav'; self.generated=self.inputs/'generated.wav'
        self.wav(self.playback,[(2, -2) if 80<=i<100 else (100,-100) for i in range(200)])
        self.wav(self.generated,[(0,0) if 140<=i<150 else (100,-100) for i in range(200)])
        self.packets=self.inputs/'packets.csv'
        self.packet_rows=[dict(file_frame=i*50,frames=50,flags=0,qpc_100ns=1000000+i*500000) for i in range(4)]
        self.csv(self.packets,self.packet_rows)
        self.capture=self.inputs/'capture.json'
        self.metadata=dict(include_only_process_tree=True,rate=1000,frames=200,packets=4,qpc_frequency=10000000,
                           discontinuity_packets=0,timestamp_error_packets=0)
        self.write(self.capture,self.metadata)
        self.supply=self.inputs/'supply.csv'
        self.supply_rows=[self.observation(1100000,10,11,1,0),self.observation(1800000,80,81,2,0),
                          self.observation(2800000,180,181,3,1)]
        self.csv(self.supply,self.supply_rows)
        self.manifest=self.inputs/'delivery.json'
        self.declaration=dict(schema='saturn-kit-audio-delivery-v1',playback=audio.pin(self.playback),
            generated=audio.pin(self.generated),packets=audio.pin(self.packets),capture_metadata=audio.pin(self.capture),
            supply=audio.pin(self.supply))
        self.write(self.manifest,self.declaration)

    def write(self,path,value):
        path.write_text(json.dumps(value),encoding='utf-8')

    def csv(self,path,rows):
        with path.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    def wav(self,path,frames,rate=1000):
        with wave.open(str(path),'wb') as stream:
            stream.setparams((2,2,rate,0,'NONE','not compressed'))
            stream.writeframes(b''.join(struct.pack('<hh',*pair) for pair in frames))

    def observation(self,time,before,after,vblank,pads):
        row={name+suffix:0 for name in ('vtime','samples','vblank','underruns','dropped','marker_pointer')
             for suffix in ('_before','_after')}
        row.update(qpc_before=time,qpc_after=time+10000,samples_before=before,samples_after=after,
                   vblank_before=vblank,vblank_after=vblank,underruns_before=pads,underruns_after=pads,
                   marker='<unmarked>',same_values_across_read=False)
        return row

    def run_analysis(self,**changes):
        args=dict(input=str(self.playback),reference=str(self.generated),delivery=str(self.manifest))
        args.update(changes)
        response=audio.analyze(dict(command='audio-gaps',arguments=args,profile=self.profile),self.outputs/'run')
        return response,json.loads(Path(response['report']).read_text())

    def repin(self,name,path):
        self.declaration[name]=audio.pin(path); self.write(self.manifest,self.declaration)

    def test_parameterized_delivery_and_uncertainty(self):
        reply,result=self.run_analysis()
        scene=result['windows']['scene']
        self.assertTrue(reply['analysis_completed'] and reply['scoring_valid'])
        self.assertEqual(scene['playback_nearzero_results']['2']['holes'],1)
        self.assertEqual(scene['playback_nearzero_results']['2']['total_ms'],20)
        self.assertEqual(scene['secondary_nearzero_results']['2']['holes'],1)
        self.assertEqual(scene['pad_counter_bounds'],[1,1])
        self.assertEqual(scene['generated_frame_inner_window'],[11,180])
        self.assertEqual(scene['generated_sustained_nearzero_runs']['2'],[
            dict(start_frame=140,end_frame=150,milliseconds=10)])
        supply=scene['supply_bounds']
        self.assertEqual((supply['generated_frames_lower'],supply['generated_frames_upper']),(169,171))
        self.assertAlmostEqual(supply['wall_seconds_lower'],.169)
        self.assertAlmostEqual(supply['wall_seconds_upper'],.171)
        self.assertFalse(result['audible_dropout_proven'] or result['waveform_alignment_inferred'])

    def test_timestamp_gap_invalidates_without_filling(self):
        self.packet_rows[2]['qpc_100ns']+=100
        self.csv(self.packets,self.packet_rows); self.repin('packets',self.packets)
        reply,result=self.run_analysis()
        self.assertFalse(reply['scoring_valid'])
        self.assertFalse(result['missing_time_filled'])
        self.assertTrue(result['windows']['scene']['packet_quality']['packet_issues'])

    def test_flag_invalidates_and_totals_must_match(self):
        self.packet_rows[2]['flags']=1; self.csv(self.packets,self.packet_rows); self.repin('packets',self.packets)
        with self.assertRaisesRegex(ValueError,'flag totals'):
            self.run_analysis()
        self.metadata['discontinuity_packets']=1; self.write(self.capture,self.metadata); self.repin('capture_metadata',self.capture)
        reply,result=self.run_analysis()
        self.assertFalse(reply['scoring_valid'])
        self.assertEqual(len(result['windows']['scene']['packet_quality']['flagged_packets']),1)

    def test_boundary_runs_are_not_clipped(self):
        self.wav(self.playback,[(0,0) if i<20 else (100,-100) for i in range(200)])
        self.repin('playback',self.playback)
        _,result=self.run_analysis()
        scored=result['windows']['scene']['playback_nearzero_results']['2']
        self.assertEqual(scored['holes'],0)
        self.assertEqual(len(scored['boundary_runs_omitted_without_clipping']),1)

    def test_no_supply_means_alignment_unknown(self):
        del self.declaration['supply']; self.write(self.manifest,self.declaration)
        _,result=self.run_analysis()
        self.assertFalse(result['windows']['whole_delivery']['generated_alignment_known'])
        self.assertNotIn('pad_counter_bounds',result['windows']['whole_delivery'])

    def test_whole_packet_fractional_end_is_not_rounded(self):
        self.profile['audio']['rate']=44100
        for path in (self.playback,self.generated):
            self.wav(path,[(100,-100)]*200,rate=44100)
        self.repin('playback',self.playback); self.repin('generated',self.generated)
        self.packet_rows=[dict(file_frame=0,frames=200,flags=0,qpc_100ns=1000000)]
        self.csv(self.packets,self.packet_rows); self.repin('packets',self.packets)
        self.metadata.update(rate=44100,packets=1)
        self.write(self.capture,self.metadata); self.repin('capture_metadata',self.capture)
        del self.declaration['supply']; self.write(self.manifest,self.declaration)
        reply,result=self.run_analysis()
        self.assertTrue(reply['scoring_valid'])
        self.assertTrue(result['windows']['whole_delivery']['packet_quality']['complete_outer_coverage'])

    def test_no_delivery_is_nearzero_only(self):
        _,result=self.run_analysis(delivery=None)
        self.assertFalse(result['delivery_evidence'])
        self.assertIsNone(result['scoring_valid'])
        self.assertEqual(result['whole_playback_nearzero_runs']['2'][0]['start_frame'],80)

    def test_changed_pin_refused(self):
        self.packet_rows[2]['flags']=1; self.csv(self.packets,self.packet_rows)
        with self.assertRaisesRegex(ValueError,'pin differs'):
            self.run_analysis()

    def test_wrong_pcm_binding_refused(self):
        self.declaration['playback']=audio.pin(self.generated); self.write(self.manifest,self.declaration)
        with self.assertRaisesRegex(ValueError,'playback identity'):
            self.run_analysis()

    def test_counter_reversal_and_bad_boolean_rejected(self):
        self.supply_rows[-1]['samples_before']=1
        self.csv(self.supply,self.supply_rows); self.repin('supply',self.supply)
        with self.assertRaisesRegex(ValueError,'reversed'):
            self.run_analysis()
        self.supply_rows[-1]['samples_before']=180
        self.supply_rows[-1]['same_values_across_read']='maybe'
        self.csv(self.supply,self.supply_rows); self.repin('supply',self.supply)
        with self.assertRaisesRegex(ValueError,'boolean'):
            self.run_analysis()

    def test_fresh_output_and_input_immutability(self):
        before={path:audio.pin(path) for path in self.inputs.iterdir()}
        self.run_analysis()
        with self.assertRaisesRegex(ValueError,'Fresh'):
            self.run_analysis()
        self.assertEqual(before,{path:audio.pin(path) for path in self.inputs.iterdir()})

    def test_signed_minimum_and_threshold_ceil(self):
        self.wav(self.playback,[(-32768,-32768)]*10+[(2,-2)]*4+[(100,100)]*186)
        _,result=self.run_analysis(delivery=None)
        self.assertEqual(result['whole_playback_nearzero_runs']['2'],[])
        self.profile['audio']['minimum_run_ms']=4
        self.outputs=self.root/'outputs2'; self.outputs.mkdir(); self.profile['output_root']=str(self.outputs)
        _,result=self.run_analysis(delivery=None)
        self.assertEqual(result['whole_playback_nearzero_runs']['2'],[
            dict(start_frame=10,end_frame=14,milliseconds=4)])

    def test_adapter_stdio_protocol(self):
        import subprocess
        request=self.root/'request.json'
        self.write(request,dict(command='audio-gaps',profile=self.profile,arguments=dict(input=str(self.playback))))
        reply=subprocess.run([sys.executable,'-B','-E',str(ADDON/'adapter.py'),'--request',str(request),
                              '--out',str(self.outputs/'protocol')],capture_output=True,text=True,timeout=20)
        self.assertEqual(reply.returncode,0,reply.stderr)
        parsed=json.loads(reply.stdout)
        self.assertEqual(parsed['schema'],'saturn-kit-adapter-result-v1')
        self.assertFalse(parsed['delivery_evidence'])

    def load_optional(self,name):
        old=list(sys.path);sys.path.insert(0,str(ADDON))
        try:
            spec=importlib.util.spec_from_file_location('saturn_kit_test_'+name,ADDON/(name+'.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            return module
        finally:
            sys.path[:]=old

    def test_event_observer_synthetic_guards_no_process(self):
        observer=self.load_optional('windows_audio_events')
        from unittest.mock import patch
        with patch.object(observer.Target,'__init__',side_effect=AssertionError('Live process forbidden')):
            with redirect_stdout(io.StringIO()) as output:
                observer.self_check()
        self.assertIn('no process opened',output.getvalue())

    def test_optional_capture_plan_never_executes(self):
        driver=self.load_optional('windows_capture')
        helper=self.inputs/'fake-helper.txt';helper.write_text('synthetic inert tool',encoding='utf-8')
        binary=self.inputs/'fake-native.txt';binary.write_text('synthetic inert player',encoding='utf-8')
        self.profile['audio_capture']=dict(helper=audio.pin(helper),binary=audio.pin(binary))
        profile_path=self.root/'profile.json';self.write(profile_path,self.profile)
        from unittest.mock import patch
        with patch.object(driver.subprocess,'run',side_effect=AssertionError('Capture forbidden')):
            result=driver.run(profile_path,123,1,self.outputs/'capture-plan')
        self.assertFalse(result['executed'])
        self.assertTrue((self.outputs/'capture-plan/plan.json').is_file())
        self.assertFalse((self.outputs/'capture-plan/playback.wav').exists())
        self.assertEqual(result['command'][4],str(self.outputs))

    def test_optional_capture_pin_and_duration_guards(self):
        driver=self.load_optional('windows_capture')
        self.profile['audio_capture']=dict(helper=audio.pin(self.playback),binary=audio.pin(self.generated))
        profile_path=self.root/'profile.json';self.write(profile_path,self.profile)
        for duration in (0,-1,601,float('nan'),True):
            with self.assertRaisesRegex(ValueError,'duration'):
                driver.prepare(profile_path,123,duration,self.outputs/'guard')
        self.wav(self.generated,[(1,1)]*200)
        with self.assertRaisesRegex(ValueError,'changed'):
            driver.prepare(profile_path,123,1,self.outputs/'guard')


if __name__=='__main__':
    unittest.main()
