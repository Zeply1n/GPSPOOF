import asyncio
import json
from pathlib import Path
import pytest
from autotune import AutoTuner
from models import Coordinate, DeviceInfo, EngineState, PersistentState
from pmd_backend import PmdBackend, Session
from route_engine import nudge, parse_gpx
from state_store import AtomicJsonStore, StateStore
from device_manager import DeviceManager
from gps_engine import GpsEngine

def test_coordinate_validation():
    Coordinate(90, 180)
    with pytest.raises(ValueError): Coordinate(90.1, 0)
    with pytest.raises(ValueError): Coordinate(0, -181)

def test_atomic_state_and_restore(tmp_path):
    store=StateStore(tmp_path/"state.json"); state=PersistentState(True, 1.2, 3.4)
    store.write(state); assert store.load().coordinate == Coordinate(1.2,3.4)
    assert json.loads((tmp_path/"state.json").read_text())["active"] is True
    assert not list(tmp_path.glob(".state.json.*"))

def test_nudge_geodesic():
    origin=Coordinate(70, 20); east=nudge(origin,"E",100)
    assert east.longitude > origin.longitude and abs(east.latitude-origin.latitude)<.001

def test_gpx():
    route=parse_gpx(b'<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>')
    assert route == [Coordinate(1,2),Coordinate(3,4)]

def test_autotune_disabled_never_changes_or_persists(tmp_path):
    path=tmp_path/"tune.json"; t=AutoTuner(False,str(path),(5,2,1),2,8,300)
    t.report_bounce(); t.report_stable(); assert t.interval == 2 and t.recycle_age == 300 and not path.exists()

def test_autotune_feedback(tmp_path):
    t=AutoTuner(True,str(tmp_path/"tune.json"),(5,2,1),2,8,300); t.select({"udid":"x"})
    t.report_bounce(); assert t.interval == 1 and t.recycle_age == 225
    t.report_stable(); assert t.public()["stable"] == 1

def config(pmd="fake", interval=.04, timeout=.1, recycle=.2):
    return {"restore_on_start":True,"shutdown_timeout":.2,"reconnect_initial":.01,"reconnect_max":.05,"reconnect_multiplier":2,
            "device_poll":.01,"pmd_version":pmd,"burst":True,"burst_delays":(0,.01),"proactive_recycling":True,
            "watchdog_check":.02,"watchdog_timeout":.15,"route_tick":.01}

def make_engine(tmp_path, backend=None, enabled=False):
    tuner=AutoTuner(enabled,str(tmp_path/"auto.json"),(5,.04,.02),.04,.1,.2)
    return GpsEngine(state_store=StateStore(tmp_path/"state.json"),devices=DeviceManager(True,None),
                     backend=backend or PmdBackend(True),tuner=tuner,config=config())

@pytest.mark.asyncio
async def test_initial_burst_periodic_and_clean_stop(tmp_path):
    e=make_engine(tmp_path); await e.start(); await e.set_location(Coordinate(1,2)); await asyncio.sleep(.12)
    assert e.metrics.set_successes >= 3 and e.state == EngineState.SPOOF_ACTIVE
    await e.stop(False)

class BrokenLocation:
    async def set(self,*args): raise ConnectionError("poison")
    async def clear(self): pass
class BrokenSession(Session):
    async def __aenter__(self): self.rsd=self.dvt=object();self.location=BrokenLocation();return self
    async def __aexit__(self,*args): pass
class CountingBackend(PmdBackend):
    def __init__(self): super().__init__(True); self.sessions=0
    def session(self,d): self.sessions+=1; return BrokenSession(d,True)

@pytest.mark.asyncio
async def test_set_exception_rebuilds_entire_session(tmp_path):
    backend=CountingBackend();e=make_engine(tmp_path,backend);await e.start();await e.set_location(Coordinate(1,2));await asyncio.sleep(.09)
    assert backend.sessions >= 2 and e.metrics.set_failures >= 2
    await e.stop(False)

class SlowLocation:
    async def set(self,*args): await asyncio.sleep(1)
    async def clear(self): pass
class SlowSession(BrokenSession):
    async def __aenter__(self): self.rsd=self.dvt=object();self.location=SlowLocation();return self
class SlowBackend(CountingBackend):
    def session(self,d): self.sessions+=1;return SlowSession(d,True)

@pytest.mark.asyncio
async def test_set_timeout_rebuild(tmp_path):
    backend=SlowBackend();e=make_engine(tmp_path,backend);await e.start();await e.set_location(Coordinate(1,2));await asyncio.sleep(.23)
    assert e.metrics.set_timeouts >= 1 and backend.sessions >= 2
    await e.stop(False)

@pytest.mark.asyncio
async def test_clear_prevents_resurrection(tmp_path):
    e=make_engine(tmp_path);await e.start();await e.set_location(Coordinate(1,2));await asyncio.sleep(.04);await e.clear()
    assert StateStore(tmp_path/"state.json").load().active is False
    await e.stop(False);e2=make_engine(tmp_path);assert e2.desired.active is False

@pytest.mark.asyncio
async def test_bounce_tunes_and_rebuilds(tmp_path):
    e=make_engine(tmp_path,enabled=True);await e.start();await e.set_location(Coordinate(1,2));await asyncio.sleep(.03)
    await e.report_bounce();assert e.metrics.bounce_reports==1 and e.tuner.interval==.02
    await e.stop(False)

@pytest.mark.asyncio
async def test_proactive_recycle(tmp_path):
    e=make_engine(tmp_path);await e.start();await e.set_location(Coordinate(1,2));await asyncio.sleep(.27)
    assert e.metrics.proactive_recycles >= 1
    await e.stop(False)

@pytest.mark.asyncio
async def test_same_device_waits_without_switching(tmp_path):
    dm=DeviceManager(True,"missing")
    assert await dm.selected() is None and dm.selected_udid=="missing"
