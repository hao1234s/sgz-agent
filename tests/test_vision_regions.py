from types import SimpleNamespace
from PIL import Image
from sgz_agent.models import TextBox


def test_crop_ocr_coordinates_are_mapped_back_to_original_frame():
    from sgz_agent.vision import Vision
    vision=Vision.__new__(Vision)
    sizes=[]
    def read(frame):
        sizes.append(frame.image.size)
        return [TextBox('(12,34)',.99,(.2,.25,.8,.75))]
    vision.read=read
    result=vision.read_regions(SimpleNamespace(image=Image.new('RGB',(1000,500))),[{'box':[100,100,300,200],'scale':3}])
    assert sizes==[(600,300)]
    assert result[0].box==(.14,.25,.26,.35)
