from dataclasses import asdict
import re

from .recognition import Recognizer, ScreenState, normalize, parse_coordinate


def in_region(text, region):
    x, y = (text.box[0] + text.box[2])/2, (text.box[1] + text.box[3])/2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def entry_coordinate(texts, regions):
    result = []
    for region in regions:
        values = {normalize(t.text) for t in texts if t.score >= .95 and in_region(t, region)}
        if len(values) != 1:
            return None
        value = values.pop()
        if re.fullmatch(r'[0-9]{1,5}', value) is None:
            return None
        result.append(int(value))
    return tuple(result) if len(result) == 2 else None


def target_fields(texts, name_region, *, detail_region=None):
    names = [normalize(t.text) for t in texts if t.score >= .9 and in_region(t,name_region)]
    name = names[0] if len(names) == 1 else None
    level = re.search(r'(?:([0-9]{1,2})级|(?:Lv\.?|等级)([0-9]{1,2}))', name or '', re.I)
    category = next((word for word in ('铁矿','木材','石料','粮食','空地','营帐','箭塔','拒马') if word in (name or '')), None)
    good = [normalize(t.text) for t in texts if t.score >= .9
            and (detail_region is None or not in_region(t, detail_region))]
    yields = {int(m[1]) for value in good if (m := re.fullmatch(r'\+([0-9]+)/小时',value))}
    defenders = {tuple(map(int,m.groups())) for value in good if (m := re.fullmatch(r'([0-9]+)[xX×]([0-9]+)',value))}
    garrison = None
    if any('守军兵力' in value for value in good) and len(defenders) == 1:
        troops,groups = defenders.pop()
        garrison = {'troops_per_group':troops,'groups':groups}
    bandit = None
    if detail_region is not None:
        details = [normalize(t.text) for t in texts if t.score >= .95 and in_region(t, detail_region)]
        titles = {m.groups() for value in details if (m := re.fullmatch(
            r'(聚义军|贼寇|山贼|黄巾军|蛮族部队)(?:(?:等级|Lv\.?|级)?([0-9]{1,2})级?)?', value, re.I))}
        if len(titles) == 1:
            title, bandit_level = titles.pop()
            forces = {tuple(map(int, m.groups())) for value in details
                      if (m := re.fullmatch(r'([0-9]+)[xX×]([0-9]+)', value))}
            force = None
            if '守军兵力' in details and len(forces) == 1:
                troops, groups = forces.pop()
                force = {'troops_per_group': troops, 'groups': groups}
            bandit = {'name': title, 'level': int(bandit_level) if bandit_level else None, 'garrison': force}
    return {'name':name, 'category':category, 'level':int(next(v for v in level.groups() if v)) if level else None,
            'yield_per_hour':next(iter(yields)) if len(yields)==1 else None, 'garrison':garrison,
            'bandit':bandit, 'bandit_checked':detail_region is not None,
            'bandit_status':'present' if bandit is not None else 'unknown',
            'visible_text':[asdict(t) for t in texts], 'recognizer':'PP-OCRv5-mobile'}


class Navigator:
    def __init__(self, ui, identity):
        self.ui, self.identity = ui, identity

    def verify_identity(self):
        # Stop can interrupt the map panel's slide animation; never treat that
        # intermediate layout as the starting page of a resumed task.
        self.ui.wait(2.2)
        if not self.ui.matches('settings_back'):
            # Army lists can return to a city list before returning to the map.
            # Each back action still requires its own calibrated visual proof.
            for _ in range(4):
                back = next((name for name in ('army_list_back', 'city_list_back', 'city_interior_back',
                            'overview_back', 'role_back', 'season_back') if self.ui.matches(name)), None)
                if back is None:
                    break
                self.ui.act(back)
            else:
                raise RuntimeError('navigation_recovery_limit')
            if not self.ui.matches('open_settings'):
                self.ui.act('open_more')
            self.ui.act('open_settings')
        frame = self.ui.capture()
        texts = self.ui.read(frame, self.ui.config['identity_crops'])
        checks = self.identity.get('checks', [])
        good = [t for t in texts if t.score >= .9]
        if not checks or not all(Recognizer._matches(check, good) for check in checks):
            raise RuntimeError('identity_mismatch')
        self.ui.act('settings_back', proof=frame,
                    extra_regions=self.ui.config.get('identity_proof_regions', []))
        return {key: self.identity.get(key) for key in ('role_name','role_id','server','region','season','world')}

    def goto(self, target):
        if not self.ui.matches('overview_back'):
            self.ui.act('open_map')
        self.ui.act('coordinate_x', value=target[0])
        self.ui.act('coordinate_y', value=target[1])
        self.ui.act('coordinate_blur')
        frame = self.ui.capture()
        texts = self.ui.read(frame, self.ui.config['entry_crops'])
        if entry_coordinate(texts, self.ui.config['entry_regions']) != target:
            raise RuntimeError('entry_coordinate_mismatch')
        self.ui.act('coordinate_go', proof=frame, extra_regions=self.ui.config['entry_proof_regions'])

    def read_target(self):
        frame = self.ui.capture()
        if not self.ui.matches('open_map', frame):
            raise RuntimeError('map_or_role_changed')
        texts = self.ui.read(frame, [*self.ui.config['target_crops'],
                                     *self.ui.config.get('target_detail_crops', [])])
        role_checks = self.ui.config.get('target_role_checks', [])
        for check in role_checks:
            readings = [t for t in texts if in_region(t, check['region'])]
            if (len(readings) != 1 or readings[0].score < .95 or
                    normalize(readings[0].text) != normalize(check['text'])):
                raise RuntimeError('map_role_mismatch')
        coord_texts = [t for t in texts if in_region(t, self.ui.config['target_coordinate_region']) and t.score >= .9]
        coords = {parse_coordinate(t.text) for t in coord_texts}
        coordinate = next(iter(coords)) if len(coords) == 1 else None
        processing = 'original'
        retry_crops = self.ui.config.get('target_coordinate_retry_crops', [])
        if coordinate is None and not any(p is not None for p in coords) and retry_crops:
            retry_texts = self.ui.read(frame, retry_crops)
            retry_coords = {parse_coordinate(t.text) for t in retry_texts
                            if t.score >= .9 and in_region(t,self.ui.config['target_coordinate_region'])}
            if len(retry_coords) == 1 and None not in retry_coords:
                coordinate = next(iter(retry_coords))
                processing = 'contrast_retry'
            texts.extend(retry_texts)
        fields = target_fields(texts,self.ui.config['target_name_region'],
                               detail_region=self.ui.config.get('target_detail_region'))
        fields['coordinate_processing'] = processing
        fields['map_role_verified'] = bool(role_checks)
        return frame, ScreenState('details', tuple(texts), coordinate, False, fields)

    def inspect(self, target):
        self.goto(target)
        for index in range(2):
            frame, screen = self.read_target()
            if screen.coordinate != target:
                raise RuntimeError('coordinate_mismatch')
            if index == 0:
                self.ui.wait(.2)
        return frame, screen
