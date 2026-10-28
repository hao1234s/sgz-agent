from PIL import ImageChops
from .guard import GuardError


def check_regions(reference, current, regions, point):
    """Compare calibrated target and context anchors, excluding known animated scenery."""
    if reference.size != current.size:
        raise GuardError('geometry_changed')
    if not isinstance(regions, (list, tuple)) or len(regions) < 2:
        raise GuardError('invalid_visual_regions')
    width, height = current.size
    boxes = []
    for region in regions:
        box = region.get('box', [])
        tolerance = region.get('max_delta', 0)
        if (len(box) != 4 or any(type(v) is not int for v in box) or
                not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height) or
                type(tolerance) is not int or not 0 <= tolerance <= 48):
            raise GuardError('invalid_visual_regions')
        boxes.append(tuple(box))
        if region.get('mode', 'delta') == 'ink':
            threshold = region.get('threshold', 180)
            if type(threshold) is not int or not 120 <= threshold <= 220:
                raise GuardError('invalid_visual_regions')
            before = reference.crop(box).convert('L').point(lambda p: 255 if p >= threshold else 0, '1')
            after = current.crop(box).convert('L').point(lambda p: 255 if p >= threshold else 0, '1')
            if before.convert('L').histogram()[255] < 16:
                raise GuardError('invalid_visual_regions')
            union = ImageChops.logical_or(before, after).convert('L').histogram()[255]
            changed = ImageChops.logical_xor(before, after).convert('L').histogram()[255]
            if changed / union > .02:
                raise GuardError('screen_changed')
        elif region.get('mode', 'delta') == 'delta':
            difference = ImageChops.difference(reference.crop(box).convert('RGB'), current.crop(box).convert('RGB'))
            if max(hi for lo, hi in difference.getextrema()) > tolerance:
                raise GuardError('screen_changed')
        else:
            raise GuardError('invalid_visual_regions')
    x, y = point
    target = boxes[0]
    if len(set(boxes)) < 2 or not (target[0] <= x < target[2] and target[1] <= y < target[3]):
        raise GuardError('invalid_visual_regions')
