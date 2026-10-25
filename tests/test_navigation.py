from types import SimpleNamespace
from PIL import Image
import pytest
from sgz_agent.models import TextBox


def box(value, region, score=.99):
    return TextBox(value, score, tuple(region))


def test_entry_coordinate_requires_exact_digits_in_both_fields():
    from sgz_agent.navigation import entry_coordinate
    regions = [[.7,.7,.8,.8],[.8,.7,.9,.8]]
    assert entry_coordinate([box('591',[.72,.72,.75,.76]),box('830',[.82,.72,.85,.76])],regions) == (591,830)
    for bad in ['830.5','83O','830|','830123']:
        assert entry_coordinate([box('591',[.72,.72,.75,.76]),box(bad,[.82,.72,.85,.76])],regions) is None
    assert entry_coordinate([box('591',[.72,.72,.75,.76]),box('830',[.82,.72,.85,.76],.4)],regions) is None


def test_navigation_does_not_click_go_if_entry_readback_disagrees():
    from sgz_agent.navigation import Navigator
    class UI:
        config = {'entry_regions': [[.7,.7,.8,.8],[.8,.7,.9,.8]], 'entry_crops': [], 'entry_proof_regions': []}
        def __init__(self): self.actions=[]
        def matches(self, name): return name=='overview_back'
        def act(self, name, **kwargs): self.actions.append(name)
        def capture(self): return object()
        def read(self, frame, crops): return [box('591',[.72,.72,.75,.76]),box('828',[.82,.72,.85,.76])]
    ui = UI()
    with pytest.raises(RuntimeError,match='entry_coordinate_mismatch'):
        Navigator(ui,{}).goto((591,830))
    assert ui.actions == ['coordinate_x','coordinate_y','coordinate_blur']


def test_identity_requires_matching_role_server_and_region_before_leaving_settings():
    from sgz_agent.navigation import Navigator
    checks = [{'text':'^115150264$', 'region':[.2,.7,.5,.8]}, {'text':'^PK26086$','region':[.2,.8,.5,.9]}]
    class UI:
        config={'identity_crops':[]}
        def __init__(self): self.actions=[]
        def wait(self, seconds): pass
        def matches(self,name): return name=='settings_back'
        def capture(self): return object()
        def read(self,frame,crops): return [box('115150265',[.3,.72,.4,.77]),box('PK26086',[.3,.82,.4,.87])]
        def act(self,name,**kwargs): self.actions.append(name)
    ui=UI()
    with pytest.raises(RuntimeError,match='identity_mismatch'):
        Navigator(ui,{'checks':checks}).verify_identity()
    assert ui.actions == []


def test_inspection_requires_two_same_target_readings():
    from sgz_agent.navigation import Navigator
    from sgz_agent.recognition import ScreenState
    nav=Navigator(SimpleNamespace(wait=lambda seconds:None),{})
    nav.goto=lambda target:None
    readings=iter([(object(),ScreenState('details',coordinate=(591,830))),
                   (object(),ScreenState('details',coordinate=(591,831)))])
    nav.read_target=lambda:next(readings)
    with pytest.raises(RuntimeError,match='coordinate_mismatch'):
        nav.inspect((591,830))


def test_visible_chinese_land_level_and_resources_are_parsed_without_guessing_missing_fields():
    from sgz_agent.navigation import target_fields
    region=[.4,.1,.6,.3]
    readings=[box('4级铁矿',[.45,.15,.55,.2]),box('+600/小时',[.2,.4,.3,.5]),
              box('守军兵力',[.1,.5,.2,.6]),box('5400x1',[.3,.5,.4,.6])]
    fields=target_fields(readings,region)
    assert (fields['name'],fields['category'],fields['level'],fields['yield_per_hour'])==('4级铁矿','铁矿',4,600)
    assert fields['garrison']=={'troops_per_group':5400,'groups':1}
    unknown=target_fields([box('空地',[.45,.15,.55,.2])],region)
    assert unknown['level'] is None and unknown['garrison'] is None and unknown['yield_per_hour'] is None


def test_recovery_allows_interrupted_transition_to_settle_before_reading_identity():
    from sgz_agent.navigation import Navigator
    class UI:
        config={'identity_crops':[]}
        settled=False
        def wait(self, seconds): self.settled=seconds>=2
        def matches(self,name):
            if not self.settled: raise RuntimeError('transition_not_settled')
            return name=='settings_back'
        def capture(self): return object()
        def read(self,frame,crops): return [box('1234',[.3,.72,.4,.77])]
        def act(self,name,**kwargs): assert name=='settings_back'
    Navigator(UI(),{'checks':[{'text':'^1234$','region':[.2,.7,.5,.8]}]}).verify_identity()
