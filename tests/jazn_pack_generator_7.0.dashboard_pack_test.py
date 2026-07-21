from __future__ import annotations
import importlib.util, sys, tempfile, threading, time
from pathlib import Path
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size

SCRIPT=Path(__file__).with_name('jazn_pack_generator_7.0.py')
spec=importlib.util.spec_from_file_location('jazn_pack_v7_packtest', SCRIPT)
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)

class Out(DummyOutput):
    def get_size(self): return Size(rows=32, columns=120)

def wait(pred, timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if pred(): return
        time.sleep(.03)
    raise AssertionError('timeout')

with tempfile.TemporaryDirectory(prefix='jazn-v7-dashboard-pack-') as raw:
    base=Path(raw); root=base/'root'; out=base/'out'; (root/'latka_jazn').mkdir(parents=True)
    (root/'latka_jazn'/'version.py').write_text(
        'DISTRIBUTION_VERSION="1.0.0"\nPACKAGE_VERSION="v1.0.0.7"\nPACKAGE_RELEASE_NAME="Dashboard test"\n', encoding='utf-8')
    (root/'run.py').write_text('print("run")\n',encoding='utf-8')
    (root/'main.py').write_text('print("main")\n',encoding='utf-8')
    (root/'SOURCE_PROVENANCE.json').write_text('{}\n',encoding='utf-8')
    (root/'README.md').write_text('test\n',encoding='utf-8')
    state=mod.InteractiveState(source=root,out_dir=out,profile='system',archive_format='independent',ui_mode='kursorowy',compatibility_checks=True)
    debug={}; box={}
    with create_pipe_input() as pipe:
        def run(): box['result']=mod.cursor_dashboard(state,_input=pipe,_output=Out(),_debug_state=debug)
        t=threading.Thread(target=run,daemon=True); t.start(); wait(lambda:debug.get('ready'))
        pipe.send_text('\r'); wait(lambda:debug.get('panel_mode')=='action')
        pipe.send_text('\r'); wait(lambda:debug.get('panel_mode') in {'result','error'} and not debug.get('busy'), timeout=30)
        assert debug['panel_mode']=='result', debug
        sidecars=list(out.glob('*.package.json')); assert len(sidecars)==1
        report=mod.verify_package_sidecar(sidecars[0]); assert report['ok']
        pipe.send_text('\x18'); wait(lambda:debug.get('panel_mode')=='exit_choice'); pipe.send_text('\x1b[A\r')
        t.join(5); assert not t.is_alive()
    print('DASHBOARD PACK PASS', report)
