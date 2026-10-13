from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from slopekg.geometry import rectangle, project_ocr_boxes
from slopekg.ocr import render_with_pymupdf, make_result_row, with_layout_groups
from slopekg.extractors import split_page_panels


class OcrGeometryTests(unittest.TestCase):
    def test_polygon_inputs_and_invalid_geometry_do_not_crash_layout(self):
        rows=[{'id':'left','bbox':[[20,10],[40,10],[40,30],[20,30]]},
              {'id':'right','bbox':[700,10,740,30]},
              {'id':'unmapped','bbox':None,'bbox_coordinate_space':'unmapped_image_pixels'}]
        panels=split_page_panels(rows)
        self.assertEqual(panels['left'],[rows[0]]);self.assertEqual(panels['right'],[rows[1]])
        for bad in [[0,0,float('nan'),5],[0,0,0,5],['bad'],None]:self.assertIsNone(rectangle(bad))

    def test_actual_rendered_ink_round_trips_to_pdf_under_crop_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for width,height,rotation,region in [(1191,842,0,'full_page'),(1191,842,0,'title_block'),(842,1191,0,'title_block'),(842,1191,90,'full_page'),(1191,842,180,'full_page'),(842,1191,270,'full_page')]:
                with self.subTest(rotation=rotation,region=region,width=width):
                    pdf=root/'source.pdf';d=fitz.open();p=d.new_page(width=width,height=height)
                    expected=fitz.Rect(20,500,50,540) if region=='title_block' and width>height else fitz.Rect(20,40,50,80)
                    p.draw_rect(expected,color=(1,0,0),fill=(1,0,0),width=0);p.set_rotation(rotation);d.save(pdf);d.close()
                    target=root/'render.png';render_with_pymupdf(pdf,1,target,region)
                    with Image.open(target) as im:
                        mask=im.convert('RGB').point(lambda v:v) # use rendered pixels, not recorded transform
                        data=Image.new('L',mask.size);data.putdata([255 if r>200 and g<30 and b<30 else 0 for r,g,b in mask.getdata()]);pixels=list(data.getbbox())
                    row={'id':'r','task_id':'t','bbox':pixels,'image_path':'render.png','bbox_coordinate_space':'image_pixels'}
                    task={'id':'t','path':'source.pdf','page':1,'region':region}
                    out=project_ocr_boxes([row],[task],root)[0]
                    self.assertEqual(out['bbox_coordinate_space'],'pdf_points')
                    for actual,value in zip(out['bbox'],expected):self.assertAlmostEqual(actual,value,delta=.4)
                    self.assertEqual(out['image_bbox'],pixels);self.assertEqual(project_ocr_boxes([out],[task],root),[out])

    def test_legacy_pixel_boxes_use_actual_image_size_and_unknown_images_remain_unmapped(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);d=fitz.open();page=d.new_page(width=1200,height=800)
            page.get_pixmap(matrix=fitz.Matrix(2,2)).save(root/'legacy-full_page-fitz.png');d.save(root/'source.pdf');d.close()
            row={'task_id':'t','bbox':[[100,20],[300,20],[300,60],[100,60]],'image_path':'legacy-full_page-fitz.png'}
            task={'id':'t','path':'source.pdf','page':1};out=project_ocr_boxes([row],[task],root)[0]
            self.assertEqual(out['bbox'],[50,10,150,30]);self.assertEqual(out['bbox_original'],row['bbox'])
            row['image_path']='unknown-crop.png';out=project_ocr_boxes([row],[task],root)[0]
            self.assertIsNone(out['bbox']);self.assertEqual(out['bbox_coordinate_space'],'unmapped_image_pixels')
            self.assertEqual(out['bbox_original'],row['bbox']);self.assertTrue(out['geometry_issue'])

    def test_new_engine_rows_preserve_raw_polygon_and_mark_pixel_units(self):
        polygon=[[10,20],[30,20],[30,40],[10,40]]
        row=make_result_row({'id':'t','document_id':'d','page_id':'p','page':1,'file_name':'source.pdf'},1,'text',.9,polygon,Path('image.png'),Path('.'))
        self.assertEqual(row['bbox_original'],polygon);self.assertEqual(row['bbox'],[10,20,30,40]);self.assertEqual(row['bbox_coordinate_space'],'image_pixels')

    def test_missing_optional_geometry_backend_keeps_raw_evidence_and_does_not_guess_units(self):
        row={'bbox':[10,20,30,40]}
        with patch.dict('sys.modules',{'fitz':None}):
            out=project_ocr_boxes([row],[],Path('.'))[0]
            self.assertEqual(project_ocr_boxes([],[],Path('.')),[])
        self.assertIsNone(out['bbox']);self.assertEqual(out['bbox_original'],row['bbox'])
        self.assertEqual(out['geometry_issue'],'pdf_geometry_backend_unavailable')

    def test_synthetic_join_keeps_both_pixel_boxes_and_raw_item_links(self):
        rows=[{'id':'a','text':'河湾路基凹岸','bbox':[10,20,70,30],'image_bbox':[30,60,210,90],'bbox_original':[30,60,210,90],'bbox_coordinate_space':'pdf_points'},
              {'id':'b','text':'冲刷','bbox':[30,31,50,41],'image_bbox':[90,93,150,123],'bbox_original':[90,93,150,123],'bbox_coordinate_space':'pdf_points'}]
        joined=with_layout_groups(rows)[-1]
        self.assertEqual(joined['source_item_ids'],['a','b']);self.assertEqual(joined['image_bbox'],[30,60,210,123])
        self.assertIsNone(joined['bbox_original']);self.assertEqual(joined['bbox_coordinate_space'],'pdf_points')
