"""Shared recognition scope: document illustrations are attachments, scans are input."""
IMAGE_POLICY = 'archive_figures_recognize_scanned_tables_v1'
INSTRUCTION = '''
本次识别范围：正文和表格（包括扫描调查表）的文字、勾选项、数值、单位及行列关系继续识别。
PDF 中的照片、剖面图、示意图、工程图和计算草图只做位置归档，不解读图中尺寸、结构关系、裂缝或风险，不从图内生成事实。
区分扫描页面与插图：扫描表仍是表格，不能因整页是位图而跳过；混合页按区域处理，保留正文和表格。
图片只记录原件、物理页、区域坐标、可明确的图题/编号；图题只能用于定位。归属不清时保持未归属，不猜给相邻边坡。
归档图片属于已处理范围，不因未解读图片内容报漏识别、补读或反复放大。无法区分表格与图片时记录待核对，不能擅自跳过。
'''


def normalized_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(type(v) in (int, float) for v in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def figure(page, box, *, caption='', kind='figure'):
    if type(page) is not int or page < 1 or not normalized_box(box):
        raise ValueError('Invalid archived figure page or region')
    return {'page': page, 'bbox': list(box), 'caption': str(caption), 'kind': kind,
            'status': 'archived_without_interpretation', 'slope_binding': 'unconfirmed'}
