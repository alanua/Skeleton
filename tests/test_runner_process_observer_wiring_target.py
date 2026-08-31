from pathlib import Path


def test_observer_is_wired_only_to_real_codex_spawn() -> None:
    text = Path("scripts/runner_poll_github_tasks.py").read_text(encoding="utf-8")
    assert text.count("observe_process_spawn=True") == 1
    expected = '''            codex_code, codex_output = run_command(
                codex_exec_command(task_content, workdir, task),
                cwd=workdir,
                observe_process_spawn=True,
            )
'''
    assert expected in text
    handoff = '''        handoff_status_code, handoff_status_output = run_command(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=workdir,
        )
'''
    assert handoff in text
