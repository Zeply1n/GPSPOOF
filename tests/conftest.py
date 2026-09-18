"""Minimal fallback async runner when pytest-asyncio is not installed."""
import asyncio
import inspect
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run this coroutine test in an event loop")

@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" in pyfuncitem.keywords and inspect.iscoroutinefunction(pyfuncitem.obj):
        kwargs={name: pyfuncitem.funcargs[name] for name in inspect.signature(pyfuncitem.obj).parameters}
        asyncio.run(pyfuncitem.obj(**kwargs)); return True
    return None
