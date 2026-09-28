from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from typing import Any, Iterable

from .storage import ensure_dir, write_json
from .slope_attribution import suggest_slope_attributions


MULTIMODAL_SCHEMA_VERSION = "1.0.0"

ASSET_SUFFIXES: dict[str, str] = {
    ".jpg": "VisualAsset",
    ".jpeg": "VisualAsset",
    ".png": "VisualAsset",
    ".webp": "VisualAsset",
    ".bmp": "VisualAsset",
    ".tif": "RasterAsset",
    ".tiff": "RasterAsset",
    ".geotiff": "RasterAsset",
    ".las": "PointCloudAsset",
    ".laz": "PointCloudAsset",
    ".copc": "PointCloudAsset",
    ".obj": "ThreeDModelAsset",
    ".osgb": "ThreeDModelAsset",
    ".gltf": "ThreeDModelAsset",
    ".glb": "ThreeDModelAsset",
    ".ply": "ThreeDModelAsset",
    ".dwg": "Drawing",
    ".dxf": "Drawing",
    ".csv": "DataTableAsset",
    ".tsv": "DataTableAsset",
    ".xlsx": "DataTableAsset",
    ".xls": "DataTableAsset",
    ".parquet": "DataTableAsset",
}

SPATIAL_ASSET_TYPES = {"RasterAsset", "PointCloudAsset", "ThreeDModelAsset"}
PAGE_PATTERN = re.compile(r"_p(?P<page>\d{4})-(?P<region>[^.]+)", re.IGNORECASE)


def multimodal_contract() -> dict[str, Any]:
    """Return the reviewable contract used before real spatial data is available."""
    return {
        "schema_name": "slopekg_multimodal_asset_layer",
        "schema_version": MULTIMODAL_SCHEMA_VERSION,
        "status": "implemented_contract_waiting_for_external_samples",
        "principles": [
            "原始文件不写入知识图谱，只保存稳定ID、位置、校验和及关系",
            "派生结果必须通过derived_from和processing_run_id追溯到输入与处理版本",
            "缺少坐标系、采集时间或空间范围时保留unknown，不做推断",
            "视觉识别候选不能直接写成正式风险事实",
        ],
        "entity_types": {
            "VisualAsset": {"required": ["id", "href", "sha256", "media_type"], "examples": ["现场照片", "PDF图片", "图纸截图", "监测曲线"]},
            "AcquisitionEvent": {"required": ["id", "acquisition_time", "platform_or_device"], "conditional": ["flight_id", "operator"]},
            "SpatialFootprint": {"required": ["id", "crs", "geometry_or_bbox"]},
            "RasterAsset": {"required": ["id", "href", "sha256", "crs"], "conditional": ["resolution", "bands", "nodata"]},
            "PointCloudAsset": {"required": ["id", "href", "sha256", "crs"], "conditional": ["point_count", "scale", "offset"]},
            "ThreeDModelAsset": {"required": ["id", "href", "sha256", "crs"], "conditional": ["texture_assets", "lod"]},
            "MonitoringSeries": {"required": ["id", "slope_id", "time_field", "value_field", "unit"], "conditional": ["quality_flag", "sensor_id", "crs"]},
            "DerivedMetric": {"required": ["id", "metric_name", "value", "unit", "derived_from", "processing_run_id"]},
            "ProcessingRun": {"required": ["id", "software", "version", "parameters", "started_at"]},
            "QualityReport": {"required": ["id", "target_id", "checks", "generated_at"]},
            "AnnotationSet": {"required": ["id", "task", "target_ids", "review_status"]},
        },
        "relations": [
            "HAS_VISUAL_ASSET",
            "HAS_SPATIAL_ASSET",
            "HAS_MONITORING_SERIES",
            "ACQUIRED_DURING",
            "COVERS_FOOTPRINT",
            "DERIVED_FROM",
            "GENERATED_BY",
            "HAS_QUALITY_REPORT",
            "HAS_ANNOTATION_SET",
            "OBSERVES_SLOPE",
        ],
        "missing_value_semantics": ["present", "unknown", "not_observed", "not_applicable"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_href(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _sidecar_metadata(path: Path) -> tuple[dict[str, Any], str | None]:
    candidates = [path.with_suffix(path.suffix + ".asset.json"), path.with_suffix(".asset.json")]
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"资产元数据必须是JSON对象: {candidate}")
        return payload, candidate.name
    return {}, None


def _image_metadata(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ExifTags

        with Image.open(path) as image:
            result: dict[str, Any] = {"width_px": image.width, "height_px": image.height}
            exif = image.getexif()
            if exif:
                names = {ExifTags.TAGS.get(key, str(key)): value for key, value in exif.items()}
                captured = names.get("DateTimeOriginal") or names.get("DateTime")
                if captured:
                    result["exif_datetime"] = str(captured)
            return result
    except Exception:
        return {}


def _visual_subtype(path: Path, origin: str) -> str:
    text = path.name.lower()
    if "title_block" in text:
        return "drawing_title_block"
    if "full_page" in text:
        return "pdf_full_page"
    if any(token in text for token in ("chart", "curve", "plot", "曲线")):
        return "monitoring_chart"
    if any(token in text for token in ("map", "ortho", "dem", "dsm", "遥感", "正射")):
        return "map_or_raster_preview"
    if any(token in text for token in ("photo", "现场", "病害", "巡查")):
        return "site_photo"
    if origin == "pdf_derived":
        return "pdf_page_region"
    return "unclassified_visual"


def _asset_origin(path: Path) -> str:
    normalized = path.as_posix().lower()
    if "/output/demo/assets/ocr_pages/" in normalized or "/assets/ocr_pages/" in normalized:
        return "pdf_derived"
    if "/data/" in normalized:
        return "external_or_source"
    return "project_generated"


def describe_asset(path: Path, project_root: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    inferred_type = ASSET_SUFFIXES[suffix]
    metadata, sidecar_name = _sidecar_metadata(path)
    asset_type = str(metadata.get("asset_type") or inferred_type)
    if asset_type not in set(ASSET_SUFFIXES.values()) | {"MonitoringSeries", "DerivedMetric"}:
        raise ValueError(f"不支持的asset_type={asset_type}: {path}")
    href = _relative_href(path, project_root)
    stable_key = href.casefold().encode("utf-8")
    stat = path.stat()
    origin = str(metadata.get("origin") or _asset_origin(path))
    page_match = PAGE_PATTERN.search(path.name)
    source_document_ref = path.name[: page_match.start()] if page_match else None
    record: dict[str, Any] = {
        "id": str(metadata.get("id") or f"asset-{hashlib.sha256(stable_key).hexdigest()[:16]}"),
        "asset_type": asset_type,
        "subtype": metadata.get("subtype"),
        "origin": origin,
        "href": href,
        "file_name": path.name,
        "extension": suffix,
        "media_type": metadata.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": stat.st_size,
        "sha256": _sha256(path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "slope_id": metadata.get("slope_id"),
        "acquisition_event_id": metadata.get("acquisition_event_id"),
        "acquisition_time": metadata.get("acquisition_time"),
        "crs": metadata.get("crs"),
        "spatial_footprint": metadata.get("spatial_footprint") or metadata.get("bbox"),
        "source_document_id": metadata.get("source_document_id"),
        "source_document_ref": metadata.get("source_document_ref") or source_document_ref,
        "source_page": metadata.get("source_page", int(page_match.group("page")) if page_match else None),
        "derived_from": metadata.get("derived_from", []),
        "processing_run_id": metadata.get("processing_run_id"),
        "roles": metadata.get("roles", ["data"]),
        "metadata_sidecar": sidecar_name,
        "expected_sha256": metadata.get("sha256"),
        "slope_review_status": metadata.get("slope_review_status", "unreviewed"),
    }
    if record["subtype"] is None and asset_type == "VisualAsset":
        record["subtype"] = _visual_subtype(path, origin)
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        record.update(_image_metadata(path))
        if not record["acquisition_time"] and record.get("exif_datetime"):
            record["acquisition_time"] = record["exif_datetime"]
    return record


def link_assets_to_documents(assets: list[dict[str, Any]], documents: Iterable[dict[str, Any]]) -> None:
    """Resolve PDF-derived images by SHA, then by an exact unique source stem."""
    document_rows = list(documents)
    prefixes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in document_rows:
        digest = str(document.get("source_sha256") or "").lower()
        if len(digest) >= 10:
            prefixes[digest[:10]].append(document)
    documents_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in document_rows:
        file_name = str(document.get("file_name") or "").strip()
        if file_name:
            documents_by_stem[Path(file_name).stem.casefold()].append(document)
    for asset in assets:
        if asset.get("source_document_id") or asset.get("origin") != "pdf_derived":
            continue
        source_ref = str(asset.get("source_document_ref") or "").strip().casefold()
        digest_match = re.search(r"_([0-9a-f]{10})$", source_ref)
        matches = prefixes.get(digest_match.group(1), []) if digest_match else []
        if not digest_match:
            source_ref = str(asset.get("source_document_ref") or "").strip().casefold()
            matches = documents_by_stem.get(source_ref, [])
        if len(matches) != 1:
            continue
        document = matches[0]
        asset["source_document_id"] = document.get("id")
        asset["source_document_title"] = document.get("title")
        if isinstance(asset.get("derived_from"), list) and all(isinstance(item, str) for item in asset["derived_from"]):
            asset["derived_from"] = list(dict.fromkeys([*asset["derived_from"], str(document.get("id"))]))


def validate_asset(
    record: dict[str, Any], *, project_root: Path | None = None,
    documents: dict[str, dict[str, Any]] | None = None,
    slope_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def require(field: str, severity: str, message: str) -> None:
        if record.get(field) in (None, "", [], {}):
            issues.append({"asset_id": str(record.get("id")), "field": field, "severity": severity, "message": message})

    def error(field: str, message: str) -> None:
        issues.append({"asset_id": str(record.get("id")), "field": field, "severity": "error", "message": message})

    for field in ("id", "href", "sha256", "media_type"):
        require(field, "error", f"资产缺少必填字段 {field}")
        if record.get(field) is not None and not isinstance(record[field], str):
            error(field, f"{field} 必须是字符串")
    if record.get("sha256") and not re.fullmatch(r"[0-9a-fA-F]{64}", str(record["sha256"])):
        error("sha256", "SHA-256 必须为64位十六进制值")
    if record.get("expected_sha256") is not None and str(record["expected_sha256"]).lower() != str(record.get("sha256", "")).lower():
        error("sha256", "文件内容与元数据中声明的SHA-256不一致")
    page = record.get("source_page")
    if page is not None and (type(page) is not int or page < 1):
        error("source_page", "来源页码必须是从1开始的整数")
    for field in ("source_document_id", "slope_id"):
        if record.get(field) is not None and not isinstance(record[field], str):
            error(field, f"{field} 必须是字符串")
    for field in ("derived_from", "roles"):
        value = record.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            error(field, f"{field} 必须是字符串列表")
    document_id = record.get("source_document_id")
    if documents is not None and isinstance(document_id, str) and document_id:
        document = documents.get(document_id)
        if document is None:
            error("source_document_id", "来源文档不在当前文档清单中")
        else:
            pages = document.get("pages")
            if type(page) is int and type(pages) is int and page > pages:
                error("source_page", f"来源页码超出文档总页数 {pages}")
            ref = re.search(r"_([0-9a-f]{10})$", str(record.get("source_document_ref") or "").lower())
            digest = str(document.get("source_sha256") or "").lower()
            if ref and digest and not digest.startswith(ref.group(1)):
                error("source_document_id", "图片来源指纹与当前文档不一致，需重新解析")
    slope_id = record.get("slope_id")
    if slope_ids is not None and isinstance(slope_id, str) and slope_id and slope_id not in slope_ids:
        error("slope_id", "边坡编号不在当前图谱中")
    if project_root is not None and isinstance(record.get("href"), str) and record["href"]:
        path = Path(record["href"])
        path = path if path.is_absolute() else project_root / path
        try:
            if not path.is_file():
                error("href", "资产文件不存在或不是普通文件")
            else:
                if _sha256(path) != str(record.get("sha256", "")).lower():
                    error("sha256", "资产文件内容与记录的SHA-256不一致")
                if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    try:
                        from PIL import Image
                        with Image.open(path) as img:
                            img.verify()
                    except ImportError:
                        issues.append({"asset_id": str(record.get("id")), "field": "image", "severity": "warning", "message": "缺少Pillow，未验证图片内容"})
                    except Exception:
                        error("image", "图片无法解码或文件内容损坏")
        except OSError as exc:
            error("href", f"资产文件无法读取：{exc}")
    if record.get("asset_type") in SPATIAL_ASSET_TYPES:
        require("crs", "warning", "空间资产缺少坐标参考系，不能可靠匹配边坡")
        require("spatial_footprint", "warning", "空间资产缺少覆盖范围，不能做空间检索")
        require("acquisition_time", "warning", "空间资产缺少采集时间，不能开展时序比较")
    if record.get("origin") == "pdf_derived":
        require("source_page", "warning", "PDF派生视觉资产缺少来源页码")
        require("source_document_id", "warning", "PDF派生视觉资产尚未关联到来源文档")
    return issues


def discover_assets(roots: Iterable[Path], project_root: Path, *, issues: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    files: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or path.name.endswith(".asset.json") or path.suffix.lower() not in ASSET_SUFFIXES:
                continue
            files[str(path.resolve()).casefold()] = path
    assets = []
    for path in sorted(files.values(), key=lambda item: item.as_posix().casefold()):
        try:
            assets.append(describe_asset(path, project_root))
        except (OSError, ValueError, TypeError) as exc:
            if issues is None:
                raise
            issues.append({"asset_id": _relative_href(path, project_root), "field": "metadata", "severity": "error", "message": f"无法建立资产记录：{exc}"})
    return assets


def _normalise_href(value: str) -> str:
    href = str(value or "").strip().replace("\\", "/")
    while href.startswith("./"):
        href = href[2:]
    return href.casefold()


def filter_current_pdf_evidence(
    assets: Iterable[dict[str, Any]], active_pdf_asset_hrefs: Iterable[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    """Exclude stale OCR render caches when an authoritative active set exists.

    The files remain on disk for traceability. Only generated images under an
    ``assets/ocr_pages`` directory are filtered; independent source assets and
    explicitly catalogued spatial data remain eligible for the catalog.
    """
    rows = list(assets)
    if active_pdf_asset_hrefs is None:
        return rows, 0
    active = {_normalise_href(value) for value in active_pdf_asset_hrefs if str(value or "").strip()}
    kept: list[dict[str, Any]] = []
    excluded = 0
    for asset in rows:
        href = _normalise_href(str(asset.get("href") or ""))
        is_ocr_render = asset.get("origin") == "pdf_derived" and "/assets/ocr_pages/" in f"/{href}"
        if is_ocr_render and href not in active:
            excluded += 1
            continue
        kept.append(asset)
    return kept, excluded


def build_multimodal_outputs(
    roots: Iterable[Path],
    project_root: Path,
    output_dir: Path,
    *,
    documents: Iterable[dict[str, Any]] = (),
    slope_ids: set[str] | None = None,
    active_pdf_asset_hrefs: Iterable[str] | None = None,
    slopes: Iterable[dict[str, Any]] = (),
    text_blocks: Iterable[dict[str, Any]] = (),
    ocr_results: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    discovered_assets = discover_assets(roots, project_root, issues=issues)
    assets, stale_pdf_evidence_excluded = filter_current_pdf_evidence(discovered_assets, active_pdf_asset_hrefs)
    document_rows = list(documents)
    link_assets_to_documents(assets, document_rows)
    document_index = {str(row["id"]): row for row in document_rows}
    issues.extend(issue for asset in assets for issue in validate_asset(
        asset, project_root=project_root, documents=document_index, slope_ids=slope_ids,
    ))
    duplicate_ids = {
        asset_id: count
        for asset_id, count in Counter(str(asset.get("id") or "") for asset in assets).items()
        if asset_id and count > 1
    }
    issues.extend(
        {
            "asset_id": asset_id,
            "field": "id",
            "severity": "error",
            "message": f"资产ID重复出现 {count} 次；请修正 .asset.json 中的 id",
        }
        for asset_id, count in sorted(duplicate_ids.items())
    )
    generated_at = datetime.now().isoformat(timespec="seconds")
    invalid_ids = {row["asset_id"] for row in issues if row["severity"] == "error"}
    for asset in assets:
        asset["validation_status"] = "invalid" if asset["id"] in invalid_ids else "valid"
    attribution = suggest_slope_attributions(
        assets, documents=document_rows, slopes=slopes,
        text_blocks=text_blocks, ocr_results=ocr_results,
    )
    attribution_by_id = {row["asset_id"]: row for row in attribution["assets"]}
    for asset in assets:
        suggestion = attribution_by_id.get(asset["id"])
        if suggestion:
            asset["slope_candidate_status"] = suggestion["status"]
            asset["slope_candidates"] = suggestion["candidates"]
    catalog = {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": "slopekg-multimodal-assets",
        "description": "SlopeKG多模态资产目录；采用STAC式字段组织，不宣称未经验证的数据具备定量分析能力。",
        "generated_at": generated_at,
        "schema_version": MULTIMODAL_SCHEMA_VERSION,
        "count": len(assets),
        "assets": assets,
    }
    issue_counts = Counter(issue["severity"] for issue in issues)
    type_counts = Counter(asset["asset_type"] for asset in assets)
    origin_counts = Counter(asset["origin"] for asset in assets)
    subtype_counts = Counter(str(asset.get("subtype") or "unknown") for asset in assets)
    quality = {
        "generated_at": generated_at,
        "schema_version": MULTIMODAL_SCHEMA_VERSION,
        "status": "ready_for_catalog_review" if not issue_counts.get("error") else "has_blocking_errors",
        "summary": {
            "assets": len(assets),
            "assets_discovered": len(discovered_assets),
            "stale_pdf_evidence_excluded": stale_pdf_evidence_excluded,
            "asset_type_counts": dict(type_counts),
            "origin_counts": dict(origin_counts),
            "subtype_counts": dict(subtype_counts),
            "issue_counts": dict(issue_counts),
            "spatial_assets": sum(1 for asset in assets if asset["asset_type"] in SPATIAL_ASSET_TYPES),
            "assets_linked_to_document": sum(1 for asset in assets if asset.get("source_document_id")),
            "assets_linked_to_slope": sum(1 for asset in assets if asset.get("slope_id")),
            "invalid_assets": sum(asset["validation_status"] == "invalid" for asset in assets),
            "assets_with_reviewed_slope": sum(bool(asset.get("slope_id")) and asset.get("slope_review_status") == "approved" and asset["validation_status"] == "valid" for asset in assets),
            "slope_attribution_candidates": attribution["summary"],
        },
        "issues": issues,
        "limitations": [
            "OCR页面证据仅统计当前解析结果引用的文件；历史渲染缓存保留在磁盘但不进入目录和图谱",
            "当前盘点仅验证文件级资产目录和元数据完整性，不执行遥感变化检测或点云配准",
            "缺少原始空间样例时，坐标系、覆盖范围和采集时间保持未知",
            "PDF渲染图只能作为视觉证据，不能替代GeoTIFF、LAS/LAZ或原始三维模型",
            "边坡归属候选仅由当前页面识别出的桩号生成，须对照原页人工核验；不会自动形成已审核归属或图谱边",
        ],
    }
    ensure_dir(output_dir)
    write_json(output_dir / "data_contract.json", multimodal_contract())
    write_json(output_dir / "asset_catalog.json", catalog)
    write_json(output_dir / "quality_report.json", quality)
    write_json(output_dir / "slope_attribution_candidates.json", attribution)
    return {"catalog": catalog, "quality": quality, "contract": multimodal_contract(), "attribution": attribution}
