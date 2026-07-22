import os
import sys

from PySide6.QtWidgets import QApplication, QMainWindow
from qt_material import apply_stylesheet

from build_info import APP_VERSION, application_title
from ui.main_window import GTAIVEditor
from utils import check_ffmpeg, install_ffmpeg, resource_path


class SplashWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(application_title())
        self.setFixedSize(400, 200)
        self.setStyleSheet("background-color: #121212;")


def run_packaged_smoke_test() -> int:
    required = (
        "assets",
        os.path.join("tools", "ivam.exe"),
        os.path.join("tools", "IVAudioConv.exe"),
    )
    missing = [resource_path(path) for path in required if not os.path.exists(resource_path(path))]
    if missing:
        print("Missing packaged resources:")
        for path in missing:
            print(path)
        return 1

    from PIL import Image  # noqa: F401 - verifies the packaged decoder
    from texfury import Texture  # noqa: F401 - verifies the packaged encoder

    print(f"GTA IV Modding Toolkit {APP_VERSION} smoke test passed")
    return 0


def main():
    if "--smoke-test" in sys.argv:
        raise SystemExit(run_packaged_smoke_test())

    app = QApplication(sys.argv)

    extra = {
        'density_scale': '-2',
        'font_family': 'Montserrat',
        'primaryTextColor': '#FFFFFF',
        'secondaryTextColor': '#B0BEC5',
        'primaryColor': '#000000',
        'secondaryColor': '#424242',
        'accentColor': '#FFC107',
        'backgroundColor': '#121212',
        'windowColor': '#121212',
        'dialogColor': '#212121',
        'borderColor': '#424242',
        'hoverColor': '#FFC107',
        'focusColor': '#FFC107',
        'buttonBackgroundColor': '#FFC107',
        'buttonForegroundColor': '#000000',
        'buttonBorderColor': '#FFC107',
    }

    apply_stylesheet(app, theme='dark_cyan.xml', extra=extra)

    splash = SplashWindow()
    splash.show()

    if not check_ffmpeg():
        if not install_ffmpeg(splash):
            sys.exit(1)

    editor = GTAIVEditor()
    editor.show()
    splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
