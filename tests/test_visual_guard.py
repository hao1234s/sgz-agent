from PIL import Image, ImageDraw
import pytest


def test_text_anchors_ignore_shaded_background_but_reject_changed_glyphs():
    from sgz_agent.visual_guard import check_regions
    reference = Image.new('RGB', (120, 60), (20,20,20))
    draw = ImageDraw.Draw(reference)
    draw.rectangle((12,12,24,26), fill='white')
    draw.rectangle((72,12,84,26), fill='white')
    current = Image.new('RGB', reference.size, (100,100,100))
    draw = ImageDraw.Draw(current)
    draw.rectangle((12,12,24,26), fill='white')
    draw.rectangle((72,12,84,26), fill='white')
    regions = [{'box':[10,10,40,40], 'mode':'ink', 'threshold':180},
               {'box':[70,10,100,40], 'mode':'ink', 'threshold':180}]
    check_regions(reference, current, regions, (20,20))
    ImageDraw.Draw(current).rectangle((28,12,34,26), fill='white')
    with pytest.raises(RuntimeError, match='screen_changed'):
        check_regions(reference, current, regions, (20,20))


def test_empty_text_anchor_is_not_visual_proof():
    from sgz_agent.visual_guard import check_regions
    image = Image.new('RGB', (120,60))
    regions = [{'box':[10,10,40,40], 'mode':'ink', 'threshold':180},
               {'box':[70,10,100,40], 'mode':'ink', 'threshold':180}]
    with pytest.raises(RuntimeError, match='invalid_visual_regions'):
        check_regions(image, image, regions, (20,20))
