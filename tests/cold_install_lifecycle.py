"""A slow first download must be cancelled when its tmux server exits."""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

def main():
    with tempfile.TemporaryDirectory(prefix='tmux-session-cold-life-') as temp:
        directory = pathlib.Path(temp)
        plugin = directory / 'plugin'; plugin.mkdir()
        shutil.copytree(ROOT / 'scripts', plugin / 'scripts')
        for name in ['VERSION', 'tmux-session.tmux']: shutil.copy(ROOT / name, plugin / name)
        fakebin = directory / 'fake'; fakebin.mkdir()
        log = directory / 'download.pid'
        curl = fakebin / 'curl'
        curl.write_text('#!/usr/bin/env python3\nimport os,time\nopen(' + repr(str(log)) +
                        ',"w").write(str(os.getpid()))\ntime.sleep(30)\n')
        curl.chmod(0o755)
        socket = directory / 'socket'
        config = directory / 'tmux.conf'
        config.write_text("run-shell '" + str(plugin / 'tmux-session.tmux') + "'\n")
        env = {**os.environ, 'PATH': str(fakebin) + ':' + os.environ['PATH']}
        def tmux(*args):
            return subprocess.check_output(['tmux', '-S', str(socket), *args], env=env,
                                           text=True, stderr=subprocess.STDOUT, timeout=5)
        def alive(pid):
            try: os.kill(pid, 0); return True
            except ProcessLookupError: return False
        pid = None
        try:
            tmux('-f', str(config), 'new-session', '-d', 'sleep 60')
            deadline = time.monotonic() + 5
            while not log.exists() and time.monotonic() < deadline: time.sleep(.05)
            assert log.exists(), 'download did not start'
            pid = int(log.read_text())
            tmux('kill-server')
            deadline = time.monotonic() + 3
            while alive(pid) and time.monotonic() < deadline: time.sleep(.05)
            assert not alive(pid), 'downloader survived shutdown'
            # The installer trap should also remove its owned download/lock directories.
            deadline = time.monotonic() + 3
            while list((plugin / 'bin').glob('.*')) and time.monotonic() < deadline: time.sleep(.05)
            assert not list((plugin / 'bin').glob('.*')), 'installer cache left incomplete downloads/locks'
            print(json.dumps({'cold_download_cancelled': True, 'partial_install_cleaned': True}))
        finally:
            try: tmux('kill-server')
            except subprocess.CalledProcessError: pass
            if pid and alive(pid): os.kill(pid, signal.SIGTERM)

if __name__ == '__main__': main()
