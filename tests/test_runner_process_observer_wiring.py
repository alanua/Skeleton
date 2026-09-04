from pathlib import Path
from types import SimpleNamespace
import time
from scripts import runner_poll_github_tasks as runner
from core.runner_process_observer import TARGET_DENIED_PATH

def test_observer_wraps_spawn(monkeypatch,tmp_path:Path):
    captured={}
    def fake_run(args,**kwargs):
        captured['args']=list(args); captured['kwargs']=dict(kwargs)
        trace=Path(args[args.index('-o')+1])
        trace.write_text(f'4242 {time.time()+.001:.6f} openat(AT_FDCWD, "{TARGET_DENIED_PATH}", O_RDONLY) = -1 EACCES (Permission denied)\n')
        return SimpleNamespace(returncode=13,stdout='',stderr='provider failed')
    monkeypatch.setattr(runner.shutil,'which',lambda n:'/usr/bin/strace' if n=='strace' else None)
    monkeypatch.setattr(runner.subprocess,'run',fake_run)
    code,out=runner.run_command(['/usr/local/bin/codex','exec'],cwd=tmp_path,input='synthetic-input',observe_process_spawn=True)
    assert captured['args'][0]=='strace'; assert code==13
    assert 'RUNNER_PROCESS_DIAGNOSTIC=' in out and TARGET_DENIED_PATH in out
    assert 'synthetic-input' not in out
    assert not list(tmp_path.glob('.runner-codegen-trace-*.log'))

def test_observer_no_tracer(monkeypatch,tmp_path:Path):
    captured={}
    def fake_run(args,**kwargs): captured['args']=list(args); return SimpleNamespace(returncode=0,stdout='ok',stderr='')
    monkeypatch.setattr(runner.shutil,'which',lambda n:None)
    monkeypatch.setattr(runner.subprocess,'run',fake_run)
    code,out=runner.run_command(['/usr/local/bin/codex','exec'],cwd=tmp_path,observe_process_spawn=True)
    assert captured['args']==['/usr/local/bin/codex','exec']; assert code==0
    assert 'RUNNER_PROCESS_DIAGNOSTIC_CAPTURE=tracer_unavailable' in out
