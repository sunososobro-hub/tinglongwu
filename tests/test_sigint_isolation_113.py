"""#113: a test that swaps the SIGINT handler (gui.App() does, process-wide) must not leak it into later tests.
Under xvfb the real-Tk GUI tests run before test_speak_ctrlc_427, whose simulated Ctrl+C (`_thread.interrupt_main`)
then went to the GUI's handler instead of raising KeyboardInterrupt, and the test hung until --timeout=120.
The two tests below run in file order: the first leaks a handler, the second checks it was restored."""
import _thread
import signal
import time

import pytest


def test_a_test_swaps_the_sigint_handler_like_gui_app():
    signal.signal(signal.SIGINT, lambda *_: None)  # gui/app.py: signal.signal(SIGINT, lambda *_: setattr(...))
    assert signal.getsignal(signal.SIGINT) is not signal.default_int_handler


def test_the_next_test_gets_ctrl_c_as_keyboardinterrupt_again():
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    with pytest.raises(KeyboardInterrupt):
        _thread.interrupt_main()
        time.sleep(1)  # the pending signal is handled at the next bytecode boundary
