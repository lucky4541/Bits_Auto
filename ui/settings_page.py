"""Configuration: Basic / Advanced tabs bound to config/config.yaml."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .widgets.common import Card, Page, PathField, button, label

BASIC = [
    ("GENERAL", [("paths.output_dir", "Output directory", "dir"), ("paths.cache_dir", "Cache directory", "dir"),
                 ("auto_open_report", "Auto-open report", "bool"), ("save_intermediate_json", "Save intermediate files", "bool")]),
    ("OCR", [("ocr.engine", "OCR engine", ["auto", "tesseract", "paddle", "none"]), ("ocr.languages", "Languages (comma separated)", "list"),
             ("ocr.dpi", "DPI", (150, 600)), ("ocr.confidence_threshold", "Confidence threshold (%)", (0, 100)),
             ("ocr.tesseract_cmd", "Tesseract executable (optional)", "file")]),
    ("BITS", [("bits.version", "BITS version", "str"), ("paths.dtd_dir", "DTD directory", "dir"),
              ("links.link_validation", "Validation mode", ["strict", "report"]), ("paths.samples_dir", "Samples directory", "dir"),
              ("paths.input_dir", "Input directory", "dir")]),
]
ADVANCED = [
    ("LAYOUT", [("layout.column_detection", "Column detection", "bool"), ("layout.table_detection", "Table detection", "bool"),
                ("layout.figure_detection", "Figure detection", "bool"), ("images.include_unlabeled_images", "Keep unlabelled images as figures", "bool"),
                ("images.dpi", "Image DPI", (72, 600))]),
    ("LINKS", [("links.citation_detection", "Citation detection", "bool"), ("links.cross_reference_detection", "Cross-reference detection", "bool"),
               ("links.bibliography_citations", "Bibliography citations", "bool"), ("placement.policy", "Figure/table placement", ["learned", "physical"])]),
    ("QA", [("qa.content_coverage_min", "Minimum content coverage", "float"), ("qa.page_coverage_warn", "Page coverage warning", "float")]),
    ("PERFORMANCE", [("performance.workers", "Workers", (1, 16)), ("performance.memory_mode", "Memory mode", ["normal", "low"]),
                     ("performance.resume", "Resume mode", "bool")]),
    ("ADVANCED", [("mode", "Mode", ["production", "development"]), ("save_intermediate_json", "Save intermediate JSON", "bool"),
                  ("qa.visual_overlays", "Visual overlays", "bool"), ("bits.system_id", "DOCTYPE system identifier", "str"),
                  ("bits.xml_lang", "xml:lang", "str")]),
]


class SettingsPage(Page):
    def __init__(self, ctx):
        super().__init__("Configuration", "Sensible defaults are set — most conversions need no changes here.")
        self.ctx = ctx
        self.fields = {}
        tabs = QTabWidget()
        tabs.addTab(self._tab(BASIC), "Basic")
        tabs.addTab(self._tab(ADVANCED), "Advanced")
        self.add(tabs)
        h = QHBoxLayout()
        h.addStretch(1)
        rs = button("Reload", icon_name="refresh")
        rs.clicked.connect(self.load)
        sv = button("Save", "primary", "check")
        sv.clicked.connect(self.save)
        h.addWidget(rs)
        h.addWidget(sv)
        w = QWidget()
        w.setLayout(h)
        self.add(w)
        self.finish()
        self.load()

    def _tab(self, groups):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(12)
        for title, items in groups:
            c = Card()
            c.lay.addWidget(label(title, "sectionTitle"))
            f = QFormLayout()
            f.setHorizontalSpacing(18)
            f.setVerticalSpacing(8)
            for key, text, kind in items:
                if kind == "bool":
                    wdg = QCheckBox()
                elif kind in ("dir", "file"):
                    wdg = PathField("", mode=kind)
                elif isinstance(kind, list):
                    wdg = QComboBox()
                    wdg.addItems(kind)
                elif isinstance(kind, tuple):
                    wdg = QSpinBox()
                    wdg.setRange(*kind)
                elif kind == "float":
                    wdg = QDoubleSpinBox()
                    wdg.setRange(0, 1)
                    wdg.setSingleStep(0.01)
                    wdg.setDecimals(3)
                else:
                    wdg = QLineEdit()
                self.fields.setdefault(key, []).append((wdg, kind))
                f.addRow(label(text, "muted"), wdg)
            c.lay.addLayout(f)
            v.addWidget(c)
        v.addStretch(1)
        return w

    def load(self):
        cfg = self.ctx.cfg
        for key, lst in self.fields.items():
            val = cfg.get(key)
            for wdg, kind in lst:
                if kind == "bool":
                    wdg.setChecked(bool(val))
                elif kind in ("dir", "file"):
                    wdg.setText(str(cfg.path(key.split(".", 1)[1])) if key.startswith("paths.") else str(val or ""))
                elif isinstance(kind, list):
                    i = wdg.findText(str(val))
                    wdg.setCurrentIndex(max(0, i))
                elif isinstance(kind, tuple):
                    wdg.setValue(int(val or kind[0]))
                elif kind == "float":
                    wdg.setValue(float(val or 0))
                elif kind == "list":
                    wdg.setText(", ".join(val or []))
                else:
                    wdg.setText(str(val or ""))

    def save(self):
        cfg = self.ctx.cfg
        for key, lst in self.fields.items():
            wdg, kind = lst[-1]
            if kind == "bool":
                v = wdg.isChecked()
            elif kind in ("dir", "file"):
                v = wdg.text()
            elif isinstance(kind, list):
                v = wdg.currentText()
            elif isinstance(kind, tuple):
                v = wdg.value()
            elif kind == "float":
                v = round(wdg.value(), 3)
            elif kind == "list":
                v = [x.strip() for x in wdg.text().split(",") if x.strip()]
            else:
                v = wdg.text()
            cfg.set(key, v)
        cfg.save()
        self.ctx.refresh_status()
        QMessageBox.information(self, "Configuration", "Settings saved to config/config.yaml.")
