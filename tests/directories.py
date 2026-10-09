"""Verify locks use the same default and expanded directories as resurrect."""
import json
import pathlib
import socket
import tempfile
from lifecycle import Server, wait

with tempfile.TemporaryDirectory(prefix='tmux-session-dirs-') as temp:
    root=pathlib.Path(temp);results={}
    for kind in ['xdg','legacy','expanded']:
        case=root/kind;case.mkdir();home=case/'home';home.mkdir();xdg=case/'xdg'
        if kind=='legacy':(home/'.tmux/resurrect').mkdir(parents=True)
        server=Server(case,'default')
        try:
            server.cmd('set-environment','-g','HOME',str(home))
            server.cmd('set-environment','-g','XDG_DATA_HOME',str(xdg))
            server.set('@tmux-session-interval','0')
            if kind=='expanded':server.set('@resurrect-dir','$HOME/snapshots/$HOSTNAME')
            expected={'xdg':xdg/'tmux/resurrect','legacy':home/'.tmux/resurrect','expanded':home/'snapshots'/socket.gethostname()}[kind]
            server.load();wait(lambda:server.opt('@tmux-session-state')=='disabled')
            assert (expected/'.tmux-session-owner').is_file(),{'expected':str(expected),'resolved':server.opt('@tmux-session-directory'),'raw':server.opt('@resurrect-dir'),'owners':[str(p) for p in case.rglob('.tmux-session-owner')]}
            if kind=='xdg':assert not (home/'.tmux/resurrect').exists()
            results[kind]=True
        finally:server.close()
    print(json.dumps(results))
