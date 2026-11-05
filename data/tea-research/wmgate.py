"""One polite shared gate for all our scripts that call Wikimedia: 170 requests a minute in total, Retry-After honoured across processes."""
import fcntl, os, time
GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw', '.wm-gate')
INTERVAL = 60 / float(os.environ.get('WM_PER_MIN', '170'))

def _update(fn):
    with open(GATE, 'a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            value = float(f.read() or 0)
        except ValueError:
            value = 0.0
        new, result = fn(value)
        f.seek(0); f.truncate(); f.write(str(new))
        return result

def wait():
    while True:
        def step(next_at):
            now = time.time()
            if now >= next_at:
                return now + INTERVAL, 0.0
            return next_at, next_at - now
        delay = _update(step)
        if not delay:
            return
        time.sleep(min(delay, 5))

def pause(seconds):
    _update(lambda next_at: (max(next_at, time.time() + seconds), None))
