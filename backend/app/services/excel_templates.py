"""Build the downloadable governed Excel template of an evidence domain from its published template
artifact (columns), the canonical schema (allowed enum values) and lookups (accepted source labels).
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.domain.artifact_content import find_field

REQUIRED_FILL = PatternFill("solid", fgColor="4F5BD5")
OPTIONAL_FILL = PatternFill("solid", fgColor="E8EAF6")
DATA_ROWS_WITH_LISTS = 5000
LIST_FORMULA_MAX = 250  # Excel limit for an inline list is 255 characters


def allowed_values(col: dict, canonical: dict, lookups: dict[str, dict]) -> list[str]:
    """Values a user may type: the lookup's source labels, else the canonical enum values."""
    lookup = lookups.get(col.get("lookup", ""))
    if lookup:
        return list(dict.fromkeys(e["source"] for e in lookup["entries"]))
    t = col.get("target")
    if t:
        field = find_field(canonical, t["entity"], t["field"])
        if field and field.get("type") == "enum":
            return list(field["values"])
    return []


def build_template_workbook(ref: str, label: str, template: dict, canonical: dict,
                            lookups: dict[str, dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = template.get("sheetName", "Data")
    for i, col in enumerate(template["columns"], start=1):
        cell = ws.cell(row=1, column=i, value=col["header"])
        required = bool(col.get("required"))
        cell.font = Font(bold=True, color="FFFFFF" if required else "1F2340")
        cell.fill = REQUIRED_FILL if required else OPTIONAL_FILL
        cell.alignment = Alignment(vertical="center")
        note = ("Required. " if required else "Optional. ") + col.get("description", "")
        if col.get("example"):
            note += f" Example: {col['example']}"
        cell.comment = Comment(note.strip(), "Product Rationalisation Agent")
        ws.column_dimensions[get_column_letter(i)].width = max(14, len(col["header"]) + 4)
        values = allowed_values(col, canonical, lookups)
        formula = '"' + ",".join(v.replace('"', "") for v in values) + '"'
        if values and len(formula) <= LIST_FORMULA_MAX and not any("," in v for v in values):
            # Suggest values but accept others: the lookup decides later (Step 5).
            dv = DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=False)
            ws.add_data_validation(dv)
            dv.add(f"{get_column_letter(i)}2:{get_column_letter(i)}{DATA_ROWS_WITH_LISTS}")
    ws.freeze_panes = "A2"

    info = wb.create_sheet("Instructions")
    info.append([f"{label} ({ref})"])
    info["A1"].font = Font(bold=True, size=13)
    info.append(["Fill the 'Data' sheet. Keep the header names. Keep Product IDs and source keys exactly as in "
                 "the source system. Save as .xlsx (no macros, no password)."])
    info.append(["Only product metadata and aggregates: no subscriber, customer or personal data."])
    info.append([])
    info.append(["Column", "Required", "Description", "Canonical field", "Accepted values", "Example"])
    for c in info[5]:
        c.font = Font(bold=True)
    for col in template["columns"]:
        t = col.get("target")
        target = f"{t['entity']}.{t['field']}" if t else (f"relationship {col['relationship']}"
                                                           if col.get("relationship") else "")
        required = "Yes" if col.get("required") else ("One of: " + col["oneOfGroup"] if col.get("oneOfGroup") else "No")
        info.append([col["header"], required, col.get("description", ""), target,
                     ", ".join(allowed_values(col, canonical, lookups)), col.get("example", "")])
    for letter, width in zip("ABCDEF", (30, 16, 50, 34, 50, 20), strict=True):
        info.column_dimensions[letter].width = width

    wb.properties.title = label
    wb.properties.subject = ref
    wb.properties.creator = "Product Rationalisation Agent"
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
