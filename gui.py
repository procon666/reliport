"""AI 调研报告生成器 - 桌面版（PyQt5）
双击运行：填主题 → 选选项 → 点生成 → 出报告（PDF/MD）
用法：./venv/bin/python gui.py
"""
import os
import sys
import threading
from pathlib import Path

# 确保能 import research_agent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QPushButton, QLabel, QTextBrowser,
    QFileDialog, QGroupBox, QMessageBox, QProgressBar, QFrame
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor

from research_agent.pipeline import run as pipeline_run
from research_agent.config import settings


# 澄清选项
CLARIFY_OPTIONS = {
    "audience": ["技术团队", "管理层/决策者", "投资人", "我自己了解"],
    "purpose": ["选型决策", "写文章/分享", "立项评估", "单纯了解"],
    "time_range": ["近一年", "近三年", "全周期/历史"],
    "market": ["全球", "中国", "欧美"],
    "depth": ["快览（要点）", "标准（结构完整）", "深度（含数据对比）"],
    "angle": ["机会为主", "风险为主", "机会与风险并重"],
}
OPTION_LABELS = {
    "audience": "目标受众", "purpose": "用途", "time_range": "时间范围",
    "market": "市场范围", "depth": "深度", "angle": "视角",
}


class Worker(QThread):
    """后台运行 pipeline，避免界面卡死。"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, topic, brief):
        super().__init__()
        self.topic = topic
        self.brief = brief

    def run(self):
        try:
            result = pipeline_run(
                self.topic, interactive=False, auto_brief=self.brief,
                output_format="both", max_queries=4,
            )
            files = {kind: str(p) for kind, p in result["result"]["produced"]}
            self.finished.emit({"md": result["md"], "files": files,
                                "topic": self.topic})
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.result = None
        self.setWindowTitle("AI 调研报告生成器")
        self.resize(860, 720)
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # 标题
        title = QLabel("AI 调研报告生成器")
        title.setObjectName("title")
        root.addWidget(title)
        sub = QLabel("输入主题，AI 自动联网调研、写报告，一键导出 PDF / Markdown")
        sub.setObjectName("sub")
        root.addWidget(sub)

        # 主题
        topic_box = QFormLayout()
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("例如：AI 编程助手市场格局 / 咖啡行业分析 / 低代码平台")
        topic_box.addRow("研究报告主题：", self.topic)
        root.addLayout(topic_box)

        # 澄清选项
        opt_group = QGroupBox("澄清选项（可按需调整，可保持默认）")
        form = QFormLayout(opt_group)
        self.combos = {}
        for key, label in OPTION_LABELS.items():
            cb = QComboBox()
            cb.addItems(CLARIFY_OPTIONS[key])
            if key == "angle":
                cb.setCurrentText("机会与风险并重")
            self.combos[key] = cb
            form.addRow(f"{label}：", cb)
        root.addWidget(opt_group)

        # 生成按钮
        self.btn = QPushButton("⚡ 生成调研报告")
        self.btn.setObjectName("genBtn")
        self.btn.setMinimumHeight(46)
        self.btn.clicked.connect(self._on_generate)
        root.addWidget(self.btn)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 不定进度
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        # 结果展示
        self.viewer = QTextBrowser()
        self.viewer.setObjectName("viewer")
        self.viewer.setVisible(False)
        root.addWidget(self.viewer, stretch=1)

        # 保存按钮
        btn_row = QHBoxLayout()
        self.save_pdf = QPushButton("💾 保存为 PDF")
        self.save_md = QPushButton("💾 保存为 Markdown")
        self.save_pdf.setEnabled(False)
        self.save_md.setEnabled(False)
        self.save_pdf.clicked.connect(lambda: self._save("pdf"))
        self.save_md.clicked.connect(lambda: self._save("md"))
        btn_row.addStretch()
        btn_row.addWidget(self.save_pdf)
        btn_row.addWidget(self.save_md)
        root.addLayout(btn_row)

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget { background: #0f2027; color: #e6edf3; font-size: 14px; }
            QLabel#title { font-size: 24px; font-weight: bold;
                           color: #79c0ff; }
            QLabel#sub { color: #8b949e; font-size: 13px; }
            QGroupBox { border: 1px solid #30363d; border-radius: 10px;
                        margin-top: 12px; padding: 14px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QLineEdit, QComboBox { background: #0d1117; border: 1px solid #30363d;
                        border-radius: 8px; padding: 10px 12px; color: #e6edf3; }
            QPushButton#genBtn { background: #2ea043; border: none; border-radius: 10px;
                        color: #fff; font-size: 16px; font-weight: bold; }
            QPushButton#genBtn:hover { background: #3fb950; }
            QPushButton#genBtn:disabled { background: #238636; opacity: .6; }
            QPushButton { background: #21262d; border: 1px solid #30363d;
                        border-radius: 8px; padding: 10px 18px; }
            QPushButton:disabled { color: #8b949e; }
            QTextBrowser#viewer { background: #fff; color: #1f2328;
                        border: 1px solid #30363d; border-radius: 10px;
                        padding: 16px; font-size: 14px; }
            QProgressBar { border: 1px solid #30363d; border-radius: 6px;
                        background: #0d1117; height: 10px; }
        """)

    def _on_generate(self):
        topic = self.topic.text().strip()
        if not topic:
            QMessageBox.warning(self, "提示", "请先输入研究报告主题")
            return
        brief = {
            "topic": topic,
            "audience": self.combos["audience"].currentText(),
            "purpose": self.combos["purpose"].currentText(),
            "time_range": self.combos["time_range"].currentText(),
            "market": self.combos["market"].currentText(),
            "depth": self.combos["depth"].currentText(),
            "angle": self.combos["angle"].currentText(),
            "must_include": [], "must_exclude": [], "format": ["pdf", "md"],
        }
        self.btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status.setText("AI 正在联网调研、写报告中（约 40~90 秒）…")
        self.viewer.setVisible(False)
        self.save_pdf.setEnabled(False)
        self.save_md.setEnabled(False)

        self.worker = Worker(topic, brief)
        self.worker.finished.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _on_done(self, result):
        self.result = result
        self.btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(f"✅ 生成完成（{result['topic']}）")
        # 展示报告（Markdown 渲染成文本）
        md_text = result["md"]
        self.viewer.setHtml(self._md_to_html(md_text))
        self.viewer.setVisible(True)
        self.save_pdf.setEnabled(True)
        self.save_md.setEnabled(True)

    def _on_fail(self, err):
        self.btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText("❌ 生成失败")
        QMessageBox.critical(self, "错误", f"生成失败：{err}")

    def _save(self, fmt):
        if not self.result:
            return
        files = self.result["files"]
        default = files.get(fmt) or files.get({"pdf": "pdf", "md": "markdown"}[fmt], "")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存报告",
            str(default) if default else f"report.{fmt}",
            "PDF 文件 (*.pdf)" if fmt == "pdf" else "Markdown 文件 (*.md)",
        )
        if not path:
            return
        src = files.get(fmt) or files.get({"pdf": "pdf", "md": "markdown"}[fmt])
        if src and os.path.exists(src):
            import shutil
            shutil.copy(src, path)
        else:
            # 从 md 现渲染 PDF
            if fmt == "pdf":
                self._render_pdf(path)
        QMessageBox.information(self, "完成", f"已保存到：{path}")

    def _render_pdf(self, path):
        try:
            from weasyprint import HTML
            html = self._md_to_html(self.result["md"])
            HTML(string=html).write_pdf(path)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"PDF 渲染失败：{e}")

    def _md_to_html(self, md_text):
        import markdown
        body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
        return f"""
        <style>
          body {{ font-family: sans-serif; line-height: 1.7; padding: 8px; }}
          h1 {{ font-size: 22px; border-bottom: 3px solid #2b6cb0; padding-bottom: 8px; }}
          h2 {{ color: #1a5276; border-left: 4px solid #2b6cb0; padding-left: 8px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th,td {{ border: 1px solid #ddd; padding: 6px 8px; }}
          blockquote {{ color: #666; border-left: 3px solid #ccc; padding-left: 10px; }}
        </style>
        {body}
        """


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
