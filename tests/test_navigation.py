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


@pytest.mark.parametrize('original, expected, reads', [('(591.832)',(591,832),2),('(590,832)',(590,832),1)])
def test_coordinate_retry_uses_new_ocr_evidence_and_never_replaces_valid_coordinates(original,expected,reads):
    from sgz_agent.navigation import Navigator
    class UI:
        config={'target_crops':[], 'target_coordinate_retry_crops':[{'box':[1,1,9,9],'binary_threshold':120}],
                'target_coordinate_region':[.4,.2,.6,.4], 'target_name_region':[.1,.1,.2,.2]}
        count=0
        def capture(self): return object()
        def matches(self,name,frame): return True
        def read(self,frame,crops):
            self.count+=1
            return [box(original if self.count==1 else '(591,832)',[.42,.24,.55,.32])]
    ui=UI();frame,screen=Navigator(ui,{}).read_target()
    assert screen.coordinate==expected and ui.count==reads


@pytest.mark.parametrize('right, score, region, extra, valid', [
    ('测试甲', .99, [.18,.08,.24,.12], [], True),
    ('测试乙', .99, [.18,.08,.24,.12], [], False),
    ('测试甲', .94, [.18,.08,.24,.12], [], False),
    ('测试甲', .99, [.5,.5,.6,.6], [], False),
    ('', .99, [.18,.08,.24,.12], [], False),
    ('测试甲', .99, [.18,.08,.24,.12], [box('测试乙',[.18,.08,.24,.12])], False),
])
def test_target_read_requires_unique_confident_role_text_in_each_calibrated_region(right,score,region,extra,valid):
    from sgz_agent.navigation import Navigator
    class UI:
        config={'target_crops':[], 'target_coordinate_region':[.4,.2,.6,.4], 'target_name_region':[.4,.1,.6,.2],
                'target_role_checks':[{'text':'示例','region':[.08,.07,.13,.13]},
                                      {'text':'测试甲','region':[.17,.07,.25,.13]}]}
        def capture(self): return object()
        def matches(self,name,frame): return True
        def read(self,frame,crops):
            return [box('(592,832)',[.42,.24,.55,.32]), box('示例',[.09,.08,.12,.12]),
                    box(right,region,score), *extra]
    if valid:
        frame,screen=Navigator(UI(),{}).read_target()
        assert screen.coordinate==(592,832) and screen.fields['map_role_verified'] is True
    else:
        with pytest.raises(RuntimeError,match='map_role_mismatch'):
            Navigator(UI(),{}).read_target()
