"""Upgrade with a pending legacy timer; runtime writes must not repaint body."""
import contextlib
import fcntl
import os
import pty
import select
import shlex
import struct
import subprocess
import tempfile
import termios
import time
from pathlib import Path
from lifecycle import Server, wait, fixture, ROOT

with tempfile.TemporaryDirectory(prefix='session-migrate-') as tmp:
    root = Path(tmp)
    script, settings, events = fixture(root)
    server = Server(root, 'socket', root/'snapshots')
    proc = None
    fd = None
    try:
        server.set('@resurrect-save-script-path', str(script))
        server.set('@tmux-session-interval', '0.1')
        token = 'legacy-pending-token'
        server.set('@tmux-session-pending', token)
        server.set('@tmux-session-next', str(time.time()+20))
        server.set('@tmux-session-initialized', 'yes')
        marker = 'STABLE_SESSION_SELECTION'
        server.cmd('respawn-pane', '-k', "printf '"+marker+"'; sleep 40")
        fd, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',24,100,0,0))
        proc = subprocess.Popen(['tmux','-S',server.socket,'attach'],stdin=slave,stdout=slave,stderr=slave,env={**os.environ,'TERM':'xterm-256color','TMUX':''})
        os.close(slave)
        def drain(seconds):
            data=b'';end=time.monotonic()+seconds
            while time.monotonic()<end:
                if select.select([fd],[],[],.03)[0]:data+=os.read(fd,65536)
            return data
        assert marker.encode() in drain(6)
        # Existing delayed job calls the same launcher after upgrade.
        command = shlex.join([str(ROOT/'scripts/start.sh'),'tick',server.socket,str(server.pid),token])
        server.cmd('run-shell','-b','-d','4',command)
        server.load()
        wait(lambda:server.opt('@tmux-session-state')=='scheduled')
        assert server.opt('@tmux-session-pending')==token, (server.opt('@tmux-session-pending'), server.cmd('show-environment','-gh'))
        data=drain(5)
        assert len(events())==2, events()
        assert server.opt('@tmux-session-pending')!=token
        assert marker.encode() not in data, 'save/runtime migration repainted body'
        # A stale pre-upgrade token must not be resurrected or save twice.
        server.cmd('run-shell',command)
        assert len(events())==2
        assert server.cmd('display','-p','#{TMUX_SESSION_RUNTIME_READY}')=='1'
        print('PASS: pending timer migrated once; one save; stale token rejected; no body redraw')
    finally:
        server.close()
        if proc:
            with contextlib.suppress(subprocess.TimeoutExpired):proc.wait(timeout=3)
            if proc.poll() is None:proc.kill();proc.wait()
        if fd is not None:os.close(fd)
