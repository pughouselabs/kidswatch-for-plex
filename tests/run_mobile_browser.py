"""Run the optional existing Chromium toolchain against synthetic HTTPS data only."""
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import URLError


def main():
    root=Path(__file__).resolve().parents[1]
    module=os.environ.get('KIDSWATCH_PLAYWRIGHT_MODULE')
    if not module:
        candidate=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core'
        if candidate.is_dir():
            module=str(candidate)
        else:
            raise SystemExit('Set KIDSWATCH_PLAYWRIGHT_MODULE to the existing playwright-core installation.')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    origin=f'https://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='kidswatch-fake-browser-') as folder:
        cert,key=Path(folder)/'fake-cert.pem',Path(folder)/'fake-key.pem'
        # Ephemeral fake test certificate, never a production or signing credential.
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                        '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost','-keyout',str(key),'-out',str(cert)],
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server=subprocess.Popen([sys.executable,'-m','tests.mobile_dev_server','--origin',origin,'--port',str(port),
                                 '--cert',str(cert),'--key',str(key)],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError(server.stderr.read().decode())
                try:
                    urlopen(origin+'/healthz',context=ssl._create_unverified_context(),timeout=1).close()
                    break
                except URLError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Fake HTTPS fixture did not become ready')
            subprocess.run(['node',str(root/'tests/mobile_browser.cjs')],cwd=root,check=True,
                           env=dict(os.environ,KIDSWATCH_PLAYWRIGHT_MODULE=module,KIDSWATCH_FIXTURE_ORIGIN=origin))
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__=='__main__':
    main()
