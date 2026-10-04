"""Report builder: choose a report, period, vehicles and columns; preview; download.

Reports are read-only, so the builder uses GET parameters: a report's URL can be bookmarked
or shared, and the download links reuse exactly the parameters that were previewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from flask import Response, abort, current_app, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Vehicle
from app.reports import bp
from app.services.energy import local_today
from app.services.exporters import ReportMeta, file_stamp, format_value, to_csv, to_pdf, to_xlsx
from app.services.reports import MAX_DAYS, MAX_ROWS, REPORTS, Column, ReportDef
from app.utils import utcnow
from app.vehicles.access import visible_vehicles_stmt

PREVIEW_ROWS = 100
FORMATS = {
    "csv": ("text/csv", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pdf": ("application/pdf", "pdf"),
}


@dataclass
class ReportRequest:
    report: ReportDef
    first: date
    last: date
    vehicles: list[Vehicle]
    columns: list[Column]
    title: str
    errors: list[str]

    @property
    def period(self) -> str:
        return f"{self.first:%d %b %Y} – {self.last:%d %b %Y}"


def _parse_date(raw: str | None, default: date) -> date | None:
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_request(all_vehicles: list[Vehicle]) -> ReportRequest:
    args, errors = request.args, []
    report = REPORTS.get(args.get("type", "fleet_summary"), REPORTS["fleet_summary"])

    today = local_today()
    first = _parse_date(args.get("start"), today - timedelta(days=29))
    last = _parse_date(args.get("end"), today)
    if first is None or last is None:
        errors.append("Dates must be in YYYY-MM-DD format.")
        first, last = today - timedelta(days=29), today
    if last > today:
        last = today
    if first > last:
        errors.append("The start date must be on or before the end date.")
        first = last
    if (last - first).days + 1 > MAX_DAYS:
        errors.append(f"Reports cover at most {MAX_DAYS} days; the start date was moved.")
        first = last - timedelta(days=MAX_DAYS - 1)

    # Only the user's own vehicles can ever be included; unknown ids are ignored.
    wanted = {int(v) for v in args.getlist("vehicle") if v.isdigit()}
    vehicles = [v for v in all_vehicles if v.id in wanted] or all_vehicles

    keys = set(args.getlist("col"))
    columns = [c for c in report.columns if c.key in keys] or list(report.columns)

    title = re.sub(r"\s+", " ", args.get("title", "")).strip()[:120] or report.title
    return ReportRequest(report, first, last, vehicles, columns, title, errors)


@bp.route("/")
@login_required
def builder():
    all_vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    req = parse_request(all_vehicles)
    data = req.report.build(req.vehicles, req.first, req.last) if "type" in request.args else None
    preview = None
    if data is not None:
        preview = [
            [format_value(c, row.get(c.key)) for c in req.columns]
            for row in data.rows[:PREVIEW_ROWS]
        ]
    return render_template(
        "reports/builder.html",
        reports=REPORTS,
        req=req,
        all_vehicles=all_vehicles,
        selected_ids={v.id for v in req.vehicles} if "vehicle" in request.args else set(),
        data=data,
        preview=preview,
        preview_rows=PREVIEW_ROWS,
        formats=FORMATS,
    )


@bp.route("/download/<fmt>")
@login_required
def download(fmt: str):
    if fmt not in FORMATS:
        abort(404)
    all_vehicles = db.session.execute(visible_vehicles_stmt()).scalars().all()
    req = parse_request(all_vehicles)
    data = req.report.build(req.vehicles, req.first, req.last)
    if len(data.rows) > MAX_ROWS:
        abort(413, description=f"This report has over {MAX_ROWS:,} rows; choose a shorter period.")

    meta = ReportMeta(
        title=req.title,
        period=req.period,
        generated_by=f"{current_user.name} ({current_user.role.label})",
        generated_at=utcnow(),
    )
    if fmt == "csv":
        body = to_csv(req.columns, data)
    elif fmt == "xlsx":
        body = to_xlsx(req.columns, data, meta)
    else:
        body = to_pdf(req.columns, data, meta)

    mimetype, ext = FORMATS[fmt]
    filename = (
        f"ev-fleet-{req.report.key.replace('_', '-')}-{file_stamp(req.first, req.last)}.{ext}"
    )
    current_app.logger.info(
        "%s exported %s (%s, %d rows)", current_user.email, req.report.key, fmt, len(data.rows)
    )
    return Response(
        body,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
