"""Count helper starts during idle status refreshes; does not measure CPU time."""
import argparse
import collections
import contextlib
import fcntl
import hashlib
import datetime
import json
import os
import pathlib
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time

ROOT=pathlib.Path(__file__).resolve().parent.parent
BIN=pathlib.Path(os.environ.get('TMUX_SESSION_BIN',ROOT/'target/release/tmux-session')).resolve()

def run(mode,directory,continuum,seconds):
    real_tmux=shutil.which('tmux');fake=directory/'helpers';fake.mkdir()
    gate=directory/'gate';gate.write_text('0');log=directory/'events.jsonl'
    helpers=['tmux','date','cut','sed','grep','awk','uname','ps','wc','mkdir','rmdir','sleep']
    for name in helpers:
        real=shutil.which(name)
        if not real:continue
        wrapper=f'''#!/usr/bin/env python3
import json,os,pathlib,sys,time
gate=pathlib.Path({str(gate)!r})
if os.getppid()!={os.getpid()} and gate.read_text()=='1':
 with open({str(log)!r},'a') as f:f.write(json.dumps({{'helper':{name!r},'time':time.time()}})+'\\n')
os.execv({real!r},[{real!r}]+sys.argv[1:])
'''
        (fake/name).write_text(wrapper);(fake/name).chmod(0o755)
    env={**os.environ,'PATH':str(fake)+':'+os.environ['PATH'],'TERM':'xterm-256color'}
    socket=str(directory/'socket')
    def tmux(*args):return subprocess.check_output([real_tmux,'-S',socket,*args],text=True,env=env,stderr=subprocess.STDOUT,timeout=10).strip()
    client=None;master=None
    try:
        tmux('-f','/dev/null','new-session','-d','-s','measure','sleep 120')
        tmux('set','-g','status-interval','5')
        tmux('set','-g','@resurrect-dir',str(directory/'snapshots'))
        tmux('set','-g','@continuum-save-last-timestamp',str(int(time.time())))
        tmux('set','-g','@resurrect-save-script-path',shutil.which('true'))
        if mode=='old':tmux('set','-g','status-right',f'#({continuum}/scripts/continuum_save.sh)')
        else:
            tmux('set','-g','@tmux-session-bin',str(BIN))
            tmux('run-shell',str(ROOT/'tmux-session.tmux'))
            end=time.monotonic()+6
            while tmux('display','-p','#{TMUX_SESSION_STATE}')!='scheduled':
                assert time.monotonic()<end,(tmux('display','-p','#{TMUX_SESSION_STATE}'),tmux('show-option','-gqv','@tmux-session-error'));time.sleep(.05)
        master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,100,0,0))
        client=subprocess.Popen([real_tmux,'-S',socket,'attach','-t','measure'],stdin=slave,stdout=slave,stderr=slave,env=env);os.close(slave)
        def drain():
            if select.select([master],[],[],0)[0]:os.read(master,65536)
        end=time.monotonic()+4
        while time.monotonic()<end:drain();time.sleep(.05)
        gate.write_text('1');started=time.monotonic();end=started+seconds
        while time.monotonic()<end:drain();time.sleep(.05)
        elapsed=time.monotonic()-started;gate.write_text('0')
        rows=[json.loads(s) for s in log.read_text().splitlines()] if log.exists() else []
        return {'mode':mode,'elapsed_seconds':elapsed,'observed_helpers':dict(collections.Counter(r['helper'] for r in rows)),'helper_starts':len(rows)}
    finally:
        with contextlib.suppress(subprocess.CalledProcessError):tmux('kill-server')
        if client:client.wait(timeout=3)
        if master is not None:os.close(master)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--continuum',required=True,type=pathlib.Path);parser.add_argument('--seconds',type=float,default=15);parser.add_argument('--output',type=pathlib.Path);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tmux-session-overhead-') as temp:
        root=pathlib.Path(temp);runs=[]
        for mode in ['old','new']:
            directory=root/mode;directory.mkdir();runs.append(run(mode,directory,args.continuum.resolve(),args.seconds))
    result={'measured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'binary_version':subprocess.check_output([str(BIN),'--version'],text=True).strip(),'binary_sha256':hashlib.sha256(BIN.read_bytes()).hexdigest(),'status_interval_seconds':5,'save_interval_minutes':15,'attached_clients':1,'measurement':'helper exec starts through logging wrappers; observer uses absolute tmux path; startup excluded; not a CPU/latency/energy benchmark','runs':runs}
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
