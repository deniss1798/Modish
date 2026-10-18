"""Small infrastructure probe with ownership of the complete DB lifecycle.

The normal sync get_db generator and sync dependency overrides that return a
generator, context manager or closable Session are supported. Unknown/async
providers fail closed; they never fall back to a different database. This is a
SELECT 1 check, not a catalogue/schema/AI check or a global connection timeout.
"""
from contextlib import closing, contextmanager
import inspect

from sqlalchemy import text


@contextmanager
def _generator_session(resource):
    yield from resource


def database_probe_succeeds(provider):
    try:
        if inspect.iscoroutinefunction(provider) or inspect.isasyncgenfunction(provider):
            return False
        resource = provider()
        if inspect.iscoroutine(resource):
            resource.close()
            return False
        if inspect.isasyncgen(resource):
            return False
        if inspect.isgenerator(resource):
            manager = _generator_session(resource)
        elif hasattr(resource, '__enter__') and hasattr(resource, '__exit__'):
            manager = resource
        elif hasattr(resource, 'close'):
            manager = closing(resource)
        else:
            return False
        with manager as db:
            with db.execute(text('SELECT 1')) as result:
                healthy = result.scalar_one() == 1
        # Do not publish a healthy response until rollback/close and provider
        # finalization have also completed successfully.
        return healthy
    except Exception:
        # Exceptions may contain credentials, SQL or driver diagnostics. Probe
        # callers receive only a bounded status and no exception/log metadata.
        return False
