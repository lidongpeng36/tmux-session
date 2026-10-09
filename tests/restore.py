"""Save and restore a real resurrect snapshot in isolated tmux servers."""
import json
import os
import pathlib
import tempfile
from lifecycle import Server, wait

ROOT=pathlib.Path(__file__).resolve().parent.parent
RESURRECT=pathlib.Path(os.environ.get('TMUX_RESURRECT_PLUGIN',ROOT/'vendor/resurrect')).resolve()

def main():
    assert (RESURRECT/'resurrect.tmux').is_file(),'set TMUX_RESURRECT_PLUGIN or checkout vendor/resurrect'
    with tempfile.TemporaryDirectory(prefix='tmux-session-restore-') as temp:
        directory=pathlib.Path(temp);snapshots=directory/'snapshots';cwd=directory/'directory with spaces';cwd.mkdir()
        first=Server(directory,'original',snapshots)
        second=None
        try:
            first.set('@resurrect-capture-pane-contents','on')
            first.cmd('run-shell',str(RESURRECT/'resurrect.tmux'))
            first.cmd('split-window','-d','-c',str(cwd),"printf 'restore marker 中文\\n'; sleep 120")
            first.cmd('new-session','-d','-s','extra','-c',str(cwd),'sleep 120')
            wait(lambda:first.cmd('display','-p','-t','work:0.1','#{pane_current_path}')==str(cwd.resolve()) and first.cmd('display','-p','-t','extra:0.0','#{pane_current_path}')==str(cwd.resolve()))
            before=sorted(first.cmd('list-panes','-a','-F','#{session_name}\t#{pane_index}\t#{pane_current_path}').splitlines())
            first.set('@tmux-session-interval','0.02');first.load()
            wait(lambda:bool(first.opt('@tmux-session-last-success')))
            first.set('@tmux-session-interval','0');first.load()
            assert (snapshots/'last').is_file()
            assert 'pane\t' in (snapshots/'last').read_text()
            first.close()
            second=Server(directory,'restored',snapshots)
            second.set('@resurrect-capture-pane-contents','on')
            second.cmd('run-shell',str(RESURRECT/'resurrect.tmux'))
            second.set('@tmux-session-interval','0');second.set('@tmux-session-restore','on');second.load()
            wait(lambda:len(second.cmd('list-panes','-a','-F','#{pane_id}').splitlines())==3)
            try:
                wait(lambda:sorted(second.cmd('list-panes','-a','-F','#{session_name}\t#{pane_index}\t#{pane_current_path}').splitlines())==before)
            except AssertionError:
                raise AssertionError({'before':before,'after':second.cmd('list-panes','-a','-F','#{session_name}\t#{pane_index}\t#{pane_current_path}\t#{pane_current_command}'),'state':second.opt('@tmux-session-state'),'error':second.opt('@tmux-session-error'),'saved':(snapshots/'last').read_text()})
            after=sorted(second.cmd('list-panes','-a','-F','#{session_name}\t#{pane_index}\t#{pane_current_path}').splitlines())
            assert after==before,(before,after)
            count=len(second.cmd('list-panes','-a','-F','#{pane_id}').splitlines())
            for _ in range(3):second.load()
            assert not second.opt('@tmux-session-restore-pending')
            assert len(second.cmd('list-panes','-a','-F','#{pane_id}').splitlines())==count
            wait(lambda:'restore marker' in second.cmd('capture-pane','-p','-t','work:0.1'))
            print(json.dumps({'real_save_restore':True,'sessions_panes_directories':True,'captured_contents_restored':True,'reload_did_not_restore_again':True}))
        finally:
            first.close()
            if second:second.close()

if __name__=='__main__':main()
