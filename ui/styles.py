BUTTON_STYLE = """
    QPushButton {
        background-color: #FFC107;
        color: #000000;
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #FFD54F;
    }
    QPushButton:pressed {
        background-color: #FFA000;
    }
"""

LINE_EDIT_STYLE = """
    QLineEdit {
        background-color: #2A2A2A;
        border: 2px solid #424242;
        border-radius: 4px;
        padding: 8px;
        color: white;
    }
    QLineEdit:focus {
        border: 2px solid #FFC107;
    }
"""

SCROLL_AREA_STYLE = """
    QScrollArea {
        border: none;
        background-color: transparent;
    }
    QScrollBar:vertical {
        background-color: #2A2A2A;
        width: 12px;
        border-radius: 6px;
    }
    QScrollBar::handle:vertical {
        background-color: #424242;
        border-radius: 6px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #FFC107;
    }
"""

SONG_LIST_STYLE = """
    QListWidget {
        background-color: #2A2A2A;
        border: 2px solid #424242;
        border-radius: 4px;
        color: white;
    }
    QListWidget::item {
        padding: 8px;
    }
    QListWidget::item:selected {
        background-color: #424242;
        color: white;
    }
    QListWidget::item:hover {
        background-color: #383838;
    }
"""

TOOL_BUTTON_STYLE = """
    QToolButton {
        background-color: #2A2A2A;
        color: white;
        border: 2px solid #424242;
        border-radius: 8px;
        padding: 8px;
        font-weight: bold;
    }
    QToolButton:checked {
        background-color: #424242;
        border: 2px solid #FFC107;
    }
    QToolButton:hover {
        background-color: #383838;
    }
"""

PROGRESS_BAR_STYLE = """
    QProgressBar {
        background-color: #2A2A2A;
        border: 2px solid #424242;
        border-radius: 4px;
        color: black;
        text-align: center;
    }
    QProgressBar::chunk {
        background-color: #FFC107;
        border-radius: 2px;
    }
"""

MESSAGE_BOX_STYLE = """
    QMessageBox {
        background-color: #1E1E1E;
    }
    QMessageBox QLabel {
        color: white;
        font-size: 12px;
        background-color: transparent;
    }
    QMessageBox QPushButton {
        background-color: #FFC107;
        color: #000000;
        border: none;
        border-radius: 4px;
        padding: 8px 16px;
        font-weight: bold;
        min-width: 80px;
    }
    QMessageBox QPushButton:hover {
        background-color: #FFD54F;
    }
    QMessageBox QPushButton:pressed {
        background-color: #FFA000;
    }
"""

PROGRESS_DIALOG_STYLE = """
    QProgressDialog {
        background-color: #1E1E1E;
    }
    QProgressDialog QLabel {
        color: white;
        font-size: 12px;
        padding: 8px;
        background-color: transparent;
    }
    QProgressDialog QProgressBar {
        background-color: #2A2A2A;
        border: 2px solid #424242;
        border-radius: 4px;
        color: black;
        text-align: center;
        height: 20px;
    }
    QProgressDialog QProgressBar::chunk {
        background-color: #FFC107;
        border-radius: 2px;
    }
"""

RADIO_BUTTON_STYLE = """
    QRadioButton {
        color: white;
        spacing: 8px;
        padding: 4px;
    }
    QRadioButton::indicator {
        width: 14px;
        height: 14px;
        border-radius: 8px;
        border: 2px solid #424242;
        background-color: #2A2A2A;
    }
    QRadioButton::indicator:checked {
        border: 2px solid #FFC107;
        background-color: #FFC107;
    }
    QRadioButton::indicator:hover {
        border: 2px solid #FFD54F;
    }
"""

GROUP_BOX_STYLE = """
    QGroupBox {
        color: #B0BEC5;
        border: 1px solid #424242;
        border-radius: 4px;
        margin-top: 1.5em;
        padding: 15px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top center;
        padding: 0 5px;
        color: #FFC107;
        font-weight: bold;
    }
"""
