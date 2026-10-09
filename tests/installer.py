"""Exercise first install, concurrent launches, offline cache, checksum refusal."""
import concurrent.futures
import hashlib
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile

BIN = pathlib.Path(os.environ.get('TMUX_SESSION_BIN', 'target/release/tmux-session')).resolve()
ROOT = pathlib.Path(__file__).resolve().parent.parent

def main():
    with tempfile.TemporaryDirectory(prefix='tmux-session-install-') as temp:
        temp = pathlib.Path(temp)
        plugin = temp / 'plugin'; plugin.mkdir()
        shutil.copytree(ROOT / 'scripts', plugin / 'scripts')
        shutil.copy(ROOT / 'VERSION', plugin / 'VERSION')
        fixtures = temp / 'fixtures'; fixtures.mkdir()
        version = (ROOT / 'VERSION').read_text().strip()
        kernel = subprocess.check_output(['uname', '-s'], text=True).strip()
        arch = subprocess.check_output(['uname', '-m'], text=True).strip()
        target = ('aarch64' if arch in ('arm64', 'aarch64') else 'x86_64') + ('-apple-darwin' if kernel == 'Darwin' else '-unknown-linux-musl')
        archive = fixtures / ('tmux-session-v' + version + '-' + target + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar: tar.add(BIN, arcname='tmux-session')
        checksum = fixtures / (archive.name + '.sha256')
        correct = hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n'
        checksum.write_text(correct)
        fakebin = temp / 'fakebin'; fakebin.mkdir()
        curl = fakebin / 'curl'
        curl.write_text('''#!/usr/bin/env bash
set -eu
url= output=
while [ "$#" -gt 0 ]; do
 case "$1" in -o) output="$2"; shift 2 ;; https://*) url="$1"; shift ;; *) shift ;; esac
done
printf '%s\\n' "$url" >> "$FIXTURES/calls"
cp "$FIXTURES/${url##*/}" "$output"
'''); curl.chmod(0o755)
        env = {**os.environ, 'PATH': str(fakebin) + ':' + os.environ['PATH'], 'FIXTURES': str(fixtures)}
        command = [str(plugin / 'scripts/install.sh')]
        def install(): return subprocess.check_output(command, text=True, env=env, timeout=20).strip()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: paths = list(pool.map(lambda _: install(), range(4)))
        assert len(set(paths)) == 1
        assert len((fixtures / 'calls').read_text().splitlines()) == 2, 'downloaded more than once'
        curl.write_text('#!/usr/bin/env bash\nexit 99\n')
        assert install() == paths[0], 'cached install required network'
        os.unlink(paths[0])
        # Restore fake downloader and corrupt the expected checksum.
        curl.write_text('''#!/usr/bin/env bash
set -eu
url= output=
while [ "$#" -gt 0 ]; do
 case "$1" in -o) output="$2"; shift 2 ;; https://*) url="$1"; shift ;; *) shift ;; esac
done
cp "$FIXTURES/${url##*/}" "$output"
''')
        checksum.write_text('0' * 64 + '  ' + archive.name + '\n')
        failed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
        assert failed.returncode != 0 and 'checksum mismatch' in failed.stderr, failed
        assert not pathlib.Path(paths[0]).exists(), 'installed corrupt release'
        checksum.write_text(correct)
        lock = plugin / 'bin' / ('.install-v' + version + '-' + target)
        lock.mkdir(); (lock / 'pid').write_text('99999999\n')
        assert install() == paths[0], 'stale installer lock was not recovered'
        print('installer: first install, concurrency, offline cache, checksum refusal, stale lock PASS')

if __name__ == '__main__': main()
