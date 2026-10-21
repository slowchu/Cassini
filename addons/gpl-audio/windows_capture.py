# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Saturn recomp agent kit contributors
"""Explicit optional process-loopback driver; default is a pinned plan only."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
from windows_host import pin


def need(value,message):
    if not value:
        raise ValueError(message)


def prepare(profile_path,pid,seconds,output):
    profile_path=Path(profile_path).resolve(strict=True)
    profile=json.loads(profile_path.read_text(encoding='utf-8-sig'))
    need(profile.get('schema')=='saturn-kit-profile-v1','Unsupported game profile')
    def resolve(value):
        p=Path(value)
        return (p if p.is_absolute() else profile_path.parent/p).resolve()
    need(type(pid) is int and 0<pid<=0xffffffff,'Explicit positive PID required')
    need(type(seconds) in (int,float) and math.isfinite(seconds) and 0<seconds<=600,'Finite (0,600] duration required')
    roots=[resolve(value) for value in profile['input_roots']]
    policy=profile['audio_capture']
    pins={}
    for key in ('binary','helper'):
        expected=dict(policy[key]); source=resolve(expected['path']); expected['path']=str(source)
        need(any(source.is_relative_to(root) for root in roots),'Pinned utility/image outside input roots')
        need(pin(source)==expected,'Pinned utility/image changed')
        pins[key]=expected
    root=resolve(profile['output_root']);out=resolve(str(output))
    need(out!=root and out.is_relative_to(root) and not out.exists(),'Fresh child of private output root required')
    command=[pins['helper']['path'],str(pid),pins['binary']['path'],str(seconds),str(root),str(out/'playback')]
    return dict(schema='saturn-kit-windows-audio-capture-v1',pid=pid,seconds=seconds,
                command=command,output=str(out),profile=pin(profile_path),pins=pins,
                rate=44100,channels=2,sample_bits=16,executed=False,
                policy='Target process tree only; no desktop fallback; game process is never terminated.')


def run(profile,pid,seconds,output,execute=False):
    plan=prepare(profile,pid,seconds,output)
    out=Path(plan['output']);out.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        with (out/name).open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')
    write('plan.json',plan)
    if not execute:
        return plan
    need(sys.platform=='win32','Process-loopback acquisition requires Windows')
    result=subprocess.run(plan['command'],capture_output=True,text=True,encoding='utf-8',timeout=seconds+20)
    (out/'capture.stdout.txt').write_text(result.stdout,encoding='utf-8')
    (out/'capture.stderr.txt').write_text(result.stderr,encoding='utf-8')
    unchanged=all(pin(Path(row['path']))==row for row in plan['pins'].values())
    receipt=dict(plan,executed=True,returncode=result.returncode,immutable_files_unchanged=unchanged,
                 passed=result.returncode==0 and unchanged)
    if result.returncode==0:
        metadata=json.loads(result.stdout)
        write('capture.json',metadata)
        receipt['artifacts']={name:pin(out/name) for name in ('playback.wav','playback.csv','capture.json')}
    write('receipt.json',receipt)
    return receipt


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--profile',type=Path,required=True)
    cli.add_argument('--pid',type=int,required=True)
    cli.add_argument('--seconds',type=float,required=True)
    cli.add_argument('--out',type=Path,required=True)
    cli.add_argument('--execute',action='store_true')
    args=cli.parse_args()
    result=run(args.profile,args.pid,args.seconds,args.out,args.execute)
    print(json.dumps(result,allow_nan=False))
    return 0 if not args.execute or result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
