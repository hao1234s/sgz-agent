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


def test_coordinate_crop_binarization_preserves_original_evidence():
    from sgz_agent.vision import Vision
    vision=Vision.__new__(Vision)
    source=Image.new('RGB',(20,10),(90,90,90))
    source.putpixel((5,5),(220,220,220))
    def read(frame):
        assert frame.image.getpixel((0,0))==(0,0,0)
        assert frame.image.getpixel((20,20))==(255,255,255)
        return []
    vision.read=read
    vision.read_regions(SimpleNamespace(image=source),[{'box':[0,0,20,10],'scale':4,'binary_threshold':120}])
    assert source.getpixel((0,0))==(90,90,90)
