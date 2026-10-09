"""Real tmux scheduling, exclusive snapshot ownership, and exit-path checks."""
import contextlib
import json
import os
import pathlib
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BIN = pathlib.Path(os.environ.get('TMUX_SESSION_BIN', ROOT/'target/release/tmux-session')).resolve()
PLUGIN = ROOT/'tmux-session.tmux'

class Server:
    def __init__(self, directory, name, snapshot=None):
        self.socket=str(directory/name)
        subprocess.run(['tmux','-S',self.socket,'-f','/dev/null','new-session','-d','-s','work','sleep 120'],check=True)
        self.pid=int(self.cmd('display','-p','#{pid}'))
        self.set('@tmux-session-bin',str(BIN))
        if snapshot is not None:self.set('@resurrect-dir',str(snapshot))
    def cmd(self,*args):return subprocess.check_output(['tmux','-S',self.socket,*args],text=True,stderr=subprocess.STDOUT,timeout=8).strip()
    def set(self,key,value):return self.cmd('set','-g',key,value)
    def opt(self,key):return self.cmd('show-option','-gqv',key)
    def load(self):self.cmd('run-shell',str(PLUGIN))
    def close(self):
        with contextlib.suppress(subprocess.CalledProcessError):self.cmd('kill-server')

def wait(check,seconds=8):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if check():return
        time.sleep(.03)
    raise AssertionError('condition timed out')

def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False

def fixture(directory):
    script=directory/'save fixture.py';log=directory/'events';settings=directory/'settings.json'
    settings.write_text(json.dumps({'sleep':.15,'fail':False}))
    script.write_text('''#!/usr/bin/env python3
import json,os,pathlib,time
base=pathlib.Path(__file__).parent
cfg=json.loads((base/'settings.json').read_text())
log=base/'events'
def record(kind):
 with log.open('a') as f:f.write(json.dumps({'kind':kind,'pid':os.getpid(),'time':time.time()})+'\\n')
record('start')
time.sleep(cfg['sleep'])
record('end')
raise SystemExit(3 if cfg['fail'] else 0)
''')
    script.chmod(0o755)
    def events():return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return script,settings,events

def main():
    results={}
    with tempfile.TemporaryDirectory(prefix='tmux-session-tests-') as temp:
        directory=pathlib.Path(temp);script,settings,events=fixture(directory)
        servers=[]
        try:
            first=Server(directory,'first',directory/'snapshots');servers.append(first)
            first.set('@resurrect-save-script-path',str(script));first.set('@tmux-session-interval','0.04')
            first.load();wait(lambda:first.opt('@tmux-session-state')=='scheduled')
            token=first.opt('@tmux-session-pending')
            for _ in range(5):first.load()
            assert first.opt('@tmux-session-pending')==token,'reload duplicated timer'
            assert not events(),'saving did not wait for interval'
            wait(lambda:len(events())>=2)
            wait(lambda:bool(first.opt('@tmux-session-last-success')))
            assert [r['kind'] for r in events()]==['start','end']
            results['reload_single_timer']=True
            first.set('@tmux-session-interval','0');first.load()
            time.sleep(2.8);assert len(events())==2,'disabled autosave still saved'
            wait(lambda:not first.opt('@tmux-session-pending'))
            results['disable']=True
            # Failure is observable, retries are spaced, and never overlap.
            settings.write_text(json.dumps({'sleep':.2,'fail':True}))
            first.set('@tmux-session-interval','0.02');first.load()
            wait(lambda:first.opt('@tmux-session-state')=='save-failed')
            assert 'exited' in first.opt('@tmux-session-error')
            settings.write_text(json.dumps({'sleep':1.4,'fail':False}))
            wait(lambda:len(events())>=6)
            wait(lambda:first.opt('@tmux-session-state')=='scheduled')
            first.set('@tmux-session-interval','0');first.load()
            data=events();assert [r['kind'] for r in data]==['start','end']*3,data
            assert data[4]['time']-data[3]['time']>=1.1,data
            results['failure_retry_no_overlap']=True
            # A second live server cannot claim the first server's save directory.
            other=Server(directory,'other',directory/'snapshots');servers.append(other)
            other.set('@resurrect-save-script-path',str(script));other.load()
            wait(lambda:other.opt('@tmux-session-state')=='configuration-error')
            assert 'another live' in other.opt('@tmux-session-error')
            results['shared_directory_refused']=True
            custom=Server(directory,'custom');servers.append(custom);custom.load()
            wait(lambda:custom.opt('@tmux-session-state')=='configuration-error')
            assert 'explicit' in custom.opt('@tmux-session-error')
            results['custom_server_requires_directory']=True
            # Timers disappear when the last session exits, without a Rust daemon.
            first.close();wait(lambda:not alive(first.pid))
            quiet=Server(directory,'quiet',directory/'quiet-snapshots');servers.append(quiet)
            quiet.set('@resurrect-save-script-path',str(script));quiet.load()
            wait(lambda:quiet.opt('@tmux-session-state')=='scheduled')
            count=len(events());quiet.cmd('kill-session');wait(lambda:not alive(quiet.pid))
            assert len(events())==count
            results['last_session_cancels_timer']=True
            # Exit while saving: both Rust job and save child must disappear.
            active=Server(directory,'active',directory/'active-snapshots');servers.append(active)
            active.set('@resurrect-save-script-path',str(script));active.set('@tmux-session-interval','0.02')
            settings.write_text(json.dumps({'sleep':30,'fail':False}))
            active.load();wait(lambda:len(events())>count)
            child=events()[-1]['pid'];active.cmd('kill-session');wait(lambda:not alive(active.pid))
            wait(lambda:not alive(child),seconds=3)
            results['active_save_exit_cleanup']=True
        finally:
            for server in servers:server.close()
    print(json.dumps(results))

if __name__=='__main__':main()
