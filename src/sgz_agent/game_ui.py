from pathlib import Path
from PIL import Image

from .guard import GuardError
from .visual_guard import check_regions


READ_ONLY_ACTIONS = {'open_map', 'open_more', 'open_settings', 'settings_back',
                     'overview_back', 'coordinate_x', 'coordinate_y',
                     'coordinate_blur', 'coordinate_go', 'role_back', 'season_back'}


class GameUI:
    def __init__(self, controller, vision, config):
        self.controller, self.vision = controller, vision
        self.config = config.ui['navigation']
        self.actions = self.config['actions']
        self.directory = config.data_dir
        self.size = tuple(config.ui['client_size'])
        self._references = {}

    def activate(self):
        self.controller.desktop.activate(self.controller.hwnd)

    def capture(self):
        frame = self.controller.observe()
        if frame.image.size != self.size:
            raise GuardError('calibration_size_changed')
        return frame

    def _reference(self, name):
        if name not in READ_ONLY_ACTIONS:
            raise ValueError('action_not_allowed')
        if name not in self._references:
            path = (self.directory / self.actions[name]['reference']).resolve()
            if not path.is_relative_to(self.directory.resolve()):
                raise ValueError('invalid_reference_path')
            with Image.open(path) as source:
                self._references[name] = source.convert('RGB')
        return self._references[name]

    def matches(self, name, frame=None):
        if name not in self.actions:
            return False
        frame = frame if frame is not None else self.capture()
        action = self.actions[name]
        try:
            check_regions(self._reference(name), frame.image, action['regions'], action['point'])
            return True
        except GuardError:
            return False

    def wait(self, seconds):
        if self.controller.stopped.wait(seconds):
            raise GuardError('stopped')

    def read(self, frame, crops):
        result = self.vision.read_regions(frame, crops)
        if self.controller.stopped.is_set():
            raise GuardError('stopped')
        return result

    def act(self, name, *, value=None, proof=None, extra_regions=()):
        if name not in READ_ONLY_ACTIONS:
            raise ValueError('action_not_allowed')
        action = self.actions[name]
        frame = self.capture()
        point, regions = action['point'], action['regions']
        check_regions(self._reference(name), frame.image, regions, point)
        if proof is not None:
            old, current = proof.observation.window, frame.observation.window
            if (old.hwnd, old.pid, old.exe, old.rect) != (current.hwnd, current.pid, current.exe, current.rect):
                raise GuardError('window_changed')
            # Rebind OCR results to freshly captured pixels before acting.
            regions = [*regions, *extra_regions]
            check_regions(proof.image, frame.image, regions, point)
        if name in {'coordinate_x', 'coordinate_y'}:
            self.controller.enter_coordinate(frame, *point, str(value), regions=regions)
        else:
            self.controller.click(frame, *point, regions=regions)
        self.wait(action.get('settle', 1.5))
