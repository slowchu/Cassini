# SPDX-License-Identifier: MIT
from .startup import check_runtime
check_runtime()

import argparse
import json
import sys
from .api import run


def main():
    p = argparse.ArgumentParser(description='Cassini: immutable inputs, fresh private outputs')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'diff', 'replay', 'capture-original', 'hw-report', 'saves', 'audio-gaps'):
        s = sub.add_parser(name)
        s.add_argument('--profile', required=True); s.add_argument('--out', required=True)
        if name in ('inspect', 'saves', 'audio-gaps'): s.add_argument('--input', required=True)
        if name == 'inspect':
            s.add_argument('--kind', choices=['auto', 'native', 'ymir-state', 'original-fixture'], default='auto')
            s.add_argument('--paired-capture')
        elif name == 'diff':
            s.add_argument('--before', required=True); s.add_argument('--after', required=True); s.add_argument('--premise')
        elif name == 'hw-report': s.add_argument('--inputs', nargs='+', required=True)
        elif name == 'replay':
            s.add_argument('--recording', required=True); s.add_argument('--frames', required=True, type=int); s.add_argument('--execute', action='store_true')
        elif name == 'capture-original':
            s.add_argument('--recipe', required=True); s.add_argument('--execute', action='store_true')
        elif name == 'saves':
            s.add_argument('--format', choices=['auto', 'bup', 'native'], default='auto')
            s.add_argument('--to', choices=['native', 'ymir'], help='Explicit conversion into fresh output only')
        elif name == 'audio-gaps':
            s.add_argument('--threshold-ms', type=float); s.add_argument('--reference')
            s.add_argument('--delivery'); s.add_argument('--events')
    server = sub.add_parser('mcp', help='stdio MCP server; no app configuration is modified')
    server.add_argument('--profile', required=True, help='Trusted profile fixed and pinned at server startup')
    a = vars(p.parse_args()); command = a.pop('command')
    if command == 'mcp':
        from .mcp_server import main as serve
        return serve(a['profile'])
    try:
        print(json.dumps(run(command, {k: v for k, v in a.items() if v is not None}), ensure_ascii=False)); return 0
    except Exception as error:
        print(json.dumps(dict(error=str(error))), file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
