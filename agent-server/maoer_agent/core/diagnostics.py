"""Exception locations without messages, source text, locals or credential paths."""
from pathlib import Path


def failure_location(error):
    frames = []
    trace = error.__traceback__
    while trace is not None:
        code = trace.tb_frame.f_code
        frames.append({'file': Path(code.co_filename).name, 'function': code.co_name, 'line': trace.tb_lineno})
        trace = trace.tb_next
    return {'failure_type': type(error).__name__, 'frames': frames}
