from core.loop_controller import LoopContext, LoopEvent, LoopPolicy, LoopState, advance_loop


def test_loop_happy_path() -> None:
    policy = LoopPolicy()
    ready = advance_loop(LoopContext(), LoopEvent.PREPARED, policy)
    running = advance_loop(ready.current, LoopEvent.STARTED, policy)
    done = advance_loop(running.current, LoopEvent.COMPLETE, policy)
    assert ready.current.state is LoopState.READY
    assert running.current.state is LoopState.RUNNING
    assert done.current.state is LoopState.DONE
